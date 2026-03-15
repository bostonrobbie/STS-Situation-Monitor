"""
Telegram OSINT connector — scrapes public Telegram channel previews via t.me/s/{channel}.
Layer: telegram
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation

log = logging.getLogger(__name__)

# Public Telegram channels to monitor
_CHANNELS = [
    "WarMonitor3",
    "intelslava",
    "ASBMilitary",
    "Tendar",
    "wartranslated",
    "Intel_Alert",
    "DefMon3",
    "bayraktar_1love",
    "UkraineWarReports",
    "AFpost",
    "nexta_tv",
    "Flash_news_UKR",
]

# Country coords for geolocation
_COUNTRY_COORDS: dict[str, tuple[float, float]] = {
    "ukraine": (49.0, 32.0), "russia": (61.5, 105.0),
    "israel": (31.5, 35.0), "gaza": (31.4, 34.3),
    "palestine": (31.9, 35.2), "iran": (32.4, 53.7),
    "china": (35.9, 104.2), "taiwan": (23.7, 121.0),
    "north korea": (40.3, 127.5), "south korea": (36.5, 127.8),
    "united states": (37.1, -95.7), "usa": (37.1, -95.7),
    "united kingdom": (55.4, -3.4), "uk": (55.4, -3.4),
    "france": (46.2, 2.2), "germany": (51.2, 10.5),
    "syria": (34.8, 38.9), "iraq": (33.2, 43.7),
    "afghanistan": (33.9, 67.7), "pakistan": (30.4, 69.3),
    "india": (20.6, 78.9), "myanmar": (17.1, 96.9),
    "sudan": (12.9, 30.2), "somalia": (5.1, 46.2),
    "ethiopia": (9.1, 40.5), "mali": (17.6, -2.0),
    "niger": (17.6, 8.1), "libya": (26.3, 17.2),
    "yemen": (15.6, 48.5), "saudi arabia": (23.9, 45.1),
    "turkey": (38.9, 35.2), "venezuela": (6.4, -66.6),
    "brazil": (-14.2, -51.9), "mexico": (23.6, -102.6),
    "colombia": (4.6, -74.1), "haiti": (18.9, -72.3),
    "japan": (36.2, 138.3), "indonesia": (-0.8, 113.9),
    "philippines": (12.9, 121.8), "nigeria": (9.1, 8.7),
    "egypt": (26.8, 30.8), "south africa": (-30.6, 22.9),
    "congo": (-4.0, 21.8), "drc": (-4.0, 21.8),
    "kyiv": (50.4, 30.5), "kharkiv": (50.0, 36.2),
    "kherson": (46.6, 32.6), "zaporizhzhia": (47.8, 35.2),
    "mariupol": (47.1, 37.5), "donetsk": (48.0, 37.8),
    "luhansk": (48.6, 39.3), "bakhmut": (48.6, 38.0),
    "avdiivka": (48.1, 37.7), "kramatorsk": (48.7, 37.6),
    "moscow": (55.8, 37.6), "st. petersburg": (59.9, 30.3),
    "belgorod": (50.6, 36.6), "kursk": (51.7, 36.2),
    "europe": (54.5, 15.3), "middle east": (29.3, 42.5),
    "africa": (8.8, 26.8), "nato": (50.8, 10.0),
    "beijing": (39.9, 116.4), "washington": (38.9, -77.0),
    "tel aviv": (32.1, 34.8), "jerusalem": (31.8, 35.2),
    "tehran": (35.7, 51.4), "kabul": (34.5, 69.2),
    "baghdad": (33.3, 44.4), "beirut": (33.9, 35.5),
}

# Keyword significance boosts
_KEYWORD_BOOSTS: dict[str, float] = {
    "explosion": 1.5, "attack": 1.0, "killed": 1.5, "dead": 1.0,
    "strike": 1.0, "airstrike": 1.5, "missile": 1.5, "bomb": 1.5,
    "troops": 0.8, "advance": 0.8, "retreat": 0.8, "ceasefire": 1.5,
    "war": 1.0, "battle": 1.0, "siege": 1.0, "offensive": 1.0,
    "nuclear": 2.0, "chemical": 2.0, "tank": 0.8, "drone": 0.8,
    "breaking": 0.5, "urgent": 0.5, "confirmed": 0.5,
    "captured": 1.0, "liberated": 1.0, "destroyed": 1.0,
}


def _extract_country(text: str) -> tuple[float, float] | None:
    """Extract coordinates from text."""
    text_lower = text.lower()
    for name in sorted(_COUNTRY_COORDS.keys(), key=len, reverse=True):
        if name in text_lower:
            return _COUNTRY_COORDS[name]
    return None


def _score_text(text: str, base: float = 6.0) -> float:
    """Score significance based on keywords."""
    text_lower = text.lower()
    sig = base
    for kw, boost in _KEYWORD_BOOSTS.items():
        if kw in text_lower:
            sig = min(10.0, sig + boost * 0.4)
    return sig


def _parse_telegram_html(html: str, channel: str) -> list[dict]:
    """Parse t.me/s/{channel} HTML to extract posts."""
    posts = []

    # Extract post bubbles
    # Telegram preview HTML has divs with class "tgme_widget_message_text"
    text_blocks = re.findall(
        r'<div[^>]*class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
        html, re.DOTALL
    )

    # Also try simpler extraction
    if not text_blocks:
        text_blocks = re.findall(
            r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</(?:div|p)>',
            html, re.DOTALL
        )

    # Extract timestamps
    timestamps = re.findall(
        r'datetime="([^"]+)"',
        html
    )

    for i, block in enumerate(text_blocks[:20]):
        # Strip HTML tags
        text = re.sub(r"<[^>]+>", " ", block).strip()
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 10:
            continue

        # Get timestamp if available
        if i < len(timestamps):
            try:
                pub_dt = datetime.fromisoformat(timestamps[i].replace("Z", "+00:00"))
            except Exception:
                pub_dt = datetime.now(UTC)
        else:
            pub_dt = datetime.now(UTC)

        posts.append({
            "text": text[:800],
            "published": pub_dt,
            "channel": channel,
        })

    return posts


class TelegramOSINTConnector:
    """Scrapes public Telegram channel previews via t.me/s/{channel}."""

    name = "telegram_osint"

    def __init__(
        self,
        channels: list[str] | None = None,
        timeout_s: float = 10.0,
        max_per_channel: int = 10,
    ) -> None:
        self.channels = channels or _CHANNELS
        self.timeout_s = timeout_s
        self.max_per_channel = max_per_channel

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        geo_events: list[dict] = []
        metadata: dict = {"layer": "telegram"}

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

        channels_fetched = 0
        seen_texts: set[str] = set()

        with httpx.Client(timeout=self.timeout_s, follow_redirects=True, headers=headers) as client:
            for channel in self.channels:
                try:
                    url = f"https://t.me/s/{channel}"
                    resp = client.get(url)
                    if resp.status_code != 200:
                        continue

                    posts = _parse_telegram_html(resp.text, channel)
                    channels_fetched += 1

                    for post in posts[:self.max_per_channel]:
                        text = post["text"]
                        pub_dt = post["published"]

                        # Dedup
                        text_key = text[:60].lower().strip()
                        if text_key in seen_texts:
                            continue
                        seen_texts.add(text_key)

                        # Geolocate
                        coords = _extract_country(text)
                        if not coords:
                            # Use channel-specific defaults
                            if "ukraine" in channel.lower() or channel in ("UkraineWarReports", "Flash_news_UKR", "bayraktar_1love", "wartranslated"):
                                coords = (49.0, 32.0)
                            elif channel in ("ASBMilitary", "DefMon3"):
                                coords = (29.3, 42.5)  # Middle East
                            else:
                                coords = (49.0, 32.0)  # Default Ukraine for war channels

                        lat, lon = coords
                        sig = _score_text(text)
                        source_id = f"telegram_{channel}_{hash(text[:60]) & 0xFFFFFF}"
                        title = f"[{channel}] {text[:200]}"

                        observations.append(Observation(
                            source=f"telegram:{channel}",
                            claim=f"[TELEGRAM] {text[:400]}",
                            url=f"https://t.me/{channel}",
                            captured_at=pub_dt,
                            reliability_hint=0.5,
                        ))
                        geo_events.append({
                            "layer": "telegram",
                            "source_id": source_id,
                            "title": title[:500],
                            "latitude": lat + (hash(text[:40]) % 100) * 0.01,
                            "longitude": lon + (hash(text[40:80]) % 100) * 0.01,
                            "magnitude": round(sig, 1),
                            "event_time": pub_dt.isoformat() if hasattr(pub_dt, "isoformat") else datetime.now(UTC).isoformat(),
                            "properties": {
                                "layer": "telegram",
                                "source": "telegram",
                                "channel": channel,
                                "url": f"https://t.me/{channel}",
                                "significance": sig,
                            },
                        })

                except Exception as exc:
                    log.debug(f"Telegram channel {channel} failed: {exc}")
                    continue

        metadata["channels_fetched"] = channels_fetched
        if channels_fetched == 0:
            log.warning("Telegram OSINT: no channels accessible")
            metadata["note"] = "All Telegram channels unavailable"

        metadata["geo_events_count"] = len(geo_events)
        return ConnectorResult(
            connector=self.name,
            observations=observations,
            metadata={**metadata, "geo_events": geo_events},
        )
