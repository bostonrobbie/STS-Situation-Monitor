"""
GeoNews connector — GDELT GEO 2.0 + Reddit for geo-tagged news layers.
Layers: news, conflict, military, political, conspiracy
"""
from __future__ import annotations
import json, re, time
from datetime import UTC, datetime
from pathlib import Path
import httpx
from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation

# ── Built-in country centroid lookup (name → (lat, lon, significance)) ──────
# significance: rough geopolitical weight 1-10
_COUNTRY_COORDS: dict[str, tuple[float, float, float]] = {
    "ukraine": (49.0, 32.0, 9.0), "russia": (61.5, 105.0, 9.5),
    "israel": (31.5, 35.0, 9.0), "gaza": (31.4, 34.3, 9.5),
    "palestine": (31.9, 35.2, 9.0), "iran": (32.4, 53.7, 8.5),
    "china": (35.9, 104.2, 9.5), "taiwan": (23.7, 121.0, 9.0),
    "north korea": (40.3, 127.5, 8.5), "south korea": (36.5, 127.8, 7.0),
    "united states": (37.1, -95.7, 9.5), "usa": (37.1, -95.7, 9.5),
    "united kingdom": (55.4, -3.4, 8.0), "uk": (55.4, -3.4, 8.0),
    "france": (46.2, 2.2, 7.5), "germany": (51.2, 10.5, 7.5),
    "syria": (34.8, 38.9, 9.0), "iraq": (33.2, 43.7, 8.5),
    "afghanistan": (33.9, 67.7, 8.5), "pakistan": (30.4, 69.3, 8.0),
    "india": (20.6, 78.9, 8.5), "myanmar": (17.1, 96.9, 8.0),
    "sudan": (12.9, 30.2, 8.5), "somalia": (5.1, 46.2, 8.0),
    "ethiopia": (9.1, 40.5, 7.5), "mali": (17.6, -2.0, 7.5),
    "niger": (17.6, 8.1, 7.5), "libya": (26.3, 17.2, 8.0),
    "yemen": (15.6, 48.5, 8.5), "saudi arabia": (23.9, 45.1, 8.0),
    "turkey": (38.9, 35.2, 8.0), "venezuela": (6.4, -66.6, 7.5),
    "brazil": (-14.2, -51.9, 7.5), "mexico": (23.6, -102.6, 7.5),
    "colombia": (4.6, -74.1, 7.0), "haiti": (18.9, -72.3, 8.0),
    "japan": (36.2, 138.3, 7.5), "indonesia": (-0.8, 113.9, 7.0),
    "philippines": (12.9, 121.8, 7.0), "nigeria": (9.1, 8.7, 7.5),
    "egypt": (26.8, 30.8, 7.5), "south africa": (-30.6, 22.9, 7.0),
    "congo": (-4.0, 21.8, 8.0), "drc": (-4.0, 21.8, 8.0),
    "europe": (54.5, 15.3, 7.0), "middle east": (29.3, 42.5, 8.5),
    "africa": (8.8, 26.8, 7.0), "asia": (34.0, 100.0, 7.0),
    "nato": (50.8, 10.0, 8.5), "un": (40.7, -74.0, 8.0),
}

# Subreddits by layer
_REDDIT_SOURCES: dict[str, list[str]] = {
    "news":       ["worldnews", "geopolitics", "UpliftingNews"],
    "conflict":   ["worldnews", "CombatFootage", "CredibleDefense", "anime_titties"],
    "military":   ["CredibleDefense", "MilitaryPorn", "AirForce", "navy", "army"],
    "political":  ["politics", "worldpolitics", "geopolitics", "europe"],
    "conspiracy": ["conspiracy", "conspiracytheories", "UFOs", "AltRight"],
}

# GDELT query by layer
_GDELT_QUERIES: dict[str, str] = {
    "news":      "breaking news world international",
    "conflict":  "war attack military strike battle bombing conflict",
    "military":  "military army navy airstrike troops weapons defense NATO",
    "political": "election government president sanctions diplomacy treaty",
    "conspiracy":"conspiracy secret hidden suppressed cover-up deep-state surveillance",
}

# Significance (magnitude 1-10) per layer
_LAYER_SIGNIFICANCE: dict[str, float] = {
    "news": 6.0, "conflict": 8.5, "military": 8.0,
    "political": 7.0, "conspiracy": 4.0,
}

# Nominatim cache file
_CACHE_PATH = Path(__file__).parent.parent / "_geocode_cache.json"


def _load_cache() -> dict[str, tuple[float, float] | None]:
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_cache(cache: dict) -> None:
    try:
        _CACHE_PATH.write_text(json.dumps(cache))
    except Exception:
        pass


def _extract_country(text: str) -> tuple[float, float, float] | None:
    """Extract a known country/region from text and return (lat, lon, significance)."""
    text_lower = text.lower()
    # Longest-match first
    for name in sorted(_COUNTRY_COORDS.keys(), key=len, reverse=True):
        if name in text_lower:
            return _COUNTRY_COORDS[name]
    return None


def _geocode_nominatim(query: str, cache: dict) -> tuple[float, float] | None:
    """Geocode using Nominatim (OSM). Rate-limited to 1 req/sec. Cached."""
    key = query.lower().strip()
    if key in cache:
        return cache[key]
    try:
        time.sleep(1.1)  # Nominatim rate limit
        r = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": "1"},
            headers={"User-Agent": "STS-Situation-Monitor/0.7"},
            timeout=8,
            follow_redirects=True,
        )
        data = r.json()
        if data:
            lat = float(data[0]["lat"])
            lon = float(data[0]["lon"])
            cache[key] = (lat, lon)
            _save_cache(cache)
            return (lat, lon)
    except Exception:
        pass
    cache[key] = None
    _save_cache(cache)
    return None


class GeoNewsConnector:
    """
    Fetches geo-tagged news events for a given layer using GDELT GEO API.
    Layer choices: news | conflict | military | political | conspiracy
    """
    name = "geo_news"

    GDELT_GEO = "https://api.gdeltproject.org/api/v2/geo/geo"
    REDDIT_BASE = "https://www.reddit.com/r/{sub}/{sort}.json?raw_json=1&limit={limit}"

    def __init__(
        self,
        *,
        layer: str = "news",
        timespan: str = "24h",
        max_geo_events: int = 100,
        include_reddit: bool = True,
        timeout_s: float = 15.0,
    ) -> None:
        self.layer = layer.lower()
        self.timespan = timespan
        self.max_geo_events = max(1, min(300, max_geo_events))
        self.include_reddit = include_reddit
        self.timeout_s = timeout_s

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        geo_events: list[dict] = []
        metadata: dict = {"layer": self.layer, "timespan": self.timespan}
        base_sig = _LAYER_SIGNIFICANCE.get(self.layer, 6.0)

        gdelt_query = query or _GDELT_QUERIES.get(self.layer, "world news")

        # ── GDELT GEO ─────────────────────────────────────────────────────
        try:
            params = {
                "query": gdelt_query,
                "format": "GeoJSON",
                "timespan": self.timespan,
            }
            with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
                resp = client.get(self.GDELT_GEO, params=params)
                resp.raise_for_status()
                data = resp.json()

            features = data.get("features") or []
            metadata["gdelt_features"] = len(features)

            for feat in features[:self.max_geo_events]:
                coords = (feat.get("geometry") or {}).get("coordinates", [])
                props = feat.get("properties") or {}
                if len(coords) < 2:
                    continue
                lon, lat = float(coords[0]), float(coords[1])
                name = props.get("name", "Unknown location")
                count = int(props.get("count", 1))
                html = props.get("html", "")

                # Extract article title from html snippet
                title_match = re.search(r'<a[^>]+>([^<]{5,120})</a>', html)
                title = title_match.group(1).strip() if title_match else f"{gdelt_query} event near {name}"

                # Scale significance: more articles = higher significance
                sig = min(10.0, base_sig + min(2.0, count * 0.2))

                observations.append(Observation(
                    source=f"gdelt_geo:{self.layer}",
                    claim=f"[{self.layer.upper()}] {title} ({name}, {count} articles)",
                    url=props.get("url", ""),
                    captured_at=datetime.now(UTC),
                    reliability_hint=0.7,
                ))
                geo_events.append({
                    "layer": self.layer,
                    "source_id": f"gdelt_geo_{self.layer}_{hash(name + title) & 0xFFFFFF}",
                    "title": f"[{count} articles] {title}"[:500],
                    "latitude": lat,
                    "longitude": lon,
                    "magnitude": round(sig, 1),
                    "event_time": datetime.now(UTC).isoformat(),
                    "properties": {
                        "layer": self.layer,
                        "article_count": count,
                        "location_name": name,
                        "significance": sig,
                        "source": "gdelt_geo",
                    },
                })
        except Exception as exc:
            metadata["gdelt_error"] = str(exc)

        # ── Reddit geo-extraction ──────────────────────────────────────────
        if self.include_reddit:
            subreddits = _REDDIT_SOURCES.get(self.layer, ["worldnews"])[:3]
            geocache = _load_cache()
            reddit_geo = 0
            headers = {"User-Agent": "STS-Situation-Monitor/0.7"}

            with httpx.Client(timeout=self.timeout_s, follow_redirects=True, headers=headers) as client:
                for sub in subreddits:
                    try:
                        url = self.REDDIT_BASE.format(sub=sub, sort="hot", limit=25)
                        resp = client.get(url)
                        resp.raise_for_status()
                        posts = (resp.json().get("data") or {}).get("children") or []
                    except Exception:
                        continue

                    for item in posts:
                        d = (item or {}).get("data") or {}
                        title = (d.get("title") or "").strip()
                        if not title or len(title) < 10:
                            continue

                        # Try to extract country/location
                        coords_info = _extract_country(title)
                        if not coords_info:
                            # Try post flair
                            flair = d.get("link_flair_text") or ""
                            coords_info = _extract_country(flair)

                        if not coords_info:
                            continue

                        lat, lon, country_sig = coords_info
                        score = d.get("score", 0) or 0
                        # Significance: country weight + Reddit score boost
                        sig = min(10.0, (country_sig * 0.6 + base_sig * 0.4) + min(1.5, score / 10000))

                        permalink = d.get("permalink") or ""
                        post_url = f"https://www.reddit.com{permalink}" if permalink else ""
                        created = d.get("created_utc")
                        captured_at = datetime.fromtimestamp(created, tz=UTC) if isinstance(created, (int, float)) else datetime.now(UTC)

                        observations.append(Observation(
                            source=f"reddit:{sub}",
                            claim=f"[{self.layer.upper()}] {title}",
                            url=post_url,
                            captured_at=captured_at,
                            reliability_hint=0.45 if self.layer == "conspiracy" else 0.55,
                        ))
                        geo_events.append({
                            "layer": self.layer,
                            "source_id": f"reddit_{sub}_{d.get('id','?')}",
                            "title": title[:500],
                            "latitude": lat + (hash(title) % 100) * 0.01,  # slight jitter
                            "longitude": lon + (hash(title[::-1]) % 100) * 0.01,
                            "magnitude": round(sig, 1),
                            "event_time": captured_at.isoformat(),
                            "properties": {
                                "layer": self.layer,
                                "subreddit": sub,
                                "reddit_score": score,
                                "significance": sig,
                                "source": "reddit",
                                "url": post_url,
                            },
                        })
                        reddit_geo += 1

            metadata["reddit_geo_events"] = reddit_geo

        metadata["total_geo_events"] = len(geo_events)
        return ConnectorResult(
            connector=self.name,
            observations=observations,
            metadata={**metadata, "geo_events": geo_events},
        )
