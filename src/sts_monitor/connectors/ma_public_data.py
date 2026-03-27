"""Massachusetts Public Data connector — Boston 311 and BPD crime incidents.

Aggregates free, no-auth public data from Boston's CKAN open-data portal.

Sources:
  - Boston 311 Service Requests (potholes, noise, streetlights, etc.)
  - Boston Police Department crime/incident reports

API docs: https://data.boston.gov/
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation

log = logging.getLogger(__name__)

# Boston open-data (CKAN) base
_CKAN_BASE = "https://data.boston.gov/api/3/action/datastore_search"

# Default resource IDs — these are subject to change when the city
# republishes datasets.  Override via constructor if they go stale.
_DEFAULT_311_RESOURCE = "2968e2c0-d479-49ba-a884-4ef523ada3c0"
_DEFAULT_CRIME_RESOURCE = "12cb3883-56f5-47de-afa5-3b1cf61b257b"


class MAPublicDataConnector:
    """Fetches recent Boston 311 reports and BPD crime incidents."""

    name = "ma_public_data"

    def __init__(
        self,
        *,
        sources: list[str] | None = None,
        boston_311_resource_id: str = _DEFAULT_311_RESOURCE,
        boston_crime_resource_id: str = _DEFAULT_CRIME_RESOURCE,
        limit: int = 25,
        timeout_s: float = 15.0,
    ) -> None:
        self.sources = sources or ["boston_311", "boston_crime"]
        self.boston_311_resource_id = boston_311_resource_id
        self.boston_crime_resource_id = boston_crime_resource_id
        self.limit = limit
        self.timeout_s = timeout_s

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_311(
        self, client: httpx.Client, query: str | None,
    ) -> tuple[list[Observation], dict]:
        """Fetch recent Boston 311 service requests."""
        observations: list[Observation] = []
        meta: dict = {}

        params: dict[str, str | int] = {
            "resource_id": self.boston_311_resource_id,
            "limit": self.limit,
            "sort": "open_dt desc",
        }
        if query:
            params["q"] = query

        try:
            resp = client.get(_CKAN_BASE, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.warning("boston_311 fetch failed: %s", exc)
            meta["boston_311_error"] = str(exc)
            return observations, meta

        records = (data.get("result") or {}).get("records") or []
        meta["boston_311_count"] = len(records)

        for rec in records:
            reason = rec.get("reason") or rec.get("REASON") or "Unknown"
            location = rec.get("location") or rec.get("LOCATION") or ""
            status = rec.get("case_status") or rec.get("CASE_STATUS") or ""
            open_dt = rec.get("open_dt") or rec.get("OPEN_DT") or ""
            lat = rec.get("latitude") or rec.get("LATITUDE")
            lon = rec.get("longitude") or rec.get("LONGITUDE")

            claim = f"311 Report: {reason}"
            if location:
                claim += f" at {location}"
            if status:
                claim += f" - {status}"

            captured_at = datetime.now(UTC)
            if open_dt:
                try:
                    captured_at = datetime.fromisoformat(
                        open_dt.replace("Z", "+00:00"),
                    )
                except (ValueError, AttributeError):
                    pass

            observations.append(
                Observation(
                    source="boston_311",
                    claim=claim,
                    url="https://data.boston.gov/dataset/311-service-requests",
                    captured_at=captured_at,
                    reliability_hint=0.8,
                ),
            )

        return observations, meta

    def _fetch_crime(
        self, client: httpx.Client, query: str | None,
    ) -> tuple[list[Observation], dict]:
        """Fetch recent Boston Police Department incident reports."""
        observations: list[Observation] = []
        meta: dict = {}

        params: dict[str, str | int] = {
            "resource_id": self.boston_crime_resource_id,
            "limit": self.limit,
            "sort": "OCCURRED_ON_DATE desc",
        }
        if query:
            params["q"] = query

        try:
            resp = client.get(_CKAN_BASE, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.warning("boston_crime fetch failed: %s", exc)
            meta["boston_crime_error"] = str(exc)
            return observations, meta

        records = (data.get("result") or {}).get("records") or []
        meta["boston_crime_count"] = len(records)

        for rec in records:
            offense = rec.get("OFFENSE_DESCRIPTION") or rec.get("offense_description") or "Unknown"
            street = rec.get("STREET") or rec.get("street") or ""
            district = rec.get("DISTRICT") or rec.get("district") or ""
            occurred = rec.get("OCCURRED_ON_DATE") or rec.get("occurred_on_date") or ""
            lat = rec.get("Lat") or rec.get("lat")
            lon = rec.get("Long") or rec.get("long")

            claim = f"BPD Incident: {offense}"
            if street:
                claim += f" at {street}"
            if district:
                claim += f" - District {district}"

            captured_at = datetime.now(UTC)
            if occurred:
                try:
                    captured_at = datetime.fromisoformat(
                        occurred.replace("Z", "+00:00"),
                    )
                except (ValueError, AttributeError):
                    pass

            observations.append(
                Observation(
                    source="boston_crime",
                    claim=claim,
                    url="https://data.boston.gov/dataset/crime-incident-reports-august-2015-to-date-source-new-system",
                    captured_at=captured_at,
                    reliability_hint=0.8,
                ),
            )

        return observations, meta

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        metadata: dict = {"sources_requested": self.sources}

        headers = {
            "User-Agent": "STS-Situation-Monitor/1.0 (situation-monitor)",
            "Accept": "application/json",
        }

        with httpx.Client(timeout=self.timeout_s, follow_redirects=True, headers=headers) as client:
            if "boston_311" in self.sources:
                obs, meta = self._fetch_311(client, query)
                observations.extend(obs)
                metadata.update(meta)

            if "boston_crime" in self.sources:
                obs, meta = self._fetch_crime(client, query)
                observations.extend(obs)
                metadata.update(meta)

        return ConnectorResult(
            connector=self.name,
            observations=observations,
            metadata=metadata,
        )
