"""Google News RSS connector — free news search via RSS feeds.

Fetches headlines from Google News RSS for configurable search queries.
No authentication required.
"""

from __future__ import annotations

from datetime import UTC, datetime
import time

import feedparser

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation


MA_REGION_QUERIES = [
    "Massachusetts breaking news",
    "Boston news today",
    "Worcester MA news",
    "Springfield Massachusetts news",
    "Cambridge MA news",
    "Lowell Massachusetts news",
    "New Bedford MA news",
    "Fall River Massachusetts news",
    "Lynn MA news",
    "Lawrence Massachusetts news",
    "Brockton MA news",
    "Quincy Massachusetts news",
    "Framingham MA news",
    "Plymouth Massachusetts news",
    "Pittsfield Massachusetts news",
    "Cape Cod news",
    "Martha's Vineyard news",
    "Nantucket news",
    "MetroWest Massachusetts news",
    "Pioneer Valley Massachusetts news",
    "Berkshires Massachusetts news",
    "North Shore Massachusetts news",
    "South Shore Massachusetts news",
    "Merrimack Valley Massachusetts news",
    "Central Massachusetts news",
    "Western Massachusetts news",
    "South Coast Massachusetts news",
    "Massachusetts crime police",
    "Massachusetts weather storm",
    "Massachusetts traffic accident",
    "MBTA delays Boston",
    "Massachusetts fire emergency",
]

_BASE_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


class GoogleNewsConnector:
    """Collects observations from Google News RSS search feeds."""

    name = "google_news"

    def __init__(
        self,
        queries: list[str],
        per_query_limit: int = 10,
        max_retries: int = 2,
    ) -> None:
        self.queries = queries
        self.per_query_limit = per_query_limit
        self.max_retries = max_retries

    def _fetch_with_retry(self, url: str):
        """Parse an RSS feed URL with retry and backoff."""
        last_error: str | None = None
        for attempt in range(self.max_retries + 1):
            try:
                parsed = feedparser.parse(
                    url,
                    request_headers={"User-Agent": "STS-Situation-Monitor/0.5"},
                )
                if getattr(parsed, "bozo", False) and getattr(parsed, "bozo_exception", None):
                    raise RuntimeError(str(parsed.bozo_exception))
                return parsed, attempt
            except Exception as exc:
                last_error = str(exc)
                if attempt < self.max_retries:
                    time.sleep(0.2 * (attempt + 1))
        return None, last_error

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        failed_queries: list[dict[str, str]] = []

        for search_query in self.queries:
            encoded = search_query.replace(" ", "+")
            url = _BASE_URL.format(query=encoded)

            parsed, parse_meta = self._fetch_with_retry(url)
            if parsed is None:
                failed_queries.append({"query": search_query, "error": str(parse_meta)})
                continue

            entries = getattr(parsed, "entries", [])[: self.per_query_limit]

            for entry in entries:
                title = entry.get("title", "Untitled")
                summary = entry.get("summary", "")
                link = entry.get("link", url)
                text = f"{title}. {summary}".strip()

                # If an additional runtime query filter was passed, apply it
                if query and query.lower() not in text.lower():
                    continue

                observations.append(
                    Observation(
                        source=f"google_news:{search_query}",
                        claim=text,
                        url=link,
                        captured_at=datetime.now(UTC),
                        reliability_hint=0.55,
                    )
                )

        return ConnectorResult(
            connector=self.name,
            observations=observations,
            metadata={
                "failed_queries": failed_queries,
                "query_count": len(self.queries),
            },
        )
