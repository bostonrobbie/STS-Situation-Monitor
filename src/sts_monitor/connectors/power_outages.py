"""Power Outage connector — Eversource and National Grid outage monitors.

Attempts to fetch outage summary data from known utility API patterns.
These URLs change frequently as utilities update their outage-map
infrastructure, so the connector is built to fail gracefully and log
warnings rather than crash.

To find updated URLs:
  - Eversource: Open https://outagemap.eversource.com/ in a browser,
    inspect network traffic for JSON requests to their data endpoints.
  - National Grid: Open https://www.nationalgridus.com/outage-central,
    inspect network traffic for JSON/XML outage feeds.
  - PowerOutage.us aggregates data but requires an API key.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation

log = logging.getLogger(__name__)

# Known Eversource outage-data URL patterns.  These are S3-hosted JSON
# files that the outage map front-end fetches.  They rotate periodically.
_DEFAULT_EVERSOURCE_URL = (
    "https://outagemap.eversource.com/resources/data/external/interval_generation_data/outage_data.json"
)

# National Grid outage summary endpoint (MA service territory).
_DEFAULT_NATIONALGRID_URL = (
    "https://www.nationalgridus.com/media/pdfs/billing-payments/outage-central/outage-data.json"
)


class PowerOutageConnector:
    """Monitors Eversource and National Grid for power outage data."""

    name = "power_outages"

    def __init__(
        self,
        *,
        eversource_url: str = _DEFAULT_EVERSOURCE_URL,
        nationalgrid_url: str = _DEFAULT_NATIONALGRID_URL,
        timeout_s: float = 15.0,
    ) -> None:
        self.eversource_url = eversource_url
        self.nationalgrid_url = nationalgrid_url
        self.timeout_s = timeout_s

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_eversource(data: Any) -> list[dict]:
        """Extract outage records from Eversource JSON.

        The exact structure varies, but typically each record has fields
        like ``customers_affected``, ``town``, ``status``, etc.  We try
        several common shapes and return a normalised list of dicts.
        """
        outages: list[dict] = []

        # Shape 1: top-level list of outage objects
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    outages.append(item)
            return outages

        # Shape 2: dict with an "outages" or "data" key
        if isinstance(data, dict):
            for key in ("outages", "data", "events", "incidents"):
                nested = data.get(key)
                if isinstance(nested, list):
                    for item in nested:
                        if isinstance(item, dict):
                            outages.append(item)
                    return outages

            # Shape 3: dict keyed by town/area name
            for key, val in data.items():
                if isinstance(val, dict):
                    val["_area"] = key
                    outages.append(val)

        return outages

    @staticmethod
    def _parse_nationalgrid(data: Any) -> list[dict]:
        """Extract outage records from National Grid JSON."""
        outages: list[dict] = []

        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    outages.append(item)
            return outages

        if isinstance(data, dict):
            for key in ("outages", "data", "areas", "incidents"):
                nested = data.get(key)
                if isinstance(nested, list):
                    for item in nested:
                        if isinstance(item, dict):
                            outages.append(item)
                    return outages

        return outages

    def _fetch_eversource(
        self, client: httpx.Client, query: str | None,
    ) -> tuple[list[Observation], dict]:
        observations: list[Observation] = []
        meta: dict = {}

        try:
            resp = client.get(self.eversource_url)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.warning("Eversource outage fetch failed: %s", exc)
            meta["eversource_error"] = str(exc)
            return observations, meta

        records = self._parse_eversource(data)
        meta["eversource_records"] = len(records)

        for rec in records:
            # Try common field names across different JSON shapes
            town = (
                rec.get("town")
                or rec.get("Town")
                or rec.get("area")
                or rec.get("_area")
                or "Unknown area"
            )
            customers = (
                rec.get("customers_affected")
                or rec.get("CustomersAffected")
                or rec.get("cust_affected")
                or rec.get("numCustOut")
                or "unknown number of"
            )
            status = (
                rec.get("status")
                or rec.get("Status")
                or rec.get("cause")
                or ""
            )
            est_restore = (
                rec.get("estimated_restoration")
                or rec.get("EstRestoreTime")
                or rec.get("etr")
                or ""
            )

            claim = f"Eversource outage in {town}: {customers} customers affected"
            if status:
                claim += f" ({status})"
            if est_restore:
                claim += f" — est. restore: {est_restore}"

            if query and query.lower() not in claim.lower():
                continue

            observations.append(
                Observation(
                    source="eversource_outage",
                    claim=claim,
                    url="https://outagemap.eversource.com/",
                    captured_at=datetime.now(UTC),
                    reliability_hint=0.7,
                ),
            )

        return observations, meta

    def _fetch_nationalgrid(
        self, client: httpx.Client, query: str | None,
    ) -> tuple[list[Observation], dict]:
        observations: list[Observation] = []
        meta: dict = {}

        try:
            resp = client.get(self.nationalgrid_url)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.warning("National Grid outage fetch failed: %s", exc)
            meta["nationalgrid_error"] = str(exc)
            return observations, meta

        records = self._parse_nationalgrid(data)
        meta["nationalgrid_records"] = len(records)

        for rec in records:
            area = (
                rec.get("area")
                or rec.get("Area")
                or rec.get("town")
                or rec.get("Town")
                or "Unknown area"
            )
            customers = (
                rec.get("customers_affected")
                or rec.get("CustomersAffected")
                or rec.get("cust_out")
                or "unknown number of"
            )
            cause = rec.get("cause") or rec.get("Cause") or ""
            est_restore = (
                rec.get("estimated_restoration")
                or rec.get("ETR")
                or rec.get("etr")
                or ""
            )

            claim = f"National Grid outage in {area}: {customers} customers affected"
            if cause:
                claim += f" ({cause})"
            if est_restore:
                claim += f" — est. restore: {est_restore}"

            if query and query.lower() not in claim.lower():
                continue

            observations.append(
                Observation(
                    source="nationalgrid_outage",
                    claim=claim,
                    url="https://www.nationalgridus.com/outage-central",
                    captured_at=datetime.now(UTC),
                    reliability_hint=0.7,
                ),
            )

        return observations, meta

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        metadata: dict = {}

        headers = {
            "User-Agent": "STS-Situation-Monitor/1.0 (situation-monitor)",
            "Accept": "application/json",
        }

        with httpx.Client(timeout=self.timeout_s, follow_redirects=True, headers=headers) as client:
            obs, meta = self._fetch_eversource(client, query)
            observations.extend(obs)
            metadata.update(meta)

            obs, meta = self._fetch_nationalgrid(client, query)
            observations.extend(obs)
            metadata.update(meta)

        return ConnectorResult(
            connector=self.name,
            observations=observations,
            metadata=metadata,
        )
