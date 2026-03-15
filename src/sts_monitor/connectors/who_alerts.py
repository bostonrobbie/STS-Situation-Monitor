"""
WHO Disease Outbreak News connector.
Fetches disease alert data from WHO API and RSS feed.
Layer: health_alert
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation

# Disease severity keyword → significance score
_DISEASE_SEVERITY: dict[str, float] = {
    "ebola": 9.0, "marburg": 9.0, "plague": 8.5,
    "cholera": 7.0, "yellow fever": 7.5, "dengue": 6.5,
    "mpox": 7.0, "monkeypox": 7.0, "anthrax": 8.0,
    "measles": 6.0, "polio": 7.0, "typhoid": 6.0,
    "influenza": 5.0, "flu": 5.0, "h5n1": 8.0, "h1n1": 7.0,
    "covid": 6.0, "coronavirus": 6.0, "sars": 8.0, "mers": 7.5,
    "meningitis": 6.5, "lassa": 8.0, "nipah": 8.5,
    "rift valley": 7.0, "hantavirus": 7.5,
}

# Country name → (lat, lon)
_COUNTRY_COORDS: dict[str, tuple[float, float]] = {
    "ukraine": (49.0, 32.0), "russia": (61.5, 105.0),
    "china": (35.9, 104.2), "india": (20.6, 78.9),
    "nigeria": (9.1, 8.7), "ethiopia": (9.1, 40.5),
    "kenya": (-0.0, 37.9), "tanzania": (-6.4, 34.9),
    "congo": (-4.0, 21.8), "drc": (-4.0, 21.8),
    "sudan": (12.9, 30.2), "south sudan": (6.9, 31.3),
    "somalia": (5.1, 46.2), "mali": (17.6, -2.0),
    "guinea": (11.0, -10.9), "sierra leone": (8.5, -11.8),
    "liberia": (6.4, -9.4), "ghana": (7.9, -1.0),
    "cameroon": (3.9, 11.5), "madagascar": (-18.8, 46.9),
    "pakistan": (30.4, 69.3), "bangladesh": (23.7, 90.4),
    "indonesia": (-0.8, 113.9), "myanmar": (17.1, 96.9),
    "brazil": (-14.2, -51.9), "colombia": (4.6, -74.1),
    "peru": (-9.2, -75.0), "haiti": (18.9, -72.3),
    "mexico": (23.6, -102.6), "united states": (37.1, -95.7),
    "usa": (37.1, -95.7), "uk": (55.4, -3.4),
    "france": (46.2, 2.2), "germany": (51.2, 10.5),
    "italy": (41.9, 12.6), "spain": (40.4, -3.7),
    "saudi arabia": (23.9, 45.1), "iran": (32.4, 53.7),
    "egypt": (26.8, 30.8), "south africa": (-30.6, 22.9),
    "senegal": (14.5, -14.5), "niger": (17.6, 8.1),
    "chad": (15.5, 18.7), "mozambique": (-18.7, 35.5),
    "zambia": (-13.1, 27.8), "zimbabwe": (-19.0, 29.1),
    "angola": (-11.2, 17.9), "uganda": (1.4, 32.3),
    "rwanda": (-2.0, 29.9), "burundi": (-3.4, 29.9),
    "malawi": (-13.3, 34.3), "zambia": (-13.1, 27.8),
    "philippines": (12.9, 121.8), "vietnam": (14.1, 108.3),
    "cambodia": (12.6, 104.8), "laos": (19.9, 102.5),
    "thailand": (15.9, 100.9), "malaysia": (4.2, 109.5),
    "afghanistan": (33.9, 67.7), "iraq": (33.2, 43.7),
    "syria": (34.8, 38.9), "lebanon": (33.9, 35.5),
    "jordan": (31.2, 36.5), "turkey": (38.9, 35.2),
    "ukraine": (49.0, 32.0), "poland": (51.9, 19.1),
    "north korea": (40.3, 127.5), "south korea": (36.5, 127.8),
    "japan": (36.2, 138.3), "australia": (-25.3, 133.8),
    "new zealand": (-40.9, 174.9),
}


def _get_disease_significance(text: str) -> float:
    """Return significance based on disease keywords found in text."""
    text_lower = text.lower()
    max_sig = 5.0  # base significance
    for disease, sig in _DISEASE_SEVERITY.items():
        if disease in text_lower:
            max_sig = max(max_sig, sig)
    return max_sig


def _extract_country(text: str) -> tuple[float, float] | None:
    """Extract country coordinates from text."""
    text_lower = text.lower()
    for country in sorted(_COUNTRY_COORDS.keys(), key=len, reverse=True):
        if country in text_lower:
            return _COUNTRY_COORDS[country]
    return None


class WHOAlertsConnector:
    """Fetches WHO Disease Outbreak News."""

    name = "who_alerts"

    WHO_API = "https://www.who.int/api/news/newsarticles?sf_culture=en&$orderby=PublicationDateAndTime+desc&$top=50&$filter=ChanelId+eq+61"
    WHO_RSS = "https://www.who.int/emergencies/disease-outbreak-news/rss"

    def __init__(self, timeout_s: float = 20.0) -> None:
        self.timeout_s = timeout_s

    def _fetch_api(self, client: httpx.Client) -> list[dict]:
        """Try WHO JSON API."""
        try:
            resp = client.get(self.WHO_API)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("value", data) if isinstance(data, dict) else data
            if not isinstance(items, list):
                return []
            results = []
            for item in items:
                title = item.get("Title", "") or item.get("title", "") or ""
                url = item.get("Url", "") or item.get("url", "") or ""
                summary = item.get("Summary", "") or item.get("summary", "") or ""
                date_str = item.get("PublicationDateAndTime", "") or item.get("date", "")
                try:
                    event_time = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
                except Exception:
                    event_time = datetime.now(UTC)
                results.append({
                    "title": str(title)[:400],
                    "url": str(url),
                    "summary": str(summary)[:800],
                    "event_time": event_time,
                })
            return results
        except Exception:
            return []

    def _fetch_rss(self, client: httpx.Client) -> list[dict]:
        """Fallback: parse WHO RSS feed."""
        try:
            resp = client.get(self.WHO_RSS)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            results = []
            channel = root.find("channel")
            if channel is None:
                return []
            for item in channel.findall("item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc = (item.findtext("description") or "").strip()
                pub_date = item.findtext("pubDate") or ""
                try:
                    from email.utils import parsedate_to_datetime
                    event_time = parsedate_to_datetime(pub_date)
                except Exception:
                    event_time = datetime.now(UTC)
                results.append({
                    "title": title[:400],
                    "url": link,
                    "summary": re.sub(r"<[^>]+>", " ", desc)[:800],
                    "event_time": event_time,
                })
            return results
        except Exception:
            return []

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        geo_events: list[dict] = []
        metadata: dict = {"layer": "health_alert"}

        with httpx.Client(
            timeout=self.timeout_s,
            follow_redirects=True,
            headers={"User-Agent": "STS-Situation-Monitor/0.7"},
        ) as client:
            items = self._fetch_api(client)
            if not items:
                items = self._fetch_rss(client)
                metadata["source"] = "rss_fallback"
            else:
                metadata["source"] = "who_api"

        metadata["item_count"] = len(items)

        for item in items:
            title = item["title"]
            if not title:
                continue
            summary = item["summary"]
            url = item["url"]
            event_time = item["event_time"]

            combined = f"{title} {summary}"
            sig = _get_disease_significance(combined)

            # Geolocate
            coords = _extract_country(combined)
            if not coords:
                coords = (20.0, 0.0)  # WHO HQ fallback (Geneva approximate)
                sig = min(sig, 6.0)

            lat, lon = coords
            source_id = f"who_alert_{hash(title) & 0xFFFFFF}"

            observations.append(Observation(
                source="who_alerts",
                claim=f"[HEALTH] {title}: {summary[:300]}",
                url=url,
                captured_at=event_time,
                reliability_hint=0.85,
            ))
            geo_events.append({
                "layer": "health_alert",
                "source_id": source_id,
                "title": title[:500],
                "latitude": lat + (hash(title) % 50) * 0.001,
                "longitude": lon + (hash(title[::-1]) % 50) * 0.001,
                "magnitude": round(sig, 1),
                "event_time": event_time.isoformat(),
                "properties": {
                    "layer": "health_alert",
                    "source": "who",
                    "url": url,
                    "summary": summary[:500],
                    "significance": sig,
                },
            })

        metadata["geo_events_count"] = len(geo_events)
        return ConnectorResult(
            connector=self.name,
            observations=observations,
            metadata={**metadata, "geo_events": geo_events},
        )
