"""OpenSky Network connector — live aircraft surveillance data.

Free for non-commercial use, no authentication required for public API.
Authenticated access increases rate limits.
API docs: https://openskynetwork.github.io/opensky-api/

Military/special flight detection based on known callsign prefixes and
ICAO24 hex ranges for military aircraft.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation


# ── Military callsign prefix database ────────────────────────────────────────

# US Military
_US_MILITARY_PREFIXES = frozenset([
    # USAF airlift / tanker
    "RCH", "REACH", "JAKE", "POLO", "ROOK",
    # Bombers
    "BONE", "DOOM", "DARK", "BUCK", "DAGGER",
    # Command & control / special mission
    "VENUS",    # E-4B Nightwatch (nuclear command post)
    "FORTE",    # E-3 AWACS
    "MYSTIC",   # RC-135 Rivet Joint reconnaissance
    "COBRA",    # EC-130 Compass Call
    "STEEL",    # Various C2 aircraft
    # Fighters / trainers
    "VIPER", "EAGLE", "FALCON", "RAPTOR", "GHOST", "SHADOW",
    "BOXER", "SABER", "LANCE", "SWORD", "SPEAR", "MAGIC",
    "RANGER", "TOPGUN",
    # Army aviation
    "ARMY",
    # Navy
    "NAVY", "NAVAL",
    # Marines
    "USMC", "MARINE",
    # Coast Guard
    "COAST", "CGAS",
    # Special operations
    "ANVIL", "DEMON", "IRON", "TITAN", "ZEUS", "APOLLO",
    "HERMES", "SIRIUS", "ORION", "ARES", "OLYMPUS",
    # ISR
    "DUKE", "JEDI", "LOBO", "WOLF",
])

# Allied military
_ALLIED_MILITARY_PREFIXES = frozenset([
    # UK Royal Air Force
    "ASCOT", "BRITON", "TARTAN", "DEVLIN", "COMET", "RAF",
    # German Luftwaffe
    "GAF",
    # French Armée de l'Air
    "FAF", "COTAM",
    # NATO
    "NATO", "AWACS", "E3TF",
    # Canadian
    "CFC", "CANFORCE",
    # Australian
    "RAAF",
    # Israeli
    "IAF",
    # Turkish
    "TUAF",
])

# Adversary / monitoring
_ADVERSARY_PREFIXES = frozenset([
    "RFF",   # Russian Federation Forces
    "RUSSIA",
    "CHINA",
    "PLA",
    "PLAN",  # PLA Navy
    "PLAAF", # PLA Air Force
])

_ALL_MILITARY_PREFIXES = _US_MILITARY_PREFIXES | _ALLIED_MILITARY_PREFIXES | _ADVERSARY_PREFIXES

# Emergency squawk codes
_EMERGENCY_SQUAWKS = {"7500", "7600", "7700"}  # hijack, radio fail, emergency

# "Interesting" aircraft patterns (government, VIP, spy)
_INTERESTING_PATTERNS = frozenset([
    "SAM",    # Special Air Mission (US president/VP)
    "C32A",   # Air Force Two
    "C40",    # VIP transport
    "E8",     # JSTARS
    "E6",     # TACAMO (nuclear sub comms)
    "RQ4",    # Global Hawk
    "U2",     # U-2 spy plane
    "SR71",   # Blackbird
    "RC135",  # Rivet Joint
])


def _classify_flight(callsign: str, squawk: str, origin_country: str, icao24: str) -> tuple[str, float, str]:
    """Classify a flight and return (category, significance, label).

    Categories: military_us, military_allied, military_adversary, emergency, interesting, civilian
    """
    cs = callsign.upper().strip()

    # Emergency squawk — highest priority
    if squawk in _EMERGENCY_SQUAWKS:
        labels = {"7500": "HIJACK", "7600": "RADIO FAILURE", "7700": "EMERGENCY"}
        return "emergency", 9.0, f"SQUAWK {squawk}: {labels.get(squawk, 'EMERGENCY')}"

    # Check military prefixes (up to first 4 chars of callsign)
    for length in (6, 5, 4, 3, 2):
        prefix = cs[:length]
        if prefix in _US_MILITARY_PREFIXES:
            return "military_us", 7.0, f"US Military ({prefix})"
        if prefix in _ALLIED_MILITARY_PREFIXES:
            return "military_allied", 6.5, f"Allied Military ({prefix})"
        if prefix in _ADVERSARY_PREFIXES:
            return "military_adversary", 8.0, f"Adversary Military ({prefix})"

    # Check interesting patterns
    for pat in _INTERESTING_PATTERNS:
        if pat in cs:
            return "interesting", 6.0, f"Special Mission ({pat})"

    # Country-based military inference
    country_upper = origin_country.upper()
    if "RUSSIA" in country_upper or "RUSSIAN" in country_upper:
        if cs.startswith(("RA-", "RF-", "RU-")):
            return "military_adversary", 5.5, "Russian Federation"
    if "CHINA" in country_upper or "CHINESE" in country_upper:
        return "interesting", 5.0, "Chinese Aircraft"
    if "IRAN" in country_upper:
        return "interesting", 6.0, "Iranian Aircraft"
    if "NORTH KOREA" in country_upper:
        return "military_adversary", 8.5, "North Korean Aircraft"

    return "civilian", 1.0, "Civilian"


class OpenSkyConnector:
    """Fetches live military aircraft from adsb.lol (primary) or OpenSky Network (fallback).

    Supports three modes:
    - mode='all': all aircraft in bbox (high data volume)
    - mode='military': only military/emergency/interesting aircraft
    - mode='emergency': only squawk emergency flights
    """

    name = "opensky"
    ADSB_LOL_URL = "https://api.adsb.lol/v2/mil"
    API_URL = "https://opensky-network.org/api/states/all"

    def __init__(
        self,
        *,
        bbox: tuple[float, float, float, float] | None = None,
        username: str | None = None,
        password: str | None = None,
        mode: str = "military",  # 'all' | 'military' | 'emergency'
        timeout_s: float = 20.0,
    ) -> None:
        self.bbox = bbox
        self.username = username
        self.password = password
        self.mode = mode
        self.timeout_s = timeout_s

    def _collect_adsb_lol(self, query: str | None, now: datetime) -> tuple[list[Observation], list[dict], dict]:
        """Fetch live military aircraft from adsb.lol API (primary source)."""
        observations: list[Observation] = []
        geo_events: list[dict] = []
        metadata: dict = {"source": "adsb.lol"}
        military_count = 0
        emergency_count = 0

        try:
            with httpx.Client(timeout=self.timeout_s, follow_redirects=True,
                              headers={"User-Agent": "STSIA-Monitor/1.0"}) as client:
                resp = client.get(self.ADSB_LOL_URL)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            metadata["error"] = str(exc)
            return observations, geo_events, metadata

        ac_list = data.get("ac", []) or []
        metadata["aircraft_total"] = len(ac_list)
        expires = now + timedelta(minutes=5)

        for ac in ac_list:
            lat = ac.get("lat")
            lon = ac.get("lon")
            if lat is None or lon is None:
                continue

            icao24 = ac.get("hex", "")
            callsign = (ac.get("flight") or icao24).strip()
            aircraft_type = ac.get("t", "?")
            alt_baro = ac.get("alt_baro", 0)
            speed = ac.get("gs", 0)
            heading = ac.get("track", 0)
            squawk = str(ac.get("squawk", "") or "")
            reg = ac.get("r", "")

            # Apply query filter
            if query:
                q_lower = query.lower()
                if q_lower not in callsign.lower() and q_lower not in icao24.lower():
                    continue

            is_emergency = squawk in _EMERGENCY_SQUAWKS
            if is_emergency:
                layer = "aircraft_emergency"
                significance = 9.0
                label = f"SQUAWK {squawk}"
                emergency_count += 1
            else:
                layer = "aircraft_military"
                significance = 7.0
                label = f"Military ({aircraft_type})"
                military_count += 1

            alt_str = f"{alt_baro}ft" if alt_baro else "?"
            speed_str = f" {speed:.0f}kts" if speed else ""
            hdg_str = f" hdg:{heading:.0f}°" if heading else ""

            claim = (
                f"[{label}] {callsign} ({aircraft_type}{f'/{reg}' if reg else ''}) "
                f"at ({lat:.3f},{lon:.3f}) alt:{alt_str}{speed_str}{hdg_str}"
            )

            observations.append(Observation(
                source=f"adsb_lol:{'emergency' if is_emergency else 'military'}",
                claim=claim,
                url=f"https://globe.adsbexchange.com/?icao={icao24}",
                captured_at=now,
                reliability_hint=0.95 if is_emergency else 0.90,
            ))

            geo_events.append({
                "layer": layer,
                "source_id": f"adsblol_{icao24}",
                "title": claim[:400],
                "latitude": lat,
                "longitude": lon,
                "altitude": alt_baro,
                "magnitude": significance,
                "event_time": now,
                "expires_at": expires,
                "properties": {
                    "icao24": icao24,
                    "callsign": callsign,
                    "aircraft_type": aircraft_type,
                    "registration": reg,
                    "velocity": speed,
                    "heading": heading,
                    "altitude_ft": alt_baro,
                    "squawk": squawk,
                    "is_emergency": is_emergency,
                    "label": label,
                    "adsb_url": f"https://globe.adsbexchange.com/?icao={icao24}",
                    "source": "adsb.lol",
                },
            })

        metadata["military_count"] = military_count
        metadata["emergency_count"] = emergency_count
        return observations, geo_events, metadata

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        geo_events: list[dict] = []
        metadata: dict = {"bbox": self.bbox, "mode": self.mode}
        now = datetime.now(UTC)

        # Try adsb.lol first (primary source — pre-filtered military, free, no auth)
        obs_lol, geo_lol, meta_lol = self._collect_adsb_lol(query, now)
        if geo_lol:
            observations.extend(obs_lol)
            geo_events.extend(geo_lol)
            metadata.update(meta_lol)
            metadata["geo_events"] = geo_events
            metadata["military_count"] = meta_lol.get("military_count", 0)
            metadata["emergency_count"] = meta_lol.get("emergency_count", 0)
            return ConnectorResult(connector=self.name, observations=observations, metadata=metadata)

        # Fallback: OpenSky Network
        metadata["adsb_lol_error"] = meta_lol.get("error", "no results")

        params: dict = {}
        if self.bbox:
            lamin, lomin, lamax, lomax = self.bbox
            params.update({
                "lamin": str(lamin), "lomin": str(lomin),
                "lamax": str(lamax), "lomax": str(lomax),
            })

        auth = None
        if self.username and self.password:
            auth = (self.username, self.password)

        try:
            with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
                resp = client.get(self.API_URL, params=params, auth=auth)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            metadata["error"] = str(exc)
            return ConnectorResult(connector=self.name, observations=[], metadata=metadata)

        states = data.get("states", []) or []
        timestamp = data.get("time", 0)
        metadata["aircraft_total"] = len(states)
        metadata["api_time"] = timestamp
        military_count = 0
        emergency_count = 0
        expires = now + timedelta(minutes=5)

        for sv in states:
            if len(sv) < 17:
                continue

            icao24 = sv[0] or ""
            callsign = (sv[1] or "").strip()
            origin_country = sv[2] or ""
            longitude = sv[5]
            latitude = sv[6]
            baro_altitude = sv[7]
            on_ground = sv[8]
            velocity = sv[9]
            true_track = sv[10]
            squawk = sv[14] or ""

            if latitude is None or longitude is None:
                continue

            # Classify the flight
            category, significance, label = _classify_flight(callsign, squawk, origin_country, icao24)

            # Filter based on mode
            if self.mode == "emergency" and category != "emergency":
                continue
            elif self.mode == "military" and category not in (
                "military_us", "military_allied", "military_adversary", "emergency", "interesting"
            ):
                continue
            # mode='all' passes everything

            # Apply query filter
            if query:
                q_lower = query.lower()
                if (q_lower not in callsign.lower()
                        and q_lower not in origin_country.lower()
                        and q_lower not in icao24.lower()
                        and q_lower not in label.lower()):
                    continue

            alt_str = f"{baro_altitude:.0f}m" if baro_altitude else ("GND" if on_ground else "?")
            vel_str = f" {velocity:.0f}m/s" if velocity else ""
            hdg_str = f" hdg:{true_track:.0f}°" if true_track else ""

            claim = (
                f"[{label}] {callsign or icao24} ({origin_country}) "
                f"at ({latitude:.3f},{longitude:.3f}) alt:{alt_str}{vel_str}{hdg_str}"
            )

            # Map category to layer sub-type
            if category == "emergency":
                layer = "aircraft_emergency"
                emergency_count += 1
            elif category in ("military_us", "military_allied", "military_adversary", "interesting"):
                layer = "aircraft_military"
                military_count += 1
            else:
                layer = "aircraft"

            observations.append(Observation(
                source=f"opensky:{category}",
                claim=claim,
                url=f"https://opensky-network.org/aircraft-profile?icao24={icao24}",
                captured_at=now,
                reliability_hint=0.95 if category == "emergency" else 0.85,
            ))

            geo_events.append({
                "layer": layer,
                "source_id": f"opensky_{icao24}",
                "title": claim[:400],
                "latitude": latitude,
                "longitude": longitude,
                "altitude": baro_altitude,
                "magnitude": significance,
                "event_time": now,
                "expires_at": expires,
                "properties": {
                    "icao24": icao24,
                    "callsign": callsign,
                    "origin_country": origin_country,
                    "velocity": velocity,
                    "heading": true_track,
                    "altitude_m": baro_altitude,
                    "on_ground": on_ground,
                    "squawk": squawk,
                    "category": category,
                    "label": label,
                    "is_emergency": category == "emergency",
                    "adsb_url": f"https://globe.adsbexchange.com/?icao={icao24}",
                },
            })

        metadata["geo_events"] = geo_events
        metadata["military_count"] = military_count
        metadata["emergency_count"] = emergency_count
        return ConnectorResult(connector=self.name, observations=observations, metadata=metadata)


def get_military_flights_global() -> ConnectorResult:
    """Convenience: fetch military flights globally (no bbox restriction)."""
    return OpenSkyConnector(mode="military").collect()


def get_emergency_flights() -> ConnectorResult:
    """Convenience: fetch only emergency squawk flights."""
    return OpenSkyConnector(mode="emergency").collect()
