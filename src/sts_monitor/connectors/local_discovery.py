"""
local_discovery.py - Universal local intelligence connector
Ingests local news and camera data for any city/location
"""
import asyncio, hashlib, json, re, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import httpx

UTC = timezone.utc

# City → subreddit lookup
_CITY_SUBREDDITS = {
    "boston": "boston", "cambridge": "boston", "worcester": "massachusetts",
    "springfield": "massachusetts", "massachusetts": "massachusetts",
    "new york": "nyc", "brooklyn": "brooklyn", "manhattan": "nyc",
    "los angeles": "losangeles", "chicago": "chicago", "miami": "miami",
    "houston": "houston", "phoenix": "phoenix", "philadelphia": "philadelphia",
    "san antonio": "sanantonio", "san diego": "sandiego", "dallas": "dallas",
    "san jose": "sanjose", "austin": "austin", "seattle": "seattle",
    "denver": "denver", "nashville": "nashville", "portland": "portland",
    "las vegas": "lasvegas", "atlanta": "atlanta", "minneapolis": "minneapolis",
    "new orleans": "neworleans", "pittsburgh": "pittsburgh", "cleveland": "cleveland",
    "baltimore": "baltimore", "washington": "washingtondc", "dc": "washingtondc",
    "richmond": "rva", "charlotte": "charlotte", "raleigh": "raleigh",
    "tampa": "tampa", "orlando": "orlando", "jacksonville": "jacksonville",
    "detroit": "detroit", "milwaukee": "milwaukee", "indianapolis": "indianapolis",
    "columbus": "columbus", "cincinnati": "cincinnati", "louisville": "louisville",
    "memphis": "memphis", "kansas city": "kansascity", "tucson": "tucson",
    "oklahoma city": "oklahomacity", "salt lake city": "saltlakecity",
    "albuquerque": "albuquerque", "sacramento": "sacramento", "fresno": "fresno",
}

# State bounding boxes: (lat_min, lon_min, lat_max, lon_max)
_STATE_BBOXES: dict[str, tuple] = {
    "massachusetts": (41.2, -73.5, 42.9, -69.9),
    "new york": (40.5, -79.8, 45.0, -71.9),
    "california": (32.5, -124.5, 42.0, -114.1),
    "texas": (25.8, -106.6, 36.5, -93.5),
    "florida": (24.4, -87.6, 31.0, -80.0),
    "illinois": (36.9, -91.5, 42.5, -87.0),
    "washington": (45.5, -124.8, 49.0, -116.9),
    "colorado": (37.0, -109.1, 41.0, -102.0),
    "georgia": (30.4, -85.6, 35.0, -80.8),
    "pennsylvania": (39.7, -80.5, 42.3, -74.7),
    "ohio": (38.4, -84.8, 41.9, -80.5),
    "michigan": (41.7, -90.4, 48.3, -82.4),
    "arizona": (31.3, -114.8, 37.0, -109.0),
    "north carolina": (33.8, -84.3, 36.6, -75.5),
    "virginia": (36.5, -83.7, 39.5, -75.2),
}

# Major cities per state with lat/lon for title-based geo-placement
_STATE_CITIES: dict[str, dict[str, tuple]] = {
    "massachusetts": {
        "boston": (42.3601, -71.0589), "worcester": (42.2626, -71.8023),
        "springfield": (42.1015, -72.5898), "lowell": (42.6334, -71.3162),
        "cambridge": (42.3736, -71.1097), "brockton": (42.0834, -71.0184),
        "new bedford": (41.6362, -70.9342), "lynn": (42.4668, -70.9495),
        "quincy": (42.2529, -71.0023), "fall river": (41.7015, -71.1550),
        "somerville": (42.3876, -71.0995), "waltham": (42.3765, -71.2356),
        "framingham": (42.2793, -71.4162), "haverhill": (42.7762, -71.0773),
        "gloucester": (42.6159, -70.6619), "lawrence": (42.7070, -71.1631),
        "medford": (42.4184, -71.1062), "newton": (42.3370, -71.2092),
        "salem": (42.5195, -70.8967), "plymouth": (41.9584, -70.6673),
        "holyoke": (42.2042, -72.6162), "attleboro": (41.9445, -71.2856),
        "malden": (42.4251, -71.0662), "brookline": (42.3318, -71.1212),
        "lexington": (42.4473, -71.2245), "concord": (42.4604, -71.3489),
        "cape cod": (41.6688, -70.2962), "martha's vineyard": (41.3810, -70.6465),
        "nantucket": (41.2835, -70.0995), "south shore": (42.1398, -70.9228),
        "north shore": (42.5584, -70.8806),
    },
    "new york": {
        "new york city": (40.7128, -74.0060), "buffalo": (42.8865, -78.8784),
        "rochester": (43.1566, -77.6088), "yonkers": (40.9312, -73.8988),
        "albany": (42.6526, -73.7562), "syracuse": (43.0481, -76.1474),
        "brooklyn": (40.6782, -73.9442), "bronx": (40.8448, -73.8648),
        "queens": (40.7282, -73.7949), "manhattan": (40.7831, -73.9712),
        "staten island": (40.5795, -74.1502), "long island": (40.7891, -73.1350),
    },
    "california": {
        "los angeles": (34.0522, -118.2437), "san francisco": (37.7749, -122.4194),
        "san diego": (32.7157, -117.1611), "san jose": (37.3382, -121.8863),
        "fresno": (36.7378, -119.7871), "sacramento": (38.5816, -121.4944),
        "oakland": (37.8044, -122.2712), "bakersfield": (35.3733, -119.0187),
        "anaheim": (33.8366, -117.9143), "santa ana": (33.7455, -117.8677),
        "riverside": (33.9806, -117.3755), "stockton": (37.9577, -121.2908),
    },
    "florida": {
        "miami": (25.7617, -80.1918), "orlando": (28.5383, -81.3792),
        "tampa": (27.9506, -82.4572), "jacksonville": (30.3322, -81.6557),
        "st. petersburg": (27.7706, -82.6400), "fort lauderdale": (26.1224, -80.1373),
        "tallahassee": (30.4383, -84.2807), "cape canaveral": (28.3920, -80.6077),
        "key west": (24.5551, -81.7800), "pensacola": (30.4213, -87.2169),
    },
    "texas": {
        "houston": (29.7604, -95.3698), "dallas": (32.7767, -96.7970),
        "san antonio": (29.4241, -98.4936), "austin": (30.2672, -97.7431),
        "fort worth": (32.7555, -97.3308), "el paso": (31.7619, -106.4850),
        "arlington": (32.7357, -97.1081), "corpus christi": (27.8006, -97.3964),
    },
}

# Helper: build YouTube embed entry
def _yt_cam(title: str, vid: str, lat: float, lon: float, source: str = "YouTube Live") -> dict:
    return {
        "title": title,
        "embed_url": f"https://www.youtube.com/embed/{vid}?rel=0&autoplay=0",
        "thumbnail": f"https://img.youtube.com/vi/{vid}/mqdefault.jpg",
        "lat": lat, "lon": lon,
        "source": source,
    }

# Curated live webcams for US cities — ALL YouTube embeds (always iframe-embeddable)
_CITY_CAMERAS = {
    "boston": [
        _yt_cam("Boston — WCVB Channel 5 Live", "NKAfvFNgLAk", 42.3601, -71.0589, "WCVB Boston"),
        _yt_cam("Boston — WBZ CBS News Live", "hzKU7n19tnQ", 42.3554, -71.0640, "WBZ CBS Boston"),
        _yt_cam("New England — NBC10 Boston Live", "s5YyM9OCXRM", 42.3600, -71.0560, "NBC10 Boston"),
    ],
    "new york": [
        _yt_cam("NYC — Times Square Live Cam", "xbB2b7y_B4A", 40.7580, -73.9855, "EarthCam"),
        _yt_cam("NYC — ABC7 Eyewitness News Live", "dN-6iHEJFuA", 40.7128, -74.0060, "ABC7 NY"),
        _yt_cam("NYC — PIX11 News Live", "HuCe0BQXCPQ", 40.7061, -73.9969, "PIX11"),
    ],
    "los angeles": [
        _yt_cam("LA — KABC7 ABC News Live", "uj7YYDI_dKA", 34.0522, -118.2437, "KABC7"),
        _yt_cam("LA — KTLA 5 News Live", "VG5d9VVEbRk", 34.1341, -118.3215, "KTLA5"),
        _yt_cam("LA — NBC4 SoCal Live", "mzX3BmLULDE", 34.0195, -118.4912, "NBC4 LA"),
    ],
    "chicago": [
        _yt_cam("Chicago — WGN9 News Live", "g8U9OwDivYQ", 41.8827, -87.6233, "WGN9"),
        _yt_cam("Chicago — ABC7 Eyewitness Live", "B8UOO0Mk5ZY", 41.8819, -87.6278, "ABC7 Chicago"),
    ],
    "miami": [
        _yt_cam("Miami — WSVN 7 News Live", "9M2P2P1vB8M", 25.7907, -80.1300, "WSVN7"),
        _yt_cam("Miami — NBC6 South Florida Live", "QMX6kkBHhzw", 25.7617, -80.1918, "NBC6 Miami"),
    ],
    "washington": [
        _yt_cam("Washington DC — Capitol Hill Live Cam", "buQtiRMBgqs", 38.8899, -77.0091, "CapitolCam"),
        _yt_cam("DC — WUSA9 CBS News Live", "86YLFOog4GM", 38.9072, -77.0369, "WUSA9 DC"),
    ],
    "seattle": [
        _yt_cam("Seattle — KING5 NBC News Live", "IfVTMhpizFw", 47.6205, -122.3493, "KING5"),
        _yt_cam("Seattle — KOMO4 News Live", "qFPFGMXk3gI", 47.6062, -122.3321, "KOMO4"),
    ],
    "san francisco": [
        _yt_cam("San Francisco — ABC7 Bay Area Live", "u8NbFYA9pnU", 37.8199, -122.4783, "ABC7 Bay Area"),
        _yt_cam("SF — KTVU Fox 2 Live", "Hk0lAMaYSek", 37.7749, -122.4194, "KTVU Fox2"),
    ],
    "las vegas": [
        _yt_cam("Las Vegas — KSNV NBC3 Live", "zkf55e8NLZY", 36.1147, -115.1728, "KSNV NBC3"),
        _yt_cam("Las Vegas Strip — Live Cam", "yzuEhEMBpKA", 36.1147, -115.1728, "Live Vegas Cam"),
    ],
    "orlando": [
        _yt_cam("Orlando — WFTV Channel 9 Live", "V0M29p0Pu_4", 28.4255, -81.4680, "WFTV9"),
        _yt_cam("Central FL — ClickOrlando Live", "XaIbLSpzEJk", 28.5383, -81.3792, "News6 Orlando"),
    ],
    "houston": [
        _yt_cam("Houston — KHOU 11 CBS Live", "iLKtHEd6JJQ", 29.7604, -95.3698, "KHOU11"),
        _yt_cam("Houston — ABC13 Eyewitness Live", "FHtvDA0W34I", 29.7633, -95.3633, "ABC13 Houston"),
    ],
    "denver": [
        _yt_cam("Denver — KDVR Fox31 Live", "KjEpFhSBHdQ", 39.7392, -104.9903, "KDVR Fox31"),
        _yt_cam("Denver — 9News NBC Live", "pXH5Cqbq9OE", 39.7294, -104.9878, "9News Denver"),
    ],
    "atlanta": [
        _yt_cam("Atlanta — WSB-TV Channel 2 Live", "h_bLlLzQ9rI", 33.7490, -84.3880, "WSB-TV Atlanta"),
        _yt_cam("Atlanta — NBC 11Alive Live", "zdA3BO_PKBQ", 33.7537, -84.3863, "11Alive Atlanta"),
    ],
    "phoenix": [
        _yt_cam("Phoenix — AZFamily 3TV Live", "BQ4JFhSTiWA", 33.4484, -112.0740, "AZFamily 3TV"),
        _yt_cam("Phoenix — ABC15 Arizona Live", "XtKbhLl5B6I", 33.4255, -112.0040, "ABC15 Arizona"),
    ],
    "dallas": [
        _yt_cam("Dallas — WFAA ABC Live", "fOKKSdIVCEE", 32.7767, -96.7970, "WFAA Dallas"),
        _yt_cam("DFW — NBC5 Live", "Tn6tmiA5pFI", 32.8998, -97.0403, "NBC5 Dallas"),
    ],
}

# State emergency/news RSS feeds
_STATE_RSS = {
    "massachusetts": [
        "https://news.google.com/rss/search?q=Massachusetts+breaking+news&hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=Boston+news+today&hl=en-US&gl=US&ceid=US:en",
    ],
    "new york": [
        "https://news.google.com/rss/search?q=New+York+City+news&hl=en-US&gl=US&ceid=US:en",
    ],
    "california": [
        "https://news.google.com/rss/search?q=California+breaking+news&hl=en-US&gl=US&ceid=US:en",
    ],
    "texas": [
        "https://news.google.com/rss/search?q=Texas+breaking+news&hl=en-US&gl=US&ceid=US:en",
    ],
    "florida": [
        "https://news.google.com/rss/search?q=Florida+breaking+news&hl=en-US&gl=US&ceid=US:en",
    ],
}

_geocode_cache: dict = {}

def _geocode_city(city: str) -> tuple:
    """Geocode city name to lat/lon using Nominatim."""
    if city in _geocode_cache:
        return _geocode_cache[city]
    try:
        r = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": city, "format": "json", "limit": 1},
            headers={"User-Agent": "STS-Intel/1.0"},
            timeout=8,
        )
        d = r.json()
        if d:
            result = (float(d[0]["lat"]), float(d[0]["lon"]))
            _geocode_cache[city] = result
            return result
    except Exception:
        pass
    return None

def _parse_google_news_rss(xml: str, default_lat: float, default_lon: float, state_cities: dict | None = None) -> list:
    """Parse Google News RSS XML and return list of event dicts."""
    import random
    events = []
    items = re.findall(r'<item>(.*?)</item>', xml, re.DOTALL)
    for item in items[:20]:
        title_m = re.search(r'<title>(.*?)</title>', item, re.DOTALL)
        link_m = re.search(r'<link>(.*?)</link>', item, re.DOTALL)
        date_m = re.search(r'<pubDate>(.*?)</pubDate>', item, re.DOTALL)
        source_m = re.search(r'<source[^>]*>(.*?)</source>', item, re.DOTALL)

        if not title_m:
            continue

        title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip()
        title = title.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')
        link = link_m.group(1).strip() if link_m else ""
        source = re.sub(r'<[^>]+>', '', source_m.group(1)).strip() if source_m else "Google News"

        # Parse date
        event_time = datetime.now(UTC)
        if date_m:
            try:
                from email.utils import parsedate_to_datetime
                event_time = parsedate_to_datetime(date_m.group(1).strip())
                if not event_time.tzinfo:
                    event_time = event_time.replace(tzinfo=UTC)
            except Exception:
                pass

        # Skip if older than 48h
        age = datetime.now(UTC) - event_time
        if age.total_seconds() > 48 * 3600:
            continue

        # Try to find a specific city mentioned in the title for better geo-placement
        lat, lon = default_lat, default_lon
        if state_cities:
            title_lower = title.lower()
            placed = False
            for city_name, (clat, clon) in state_cities.items():
                if city_name in title_lower:
                    lat = clat + random.uniform(-0.015, 0.015)
                    lon = clon + random.uniform(-0.015, 0.015)
                    placed = True
                    break
            if not placed:
                # No specific city — spread around the state/region center
                lat = default_lat + random.uniform(-0.25, 0.25)
                lon = default_lon + random.uniform(-0.3, 0.3)
        else:
            jitter = 0.02
            lat = default_lat + random.uniform(-jitter, jitter)
            lon = default_lon + random.uniform(-jitter, jitter)

        # Score significance based on keywords
        sig = 4.0
        high_kw = ['emergency', 'shooting', 'fire', 'explosion', 'crash', 'storm', 'flooding', 'murder', 'attack', 'arrest']
        for kw in high_kw:
            if kw in title.lower():
                sig = min(sig + 1.5, 9.0)
                break

        uid = hashlib.md5(f"{title}{link}".encode()).hexdigest()[:16]
        events.append({
            "source_id": f"gnews-{uid}",
            "layer": "local_news",
            "title": title[:200],
            "lat": lat,
            "lon": lon,
            "magnitude": sig,
            "event_time": event_time,
            "properties": {"url": link, "source": source, "layer_type": "local_news"},
        })
    return events


def fetch_local_news(city: str, state: str = "", lat: float = 0, lon: float = 0) -> list:
    """Fetch local news for a city using Google News RSS."""
    if not lat or not lon:
        coords = _geocode_city(f"{city}, {state}" if state else city)
        if coords:
            lat, lon = coords
        else:
            return []

    query = f"{city} {state} news".strip()
    url = f"https://news.google.com/rss/search?q={query.replace(' ', '+')}&hl=en-US&gl=US&ceid=US:en"

    try:
        r = httpx.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            state_cities_lookup = _STATE_CITIES.get(state.lower()) or _STATE_CITIES.get(city.lower())
            return _parse_google_news_rss(r.text, lat, lon, state_cities_lookup)
    except Exception:
        pass
    return []


def fetch_local_reddit(city: str, lat: float, lon: float, limit: int = 15) -> list:
    """Fetch local Reddit posts for a city."""
    import random
    city_lower = city.lower()
    subreddit = None
    for key, sub in _CITY_SUBREDDITS.items():
        if key in city_lower:
            subreddit = sub
            break

    if not subreddit:
        return []

    try:
        url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit={limit}"
        r = httpx.get(url, timeout=10, headers={"User-Agent": "STS-Intel/1.0"})
        if r.status_code != 200:
            return []

        posts = r.json()["data"]["children"]
        events = []
        for post in posts:
            d = post["data"]
            if d.get("is_video") or d.get("stickied"):
                continue
            title = d.get("title", "")
            if not title:
                continue
            score = d.get("score", 0)
            sig = min(3.0 + (score / 500), 7.0)
            lat_j = lat + random.uniform(-0.015, 0.015)
            lon_j = lon + random.uniform(-0.015, 0.015)
            uid = hashlib.md5(f"reddit-{d.get('id','')}".encode()).hexdigest()[:16]
            events.append({
                "source_id": f"reddit-local-{uid}",
                "layer": "local_news",
                "title": title[:200],
                "lat": lat_j,
                "lon": lon_j,
                "magnitude": sig,
                "event_time": datetime.fromtimestamp(d.get("created_utc", time.time()), UTC),
                "properties": {
                    "url": f"https://reddit.com{d.get('permalink','')}",
                    "source": f"r/{subreddit}",
                    "score": score,
                    "layer_type": "local_news",
                },
            })
        return events
    except Exception:
        return []


def fetch_state_news(state: str, lat: float, lon: float) -> list:
    """Fetch news for an entire state from multiple city-level queries for better coverage."""
    state_lower = state.lower()
    state_cities = _STATE_CITIES.get(state_lower, {})
    events: list = []
    seen_ids: set = set()

    def _add_unique(new_events: list) -> None:
        for evt in new_events:
            if evt['source_id'] not in seen_ids:
                seen_ids.add(evt['source_id'])
                events.append(evt)

    # 1. State-wide Google News
    url = f"https://news.google.com/rss/search?q={state.replace(' ','+')}&hl=en-US&gl=US&ceid=US:en"
    try:
        r = httpx.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            _add_unique(_parse_google_news_rss(r.text, lat, lon, state_cities))
    except Exception:
        pass

    # 2. Breaking news for state
    url2 = f"https://news.google.com/rss/search?q={state.replace(' ','+')}+breaking+news&hl=en-US&gl=US&ceid=US:en"
    try:
        r2 = httpx.get(url2, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r2.status_code == 200:
            _add_unique(_parse_google_news_rss(r2.text, lat, lon, state_cities))
    except Exception:
        pass

    # 3. Top 6 city-specific queries
    major = list(state_cities.items())[:6]
    for city_name, (clat, clon) in major:
        city_url = f"https://news.google.com/rss/search?q={city_name.replace(' ','+')}&hl=en-US&gl=US&ceid=US:en"
        try:
            r = httpx.get(city_url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                _add_unique(_parse_google_news_rss(r.text, clat, clon, state_cities))
        except Exception:
            pass

    return events


def fetch_cameras_for_location(city: str, lat: float, lon: float) -> list:
    """Return cameras for a given city/location. Curated list + Overpass surveillance nodes."""
    cameras = []

    # 1. Check curated city cameras
    city_lower = city.lower()
    for key, cams in _CITY_CAMERAS.items():
        if key in city_lower or city_lower in key:
            cameras.extend(cams)
            break

    # 2. Overpass API - surveillance camera nodes near location
    if lat and lon:
        bbox = f"{lat-0.3},{lon-0.4},{lat+0.3},{lon+0.4}"
        overpass_q = f'[out:json];node["man_made"="surveillance"]({bbox});out 20;'
        try:
            r = httpx.get(
                "https://overpass-api.de/api/interpreter",
                params={"data": overpass_q},
                timeout=10,
            )
            if r.status_code == 200:
                d = r.json()
                for el in d.get("elements", [])[:15]:
                    tags = el.get("tags", {})
                    name = tags.get("name", tags.get("description", f"Surveillance Camera {el['id']}"))
                    cameras.append({
                        "title": name[:80],
                        "embed_url": "",
                        "lat": el["lat"],
                        "lon": el["lon"],
                        "thumbnail": "",
                        "source": "OpenStreetMap",
                        "note": "Camera location from OSM - may not have live stream",
                    })
        except Exception:
            pass

    # Only return cameras that have a playable embed_url (exclude raw OSM stubs)
    return [c for c in cameras if c.get("embed_url")]
