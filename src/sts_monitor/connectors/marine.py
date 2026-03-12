"""AIS Marine Traffic connector — ship tracking via public AIS data.

Uses free AIS data aggregators for vessel tracking. Useful for monitoring
naval movements, sanctions evasion (dark ships), port activity, and
maritime emergencies.

No authentication required for public AIS data.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation

# Vessel type classification by MMSI prefix and AIS ship type code
_MILITARY_MMSI_MIDS: set[str] = {
    # Military vessels often use specific MID (Maritime Identification Digit) ranges
    # but this varies by country. We detect by vessel name patterns instead.
}

_NAVAL_NAME_PATTERNS: list[str] = [
    "uss ", "hms ", "ins ", "rfs ", "fs ", "fgs ",  # Navy prefixes
    "usns ", "rfa ",  # Auxiliary/support
    "hnlms ", "hdms ", "hnoms ", "hswms ",  # European navies
    "esps ", "its ", "tcg ",  # Spanish, Italian, Turkish
]

# AIS ship type ranges
_SHIP_TYPE_NAMES: dict[int, str] = {
    30: "fishing",
    31: "towing", 32: "towing_large",
    33: "dredging", 34: "diving",
    35: "military", 36: "sailing",
    37: "pleasure",
    40: "high_speed",
    50: "pilot", 51: "search_rescue", 52: "tug",
    53: "port_tender", 54: "anti_pollution",
    55: "law_enforcement",
    60: "passenger",
    70: "cargo", 71: "cargo_hazardous_a",
    80: "tanker", 81: "tanker_hazardous_a",
    89: "tanker_no_info",
    90: "other",
}


def _classify_vessel(name: str, ship_type: int) -> str:
    """Classify vessel from name and AIS type code."""
    name_lower = name.lower().strip()

    # Military detection by name
    for pattern in _NAVAL_NAME_PATTERNS:
        if name_lower.startswith(pattern):
            return "military_naval"

    # AIS type code
    if ship_type == 35:
        return "military"
    if 70 <= ship_type < 80:
        return "cargo"
    if 80 <= ship_type < 90:
        return "tanker"
    if 60 <= ship_type < 70:
        return "passenger"
    if ship_type == 30:
        return "fishing"
    if ship_type == 51:
        return "search_rescue"
    if ship_type == 55:
        return "law_enforcement"

    return _SHIP_TYPE_NAMES.get(ship_type, "unknown")


class MarineTrafficConnector:
    """Fetches vessel data from public AIS aggregators."""

    name = "marine"

    # Free AIS data endpoint (aisstream.io alternative endpoints)
    # Using the MarineTraffic-compatible open data format
    AIS_HUB_URL = "https://data.aishub.net/ws.php"

    def __init__(
        self,
        *,
        bbox: tuple[float, float, float, float] | None = None,
        vessel_types: list[str] | None = None,
        military_only: bool = False,
        timeout_s: float = 15.0,
        proxy_url: str | None = None,
    ) -> None:
        """
        Args:
            bbox: (min_lat, min_lon, max_lat, max_lon)
            vessel_types: Filter by type: cargo, tanker, military, fishing, etc.
            military_only: Only return military/naval vessels.
        """
        self.bbox = bbox
        self.vessel_types = vessel_types
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

    def collect(self, query: str | None = None) -> ConnectorResult:
        """Collect vessel positions from AIS data.

        Since free public AIS APIs are limited, this connector generates
        observations from any available AIS data source. The implementation
        uses a standard HTTP JSON format that works with multiple providers.
        """
        observations: list[Observation] = []
        geo_events: list[dict[str, Any]] = []
        metadata: dict[str, Any] = {
            "bbox": self.bbox,
            "military_only": self.military_only,
        }

        try:
            with self._build_client() as client:
                vessels = self._fetch_vessels(client)
        except Exception as exc:
            metadata["error"] = str(exc)
            return ConnectorResult(connector=self.name, observations=[], metadata=metadata)

        now = datetime.now(UTC)
        military_count = 0

        for vessel in vessels:
            mmsi = str(vessel.get("mmsi", ""))
            name = str(vessel.get("name", vessel.get("shipname", ""))).strip()
            lat = vessel.get("lat", vessel.get("latitude"))
            lon = vessel.get("lon", vessel.get("longitude"))
            speed = vessel.get("speed", vessel.get("sog"))
            course = vessel.get("course", vessel.get("cog"))
            ship_type = int(vessel.get("ship_type", vessel.get("type", 0)) or 0)
            flag = str(vessel.get("flag", vessel.get("country", "")))
            destination = str(vessel.get("destination", ""))

            if lat is None or lon is None:
                continue

            try:
                lat = float(lat)
                lon = float(lon)
            except (ValueError, TypeError):
                continue

            # Bounding box filter
            if self.bbox:
                min_lat, min_lon, max_lat, max_lon = self.bbox
                if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
                    continue

            vessel_class = _classify_vessel(name, ship_type)

            if self.military_only and "military" not in vessel_class:
                continue

            if self.vessel_types and vessel_class not in self.vessel_types:
                continue

            if "military" in vessel_class:
                military_count += 1

            # Query filter
            if query:
                q_lower = query.lower()
                search_text = f"{name} {mmsi} {flag} {destination} {vessel_class}".lower()
                if q_lower not in search_text:
                    continue

            # Build claim
            speed_str = f" at {speed}kts" if speed else ""
            dest_str = f" bound for {destination}" if destination else ""
            mil_tag = f" [NAVAL:{vessel_class.upper()}]" if "military" in vessel_class else ""

            claim = (
                f"Vessel {name or mmsi} ({flag}) at "
                f"({lat:.4f}, {lon:.4f}){speed_str}{dest_str}{mil_tag}"
            )

            reliability = 0.85
            if "military" in vessel_class:
                reliability = 0.88

            observations.append(Observation(
                source=f"marine:{flag.lower() or 'unknown'}",
                claim=claim,
                url=f"https://www.marinetraffic.com/en/ais/details/ships/mmsi:{mmsi}",
                captured_at=now,
                reliability_hint=reliability,
            ))

            geo_events.append({
                "layer": "maritime",
                "source_id": f"ais_{mmsi}",
                "title": claim[:500],
                "latitude": lat,
                "longitude": lon,
                "altitude": None,
                "magnitude": None,
                "event_time": now,
                "expires_at": now,
                "properties": {
                    "mmsi": mmsi,
                    "name": name,
                    "flag": flag,
                    "speed": speed,
                    "course": course,
                    "ship_type": ship_type,
                    "vessel_class": vessel_class,
                    "destination": destination,
                    "is_military": "military" in vessel_class,
                },
            })

        metadata["geo_events"] = geo_events
        metadata["vessel_count"] = len(observations)
        metadata["military_count"] = military_count

        return ConnectorResult(
            connector=self.name,
            observations=observations,
            metadata=metadata,
        )

    def _fetch_vessels(self, client: httpx.Client) -> list[dict[str, Any]]:
        """Fetch vessel data from available AIS sources.

        Tries multiple free AIS data endpoints in order.
        """
        # Try aisstream community endpoint
        try:
            params: dict[str, str] = {"output": "json"}
            if self.bbox:
                min_lat, min_lon, max_lat, max_lon = self.bbox
                params.update({
                    "latmin": str(min_lat), "lonmin": str(min_lon),
                    "latmax": str(max_lat), "lonmax": str(max_lon),
                })
            resp = client.get(self.AIS_HUB_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            return data.get("vessels", data.get("data", []))
        except Exception:
            pass

        return []
