from __future__ import annotations

from datetime import UTC, datetime

import feedparser

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation


class RSSConnector:
    """Collects observations from RSS/Atom feeds."""

    name = "rss"

    def __init__(self, feed_urls: list[str], per_feed_limit: int = 10) -> None:
        self.feed_urls = feed_urls
        self.per_feed_limit = per_feed_limit

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []

        for url in self.feed_urls:
            parsed = feedparser.parse(url)
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

        return ConnectorResult(connector=self.name, observations=observations)
