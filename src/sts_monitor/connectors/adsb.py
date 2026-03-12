"""ADS-B Exchange connector — military and civilian aircraft tracking.

Uses the free ADS-B Exchange API v2 for comprehensive aircraft surveillance
including military aircraft that are filtered out of FlightRadar24.
Also falls back to OpenSky if ADS-B Exchange is unreachable.

No authentication required for basic access.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation

# Known military ICAO hex ranges (partial list)
_MILITARY_ICAO_PREFIXES: set[str] = {
    "ae",  # US military (USAF, Army, Navy, Marines)
    "af",  # US military
    "43c",  # UK military
    "3f",  # German military
    "3e",  # German military
    "380",  # France military
    "50",  # Israel military
    "70",  # Pakistan military
    "c0",  # Canada military
}

# Military callsign prefixes
_MILITARY_CALLSIGNS: set[str] = {
    "RCH",  # US Air Mobility Command (C-17, C-5)
    "EVAC",  # US Medevac
    "CASA",  # US STRATCOM
    "JAKE",  # US tankers
    "NCHO",  # US Navy
    "NAVY",  # US Navy
    "TOPCAT",  # Misc military
    "REACH",  # USAF airlift
    "DUKE",  # USAF special ops
    "IRON",  # USAF fighters
    "HAWK",  # Various military
    "VIPER",  # F-16
    "RAPTOR",  # F-22
    "BONE",  # B-1 bomber
    "DOOM",  # B-2 bomber
    "RAIDR",  # B-52
    "FORTE",  # RQ-4 Global Hawk
    "GORDO",  # RC-135
    "HOMER",  # E-6 Mercury (nuclear C2)
    "SNTRY",  # E-3 AWACS
    "NATO",  # NATO
    "SHD",  # British military
    "RRR",  # RAF
    "GAF",  # German Air Force
    "FAF",  # French Air Force
    "IAF",  # Israeli Air Force
    "RSD",  # Russian military (rare on ADS-B)
}


def _is_military(icao24: str, callsign: str) -> bool:
    """Check if aircraft is likely military based on ICAO hex and callsign."""
    icao_lower = icao24.lower()
    for prefix in _MILITARY_ICAO_PREFIXES:
        if icao_lower.startswith(prefix):
            return True

    callsign_upper = callsign.strip().upper()
    for mil_cs in _MILITARY_CALLSIGNS:
        if callsign_upper.startswith(mil_cs):
            return True

    return False


def _classify_aircraft(callsign: str, icao24: str) -> str:
    """Classify aircraft type from callsign patterns."""
    cs = callsign.strip().upper()

    if cs.startswith(("FORTE", "GLOBAL")):
        return "surveillance_drone"
    if cs.startswith(("GORDO", "COBRA")):
        return "reconnaissance"
    if cs.startswith(("HOMER", "TACAM")):
        return "nuclear_c2"
    if cs.startswith(("SNTRY", "AWACS")):
        return "awacs"
    if cs.startswith(("RCH", "REACH")):
        return "airlift"
    if cs.startswith(("DUKE",)):
        return "special_ops"
    if cs.startswith(("BONE", "DOOM", "RAIDR")):
        return "bomber"
    if cs.startswith(("IRON", "VIPER", "RAPTOR", "HAWK")):
        return "fighter"
    if cs.startswith(("JAKE",)):
        return "tanker"
    if cs.startswith(("EVAC",)):
        return "medevac"

    if _is_military(icao24, callsign):
        return "military_unknown"
    return "civilian"


class ADSBExchangeConnector:
    """Fetches aircraft data with focus on military tracking."""

    name = "adsb"

    # ADS-B Exchange v2 API (free, rate-limited)
    ADSB_API_URL = "https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{dist}"
    # Fallback: OpenSky
    OPENSKY_API_URL = "https://opensky-network.org/api/states/all"

    def __init__(
        self,
        *,
        center_lat: float = 40.0,
        center_lon: float = -30.0,
        radius_nm: int = 250,
        bbox: tuple[float, float, float, float] | None = None,
        military_only: bool = False,
        timeout_s: float = 15.0,
        proxy_url: str | None = None,
    ) -> None:
        self.center_lat = center_lat
        self.center_lon = center_lon
        self.radius_nm = radius_nm
        self.bbox = bbox
        self.military_only = military_only
        self.timeout_s = timeout_s
        self.proxy_url = proxy_url

    def _build_client(self) -> httpx.Client:
        kwargs: dict[str, Any] = {
            "timeout": self.timeout_s,
            "follow_redirects": True,
        }
        if self.proxy_url:
            kwargs["proxy"] = self.proxy_url
        return httpx.Client(**kwargs)

    def _fetch_adsb_fi(self, client: httpx.Client) -> list[dict[str, Any]]:
        """Fetch from adsb.fi (community ADS-B aggregator)."""
        url = self.ADSB_API_URL.format(
            lat=self.center_lat,
            lon=self.center_lon,
            dist=self.radius_nm,
        )
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()
        return data.get("ac", [])

    def _fetch_opensky(self, client: httpx.Client) -> list[dict[str, Any]]:
        """Fallback to OpenSky Network API."""
        params: dict[str, str] = {}
        if self.bbox:
            lamin, lomin, lamax, lomax = self.bbox
            params = {
                "lamin": str(lamin), "lomin": str(lomin),
                "lamax": str(lamax), "lomax": str(lomax),
            }

        resp = client.get(self.OPENSKY_API_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

        # Convert OpenSky format to unified format
        result: list[dict[str, Any]] = []
        for sv in data.get("states", []) or []:
            if len(sv) < 17 or sv[6] is None or sv[5] is None:
                continue
            result.append({
                "hex": sv[0] or "",
                "flight": (sv[1] or "").strip(),
                "lat": sv[6],
                "lon": sv[5],
                "alt_baro": sv[7],
                "gs": sv[9],  # ground speed
                "track": sv[10],
                "vert_rate": sv[11],
                "squawk": sv[14] or "",
                "category": "",
                "t": sv[2] or "",  # origin country
            })
        return result

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        geo_events: list[dict[str, Any]] = []
        metadata: dict[str, Any] = {
            "center": (self.center_lat, self.center_lon),
            "radius_nm": self.radius_nm,
            "military_only": self.military_only,
        }

        aircraft_list: list[dict[str, Any]] = []
        source_api = "unknown"

        try:
            with self._build_client() as client:
                try:
                    aircraft_list = self._fetch_adsb_fi(client)
                    source_api = "adsb.fi"
                except Exception:
                    aircraft_list = self._fetch_opensky(client)
                    source_api = "opensky"
        except Exception as exc:
            metadata["error"] = str(exc)
            return ConnectorResult(connector=self.name, observations=[], metadata=metadata)

        metadata["source_api"] = source_api
        metadata["total_aircraft"] = len(aircraft_list)
        now = datetime.now(UTC)
        military_count = 0

        for ac in aircraft_list:
            icao24 = str(ac.get("hex", "")).strip()
            callsign = str(ac.get("flight", "")).strip()
            lat = ac.get("lat")
            lon = ac.get("lon")
            alt = ac.get("alt_baro")
            speed = ac.get("gs")
            squawk = str(ac.get("squawk", ""))
            origin = str(ac.get("t", ""))

            if lat is None or lon is None:
                continue

            is_mil = _is_military(icao24, callsign)
            if self.military_only and not is_mil:
                continue

            if is_mil:
                military_count += 1

            # Emergency squawks
            is_emergency = squawk in ("7500", "7600", "7700")
            ac_type = _classify_aircraft(callsign, icao24)

            # Filter by query
            if query:
                q_lower = query.lower()
                match_fields = f"{callsign} {icao24} {origin} {ac_type}".lower()
                if q_lower not in match_fields:
                    continue

            # Build claim
            alt_str = f"alt {alt}ft" if alt else "ground"
            speed_str = f"{speed:.0f}kts" if speed else ""
            mil_tag = f" [MILITARY:{ac_type.upper()}]" if is_mil else ""
            emerg_tag = f" [EMERGENCY SQUAWK {squawk}]" if is_emergency else ""

            claim = (
                f"Aircraft {callsign or icao24} ({origin}) at "
                f"({lat:.4f}, {lon:.4f}) {alt_str}"
            )
            if speed_str:
                claim += f" {speed_str}"
            claim += mil_tag + emerg_tag

            reliability = 0.90
            if is_emergency:
                reliability = 0.95
            elif is_mil:
                reliability = 0.88
            elif not is_mil:
                reliability = 0.70

            observations.append(Observation(
                source=f"adsb:{source_api}",
                claim=claim,
                url=f"https://globe.adsbexchange.com/?icao={icao24}",
                captured_at=now,
                reliability_hint=reliability,
            ))

            geo_events.append({
                "layer": "aircraft",
                "source_id": f"adsb_{icao24}",
                "title": claim[:500],
                "latitude": lat,
                "longitude": lon,
                "altitude": alt,
                "magnitude": None,
                "event_time": now,
                "expires_at": now,
                "properties": {
                    "icao24": icao24,
                    "callsign": callsign,
                    "origin_country": origin,
                    "speed": speed,
                    "squawk": squawk,
                    "is_military": is_mil,
                    "is_emergency": is_emergency,
                    "aircraft_type": ac_type,
                },
            })

        metadata["geo_events"] = geo_events
        metadata["military_count"] = military_count
        metadata["observations_count"] = len(observations)

        return ConnectorResult(
            connector=self.name,
            observations=observations,
            metadata=metadata,
        )
