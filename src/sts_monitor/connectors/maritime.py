"""
Maritime vessel tracking connector.
Attempts multiple free/public sources. Gracefully returns empty on failure.
Layer: maritime
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation

log = logging.getLogger(__name__)

# Major port/strait locations for vessel distribution
_PORT_LOCATIONS = [
    ("Strait of Hormuz", 26.6, 56.4),
    ("Strait of Malacca", 2.5, 101.5),
    ("Suez Canal", 30.7, 32.3),
    ("Panama Canal", 8.9, -79.7),
    ("English Channel", 50.9, 1.4),
    ("South China Sea", 15.0, 114.0),
    ("Gulf of Aden", 12.0, 47.0),
    ("Mediterranean Sea", 35.0, 18.0),
    ("Black Sea", 43.0, 34.0),
    ("Baltic Sea", 57.0, 19.0),
    ("Red Sea", 22.0, 38.0),
    ("Persian Gulf", 26.0, 52.0),
    ("Bay of Bengal", 13.0, 85.0),
    ("Taiwan Strait", 24.5, 119.5),
    ("Bab-el-Mandeb", 12.6, 43.4),
]

# Sources to try in order
_MARINETRAFFIC_URL = "https://www.marinetraffic.com/getData/get_data_json_4/z:1/X:0/Y:0/station:0"
_AISHUB_URL = "http://data.aishub.net/ws.php?username=0&format=1&output=json&compress=0&latmin=-90&latmax=90&lonmin=-180&lonmax=180"


class MaritimeConnector:
    """Fetches maritime vessel data from public AIS sources."""

    name = "maritime"

    def __init__(self, timeout_s: float = 15.0) -> None:
        self.timeout_s = timeout_s

    def _try_marinetraffic(self, client: httpx.Client) -> list[dict]:
        """Attempt MarineTraffic public endpoint."""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.marinetraffic.com/",
            }
            resp = client.get(_MARINETRAFFIC_URL, headers=headers)
            if resp.status_code != 200:
                return []
            data = resp.json()
            vessels = []
            # MarineTraffic returns nested data
            if isinstance(data, dict):
                raw = data.get("data", data.get("rows", []))
            elif isinstance(data, list):
                raw = data
            else:
                return []

            for v in raw[:100]:
                if isinstance(v, list) and len(v) >= 5:
                    vessels.append({
                        "mmsi": str(v[0]) if v[0] else "unknown",
                        "lat": float(v[1]) if v[1] else 0,
                        "lon": float(v[2]) if v[2] else 0,
                        "name": str(v[3]) if len(v) > 3 else "Unknown Vessel",
                        "type": str(v[4]) if len(v) > 4 else "unknown",
                    })
                elif isinstance(v, dict):
                    vessels.append({
                        "mmsi": str(v.get("MMSI", v.get("mmsi", "unknown"))),
                        "lat": float(v.get("LAT", v.get("lat", 0)) or 0),
                        "lon": float(v.get("LON", v.get("lon", 0)) or 0),
                        "name": str(v.get("SHIPNAME", v.get("name", "Unknown Vessel"))),
                        "type": str(v.get("TYPE_NAME", v.get("type", "unknown"))),
                    })
            return vessels
        except Exception as exc:
            log.debug(f"MarineTraffic failed: {exc}")
            return []

    def _try_aishub(self, client: httpx.Client) -> list[dict]:
        """Attempt AISHub public API."""
        try:
            resp = client.get(_AISHUB_URL)
            if resp.status_code != 200:
                return []
            # AISHub returns array of arrays or JSON
            data = resp.json()
            if not isinstance(data, list) or len(data) < 2:
                return []
            vessels = []
            # First element is header, rest are data
            for item in data[1:101]:
                if isinstance(item, dict):
                    vessels.append({
                        "mmsi": str(item.get("MMSI", "unknown")),
                        "lat": float(item.get("LATITUDE", 0) or 0),
                        "lon": float(item.get("LONGITUDE", 0) or 0),
                        "name": str(item.get("NAME", "Unknown")),
                        "type": str(item.get("TYPE", "unknown")),
                    })
            return vessels
        except Exception as exc:
            log.debug(f"AISHub failed: {exc}")
            return []

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        geo_events: list[dict] = []
        metadata: dict = {"layer": "maritime"}

        vessels = []
        source_used = "none"

        with httpx.Client(
            timeout=self.timeout_s,
            follow_redirects=True,
        ) as client:
            vessels = self._try_marinetraffic(client)
            if vessels:
                source_used = "marinetraffic"
            else:
                vessels = self._try_aishub(client)
                if vessels:
                    source_used = "aishub"

        metadata["source"] = source_used
        metadata["vessels_fetched"] = len(vessels)

        if not vessels:
            log.info("Maritime: all live sources failed, returning empty")
            metadata["note"] = "All maritime data sources unavailable"
            return ConnectorResult(connector=self.name, observations=[], metadata=metadata)

        for vessel in vessels:
            mmsi = vessel.get("mmsi", "unknown")
            lat = vessel.get("lat", 0)
            lon = vessel.get("lon", 0)
            name = vessel.get("name", "Unknown Vessel")
            vtype = vessel.get("type", "unknown")

            # Skip vessels with no/invalid coordinates
            if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                continue
            if lat == 0 and lon == 0:
                continue

            # Significance: cargo ships = 5, tankers = 6, suspicious = 7+
            sig = 5.0
            type_lower = vtype.lower()
            if "tanker" in type_lower or "chemical" in type_lower:
                sig = 6.0
            elif "military" in type_lower or "naval" in type_lower or "warship" in type_lower:
                sig = 8.0
            elif "fishing" in type_lower:
                sig = 4.0

            source_id = f"maritime_{mmsi}_{int(lat*10)}_{int(lon*10)}"
            title = f"{name} ({vtype}) — MMSI: {mmsi}"

            observations.append(Observation(
                source=f"maritime:{source_used}",
                claim=f"[MARITIME] Vessel: {name}, Type: {vtype}, MMSI: {mmsi}",
                url="",
                captured_at=datetime.now(UTC),
                reliability_hint=0.6,
            ))
            geo_events.append({
                "layer": "maritime",
                "source_id": source_id,
                "title": title[:500],
                "latitude": lat,
                "longitude": lon,
                "magnitude": round(sig, 1),
                "event_time": datetime.now(UTC).isoformat(),
                "properties": {
                    "layer": "maritime",
                    "source": source_used,
                    "mmsi": mmsi,
                    "vessel_name": name,
                    "vessel_type": vtype,
                    "significance": sig,
                },
            })

        metadata["geo_events_count"] = len(geo_events)
        return ConnectorResult(
            connector=self.name,
            observations=observations,
            metadata={**metadata, "geo_events": geo_events},
        )
