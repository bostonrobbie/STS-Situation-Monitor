from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote_plus

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation


class RedditConnector:
    """Collect public Reddit posts from JSON listing endpoints."""

    name = "reddit"

    def __init__(
        self,
        *,
        subreddits: list[str],
        per_subreddit_limit: int = 25,
        sort: str = "new",
        timeout_s: float = 10.0,
        user_agent: str = "STS-Situation-Monitor/0.6",
    ) -> None:
        self.subreddits = [s.strip().lower() for s in subreddits if s.strip()]
        self.per_subreddit_limit = max(1, min(100, per_subreddit_limit))
        self.sort = sort if sort in {"new", "hot", "top"} else "new"
        self.timeout_s = timeout_s
        self.user_agent = user_agent

    def _url(self, subreddit: str) -> str:
        return f"https://www.reddit.com/r/{quote_plus(subreddit)}/{self.sort}.json?raw_json=1&limit={self.per_subreddit_limit}"

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        failed_subreddits: list[dict[str, str]] = []

        headers = {"User-Agent": self.user_agent}
        with httpx.Client(timeout=self.timeout_s, follow_redirects=True, headers=headers) as client:
            for subreddit in self.subreddits:
                url = self._url(subreddit)
                try:
                    response = client.get(url)
                    response.raise_for_status()
                    payload = response.json()
                except Exception as exc:
                    failed_subreddits.append({"subreddit": subreddit, "error": str(exc)})
                    continue

                posts = (((payload or {}).get("data") or {}).get("children") or [])
                for item in posts:
                    data = (item or {}).get("data") or {}
                    title = (data.get("title") or "").strip()
                    selftext = (data.get("selftext") or "").strip()
                    permalink = data.get("permalink") or ""
                    post_url = f"https://www.reddit.com{permalink}" if permalink.startswith("/") else data.get("url") or url
                    if not title:
                        continue

                    claim = f"{title}. {selftext}".strip()
                    if query and query.lower() not in claim.lower():
                        continue

                    created_utc = data.get("created_utc")
                    captured_at = datetime.now(UTC)
                    if isinstance(created_utc, (int, float)):
                        captured_at = datetime.fromtimestamp(created_utc, tz=UTC)

                    observations.append(
                        Observation(
                            source=f"reddit:r/{subreddit}",
                            claim=claim,
                            url=post_url,
                            captured_at=captured_at,
                            reliability_hint=0.52,
                        )
                    )

        return ConnectorResult(
            connector=self.name,
            observations=observations,
            metadata={
                "subreddit_count": len(self.subreddits),
                "failed_subreddits": failed_subreddits,
                "sort": self.sort,
            },
        )
