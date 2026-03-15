"""NWS (National Weather Service) alerts connector — US severe weather warnings.

Free, no authentication required.
API docs: https://www.weather.gov/documentation/services-web-api
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation


_SEVERITY_WEIGHT: dict[str, float] = {
    "Extreme": 0.95,
    "Severe": 0.85,
    "Moderate": 0.70,
    "Minor": 0.55,
    "Unknown": 0.50,
}


class NWSAlertConnector:
    """Fetches active weather alerts from the NWS API."""

    name = "nws_alerts"

    ALERTS_URL = "https://api.weather.gov/alerts/active"

    def __init__(
        self,
        *,
        severity_filter: str = "Extreme,Severe",
        status: str = "actual",
        urgency: str | None = None,
        area: str | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self.severity_filter = [s.strip() for s in severity_filter.split(",") if s.strip()]
        self.status = status
        self.urgency = urgency
        self.area = area
        self.timeout_s = timeout_s

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        geo_events: list[dict] = []
        metadata: dict = {"severity_filter": self.severity_filter}

        headers = {
            "User-Agent": "STS-Situation-Monitor/1.0 (situation-monitor)",
            "Accept": "application/geo+json",
        }

        params: dict[str, str] = {"status": self.status}
        if self.severity_filter:
            params["severity"] = ",".join(self.severity_filter)
        if self.urgency:
            params["urgency"] = self.urgency
        if self.area:
            params["area"] = self.area

        try:
            with httpx.Client(timeout=self.timeout_s, follow_redirects=True, headers=headers) as client:
                resp = client.get(self.ALERTS_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            metadata["error"] = str(exc)
            return ConnectorResult(connector=self.name, observations=[], metadata=metadata)

        features = data.get("features") or []
        metadata["alert_count"] = len(features)

        for feature in features:
            props = feature.get("properties") or {}
            geometry = feature.get("geometry")

            alert_id = props.get("id", "")
            event = props.get("event", "")
            headline = props.get("headline", "")
            description = props.get("description", "")
            severity = props.get("severity", "Unknown")
            certainty = props.get("certainty", "")
            urgency_val = props.get("urgency", "")
            area_desc = props.get("areaDesc", "")
            sender_name = props.get("senderName", "")
            effective = props.get("effective", "")
            expires = props.get("expires", "")
            nws_url = props.get("@id", "")

            captured_at = datetime.now(UTC)
            if effective:
                try:
                    captured_at = datetime.fromisoformat(effective.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    pass

            expires_at = None
            if expires:
                try:
                    expires_at = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    pass

            claim = headline or f"{event} — {area_desc}"
            if description and not headline:
                claim += f". {description[:200]}"

            if query and query.lower() not in claim.lower():
                continue

            reliability = _SEVERITY_WEIGHT.get(severity, 0.50)

            observations.append(
                Observation(
                    source=f"nws:{event.lower().replace(' ', '_')}",
                    claim=claim,
                    url=nws_url or "https://alerts.weather.gov/",
                    captured_at=captured_at,
                    reliability_hint=reliability,
                )
            )

            lat, lon = None, None
            if geometry and geometry.get("type") == "Point":
                coords = geometry.get("coordinates", [])
                if len(coords) >= 2:
                    lon, lat = coords[0], coords[1]
            elif geometry and geometry.get("type") == "Polygon":
                coords = geometry.get("coordinates", [[]])
                if coords and coords[0]:
                    lats = [c[1] for c in coords[0] if len(c) >= 2]
                    lons = [c[0] for c in coords[0] if len(c) >= 2]
                    if lats and lons:
                        lat = sum(lats) / len(lats)
                        lon = sum(lons) / len(lons)

            if lat is not None and lon is not None:
                geo_events.append({
                    "layer": "weather_alert",
                    "source_id": alert_id,
                    "title": claim[:500],
                    "latitude": lat,
                    "longitude": lon,
                    "magnitude": {"Extreme": 5, "Severe": 4, "Moderate": 3, "Minor": 2, "Unknown": 1}.get(severity, 0),
                    "event_time": captured_at,
                    "expires_at": expires_at,
                    "properties": {
                        "event": event,
                        "severity": severity,
                        "certainty": certainty,
                        "urgency": urgency_val,
                        "area": area_desc,
                        "sender": sender_name,
                        "nws_id": alert_id,
                    },
                })

        metadata["geo_events"] = geo_events
        return ConnectorResult(connector=self.name, observations=observations, metadata=metadata)
