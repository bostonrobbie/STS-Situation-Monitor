"""FEMA OpenFEMA connector — US disaster declarations and emergency management.

Free, no authentication required.
API docs: https://www.fema.gov/about/openfema/api
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation


_DISASTER_TYPE_RELIABILITY: dict[str, float] = {
    "DR": 0.95,  # Major Disaster Declaration
    "EM": 0.93,  # Emergency Declaration
    "FM": 0.90,  # Fire Management
    "FS": 0.88,  # Fire Suppression
}


class FEMADisasterConnector:
    """Fetches disaster declarations from FEMA OpenFEMA API."""

    name = "fema"

    API_URL = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"

    # State FIPS → approximate centroids for mapping
    _STATE_COORDS: dict[str, tuple[float, float]] = {
        "AL": (32.8, -86.8), "AK": (64.2, -152.5), "AZ": (34.3, -111.7),
        "AR": (34.8, -92.2), "CA": (37.2, -119.5), "CO": (39.0, -105.5),
        "CT": (41.6, -72.7), "DE": (39.0, -75.5), "FL": (28.6, -82.5),
        "GA": (32.7, -83.4), "HI": (20.8, -156.3), "ID": (44.4, -114.6),
        "IL": (40.0, -89.2), "IN": (39.9, -86.3), "IA": (42.0, -93.5),
        "KS": (38.5, -98.3), "KY": (37.5, -85.3), "LA": (31.0, -92.0),
        "ME": (45.4, -69.2), "MD": (39.0, -76.8), "MA": (42.3, -72.0),
        "MI": (44.3, -85.4), "MN": (46.3, -94.3), "MS": (32.7, -89.7),
        "MO": (38.4, -92.5), "MT": (47.1, -109.6), "NE": (41.5, -99.8),
        "NV": (39.9, -117.2), "NH": (43.7, -71.6), "NJ": (40.1, -74.7),
        "NM": (34.5, -106.1), "NY": (42.9, -75.5), "NC": (35.6, -79.4),
        "ND": (47.5, -100.5), "OH": (40.4, -82.8), "OK": (35.6, -97.5),
        "OR": (44.1, -120.5), "PA": (40.9, -77.8), "RI": (41.7, -71.5),
        "SC": (33.9, -80.9), "SD": (44.4, -100.2), "TN": (35.9, -86.4),
        "TX": (31.5, -99.3), "UT": (39.3, -111.7), "VT": (44.1, -72.7),
        "VA": (37.5, -78.9), "WA": (47.4, -120.7), "WV": (38.6, -80.6),
        "WI": (44.6, -89.8), "WY": (43.0, -107.6), "DC": (38.9, -77.0),
        "PR": (18.2, -66.5), "VI": (18.3, -64.9), "GU": (13.4, 144.8),
        "AS": (-14.3, -170.7), "MP": (15.2, 145.7),
    }

    def __init__(
        self,
        *,
        lookback_days: int = 30,
        limit: int = 100,
        state: str | None = None,
        declaration_type: str | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self.lookback_days = max(1, min(365, lookback_days))
        self.limit = max(1, min(1000, limit))
        self.state = state
        self.declaration_type = declaration_type
        self.timeout_s = timeout_s

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        geo_events: list[dict] = []
        metadata: dict = {
            "lookback_days": self.lookback_days,
            "state": self.state,
        }

        cutoff = (datetime.now(UTC) - timedelta(days=self.lookback_days)).strftime("%Y-%m-%dT00:00:00.000z")

        filter_parts = [f"declarationDate gt '{cutoff}'"]
        if self.state:
            filter_parts.append(f"state eq '{self.state.upper()}'")
        if self.declaration_type:
            filter_parts.append(f"declarationType eq '{self.declaration_type.upper()}'")

        params = {
            "$filter": " and ".join(filter_parts),
            "$top": str(self.limit),
            "$orderby": "declarationDate desc",
        }

        try:
            with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
                resp = client.get(self.API_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            metadata["error"] = str(exc)
            return ConnectorResult(connector=self.name, observations=[], metadata=metadata)

        records = data.get("DisasterDeclarationsSummaries") or []
        metadata["record_count"] = len(records)

        for record in records:
            disaster_id = record.get("disasterNumber", "")
            declaration_date = record.get("declarationDate", "")
            state = record.get("state", "")
            declaration_type = record.get("declarationType", "")
            incident_type = record.get("incidentType", "")
            title = record.get("declarationTitle", "")
            designated_area = record.get("designatedArea", "")
            begin_date = record.get("incidentBeginDate", "")
            end_date = record.get("incidentEndDate", "")
            _place_code = record.get("placeCode", "")

            captured_at = datetime.now(UTC)
            if declaration_date:
                try:
                    captured_at = datetime.fromisoformat(declaration_date.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    pass

            claim = f"FEMA {declaration_type} Declaration: {title}"
            if state:
                claim += f" in {state}"
            if designated_area:
                claim += f" ({designated_area})"
            if incident_type:
                claim += f" — {incident_type}"

            if query and query.lower() not in claim.lower():
                continue

            reliability = _DISASTER_TYPE_RELIABILITY.get(declaration_type, 0.85)

            fema_url = f"https://www.fema.gov/disaster/{disaster_id}" if disaster_id else "https://www.fema.gov/"

            observations.append(
                Observation(
                    source=f"fema:{incident_type.lower().replace(' ', '_')}",
                    claim=claim,
                    url=fema_url,
                    captured_at=captured_at,
                    reliability_hint=reliability,
                )
            )

            coords = self._STATE_COORDS.get(state.upper()) if state else None
            if coords:
                geo_events.append({
                    "layer": "disaster",
                    "source_id": f"fema_{disaster_id}",
                    "title": claim[:500],
                    "latitude": coords[0],
                    "longitude": coords[1],
                    "magnitude": None,
                    "event_time": captured_at,
                    "properties": {
                        "disaster_number": disaster_id,
                        "declaration_type": declaration_type,
                        "incident_type": incident_type,
                        "state": state,
                        "designated_area": designated_area,
                        "begin_date": begin_date,
                        "end_date": end_date,
                    },
                })

        metadata["geo_events"] = geo_events
        return ConnectorResult(connector=self.name, observations=observations, metadata=metadata)
