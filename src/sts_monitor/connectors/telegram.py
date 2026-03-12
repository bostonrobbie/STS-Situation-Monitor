"""Telegram public channel connector — OSINT from public Telegram channels.

Scrapes public Telegram channels via the t.me web preview (no API key needed).
Many OSINT communities, conflict reporters, and breaking news accounts post
on Telegram first before anywhere else.

No authentication required — only accesses PUBLIC channels.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation

# Curated OSINT Telegram channels (public, high-value)
OSINT_CHANNELS: dict[str, list[dict[str, str]]] = {
    "conflict": [
        {"handle": "inaborispas", "label": "Ukraine War Reports"},
        {"handle": "ryaborsgram", "label": "Rybar (conflict mapping)"},
        {"handle": "militaryoperationz", "label": "Military Operations Z"},
        {"handle": "mod_russia_en", "label": "Russia MoD (English)"},
        {"handle": "Ukraine_NOW_eng", "label": "Ukraine NOW English"},
        {"handle": "SputnikInt", "label": "Sputnik International"},
        {"handle": "liveuamap", "label": "LiveUAMap"},
    ],
    "osint": [
        {"handle": "osaborispas", "label": "OSINT Aggregator"},
        {"handle": "intelopers", "label": "Intelopers OSINT"},
        {"handle": "belaborispas", "label": "Bellingcat"},
        {"handle": "GeoConfirmed", "label": "GeoConfirmed"},
    ],
    "geopolitics": [
        {"handle": "middleeastobserver", "label": "Middle East Observer"},
        {"handle": "Geopolitics_Worldwide", "label": "Geopolitics Worldwide"},
        {"handle": "southchinamorningpost", "label": "SCMP"},
    ],
    "breaking": [
        {"handle": "BreakingI", "label": "Breaking International"},
        {"handle": "alertsworld", "label": "World Alerts"},
    ],
}


def get_channels_for_categories(
    categories: list[str] | None = None,
) -> list[dict[str, str]]:
    """Get channel list for given categories. None = all."""
    if categories is None:
        categories = list(OSINT_CHANNELS.keys())
    channels: list[dict[str, str]] = []
    seen: set[str] = set()
    for cat in categories:
        for ch in OSINT_CHANNELS.get(cat, []):
            if ch["handle"] not in seen:
                seen.add(ch["handle"])
                channels.append(ch)
    return channels


class _TelegramPostParser(HTMLParser):
    """Parse Telegram's t.me web preview for post content."""

    def __init__(self) -> None:
        super().__init__()
        self.posts: list[dict[str, str]] = []
        self._in_message = False
        self._in_text = False
        self._in_date = False
        self._current: dict[str, str] = {}
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        classes = attr_dict.get("class", "") or ""

        if "tgme_widget_message_wrap" in classes:
            self._in_message = True
            self._current = {}
            self._text_parts = []

        if self._in_message:
            if "tgme_widget_message_text" in classes:
                self._in_text = True
            if tag == "time" and attr_dict.get("datetime"):
                self._current["datetime"] = attr_dict["datetime"]
            if tag == "a" and "tgme_widget_message_date" in classes:
                self._current["url"] = attr_dict.get("href", "")

    def handle_endtag(self, tag: str) -> None:
        if self._in_text and tag == "div":
            self._in_text = False
            self._current["text"] = " ".join(self._text_parts).strip()

        if self._in_message and tag == "div" and self._current.get("text"):
            self.posts.append(self._current.copy())
            self._in_message = False

    def handle_data(self, data: str) -> None:
        if self._in_text:
            self._text_parts.append(data.strip())


class TelegramConnector:
    """Scrape public Telegram channels via t.me web preview."""

    name = "telegram"

    def __init__(
        self,
        *,
        channels: list[dict[str, str]] | None = None,
        categories: list[str] | None = None,
        per_channel_limit: int = 20,
        timeout_s: float = 12.0,
        proxy_url: str | None = None,
    ) -> None:
        if channels:
            self.channels = channels
        else:
            self.channels = get_channels_for_categories(categories)
        self.per_channel_limit = per_channel_limit
        self.timeout_s = timeout_s
        self.proxy_url = proxy_url

    def _build_client(self) -> httpx.Client:
        kwargs: dict[str, Any] = {
            "timeout": self.timeout_s,
            "follow_redirects": True,
            "headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
        }
        if self.proxy_url:
            kwargs["proxy"] = self.proxy_url
        return httpx.Client(**kwargs)

    def _scrape_channel(
        self,
        client: httpx.Client,
        handle: str,
        label: str,
        query: str | None,
    ) -> list[Observation]:
        """Scrape a single Telegram channel's public posts."""
        url = f"https://t.me/s/{handle}"
        observations: list[Observation] = []

        try:
            resp = client.get(url)
            resp.raise_for_status()
            html = resp.text
        except Exception:
            return []

        parser = _TelegramPostParser()
        try:
            parser.feed(html)
        except Exception:
            return []

        for post in parser.posts[:self.per_channel_limit]:
            text = post.get("text", "").strip()
            if not text or len(text) < 15:
                continue

            # Query filter
            if query and query.lower() not in text.lower():
                continue

            post_url = post.get("url", f"https://t.me/{handle}")
            dt_str = post.get("datetime", "")
            try:
                captured = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                captured = datetime.now(UTC)

            observations.append(Observation(
                source=f"telegram:{handle}",
                claim=text[:1000],
                url=post_url,
                captured_at=captured,
                reliability_hint=0.45,  # Social media baseline
            ))

        return observations

    def collect(self, query: str | None = None) -> ConnectorResult:
        all_observations: list[Observation] = []
        errors: list[str] = []

        try:
            with self._build_client() as client:
                for channel in self.channels:
                    handle = channel.get("handle", "")
                    label = channel.get("label", handle)
                    try:
                        obs = self._scrape_channel(client, handle, label, query)
                        all_observations.extend(obs)
                    except Exception as exc:
                        errors.append(f"{handle}: {exc}")
        except Exception as exc:
            errors.append(f"Client error: {exc}")

        return ConnectorResult(
            connector=self.name,
            observations=all_observations,
            metadata={
                "channels_scraped": len(self.channels),
                "total_posts": len(all_observations),
                "errors": errors[:5],
            },
        )
