"""USGS Earthquake Hazards connector — real-time global seismic events.

Free, no authentication required. GeoJSON output with lat/lon.
API docs: https://earthquake.usgs.gov/fdsnws/event/1/
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation


class USGSEarthquakeConnector:
    """Fetches earthquake events from the USGS API."""

    name = "usgs_earthquake"

    FEED_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
    SUMMARY_FEEDS = {
        "significant_hour": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_hour.geojson",
        "m4.5_day": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson",
        "m2.5_day": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson",
        "all_hour": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson",
    }

    def __init__(
        self,
        *,
        min_magnitude: float = 4.0,
        lookback_hours: int = 24,
        max_events: int = 100,
        timeout_s: float = 10.0,
        use_summary_feed: str | None = None,
    ) -> None:
        self.min_magnitude = min_magnitude
        self.lookback_hours = max(1, min(720, lookback_hours))
        self.max_events = max(1, min(500, max_events))
        self.timeout_s = timeout_s
        self.use_summary_feed = use_summary_feed

    def _fetch_geojson(self) -> dict:
        """Fetch earthquake data as GeoJSON."""
        if self.use_summary_feed and self.use_summary_feed in self.SUMMARY_FEEDS:
            url = self.SUMMARY_FEEDS[self.use_summary_feed]
            with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.json()

        start = datetime.now(UTC) - timedelta(hours=self.lookback_hours)
        params = {
            "format": "geojson",
            "starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "minmagnitude": str(self.min_magnitude),
            "limit": str(self.max_events),
            "orderby": "time",
        }
        with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
            resp = client.get(self.FEED_URL, params=params)
            resp.raise_for_status()
            return resp.json()

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        geo_events: list[dict] = []
        metadata: dict = {
            "min_magnitude": self.min_magnitude,
            "lookback_hours": self.lookback_hours,
        }

        try:
            data = self._fetch_geojson()
        except Exception as exc:
            metadata["error"] = str(exc)
            return ConnectorResult(connector=self.name, observations=[], metadata=metadata)

        features = data.get("features") or []
        metadata["event_count"] = len(features)

        for feature in features:
            props = feature.get("properties") or {}
            geometry = feature.get("geometry") or {}
            coords = geometry.get("coordinates") or []

            if len(coords) < 2:
                continue

            lon, lat = coords[0], coords[1]
            depth = coords[2] if len(coords) > 2 else None

            mag = props.get("mag")
            place = props.get("place", "Unknown location")
            event_time_ms = props.get("time")
            url = props.get("url", "")
            detail_url = props.get("detail", "")
            tsunami = props.get("tsunami", 0)
            alert_level = props.get("alert")
            felt = props.get("felt")
            sig = props.get("sig", 0)
            event_type = props.get("type", "earthquake")
            event_id = feature.get("id", "")

            if url and not url.startswith("http"):
                url = f"https://earthquake.usgs.gov/earthquakes/eventpage/{event_id}"

            captured_at = datetime.now(UTC)
            if event_time_ms:
                try:
                    captured_at = datetime.fromtimestamp(event_time_ms / 1000, tz=UTC)
                except (ValueError, OSError):
                    pass

            mag_str = f"M{mag:.1f}" if mag is not None else "M?"
            claim = f"{mag_str} {event_type} near {place}"
            if tsunami:
                claim += " [tsunami warning]"

            if query and query.lower() not in claim.lower():
                continue

            observations.append(
                Observation(
                    source=f"usgs:{event_type}",
                    claim=claim,
                    url=url,
                    captured_at=captured_at,
                    reliability_hint=0.95,
                )
            )

            geo_events.append({
                "layer": "earthquake",
                "source_id": event_id,
                "title": claim,
                "latitude": lat,
                "longitude": lon,
                "altitude": depth,
                "magnitude": mag,
                "event_time": captured_at,
                "properties": {
                    "place": place,
                    "mag": mag,
                    "depth_km": depth,
                    "tsunami": tsunami,
                    "alert": alert_level,
                    "felt": felt,
                    "sig": sig,
                    "type": event_type,
                    "usgs_id": event_id,
                    "detail_url": detail_url,
                },
            })

        metadata["geo_events"] = geo_events
        return ConnectorResult(connector=self.name, observations=observations, metadata=metadata)
