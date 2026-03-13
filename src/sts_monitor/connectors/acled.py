"""ACLED (Armed Conflict Location & Event Data) connector.

Structured conflict and protest event data with geocoding.
Free with registration at https://acleddata.com/
API docs: https://acleddata.com/acled-api-documentation/
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation


class ACLEDConnector:
    """Fetches conflict and protest events from the ACLED API."""

    name = "acled"

    API_URL = "https://api.acleddata.com/acled/read"

    EVENT_TYPE_RELIABILITY: dict[str, float] = {
        "Battles": 0.80,
        "Explosions/Remote violence": 0.80,
        "Violence against civilians": 0.82,
        "Protests": 0.75,
        "Riots": 0.75,
        "Strategic developments": 0.78,
    }

    def __init__(
        self,
        *,
        api_key: str = "",
        email: str = "",
        lookback_days: int = 7,
        limit: int = 500,
        country: str | None = None,
        region: int | None = None,
        event_type: str | None = None,
        timeout_s: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self.email = email
        self.lookback_days = max(1, min(365, lookback_days))
        self.limit = max(1, min(5000, limit))
        self.country = country
        self.region = region
        self.event_type = event_type
        self.timeout_s = timeout_s

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        geo_events: list[dict] = []
        metadata: dict = {
            "lookback_days": self.lookback_days,
            "country": self.country,
        }

        if not self.api_key or not self.email:
            metadata["error"] = "ACLED requires api_key and email (set STS_ACLED_API_KEY and STS_ACLED_EMAIL)"
            return ConnectorResult(connector=self.name, observations=[], metadata=metadata)

        start_date = (datetime.now(UTC) - timedelta(days=self.lookback_days)).strftime("%Y-%m-%d")
        end_date = datetime.now(UTC).strftime("%Y-%m-%d")

        params: dict[str, str] = {
            "key": self.api_key,
            "email": self.email,
            "event_date": f"{start_date}|{end_date}",
            "event_date_where": "BETWEEN",
            "limit": str(self.limit),
        }
        if self.country:
            params["country"] = self.country
        if self.region is not None:
            params["region"] = str(self.region)
        if self.event_type:
            params["event_type"] = self.event_type

        try:
            with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
                resp = client.get(self.API_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            metadata["error"] = str(exc)
            return ConnectorResult(connector=self.name, observations=[], metadata=metadata)

        events = data.get("data") or []
        metadata["event_count"] = len(events)

        for event in events:
            event_id = event.get("data_id", "")
            event_date = event.get("event_date", "")
            etype = event.get("event_type", "Unknown")
            sub_type = event.get("sub_event_type", "")
            actor1 = event.get("actor1", "")
            actor2 = event.get("actor2", "")
            country = event.get("country", "")
            admin1 = event.get("admin1", "")
            location = event.get("location", "")
            lat_str = event.get("latitude", "")
            lon_str = event.get("longitude", "")
            fatalities = event.get("fatalities", 0)
            notes = event.get("notes", "")
            source = event.get("source", "")
            source_scale = event.get("source_scale", "")
            inter1 = event.get("inter1", "")

            try:
                lat = float(lat_str)
                lon = float(lon_str)
            except (ValueError, TypeError):
                continue

            captured_at = datetime.now(UTC)
            if event_date:
                try:
                    captured_at = datetime.strptime(event_date, "%Y-%m-%d").replace(tzinfo=UTC)
                except ValueError:
                    pass

            location_str = ", ".join(filter(None, [location, admin1, country]))
            claim = f"{etype}: {sub_type}" if sub_type else etype
            if actor1:
                claim += f" involving {actor1}"
                if actor2:
                    claim += f" vs {actor2}"
            claim += f" in {location_str}"
            if fatalities and int(fatalities) > 0:
                claim += f" ({fatalities} fatalities)"
            if notes:
                claim += f". {notes[:300]}"

            if query and query.lower() not in claim.lower():
                continue

            reliability = self.EVENT_TYPE_RELIABILITY.get(etype, 0.75)

            acled_url = f"https://acleddata.com/data-export-tool/?event_id={event_id}" if event_id else "https://acleddata.com/"

            observations.append(
                Observation(
                    source=f"acled:{etype.lower().replace(' ', '_')}",
                    claim=claim,
                    url=acled_url,
                    captured_at=captured_at,
                    reliability_hint=reliability,
                )
            )

            geo_events.append({
                "layer": "conflict",
                "source_id": f"acled_{event_id}",
                "title": claim[:500],
                "latitude": lat,
                "longitude": lon,
                "magnitude": float(fatalities) if fatalities else 0.0,
                "event_time": captured_at,
                "properties": {
                    "event_type": etype,
                    "sub_event_type": sub_type,
                    "actor1": actor1,
                    "actor2": actor2,
                    "country": country,
                    "location": location_str,
                    "fatalities": fatalities,
                    "source": source,
                    "source_scale": source_scale,
                    "acled_id": event_id,
                },
            })

        metadata["geo_events"] = geo_events
        return ConnectorResult(connector=self.name, observations=observations, metadata=metadata)
