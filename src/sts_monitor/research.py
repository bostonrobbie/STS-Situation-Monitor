from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote_plus

import feedparser

from sts_monitor.pipeline import Observation


@dataclass(slots=True)
class TrendingTopic:
    topic: str
    traffic: str | None
    published_at: str | None
    source_url: str


class TrendingResearchScanner:
    """Fetch trending topics and related headlines from public RSS endpoints."""

    trends_feed_template = "https://trends.google.com/trending/rss?geo={geo}"
    news_search_template = "https://news.google.com/rss/search?q={query}"

    def __init__(self, *, timeout_s: float = 10.0, max_topics: int = 10, per_topic_limit: int = 5) -> None:
        self.timeout_s = timeout_s
        self.max_topics = max(1, min(50, max_topics))
        self.per_topic_limit = max(1, min(20, per_topic_limit))

    def fetch_topics(self, geo: str = "US") -> list[TrendingTopic]:
        feed_url = self.trends_feed_template.format(geo=geo.upper())
        parsed = feedparser.parse(feed_url, request_headers={"User-Agent": "STS-Situation-Monitor/0.5"})
        topics: list[TrendingTopic] = []

        for entry in getattr(parsed, "entries", [])[: self.max_topics]:
            title = (entry.get("title") or "").strip()
            if not title:
                continue
            tags = entry.get("tags") or []
            traffic = None
            if tags and isinstance(tags, list):
                traffic = tags[0].get("term") if isinstance(tags[0], dict) else None

            topics.append(
                TrendingTopic(
                    topic=title,
                    traffic=traffic,
                    published_at=entry.get("published"),
                    source_url=entry.get("link") or feed_url,
                )
            )

        return topics

    def collect_observations(self, geo: str = "US") -> tuple[list[Observation], dict[str, object]]:
        topics = self.fetch_topics(geo=geo)
        observations: list[Observation] = []
        failed_topics: list[str] = []

        for trend in topics:
            query = quote_plus(trend.topic)
            news_url = self.news_search_template.format(query=query)
            parsed = feedparser.parse(news_url, request_headers={"User-Agent": "STS-Situation-Monitor/0.5"})
            entries = getattr(parsed, "entries", [])[: self.per_topic_limit]
            if not entries:
                failed_topics.append(trend.topic)
                continue

            for entry in entries:
                headline = entry.get("title", "Untitled")
                summary = entry.get("summary", "")
                link = entry.get("link", news_url)
                text = f"Trending topic '{trend.topic}': {headline}. {summary}".strip()
                observations.append(
                    Observation(
                        source=f"trending:{geo.upper()}:{trend.topic}",
                        claim=text,
                        url=link,
                        captured_at=datetime.now(UTC),
                        reliability_hint=0.55,
                    )
                )

        metadata: dict[str, object] = {
            "geo": geo.upper(),
            "topics_scanned": len(topics),
            "failed_topics": failed_topics,
        }
        return observations, metadata
