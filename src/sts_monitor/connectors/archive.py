"""Internet Archive / Wayback Machine connector.

Retrieves cached versions of pages that may have been deleted, modified, or
censored. Useful for:
  - Recovering deleted social media posts
  - Tracking website changes over time
  - Accessing content blocked in certain regions
  - Verifying historical claims

Uses the free Wayback Machine API (no auth required).
"""
from __future__ import annotations

from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation


class _TextExtractor(HTMLParser):
    """Extract visible text from HTML, stripping scripts/styles."""

    def __init__(self) -> None:
        super().__init__()
        self._text: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            cleaned = data.strip()
            if cleaned:
                self._text.append(cleaned)

    def get_text(self) -> str:
        return " ".join(self._text)


class InternetArchiveConnector:
    """Search and retrieve content from the Internet Archive / Wayback Machine."""

    name = "archive"

    AVAILABILITY_URL = "https://archive.org/wayback/available"
    CDX_API_URL = "https://web.archive.org/cdx/search/cdx"
    WAYBACK_URL = "https://web.archive.org/web/{timestamp}/{url}"

    def __init__(
        self,
        *,
        urls_to_check: list[str] | None = None,
        search_query: str | None = None,
        max_snapshots: int = 10,
        timeout_s: float = 15.0,
        proxy_url: str | None = None,
    ) -> None:
        self.urls_to_check = urls_to_check or []
        self.search_query = search_query
        self.max_snapshots = max_snapshots
        self.timeout_s = timeout_s
        self.proxy_url = proxy_url

    def _build_client(self) -> httpx.Client:
        kwargs: dict[str, Any] = {
            "timeout": self.timeout_s,
            "follow_redirects": True,
            "headers": {
                "User-Agent": "STS-Situation-Monitor/0.6 (OSINT Research Tool)",
            },
        }
        if self.proxy_url:
            kwargs["proxy"] = self.proxy_url
        return httpx.Client(**kwargs)

    def _check_availability(
        self,
        client: httpx.Client,
        url: str,
    ) -> dict[str, Any] | None:
        """Check if a URL has been archived."""
        try:
            resp = client.get(
                self.AVAILABILITY_URL,
                params={"url": url, "timestamp": "20240101"},
            )
            resp.raise_for_status()
            data = resp.json()
            snapshot = data.get("archived_snapshots", {}).get("closest")
            return snapshot
        except Exception:
            return None

    def _search_cdx(
        self,
        client: httpx.Client,
        url: str,
        limit: int = 10,
    ) -> list[dict[str, str]]:
        """Search the CDX API for all snapshots of a URL."""
        try:
            resp = client.get(
                self.CDX_API_URL,
                params={
                    "url": url,
                    "output": "json",
                    "limit": str(limit),
                    "fl": "timestamp,original,mimetype,statuscode,length",
                    "filter": "statuscode:200",
                    "collapse": "timestamp:8",  # One per day
                },
            )
            resp.raise_for_status()
            data = resp.json()

            if not data or len(data) < 2:
                return []

            headers = data[0]
            results: list[dict[str, str]] = []
            for row in data[1:]:
                results.append(dict(zip(headers, row)))
            return results
        except Exception:
            return []

    def _fetch_archived_page(
        self,
        client: httpx.Client,
        url: str,
        timestamp: str,
    ) -> str:
        """Fetch the actual archived page content."""
        wayback_url = self.WAYBACK_URL.format(timestamp=timestamp, url=url)
        try:
            resp = client.get(wayback_url)
            resp.raise_for_status()
            return resp.text
        except Exception:
            return ""

    def _extract_text(self, html: str) -> str:
        """Extract visible text from HTML."""
        parser = _TextExtractor()
        try:
            parser.feed(html)
        except Exception:
            pass
        return parser.get_text()[:2000]

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        metadata: dict[str, Any] = {
            "urls_checked": len(self.urls_to_check),
        }

        effective_query = query or self.search_query

        try:
            with self._build_client() as client:
                for url in self.urls_to_check[:20]:
                    snapshots = self._search_cdx(
                        client, url, limit=self.max_snapshots,
                    )

                    for snap in snapshots:
                        timestamp = snap.get("timestamp", "")
                        original_url = snap.get("original", url)

                        # Parse timestamp (YYYYMMDDHHmmss)
                        try:
                            captured = datetime.strptime(
                                timestamp[:14], "%Y%m%d%H%M%S",
                            ).replace(tzinfo=UTC)
                        except (ValueError, TypeError):
                            captured = datetime.now(UTC)

                        # Fetch page content for text extraction
                        html = self._fetch_archived_page(
                            client, original_url, timestamp,
                        )
                        text = self._extract_text(html) if html else ""

                        if not text:
                            text = f"Archived snapshot of {original_url} from {timestamp}"

                        # Query filter
                        if effective_query and effective_query.lower() not in text.lower():
                            continue

                        wayback_url = self.WAYBACK_URL.format(
                            timestamp=timestamp, url=original_url,
                        )

                        observations.append(Observation(
                            source=f"archive:{_extract_domain(original_url)}",
                            claim=text[:1000],
                            url=wayback_url,
                            captured_at=captured,
                            reliability_hint=0.65,  # Archived content, verified to exist
                        ))

        except Exception as exc:
            metadata["error"] = str(exc)

        metadata["snapshots_found"] = len(observations)
        return ConnectorResult(
            connector=self.name,
            observations=observations,
            metadata=metadata,
        )


def _extract_domain(url: str) -> str:
    """Extract domain from URL."""
    url = url.lower().strip()
    if "://" in url:
        url = url.split("://", 1)[1]
    url = url.split("/", 1)[0].split("?", 1)[0]
    if url.startswith("www."):
        url = url[4:]
    return url
