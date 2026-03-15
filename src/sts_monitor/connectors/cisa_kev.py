"""
CISA Known Exploited Vulnerabilities (KEV) connector.
Fetches CVEs added in the last 7 days.
Layer: cyber
"""
from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation

# CISA KEV JSON feed
_CISA_KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"

# USA coordinates base (CISA is Washington DC)
_USA_LAT = 38.9
_USA_LON = -77.0

# Max jitter range in degrees
_JITTER = 3.0


class CISAKEVConnector:
    """Fetches CISA Known Exploited Vulnerabilities added in last 7 days."""

    name = "cisa_kev"

    def __init__(
        self,
        lookback_days: int = 7,
        timeout_s: float = 20.0,
    ) -> None:
        self.lookback_days = lookback_days
        self.timeout_s = timeout_s

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        geo_events: list[dict] = []
        metadata: dict = {"layer": "cyber"}

        cutoff = datetime.now(UTC) - timedelta(days=self.lookback_days)

        try:
            with httpx.Client(
                timeout=self.timeout_s,
                follow_redirects=True,
                headers={"User-Agent": "STS-Situation-Monitor/0.7"},
            ) as client:
                resp = client.get(_CISA_KEV_URL)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            metadata["error"] = str(exc)
            return ConnectorResult(connector=self.name, observations=[], metadata=metadata)

        vulnerabilities = data.get("vulnerabilities", [])
        metadata["total_in_feed"] = len(vulnerabilities)

        recent_vulns = []
        for vuln in vulnerabilities:
            date_added = vuln.get("dateAdded", "")
            try:
                added_dt = datetime.fromisoformat(date_added)
                if added_dt.tzinfo is None:
                    added_dt = added_dt.replace(tzinfo=UTC)
                if added_dt < cutoff:
                    continue
            except Exception:
                continue
            recent_vulns.append(vuln)

        metadata["recent_vulns"] = len(recent_vulns)

        for vuln in recent_vulns:
            cve_id = vuln.get("cveID", "CVE-UNKNOWN")
            vendor = vuln.get("vendorProject", "Unknown Vendor")
            product = vuln.get("product", "Unknown Product")
            vuln_name = vuln.get("vulnerabilityName", "")
            description = vuln.get("shortDescription", "")
            date_added = vuln.get("dateAdded", "")
            due_date = vuln.get("dueDate", "")
            required_action = vuln.get("requiredAction", "")

            # Significance: use CVSS-like heuristics
            sig = 7.0  # CISA KEV = actively exploited = high significance
            title_text = f"{cve_id}: {vendor} {product} - {vuln_name}"

            # Critical keyword boost
            crit_keywords = ["remote code", "rce", "unauthenticated", "critical", "zero-day", "0-day", "ransomware"]
            combined_text = f"{vuln_name} {description}".lower()
            for kw in crit_keywords:
                if kw in combined_text:
                    sig = min(10.0, sig + 0.5)

            try:
                event_time = datetime.fromisoformat(date_added)
                if event_time.tzinfo is None:
                    event_time = event_time.replace(tzinfo=UTC)
            except Exception:
                event_time = datetime.now(UTC)

            # Jitter around USA coords
            seed = hash(cve_id)
            lat = _USA_LAT + ((seed % 1000) / 1000.0 - 0.5) * _JITTER * 2
            lon = _USA_LON + (((seed >> 10) % 1000) / 1000.0 - 0.5) * _JITTER * 2

            source_id = f"cisa_kev_{cve_id}"

            observations.append(Observation(
                source="cisa_kev",
                claim=f"[CYBER] {cve_id} — {vendor} {product}: {description[:300]}",
                url=f"https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
                captured_at=event_time,
                reliability_hint=0.95,  # CISA is authoritative
            ))
            geo_events.append({
                "layer": "cyber",
                "source_id": source_id,
                "title": title_text[:500],
                "latitude": lat,
                "longitude": lon,
                "magnitude": round(sig, 1),
                "event_time": event_time.isoformat(),
                "properties": {
                    "layer": "cyber",
                    "source": "cisa_kev",
                    "cveID": cve_id,
                    "vendorProject": vendor,
                    "product": product,
                    "vulnerabilityName": vuln_name,
                    "shortDescription": description[:500],
                    "dateAdded": date_added,
                    "dueDate": due_date,
                    "requiredAction": required_action,
                    "significance": sig,
                },
            })

        metadata["geo_events_count"] = len(geo_events)
        return ConnectorResult(
            connector=self.name,
            observations=observations,
            metadata={**metadata, "geo_events": geo_events},
        )
