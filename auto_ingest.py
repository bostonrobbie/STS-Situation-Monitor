"""
Auto-ingest worker — runs continuously and refreshes real data every 15 minutes.
Managed by PM2 as 'sts-auto-ingest'.
"""
import os, sys, time, logging
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("auto_ingest")

import json
from datetime import datetime, timezone
UTC = timezone.utc
import httpx
from dotenv import load_dotenv
load_dotenv()

BASE = "http://127.0.0.1:8080"
KEY = os.getenv("STS_AUTH_API_KEY", "change-me")
HEADERS = {"X-API-Key": KEY}
INTERVAL_SECONDS = 15 * 60  # 15 minutes

# Track cycle count for less-frequent operations
_cycle_count = 0


def cleanup_stale_data():
    """Remove expired/stale geo events to keep DB fresh."""
    cleanup_rules = {
        "aircraft_military": 2,    # 2 hours
        "aircraft_emergency": 1,   # 1 hour
        "simulated": 0,            # delete all
        "weather_alert": 48,       # 48 hours
        "news": 72,                # 3 days
        "social_media": 48,        # 48 hours
        "social_intel": 24,        # 24 hours
        "telegram": 72,            # 3 days
        "local_news": 48,          # 48 hours
    }
    try:
        r = httpx.post(
            f"{BASE}/admin/cleanup",
            json=cleanup_rules,
            headers=HEADERS,
            timeout=30,
        )
        if r.status_code == 200:
            log.info(f"[cleanup] {r.json()}")
        else:
            log.warning(f"[cleanup] HTTP {r.status_code}")
    except Exception as e:
        log.error(f"[cleanup] error: {e}")


def ingest_round():
    """Run one full ingest cycle across all geo connectors."""
    global _cycle_count
    _cycle_count += 1

    # Get primary investigation
    try:
        r = httpx.get(f"{BASE}/investigations", headers=HEADERS, timeout=10)
        invs = r.json() if r.status_code == 200 else []
    except Exception as e:
        log.error(f"Cannot reach API: {e}")
        return

    if not invs:
        log.warning("No investigations found — skipping")
        return

    inv_id = invs[0]["id"]
    topic = invs[0].get("topic", "?")
    log.info(f"Ingesting into [{inv_id}] {topic} (cycle {_cycle_count})")

    # ── Core geo connectors (every 15 min) ────────────────────────────────
    connectors = [
        ("/ingest/usgs",  {"min_magnitude": 2.5, "lookback_hours": 48, "max_events": 200}),
        ("/ingest/nws",   {}),
        ("/ingest/gdelt", {"query": "conflict disaster earthquake flood humanitarian",
                           "max_records": 75, "timespan": "24h"}),
        ("/ingest/fema",  {}),
    ]

    total_geo = 0
    for path, body in connectors:
        try:
            r = httpx.post(f"{BASE}/investigations/{inv_id}{path}",
                           headers=HEADERS, json=body, timeout=45)
            if r.status_code == 200:
                d = r.json()
                geo = d.get("geo_events_count", 0)
                ing = d.get("ingested_count", d.get("stored_count", 0))
                total_geo += geo
                log.info(f"  {path}: ingested={ing} geo={geo}")
            else:
                log.warning(f"  {path}: HTTP {r.status_code}")
        except Exception as e:
            log.error(f"  {path}: {e}")

    # ── Geo news layers (every 15 min) ────────────────────────────────────
    geo_news_topics = [
        ("/ingest/geo-news", {"layer": "conflict",  "timespan": "24h", "max_geo_events": 80}),
        ("/ingest/geo-news", {"layer": "military",  "timespan": "24h", "max_geo_events": 60}),
        ("/ingest/geo-news", {"layer": "news",      "timespan": "12h", "max_geo_events": 80}),
        ("/ingest/geo-news", {"layer": "political", "timespan": "24h", "max_geo_events": 60}),
        ("/ingest/geo-news", {"layer": "conspiracy","timespan": "24h", "max_geo_events": 40, "include_reddit": True}),
    ]
    for path, body in geo_news_topics:
        try:
            r = httpx.post(f"{BASE}/investigations/{inv_id}{path}",
                           headers=HEADERS, json=body, timeout=60)
            if r.status_code == 200:
                d = r.json()
                geo = d.get("geo_events_count", 0)
                log.info(f"  {path} layer={body['layer']}: geo={geo}")
            else:
                log.warning(f"  {path}: HTTP {r.status_code}")
        except Exception as e:
            log.error(f"  {path}: {e}")

    # ── New intelligence connectors (every 15 min) ────────────────────────
    new_connectors = [
        ("/ingest/who-alerts",     {"query": None}),
        ("/ingest/cisa-kev",       {"lookback_days": 7}),
        ("/ingest/twitter-osint",  {}),
        ("/ingest/telegram-osint", {}),
        ("/ingest/maritime",       {}),
    ]
    for path, body in new_connectors:
        try:
            r = httpx.post(f"{BASE}/investigations/{inv_id}{path}",
                           headers=HEADERS, json=body, timeout=60)
            if r.status_code == 200:
                d = r.json()
                geo = d.get("geo_events_count", 0)
                ing = d.get("ingested_count", 0)
                log.info(f"  {path}: ingested={ing} geo={geo}")
            else:
                log.warning(f"  {path}: HTTP {r.status_code} — {r.text[:200]}")
        except Exception as e:
            log.error(f"  {path}: {e}")

    # ── Local news for major US cities (every 30 min = every other cycle) ─
    if _cycle_count % 2 == 0:
        major_cities = [
            ("Boston", "Massachusetts", 42.3601, -71.0589),
            ("New York", "New York", 40.7128, -74.0060),
            ("Los Angeles", "California", 34.0522, -118.2437),
            ("Chicago", "Illinois", 41.8781, -87.6298),
            ("Houston", "Texas", 29.7604, -95.3698),
            ("Miami", "Florida", 25.7617, -80.1918),
            ("Seattle", "Washington", 47.6062, -122.3321),
            ("Atlanta", "Georgia", 33.7490, -84.3880),
        ]
        try:
            from sts_monitor.connectors.local_discovery import fetch_local_news
            from sts_monitor.database import get_session
            from sts_monitor.models import GeoEventORM
            from datetime import timedelta
            for city, state, clat, clon in major_cities:
                try:
                    news = fetch_local_news(city, state, clat, clon)
                    with get_session() as db_session:
                        count = 0
                        for ev in news:
                            if not db_session.query(GeoEventORM).filter_by(source_id=ev["source_id"]).first():
                                db_session.add(GeoEventORM(
                                    layer=ev["layer"], source_id=ev["source_id"],
                                    title=ev["title"], latitude=ev["lat"], longitude=ev["lon"],
                                    magnitude=ev["magnitude"], event_time=ev["event_time"],
                                    properties_json=json.dumps(ev["properties"]),
                                    expires_at=datetime.now(UTC) + timedelta(hours=24),
                                ))
                                count += 1
                        db_session.commit()
                        if count > 0:
                            log.info(f"  local_news {city}: +{count} events")
                except Exception as e:
                    log.error(f"  local_news {city}: ERROR {e}")
        except Exception as e:
            log.error(f"  local_news import error: {e}")

    # ── Expanded RSS (every 30 min = every other cycle) ───────────────────
    if _cycle_count % 2 == 0:
        try:
            from sts_monitor.connectors.rss import get_expanded_feed_urls
            feed_urls = get_expanded_feed_urls()
            r = httpx.post(
                f"{BASE}/investigations/{inv_id}/ingest/rss",
                headers=HEADERS,
                json={"feed_urls": feed_urls[:20], "per_feed_limit": 5},
                timeout=90,
            )
            if r.status_code == 200:
                d = r.json()
                log.info(f"  /ingest/rss: ingested={d.get('ingested_count', 0)}")
            else:
                log.warning(f"  /ingest/rss: HTTP {r.status_code}")
        except Exception as e:
            log.error(f"  /ingest/rss expanded: {e}")

    log.info(f"Round complete — {total_geo} new core geo events")

    # Always clean up stale data at end of every cycle
    cleanup_stale_data()


if __name__ == "__main__":
    log.info("Auto-ingest worker starting (interval: 15 min)")
    # Run immediately on startup
    ingest_round()
    while True:
        log.info(f"Sleeping {INTERVAL_SECONDS // 60} minutes...")
        time.sleep(INTERVAL_SECONDS)
        ingest_round()
