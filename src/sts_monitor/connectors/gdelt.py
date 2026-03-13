"""GDELT DOC 2.0 API connector — global news events across 100+ languages.

Updates every 15 minutes. Free, no authentication required.
API docs: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote_plus

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation


# Domain trust scores for well-known outlets
_TRUSTED_DOMAINS: dict[str, float] = {
    "reuters.com": 0.82, "apnews.com": 0.82, "bbc.co.uk": 0.80, "bbc.com": 0.80,
    "aljazeera.com": 0.75, "theguardian.com": 0.75, "nytimes.com": 0.78,
    "washingtonpost.com": 0.76, "france24.com": 0.74, "dw.com": 0.74,
    "npr.org": 0.76, "pbs.org": 0.74, "economist.com": 0.77,
    "ft.com": 0.77, "bloomberg.com": 0.76, "cnn.com": 0.68,
    "foxnews.com": 0.58, "rt.com": 0.40, "sputniknews.com": 0.35,
}


def _domain_reliability(domain: str) -> float:
    """Return reliability hint based on domain reputation."""
    domain = domain.lower().strip()
    for known, score in _TRUSTED_DOMAINS.items():
        if domain == known or domain.endswith("." + known):
            return score
    return 0.55


class GDELTConnector:
    """Fetches articles from the GDELT DOC 2.0 API."""

    name = "gdelt"

    DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
    GEO_API = "https://api.gdeltproject.org/api/v2/geo/geo"

    def __init__(
        self,
        *,
        timespan: str = "3h",
        max_records: int = 75,
        mode: str = "ArtList",
        timeout_s: float = 15.0,
        source_country: str | None = None,
        source_lang: str | None = None,
    ) -> None:
        self.timespan = timespan
        self.max_records = max(1, min(250, max_records))
        self.mode = mode
        self.timeout_s = timeout_s
        self.source_country = source_country
        self.source_lang = source_lang

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        metadata: dict = {"api": "doc_v2", "timespan": self.timespan}

        if not query:
            metadata["error"] = "GDELT requires a query string"
            return ConnectorResult(connector=self.name, observations=[], metadata=metadata)

        params: dict[str, str] = {
            "query": query,
            "mode": self.mode,
            "format": "json",
            "maxrecords": str(self.max_records),
            "timespan": self.timespan,
            "sort": "DateDesc",
        }
        if self.source_country:
            params["query"] += f" sourcecountry:{self.source_country}"
        if self.source_lang:
            params["query"] += f" sourcelang:{self.source_lang}"

        try:
            with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
                resp = client.get(self.DOC_API, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            metadata["error"] = str(exc)
            return ConnectorResult(connector=self.name, observations=[], metadata=metadata)

        articles = data.get("articles") or []
        metadata["article_count"] = len(articles)

        for article in articles:
            url = article.get("url", "")
            title = article.get("title", "").strip()
            domain = article.get("domain", "")
            seendate = article.get("seendate", "")
            source_country = article.get("sourcecountry", "")
            language = article.get("language", "")

            if not title or not url:
                continue

            captured_at = datetime.now(UTC)
            if seendate:
                try:
                    captured_at = datetime.strptime(seendate[:14], "%Y%m%d%H%M%S").replace(tzinfo=UTC)
                except (ValueError, IndexError):
                    pass

            claim = title
            reliability = _domain_reliability(domain)

            observations.append(
                Observation(
                    source=f"gdelt:{domain}",
                    claim=claim,
                    url=url,
                    captured_at=captured_at,
                    reliability_hint=reliability,
                )
            )

        metadata["source_country_filter"] = self.source_country
        metadata["source_lang_filter"] = self.source_lang
        return ConnectorResult(connector=self.name, observations=observations, metadata=metadata)

    def collect_geo(self, query: str, timespan: str | None = None) -> list[dict]:
        """Fetch geotagged events from the GDELT GEO 2.0 API.

        Returns list of dicts with lat, lon, count, and article URLs.
        """
        params = {
            "query": query,
            "format": "GeoJSON",
            "timespan": timespan or self.timespan,
        }
        try:
            with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
                resp = client.get(self.GEO_API, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []

        results = []
        for feature in (data.get("features") or []):
            coords = (feature.get("geometry") or {}).get("coordinates", [])
            props = feature.get("properties") or {}
            if len(coords) >= 2:
                results.append({
                    "latitude": coords[1],
                    "longitude": coords[0],
                    "count": props.get("count", 1),
                    "name": props.get("name", ""),
                    "url": props.get("url", ""),
                    "html": props.get("html", ""),
                })
        return results
