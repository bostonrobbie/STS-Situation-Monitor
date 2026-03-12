"""Web scraper connector with crawl capability.

Fetches web pages, extracts article text, and optionally follows links
to crawl related pages. Uses httpx for requests and basic HTML parsing
to extract readable content without heavy dependencies like Selenium.

Designed for OSINT research: follows links within scope, respects
depth limits and domain restrictions, and extracts clean text for
downstream LLM analysis.
"""
from __future__ import annotations

import re
import time
from collections import deque
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation


# ── HTML text extractor ─────────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    """Minimal HTML-to-text extractor, stripping scripts/styles."""

    _skip_tags = frozenset({"script", "style", "noscript", "svg", "path", "head"})

    def __init__(self) -> None:
        super().__init__()
        self._pieces: list[str] = []
        self._skip_depth = 0
        self.title: str = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._skip_tags:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in self._skip_tags and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in ("p", "br", "div", "h1", "h2", "h3", "h4", "li", "tr"):
            self._pieces.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data.strip()
        if self._skip_depth == 0:
            self._pieces.append(data)

    def get_text(self) -> str:
        raw = " ".join(self._pieces)
        # Collapse whitespace
        raw = re.sub(r"\s+", " ", raw).strip()
        return raw


class _LinkExtractor(HTMLParser):
    """Extract href links from HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self.links.append(value)


def extract_text(html: str) -> tuple[str, str]:
    """Extract (title, body_text) from HTML."""
    extractor = _TextExtractor()
    try:
        extractor.feed(html)
    except Exception:
        pass
    return extractor.title.strip(), extractor.get_text()


def extract_links(html: str, base_url: str) -> list[str]:
    """Extract absolute URLs from HTML."""
    parser = _LinkExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    urls: list[str] = []
    for href in parser.links:
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href)
        if absolute.startswith(("http://", "https://")):
            urls.append(absolute)
    return urls


# ── Scope controls ──────────────────────────────────────────────────────

def _same_domain(url1: str, url2: str) -> bool:
    """Check if two URLs share the same registered domain."""
    d1 = urlparse(url1).netloc.lower()
    d2 = urlparse(url2).netloc.lower()
    return d1 == d2


def _is_crawlable(url: str) -> bool:
    """Filter out non-content URLs."""
    parsed = urlparse(url)
    path = parsed.path.lower()
    skip_extensions = {
        ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
        ".css", ".js", ".woff", ".woff2", ".ttf", ".eot",
        ".pdf", ".zip", ".tar", ".gz", ".mp3", ".mp4", ".avi",
    }
    return not any(path.endswith(ext) for ext in skip_extensions)


# ── Scraper/Crawler ─────────────────────────────────────────────────────

class WebScraperConnector:
    """Scrape web pages with optional crawling."""

    name = "web_scraper"

    def __init__(
        self,
        *,
        seed_urls: list[str] | None = None,
        max_depth: int = 2,
        max_pages: int = 50,
        same_domain_only: bool = True,
        allowed_domains: list[str] | None = None,
        timeout_s: float = 15.0,
        delay_between_requests_s: float = 1.0,
        min_text_length: int = 100,
        user_agent: str = "STS-Situation-Monitor/0.7",
    ) -> None:
        self.seed_urls = list(seed_urls or [])
        self.max_depth = max(0, min(5, max_depth))  # Cap at 5 to prevent runaway
        self.max_pages = max(1, min(200, max_pages))  # Cap at 200
        self.same_domain_only = same_domain_only
        self.allowed_domains = set(d.lower() for d in (allowed_domains or []))
        self.timeout_s = timeout_s
        self.delay_s = max(0.5, delay_between_requests_s)  # Minimum 0.5s politeness
        self.min_text_length = min_text_length
        self.user_agent = user_agent

    def _is_in_scope(self, url: str, seed_url: str) -> bool:
        """Check if a URL is within crawl scope."""
        if self.allowed_domains:
            domain = urlparse(url).netloc.lower()
            return any(domain.endswith(d) for d in self.allowed_domains)
        if self.same_domain_only:
            return _same_domain(url, seed_url)
        return True

    def _fetch_page(self, client: httpx.Client, url: str) -> tuple[str | None, int]:
        """Fetch a page, return (html, status_code)."""
        try:
            resp = client.get(url, follow_redirects=True)
            content_type = resp.headers.get("content-type", "")
            if "text/html" not in content_type and "text/xml" not in content_type:
                return None, resp.status_code
            resp.raise_for_status()
            return resp.text, resp.status_code
        except Exception:
            return None, 0

    def _crawl(self, client: httpx.Client, seed_url: str, query: str | None) -> list[dict[str, Any]]:
        """BFS crawl from a seed URL."""
        visited: set[str] = set()
        results: list[dict[str, Any]] = []
        # (url, depth)
        queue: deque[tuple[str, int]] = deque([(seed_url, 0)])

        while queue and len(results) < self.max_pages:
            url, depth = queue.popleft()

            # Normalize URL for dedup (strip fragment)
            clean_url = url.split("#")[0].rstrip("/")
            if clean_url in visited:
                continue
            visited.add(clean_url)

            if not _is_crawlable(url):
                continue

            html, status = self._fetch_page(client, url)
            if html is None:
                continue

            title, text = extract_text(html)

            # Skip pages with too little text content
            if len(text) < self.min_text_length:
                continue

            # Optional query filtering
            if query and query.lower() not in text.lower() and query.lower() not in title.lower():
                continue

            results.append({
                "url": url,
                "title": title or url,
                "text": text[:5000],  # Cap text for memory
                "depth": depth,
            })

            # Follow links if within depth limit
            if depth < self.max_depth:
                links = extract_links(html, url)
                for link in links:
                    link_clean = link.split("#")[0].rstrip("/")
                    if link_clean not in visited and self._is_in_scope(link, seed_url):
                        queue.append((link, depth + 1))

            # Politeness delay
            if queue:
                time.sleep(self.delay_s)

        return results

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        crawl_stats: dict[str, Any] = {
            "pages_fetched": 0,
            "seeds": len(self.seed_urls),
            "failed_seeds": [],
        }

        headers = {"User-Agent": self.user_agent}
        with httpx.Client(timeout=self.timeout_s, headers=headers) as client:
            for seed_url in self.seed_urls:
                try:
                    pages = self._crawl(client, seed_url, query)
                    crawl_stats["pages_fetched"] += len(pages)

                    for page in pages:
                        claim = f"{page['title']}. {page['text'][:1000]}"
                        observations.append(
                            Observation(
                                source=f"web:{urlparse(page['url']).netloc}",
                                claim=claim[:2000],
                                url=page["url"],
                                captured_at=datetime.now(UTC),
                                reliability_hint=0.50,
                            )
                        )
                except Exception as exc:
                    crawl_stats["failed_seeds"].append({
                        "url": seed_url,
                        "error": str(exc),
                    })

        return ConnectorResult(
            connector=self.name,
            observations=observations,
            metadata=crawl_stats,
        )
