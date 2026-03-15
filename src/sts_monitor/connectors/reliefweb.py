"""ReliefWeb connector — UN OCHA humanitarian situation reports.

Free, no authentication required.
API docs: https://apidoc.reliefweb.int/
Covers: disasters, humanitarian crises, country reports, situation updates worldwide.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation


_SOURCE_RELIABILITY: dict[str, float] = {
    "situation_report": 0.88,
    "analysis": 0.85,
    "assessment": 0.87,
    "map": 0.80,
    "news_and_press_release": 0.75,
    "evaluation": 0.90,
    "manual_and_guideline": 0.70,
}


class ReliefWebConnector:
    """Fetches humanitarian reports from ReliefWeb API."""

    name = "reliefweb"

    API_URL = "https://api.reliefweb.int/v1/reports"

    def __init__(
        self,
        *,
        lookback_days: int = 7,
        limit: int = 50,
        country: str | None = None,
        disaster_type: str | None = None,
        content_format: str | None = None,
        timeout_s: float = 15.0,
    ) -> None:
        self.lookback_days = max(1, min(365, lookback_days))
        self.limit = max(1, min(500, limit))
        self.country = country
        self.disaster_type = disaster_type
        self.content_format = content_format
        self.timeout_s = timeout_s

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        geo_events: list[dict] = []
        metadata: dict = {
            "lookback_days": self.lookback_days,
            "country": self.country,
        }

        cutoff = (datetime.now(UTC) - timedelta(days=self.lookback_days)).strftime("%Y-%m-%dT00:00:00+00:00")

        # Build request body (ReliefWeb uses POST for search)
        body: dict = {
            "appname": "sts-situation-monitor",
            "limit": self.limit,
            "sort": ["date.created:desc"],
            "fields": {
                "include": [
                    "title", "body-html", "url_alias", "source", "country",
                    "date.created", "format", "disaster_type", "primary_country",
                ],
            },
            "filter": {
                "operator": "AND",
                "conditions": [
                    {"field": "date.created", "value": {"from": cutoff}},
                ],
            },
        }

        if query:
            body["query"] = {"value": query}
        if self.country:
            body["filter"]["conditions"].append(
                {"field": "primary_country.name", "value": self.country}
            )
        if self.content_format:
            body["filter"]["conditions"].append(
                {"field": "format.name", "value": self.content_format}
            )
        if self.disaster_type:
            body["filter"]["conditions"].append(
                {"field": "disaster_type.name", "value": self.disaster_type}
            )

        try:
            with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
                resp = client.post(self.API_URL, json=body)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            metadata["error"] = str(exc)
            return ConnectorResult(connector=self.name, observations=[], metadata=metadata)

        reports = data.get("data", [])
        metadata["report_count"] = len(reports)

        for report in reports:
            fields = report.get("fields", {})
            title = fields.get("title", "")
            url_alias = fields.get("url_alias", "")
            date_created = (fields.get("date") or {}).get("created", "")
            sources = fields.get("source") or []
            countries = fields.get("country") or []
            primary_country = fields.get("primary_country") or {}
            formats = fields.get("format", [])
            disaster_types = fields.get("disaster_type", [])

            # Parse date
            captured_at = datetime.now(UTC)
            if date_created:
                try:
                    captured_at = datetime.fromisoformat(date_created.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    pass

            # Build claim
            source_names = [s.get("name", "") for s in sources[:3]]
            country_names = [c.get("name", "") for c in countries[:3]]
            format_name = formats[0].get("name", "") if formats else ""
            disaster_name = disaster_types[0].get("name", "") if disaster_types else ""

            claim = f"ReliefWeb: {title}"
            if country_names:
                claim += f" [{', '.join(country_names)}]"
            if disaster_name:
                claim += f" — {disaster_name}"

            # Determine reliability from content format
            format_key = format_name.lower().replace(" ", "_")
            reliability = _SOURCE_RELIABILITY.get(format_key, 0.75)

            url = f"https://reliefweb.int{url_alias}" if url_alias else "https://reliefweb.int/"

            observations.append(Observation(
                source=f"reliefweb:{format_key or 'report'}",
                claim=claim,
                url=url,
                captured_at=captured_at,
                reliability_hint=reliability,
            ))

            # Geo events from primary country
            pc_location = primary_country.get("location", {})
            if pc_location and pc_location.get("lat") and pc_location.get("lon"):
                geo_events.append({
                    "layer": "humanitarian",
                    "source_id": f"reliefweb_{report.get('id', '')}",
                    "title": claim[:500],
                    "latitude": pc_location["lat"],
                    "longitude": pc_location["lon"],
                    "magnitude": None,
                    "event_time": captured_at,
                    "properties": {
                        "sources": source_names,
                        "countries": country_names,
                        "format": format_name,
                        "disaster_type": disaster_name,
                    },
                })

        metadata["geo_events"] = geo_events
        return ConnectorResult(connector=self.name, observations=observations, metadata=metadata)
