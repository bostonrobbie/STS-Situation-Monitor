"""Search engine connector using DuckDuckGo HTML search.

Provides autonomous URL discovery by searching DuckDuckGo and extracting
result links and snippets. No API key required — uses the HTML search
endpoint and parses results.

Used by the autonomous research agent to find new sources and URLs
to feed into the web scraper connector.
"""
from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote_plus, unquote, urlparse

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation


# ── DuckDuckGo result parser ────────────────────────────────────────────

class _DDGResultParser(HTMLParser):
    """Parse DuckDuckGo HTML search results page."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] = {}
        self._in_result_link = False
        self._in_snippet = False
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        # Result link
        if tag == "a" and "result__a" in classes:
            href = attr_dict.get("href", "")
            self._current = {"url": href, "title": "", "snippet": ""}
            self._in_result_link = True
            self._text_parts = []

        # Snippet
        if tag == "a" and "result__snippet" in classes:
            self._in_snippet = True
            self._text_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_result_link:
            self._current["title"] = " ".join(self._text_parts).strip()
            self._in_result_link = False
            self._text_parts = []
        if tag == "a" and self._in_snippet:
            self._current["snippet"] = " ".join(self._text_parts).strip()
            self._in_snippet = False
            self._text_parts = []
            if self._current.get("url"):
                self.results.append(self._current)
            self._current = {}

    def handle_data(self, data: str) -> None:
        if self._in_result_link or self._in_snippet:
            self._text_parts.append(data.strip())


def _extract_real_url(ddg_url: str) -> str:
    """Extract the actual URL from DuckDuckGo redirect URLs."""
    if "duckduckgo.com" in ddg_url and "uddg=" in ddg_url:
        match = re.search(r"uddg=([^&]+)", ddg_url)
        if match:
            return unquote(match.group(1))
    return ddg_url


# ── Search connector ────────────────────────────────────────────────────

class SearchConnector:
    """Search the web via DuckDuckGo and return results as observations."""

    name = "search"

    SEARCH_URL = "https://html.duckduckgo.com/html/"

    def __init__(
        self,
        *,
        max_results: int = 20,
        timeout_s: float = 15.0,
        user_agent: str = "STS-Situation-Monitor/0.7",
    ) -> None:
        self.max_results = max(1, min(50, max_results))
        self.timeout_s = timeout_s
        self.user_agent = user_agent

    def search(self, query: str) -> list[dict[str, str]]:
        """Execute a search and return raw results."""
        headers = {"User-Agent": self.user_agent}
        data = {"q": query, "kl": "us-en"}

        try:
            with httpx.Client(timeout=self.timeout_s, headers=headers) as client:
                resp = client.post(self.SEARCH_URL, data=data, follow_redirects=True)
                resp.raise_for_status()
                html = resp.text
        except Exception:
            return []

        parser = _DDGResultParser()
        try:
            parser.feed(html)
        except Exception:
            pass

        results: list[dict[str, str]] = []
        for r in parser.results[: self.max_results]:
            real_url = _extract_real_url(r["url"])
            if not real_url.startswith(("http://", "https://")):
                continue
            results.append({
                "url": real_url,
                "title": unescape(r.get("title", "")),
                "snippet": unescape(r.get("snippet", "")),
            })
        return results

    def collect(self, query: str | None = None) -> ConnectorResult:
        if not query:
            return ConnectorResult(
                connector=self.name,
                observations=[],
                metadata={"error": "query required for search connector"},
            )

        results = self.search(query)
        observations: list[Observation] = []

        for result in results:
            claim = f"{result['title']}. {result['snippet']}"
            observations.append(
                Observation(
                    source=f"search:ddg:{urlparse(result['url']).netloc}",
                    claim=claim[:2000],
                    url=result["url"],
                    captured_at=datetime.now(UTC),
                    reliability_hint=0.55,
                )
            )

        return ConnectorResult(
            connector=self.name,
            observations=observations,
            metadata={
                "query": query,
                "result_count": len(results),
            },
        )
