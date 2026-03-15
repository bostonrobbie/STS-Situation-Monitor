"""Twitter/X connector via Nitter RSS proxies.

Nitter instances expose public tweets as RSS feeds, requiring no API keys.
Supports searching by keyword, following specific accounts, and fetching
trending content. Falls back across multiple Nitter instances for resilience.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime
from urllib.parse import quote_plus

import feedparser

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation


# Public Nitter instances — rotated on failure
DEFAULT_NITTER_INSTANCES: list[str] = [
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.woodland.cafe",
    "https://nitter.1d4.us",
]

# OSINT-relevant accounts for default monitoring
OSINT_ACCOUNTS: dict[str, list[str]] = {
    "conflict": [
        "RALee85", "TheStudyofWar", "Nrg8000", "JulianRoepcke",
        "sentdefender", "WarMonitor3", "IntelCrab",
    ],
    "natural_disaster": [
        "ABORITO1", "TheWeatherMan78", "NWStweets",
        "earthquakeBot", "USGSBigQuakes", "NWS",
    ],
    "geopolitics": [
        "AFP", "Reuters", "BBCBreaking", "AP",
        "BNONews", "spectaborindex",
    ],
    "osint": [
        "bellingcat", "christogrozev", "DFRLab",
        "IntelDoge", "OSINTdefender",
    ],
}


def list_osint_categories() -> list[str]:
    """Return available OSINT account categories."""
    return list(OSINT_ACCOUNTS.keys())


def get_accounts_for_categories(categories: list[str] | None = None) -> list[str]:
    """Get Twitter handles for given categories, or all if None."""
    if categories:
        accounts: list[str] = []
        for cat in categories:
            accounts.extend(OSINT_ACCOUNTS.get(cat, []))
        return list(set(accounts))
    all_accounts: list[str] = []
    for accts in OSINT_ACCOUNTS.values():
        all_accounts.extend(accts)
    return list(set(all_accounts))


class NitterConnector:
    """Collect tweets via Nitter RSS feeds."""

    name = "nitter"

    def __init__(
        self,
        *,
        instances: list[str] | None = None,
        accounts: list[str] | None = None,
        per_account_limit: int = 20,
        timeout_s: float = 12.0,
        max_retries: int = 2,
    ) -> None:
        self.instances = list(instances or DEFAULT_NITTER_INSTANCES)
        self.accounts = list(accounts or [])
        self.per_account_limit = max(1, min(100, per_account_limit))
        self.timeout_s = timeout_s
        self.max_retries = max_retries

    def _try_parse(self, path: str) -> tuple[object | None, str | None]:
        """Try parsing an RSS feed across Nitter instances with fallback."""
        for instance in self.instances:
            url = f"{instance.rstrip('/')}/{path}"
            for attempt in range(self.max_retries + 1):
                try:
                    parsed = feedparser.parse(
                        url,
                        request_headers={"User-Agent": "STS-Situation-Monitor/0.7"},
                    )
                    entries = getattr(parsed, "entries", [])
                    if entries:
                        return parsed, None
                    if getattr(parsed, "bozo", False):
                        raise RuntimeError(str(getattr(parsed, "bozo_exception", "parse error")))
                except Exception:
                    if attempt < self.max_retries:
                        time.sleep(0.3 * (attempt + 1))
        return None, f"all instances failed for /{path}"

    def _search_feed_path(self, query: str) -> str:
        return f"search?q={quote_plus(query)}"

    def _account_feed_path(self, handle: str) -> str:
        return f"{handle.lstrip('@')}/rss"

    def _parse_entries_to_observations(
        self,
        parsed: object,
        source_tag: str,
        query: str | None,
    ) -> list[Observation]:
        observations: list[Observation] = []
        entries = getattr(parsed, "entries", [])[: self.per_account_limit]
        for entry in entries:
            title = (entry.get("title") or "").strip()
            summary = (entry.get("summary") or "").strip()
            link = entry.get("link") or ""
            author = entry.get("author") or ""

            text = title or summary
            if not text:
                continue

            if query and query.lower() not in text.lower():
                continue

            # Parse published date
            published = entry.get("published_parsed")
            if published:
                try:
                    captured_at = datetime(*published[:6], tzinfo=UTC)
                except Exception:
                    captured_at = datetime.now(UTC)
            else:
                captured_at = datetime.now(UTC)

            full_text = f"@{author}: {text}" if author else text
            observations.append(
                Observation(
                    source=source_tag,
                    claim=full_text[:2000],
                    url=link,
                    captured_at=captured_at,
                    reliability_hint=0.45,  # Social media — lower baseline
                )
            )
        return observations

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        failed: list[dict[str, str]] = []

        # 1) Search by keyword if query provided
        if query:
            parsed, err = self._try_parse(self._search_feed_path(query))
            if parsed:
                observations.extend(
                    self._parse_entries_to_observations(parsed, f"nitter:search:{query}", query=None)
                )
            elif err:
                failed.append({"type": "search", "query": query, "error": err})

        # 2) Fetch from monitored accounts
        for handle in self.accounts:
            parsed, err = self._try_parse(self._account_feed_path(handle))
            if parsed:
                observations.extend(
                    self._parse_entries_to_observations(parsed, f"nitter:@{handle}", query=query)
                )
            elif err:
                failed.append({"type": "account", "handle": handle, "error": err})

        return ConnectorResult(
            connector=self.name,
            observations=observations,
            metadata={
                "instances_available": len(self.instances),
                "accounts_monitored": len(self.accounts),
                "search_query": query,
                "failed": failed,
            },
        )
