"""MBTA real-time alerts connector.

The MBTA V3 API provides free, real-time service alerts for all transit
lines across Massachusetts.  No API key required for basic alert access,
though a free key (https://api-v3.mbta.com/register) raises rate limits.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation

# MBTA lines and their approximate center coordinates
_LINE_COORDS: dict[str, tuple[float, float]] = {
    "Red": (42.3523, -71.0553),
    "Orange": (42.3662, -71.0621),
    "Blue": (42.3614, -71.0170),
    "Green": (42.3519, -71.0769),
    "Green-B": (42.3509, -71.1058),
    "Green-C": (42.3407, -71.1100),
    "Green-D": (42.3312, -71.1283),
    "Green-E": (42.3414, -71.0870),
    "Mattapan": (42.2842, -71.0932),
    "Silver": (42.3467, -71.0396),
    "Commuter Rail": (42.3662, -71.0621),
    "Bus": (42.3601, -71.0589),
    "Ferry": (42.3534, -71.0496),
}


class MBTAConnector:
    """Fetches real-time MBTA service alerts."""

    name = "mbta"

    ALERTS_URL = "https://api-v3.mbta.com/alerts"

    def __init__(
        self,
        *,
        api_key: str = "",
        severity_min: int = 3,  # 0-10, higher = more severe
        timeout_s: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self.severity_min = severity_min
        self.timeout_s = timeout_s

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        metadata: dict[str, Any] = {}

        headers: dict[str, str] = {"Accept": "application/vnd.api+json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key

        params: dict[str, str] = {
            "filter[activity]": "BOARD,EXIT,RIDE",
            "sort": "-severity",
        }

        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                resp = client.get(self.ALERTS_URL, headers=headers, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            metadata["error"] = str(exc)
            return ConnectorResult(connector=self.name, observations=[], metadata=metadata)

        alerts = data.get("data", [])
        metadata["total_alerts"] = len(alerts)

        for alert in alerts:
            attrs = alert.get("attributes", {})
            severity = attrs.get("severity", 0)
            if severity < self.severity_min:
                continue

            header = attrs.get("header", "")
            description = attrs.get("description", "") or ""
            effect = attrs.get("effect", "")
            lifecycle = attrs.get("lifecycle", "")
            url = attrs.get("url", "") or ""

            # Extract affected routes
            routes: list[str] = []
            for entity in attrs.get("informed_entity", []):
                route = entity.get("route", "")
                if route and route not in routes:
                    routes.append(route)

            route_str = ", ".join(routes) if routes else "System-wide"
            claim = f"MBTA {effect}: {header}"
            if description and len(description) < 300:
                claim += f" — {description}"

            # Use first route's coordinates for geo-tagging
            lat, lon = 42.3601, -71.0589  # default Boston center
            for r in routes:
                if r in _LINE_COORDS:
                    lat, lon = _LINE_COORDS[r]
                    break

            if query and query.lower() not in claim.lower():
                continue

            observations.append(Observation(
                source=f"mbta:{route_str}",
                claim=claim[:600],
                url=url or "https://www.mbta.com/alerts",
                captured_at=datetime.now(UTC),
                reliability_hint=0.9,
            ))

        metadata["filtered_count"] = len(observations)
        return ConnectorResult(connector=self.name, observations=observations, metadata=metadata)
