"""NASA FIRMS (Fire Information for Resource Management System) connector.

Near real-time global wildfire/hotspot detection from MODIS and VIIRS satellites.
Free with MAP_KEY registration at https://firms.modaps.eosdis.nasa.gov/api/map_key/
API docs: https://firms.modaps.eosdis.nasa.gov/api/
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation


class NASAFIRMSConnector:
    """Fetches active fire/hotspot data from NASA FIRMS."""

    name = "nasa_firms"

    AREA_API = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
    COUNTRY_API = "https://firms.modaps.eosdis.nasa.gov/api/country/csv"

    # Sensors available
    SENSORS = {
        "VIIRS_NOAA20_NRT": "VIIRS NOAA-20 (near real-time)",
        "VIIRS_NOAA21_NRT": "VIIRS NOAA-21 (near real-time)",
        "VIIRS_SNPP_NRT": "VIIRS Suomi-NPP (near real-time)",
        "MODIS_NRT": "MODIS (near real-time)",
    }

    def __init__(
        self,
        *,
        map_key: str = "",
        sensor: str = "VIIRS_NOAA20_NRT",
        country_code: str | None = None,
        area_bbox: str | None = None,
        days: int = 1,
        min_confidence: str = "nominal",
        timeout_s: float = 15.0,
    ) -> None:
        self.map_key = map_key
        self.sensor = sensor if sensor in self.SENSORS else "VIIRS_NOAA20_NRT"
        self.country_code = country_code
        self.area_bbox = area_bbox
        self.days = max(1, min(10, days))
        self.min_confidence = min_confidence
        self.timeout_s = timeout_s

    def _build_url(self) -> str:
        if self.country_code:
            return f"{self.COUNTRY_API}/{self.map_key}/{self.sensor}/{self.country_code}/{self.days}"
        if self.area_bbox:
            return f"{self.AREA_API}/{self.map_key}/{self.sensor}/{self.area_bbox}/{self.days}"
        return f"{self.AREA_API}/{self.map_key}/{self.sensor}/world/{self.days}"

    @staticmethod
    def _confidence_passes(confidence: str, min_level: str) -> bool:
        levels = {"low": 0, "nominal": 1, "n": 1, "high": 2, "h": 2}
        actual = levels.get(confidence.lower().strip(), 0)
        required = levels.get(min_level.lower().strip(), 1)
        return actual >= required

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        geo_events: list[dict] = []
        metadata: dict = {
            "sensor": self.sensor,
            "country_code": self.country_code,
            "days": self.days,
        }

        if not self.map_key:
            metadata["error"] = "NASA FIRMS requires a MAP_KEY (set STS_NASA_FIRMS_MAP_KEY)"
            return ConnectorResult(connector=self.name, observations=[], metadata=metadata)

        url = self._build_url()

        try:
            with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
                resp = client.get(url)
                resp.raise_for_status()
                text = resp.text
        except Exception as exc:
            metadata["error"] = str(exc)
            return ConnectorResult(connector=self.name, observations=[], metadata=metadata)

        reader = csv.DictReader(io.StringIO(text))
        total = 0
        filtered = 0

        for row in reader:
            total += 1
            confidence = row.get("confidence", "nominal")
            if not self._confidence_passes(confidence, self.min_confidence):
                filtered += 1
                continue

            lat_str = row.get("latitude", "")
            lon_str = row.get("longitude", "")
            try:
                lat = float(lat_str)
                lon = float(lon_str)
            except (ValueError, TypeError):
                continue

            brightness = row.get("bright_ti4") or row.get("brightness", "")
            frp = row.get("frp", "")
            acq_date = row.get("acq_date", "")
            acq_time = row.get("acq_time", "")
            satellite = row.get("satellite", self.sensor)
            daynight = row.get("daynight", "")

            captured_at = datetime.now(UTC)
            if acq_date:
                try:
                    time_str = acq_time.zfill(4) if acq_time else "0000"
                    captured_at = datetime.strptime(
                        f"{acq_date} {time_str}", "%Y-%m-%d %H%M"
                    ).replace(tzinfo=UTC)
                except (ValueError, IndexError):
                    pass

            claim = f"Active fire detected at ({lat:.3f}, {lon:.3f})"
            if brightness:
                claim += f", brightness {brightness}K"
            if frp:
                claim += f", FRP {frp}MW"

            if query and query.lower() not in claim.lower():
                continue

            firms_url = f"https://firms.modaps.eosdis.nasa.gov/map/#d:24hrs;@{lon},{lat},10z"

            observations.append(
                Observation(
                    source=f"nasa_firms:{satellite}",
                    claim=claim,
                    url=firms_url,
                    captured_at=captured_at,
                    reliability_hint=0.90,
                )
            )

            geo_events.append({
                "layer": "fire",
                "source_id": f"firms_{lat}_{lon}_{acq_date}_{acq_time}",
                "title": claim,
                "latitude": lat,
                "longitude": lon,
                "magnitude": float(brightness) if brightness else None,
                "event_time": captured_at,
                "properties": {
                    "brightness": brightness,
                    "frp": frp,
                    "confidence": confidence,
                    "satellite": satellite,
                    "daynight": daynight,
                    "sensor": self.sensor,
                },
            })

        metadata["total_detections"] = total
        metadata["filtered_low_confidence"] = filtered
        metadata["accepted_detections"] = len(observations)
        metadata["geo_events"] = geo_events
        return ConnectorResult(connector=self.name, observations=observations, metadata=metadata)
