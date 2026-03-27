from __future__ import annotations

from datetime import UTC, datetime
import time

import feedparser

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation


class RSSConnector:
    """Collects observations from RSS/Atom feeds with basic retry/backoff."""

    name = "rss"

    def __init__(
        self,
        feed_urls: list[str],
        per_feed_limit: int = 10,
        timeout_s: float = 10.0,
        max_retries: int = 2,
    ) -> None:
        self.feed_urls = feed_urls
        self.per_feed_limit = per_feed_limit
        self.timeout_s = timeout_s
        self.max_retries = max_retries

    def _parse_with_retry(self, url: str):
        last_error: str | None = None
        for attempt in range(self.max_retries + 1):
            try:
                parsed = feedparser.parse(url, request_headers={"User-Agent": "STS-Situation-Monitor/0.5"})
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
        failed_feeds: list[dict[str, str]] = []

        for url in self.feed_urls:
            parsed, parse_meta = self._parse_with_retry(url)
            if parsed is None:
                failed_feeds.append({"url": url, "error": str(parse_meta)})
                continue

            entries = getattr(parsed, "entries", [])[: self.per_feed_limit]

            for entry in entries:
                title = entry.get("title", "Untitled")
                summary = entry.get("summary", "")
                link = entry.get("link", url)
                text = f"{title}. {summary}".strip()

                if query and query.lower() not in text.lower():
                    continue

                observations.append(
                    Observation(
                        source=f"rss:{url}",
                        claim=text,
                        url=link,
                        captured_at=datetime.now(UTC),
                        reliability_hint=0.6,
                    )
                )

        return ConnectorResult(
            connector=self.name,
            observations=observations,
            metadata={"failed_feeds": failed_feeds, "feed_count": len(self.feed_urls)},
        )


def get_expanded_feed_urls() -> list[str]:
    """Return all curated Massachusetts-focused feed URLs for auto-ingestion."""
    from sts_monitor.collection_plan import get_curated_feeds

    feeds = get_curated_feeds([
        "boston_metro",
        "central_ma",
        "western_ma",
        "north_shore_merrimack",
        "south_shore_coast",
        "cape_islands",
        "massachusetts_government",
        "massachusetts_public_safety",
        "weather_environment",
        "massachusetts_general",
        "new_england_regional",
    ])
    return [f["url"] for f in feeds]
