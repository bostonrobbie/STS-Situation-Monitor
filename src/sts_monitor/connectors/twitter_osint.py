"""
Twitter/X OSINT connector via Nitter RSS feeds (public, no auth required).
Layer: social_media
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation

log = logging.getLogger(__name__)

# Nitter instances to try (in order)
_NITTER_INSTANCES = [
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.1d4.us",
    "https://nitter.net",
]

# Search queries for geo-tagged political/conflict content
_SEARCH_QUERIES = [
    "/search/rss?q=breaking+news+conflict&f=tweets",
    "/search/rss?q=explosion+attack+military&f=tweets",
    "/search/rss?q=war+ceasefire+sanctions&f=tweets",
    "/search/rss?q=protest+riot+uprising&f=tweets",
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
    "europe": (54.5, 15.3), "middle east": (29.3, 42.5),
    "africa": (8.8, 26.8), "asia": (34.0, 100.0),
    "nato": (50.8, 10.0), "un": (40.7, -74.0),
    "kyiv": (50.4, 30.5), "moscow": (55.8, 37.6),
    "beijing": (39.9, 116.4), "washington": (38.9, -77.0),
    "london": (51.5, -0.1), "paris": (48.9, 2.4),
    "berlin": (52.5, 13.4), "brussels": (50.8, 4.4),
    "tel aviv": (32.1, 34.8), "jerusalem": (31.8, 35.2),
    "tehran": (35.7, 51.4), "kabul": (34.5, 69.2),
    "baghdad": (33.3, 44.4), "damascus": (33.5, 36.3),
    "cairo": (30.1, 31.2), "riyadh": (24.7, 46.7),
    "beirut": (33.9, 35.5), "ankara": (39.9, 32.9),
}

# Keyword significance boosts
_KEYWORD_WEIGHTS: dict[str, float] = {
    "explosion": 2.0, "attack": 1.5, "killed": 2.0, "dead": 1.5,
    "war": 2.0, "military": 1.5, "airstrike": 2.0, "bomb": 2.0,
    "missile": 2.0, "troops": 1.5, "ceasefire": 2.0, "sanctions": 1.5,
    "nuclear": 2.5, "chemical": 2.0, "biological": 2.0,
    "coup": 2.5, "protest": 1.0, "riot": 1.5, "uprising": 2.0,
    "arrest": 1.0, "crisis": 1.5, "emergency": 1.5,
    "breaking": 0.5, "urgent": 0.5,
}


def _extract_country(text: str) -> tuple[float, float] | None:
    """Extract country/city coordinates from text."""
    text_lower = text.lower()
    for name in sorted(_COUNTRY_COORDS.keys(), key=len, reverse=True):
        if name in text_lower:
            return _COUNTRY_COORDS[name]
    return None


def _score_text(text: str, base: float = 5.0) -> float:
    """Score tweet significance based on keywords."""
    text_lower = text.lower()
    sig = base
    for kw, boost in _KEYWORD_WEIGHTS.items():
        if kw in text_lower:
            sig = min(10.0, sig + boost * 0.3)
    return sig


def _parse_rss_items(xml_text: str) -> list[dict]:
    """Parse RSS XML and extract items."""
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []

    items = []
    channel = root.find("channel")
    if channel is None:
        # Try atom
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("atom:entry", ns):
            title = (entry.findtext("atom:title", namespaces=ns) or "").strip()
            link_el = entry.find("atom:link", ns)
            link = link_el.get("href", "") if link_el is not None else ""
            content = (entry.findtext("atom:content", namespaces=ns) or "").strip()
            content = re.sub(r"<[^>]+>", " ", content).strip()
            published = entry.findtext("atom:published", namespaces=ns) or ""
            try:
                from email.utils import parsedate_to_datetime
                pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            except Exception:
                pub_dt = datetime.now(UTC)
            items.append({"title": title, "link": link, "text": content, "published": pub_dt})
        return items

    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "").strip()
        pub_date = item.findtext("pubDate") or ""
        try:
            from email.utils import parsedate_to_datetime
            pub_dt = parsedate_to_datetime(pub_date)
        except Exception:
            pub_dt = datetime.now(UTC)
        items.append({
            "title": title, "link": link,
            "text": f"{title} {desc}".strip(), "published": pub_dt,
        })
    return items


class TwitterOSINTConnector:
    """Scrapes Twitter content via Nitter RSS (no auth required)."""

    name = "twitter_osint"

    def __init__(self, timeout_s: float = 12.0, max_per_query: int = 20) -> None:
        self.timeout_s = timeout_s
        self.max_per_query = max_per_query

    def _fetch_nitter(self, client: httpx.Client, instance: str, path: str) -> str | None:
        """Try fetching from a Nitter instance."""
        try:
            url = f"{instance}{path}"
            resp = client.get(url)
            if resp.status_code == 200 and len(resp.text) > 100:
                return resp.text
        except Exception:
            pass
        return None

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        geo_events: list[dict] = []
        metadata: dict = {"layer": "social_media"}

        working_instance = None
        successful_queries = 0
        seen_titles: set[str] = set()

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/rss+xml, application/xml, text/xml",
        }

        with httpx.Client(timeout=self.timeout_s, follow_redirects=True, headers=headers) as client:
            # Find working Nitter instance
            for instance in _NITTER_INSTANCES:
                try:
                    r = client.get(f"{instance}/search/rss?q=test&f=tweets")
                    if r.status_code == 200:
                        working_instance = instance
                        break
                except Exception:
                    continue

            if not working_instance:
                metadata["nitter_error"] = "All Nitter instances unavailable"
                log.warning("Twitter OSINT: all Nitter instances failed — using LLM fallback")
                return self._llm_fallback(metadata)

            metadata["nitter_instance"] = working_instance

            # Fetch each search query
            for search_path in _SEARCH_QUERIES:
                xml_text = self._fetch_nitter(client, working_instance, search_path)
                if not xml_text:
                    continue

                items = _parse_rss_items(xml_text)
                successful_queries += 1

                for item in items[:self.max_per_query]:
                    text = item.get("text", "") or item.get("title", "")
                    if not text or len(text) < 15:
                        continue

                    # Dedup
                    title_key = text[:80].lower().strip()
                    if title_key in seen_titles:
                        continue
                    seen_titles.add(title_key)

                    link = item.get("link", "")
                    pub_dt = item.get("published", datetime.now(UTC))

                    # Geolocate
                    coords = _extract_country(text)
                    if not coords:
                        continue  # Skip ungeolocatable tweets

                    lat, lon = coords
                    sig = _score_text(text, base=5.0)
                    source_id = f"twitter_osint_{hash(text[:80]) & 0xFFFFFF}"

                    title_short = text[:200].replace("\n", " ")

                    observations.append(Observation(
                        source=f"twitter_osint:{working_instance}",
                        claim=f"[SOCIAL] {title_short}",
                        url=link,
                        captured_at=pub_dt,
                        reliability_hint=0.45,  # Social media = lower reliability
                    ))
                    geo_events.append({
                        "layer": "social_media",
                        "source_id": source_id,
                        "title": title_short[:500],
                        "latitude": lat + (hash(text[:40]) % 100) * 0.01,
                        "longitude": lon + (hash(text[40:80]) % 100) * 0.01,
                        "magnitude": round(sig, 1),
                        "event_time": pub_dt.isoformat() if hasattr(pub_dt, 'isoformat') else datetime.now(UTC).isoformat(),
                        "properties": {
                            "layer": "social_media",
                            "source": "nitter",
                            "nitter_instance": working_instance,
                            "url": link,
                            "significance": sig,
                        },
                    })

        metadata["successful_queries"] = successful_queries
        metadata["geo_events_count"] = len(geo_events)
        # If nitter returned no usable geo-events, fall back to LLM synthesis
        if not geo_events:
            log.info("Twitter OSINT: nitter returned 0 geo-events — using LLM fallback")
            return self._llm_fallback(metadata)
        return ConnectorResult(
            connector=self.name,
            observations=observations,
            metadata={**metadata, "geo_events": geo_events},
        )

    def _llm_fallback(self, metadata: dict) -> ConnectorResult:
        """Use Ollama to synthesize OSINT intelligence signals when Nitter is unavailable."""
        import json as _json
        observations: list[Observation] = []
        geo_events: list[dict] = []
        now = datetime.now(UTC)
        metadata["source"] = "llm_synthesis"

        prompt = (
            "Based on current global events (conflicts in Ukraine, Middle East, tensions with Iran, Taiwan strait), "
            "generate 10 realistic intelligence signal summaries in the format of short social media posts from the "
            "perspective of OSINT analysts. Each should mention a specific location. Return ONLY a JSON array:\n"
            '[{"text": "...", "location": "city, country", "lat": N, "lon": N, "significance": 0-10}]'
        )

        try:
            with httpx.Client(timeout=45.0) as client:
                resp = client.post(
                    "http://localhost:11434/api/generate",
                    json={"model": "qwen2.5:14b", "prompt": prompt, "stream": False},
                    timeout=45.0,
                )
                resp.raise_for_status()
                raw = resp.json().get("response", "")
        except Exception as exc:
            log.warning(f"Twitter OSINT LLM fallback failed: {exc}")
            metadata["llm_error"] = str(exc)
            return ConnectorResult(connector=self.name, observations=[], metadata={**metadata, "geo_events": []})

        # Parse JSON from LLM response
        signals = None
        try:
            signals = _json.loads(raw.strip())
        except Exception:
            import re as _re
            m = _re.search(r"\[[\s\S]*\]", raw)
            if m:
                try:
                    signals = _json.loads(m.group(0))
                except Exception:
                    pass

        if not isinstance(signals, list):
            log.warning("Twitter OSINT LLM fallback: could not parse JSON response")
            metadata["llm_parse_error"] = True
            return ConnectorResult(connector=self.name, observations=[], metadata={**metadata, "geo_events": []})

        for item in signals[:15]:
            text = item.get("text", "")
            lat = item.get("lat")
            lon = item.get("lon")
            if not text or lat is None or lon is None:
                continue
            try:
                lat, lon = float(lat), float(lon)
            except (TypeError, ValueError):
                continue

            sig = float(item.get("significance", 5.0))
            location = item.get("location", "Unknown")
            source_id = f"llm_osint_{hash(text[:80]) & 0xFFFFFF}"
            title_short = text[:200].replace("\n", " ")

            observations.append(Observation(
                source="twitter_osint:llm_synthesis",
                claim=f"[LLM OSINT] {title_short}",
                url="",
                captured_at=now,
                reliability_hint=0.35,
            ))
            geo_events.append({
                "layer": "social_intel",
                "source_id": source_id,
                "title": title_short[:500],
                "latitude": lat,
                "longitude": lon,
                "magnitude": round(sig, 1),
                "event_time": now.isoformat(),
                "properties": {
                    "layer": "social_intel",
                    "source": "llm_synthesis",
                    "location": location,
                    "significance": sig,
                },
            })

        metadata["llm_signals"] = len(geo_events)
        return ConnectorResult(
            connector=self.name,
            observations=observations,
            metadata={**metadata, "geo_events": geo_events},
        )


def fetch_twitter_with_ntscraper(query: str, lat: float, lon: float, limit: int = 20) -> list:
    """
    Try to fetch real tweets via ntscraper (uses Nitter instances).
    Falls back to LLM synthesis if Nitter is unavailable.
    """
    import random
    from datetime import datetime, timezone, timedelta
    import hashlib

    events = []
    try:
        from ntscraper import Nitter
        scraper = Nitter(log_level=0, skip_instance_check=False)
        tweets = scraper.get_tweets(query, mode='term', number=limit)
        tweet_list = tweets.get('tweets', [])

        for tweet in tweet_list:
            text = tweet.get('text', '')
            if not text or len(text) < 10:
                continue
            username = tweet.get('user', {}).get('name', 'unknown')
            likes = tweet.get('likes', 0)
            sig = min(3.0 + likes / 200, 7.0)

            # Add jitter around the query location
            jlat = lat + random.uniform(-0.08, 0.08)
            jlon = lon + random.uniform(-0.1, 0.1)
            uid = hashlib.md5(text[:50].encode()).hexdigest()[:16]

            events.append({
                'source_id': f'twitter-{uid}',
                'layer': 'social_intel',
                'title': text[:200],
                'lat': jlat,
                'lon': jlon,
                'magnitude': sig,
                'event_time': datetime.now(timezone.utc),
                'properties': {
                    'source': f'Twitter/@{username}',
                    'topic': query,
                    'likes': likes,
                    'layer_type': 'social_intel',
                },
            })
    except Exception:
        pass  # ntscraper failed — caller should use LLM fallback

    return events
