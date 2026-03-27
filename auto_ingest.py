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

# New feature imports (add after existing imports)
try:
    from sts_monitor.correlation import run_correlation
    _CORRELATION_AVAILABLE = True
except Exception:
    _CORRELATION_AVAILABLE = False

try:
    from sts_monitor.telegram_alerts import send_situation_alert, send_watch_rule_alert
    _TELEGRAM_AVAILABLE = True
except Exception:
    _TELEGRAM_AVAILABLE = False

try:
    from sts_monitor.predictive import score_event as _score_event
    _PREDICTIVE_AVAILABLE = True
except Exception:
    _PREDICTIVE_AVAILABLE = False

BASE = "http://127.0.0.1:8080"
KEY = os.getenv("STS_AUTH_API_KEY", "change-me")
HEADERS = {"X-API-Key": KEY}
INTERVAL_SECONDS = 15 * 60  # 15 minutes

# Track cycle count for less-frequent operations
_cycle_count = 0


def evaluate_watch_rules(session, new_events: list) -> None:
    """Check newly ingested geo events against stored geo watch rules. Fire alerts if matched."""
    if not new_events:
        return
    try:
        from sqlalchemy import select
        from sts_monitor.models import GeoWatchRuleORM
        import json, math
        from datetime import datetime, timezone, timedelta

        rules = session.scalars(
            select(GeoWatchRuleORM).where(GeoWatchRuleORM.is_active == True)
        ).all()

        if not rules:
            return

        def haversine_km(lat1, lon1, lat2, lon2):
            R = 6371.0
            dlat = math.radians(lat2 - lat1)
            dlon = math.radians(lon2 - lon1)
            a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
            return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

        now = datetime.now(timezone.utc)
        cooldown = timedelta(minutes=30)  # Don't re-alert same rule within 30 min

        for rule in rules:
            # Skip if recently triggered
            if rule.last_triggered_at and (now - rule.last_triggered_at) < cooldown:
                continue

            allowed_layers = json.loads(rule.layers_json or '[]')

            for evt in new_events:
                elat = evt.get('latitude', evt.get('lat', 0))
                elon = evt.get('longitude', evt.get('lon', 0))
                emag = evt.get('magnitude') or 0
                elayer = evt.get('layer', '')

                # Check layer filter
                if allowed_layers and elayer not in allowed_layers:
                    continue
                # Check magnitude threshold
                if emag < rule.min_magnitude:
                    continue
                # Check distance
                dist = haversine_km(rule.lat, rule.lon, elat, elon)
                if dist > rule.radius_km:
                    continue

                # Rule triggered!
                rule.last_triggered_at = now
                session.commit()

                title = evt.get('title', 'Event detected')
                print(f"[ALERT] Watch rule '{rule.name}' triggered by: {title}")

                if rule.notify_telegram and _TELEGRAM_AVAILABLE:
                    try:
                        send_watch_rule_alert(rule.name, title, emag if emag else None, elat, elon)
                    except Exception as e:
                        print(f"[ALERT] Telegram send failed: {e}")
                break  # One trigger per rule per cycle
    except Exception as e:
        print(f"[evaluate_watch_rules] Error: {e}")


def run_subscription_fetches(session) -> None:
    """Fetch fresh news for all active location subscriptions."""
    try:
        from sqlalchemy import select
        from sts_monitor.models import LocationSubscriptionORM, GeoEventORM
        from datetime import datetime, timezone, timedelta
        import json

        subs = session.scalars(
            select(LocationSubscriptionORM).where(LocationSubscriptionORM.is_active == True)
        ).all()

        if not subs:
            return

        now = datetime.now(timezone.utc)
        refresh_interval = timedelta(minutes=45)

        for sub in subs:
            if sub.last_fetched_at and (now - sub.last_fetched_at) < refresh_interval:
                continue

            print(f"[SUBSCRIPTIONS] Fetching news for: {sub.display_name}")
            try:
                from sts_monitor.connectors.local_discovery import fetch_local_news, fetch_state_news
                from sts_monitor.connectors.local_discovery import _STATE_BBOXES

                if sub.state.lower() in _STATE_BBOXES and not sub.city:
                    events = fetch_state_news(sub.state, sub.lat, sub.lon)
                else:
                    events = fetch_local_news(sub.city, sub.state, sub.lat, sub.lon)

                stored = 0
                for evt in events:
                    try:
                        from sqlalchemy import select as sel
                        exists = session.scalar(sel(GeoEventORM.id).where(GeoEventORM.source_id == evt['source_id']))
                        if not exists:
                            row = GeoEventORM(
                                layer=evt.get('layer', 'local_news'),
                                source_id=evt['source_id'],
                                title=evt['title'],
                                latitude=evt['lat'],
                                longitude=evt['lon'],
                                magnitude=evt.get('magnitude'),
                                properties_json=json.dumps(evt.get('properties', {})),
                                event_time=evt['event_time'],
                            )
                            session.add(row)
                            stored += 1
                    except Exception:
                        pass

                session.commit()
                sub.last_fetched_at = now
                session.commit()
                print(f"[SUBSCRIPTIONS] {sub.display_name}: {stored} new events stored")
            except Exception as e:
                print(f"[SUBSCRIPTIONS] Failed for {sub.display_name}: {e}")
    except Exception as e:
        print(f"[run_subscription_fetches] Error: {e}")


def run_signal_correlation(session) -> None:
    """Detect developing situations from correlated multi-source events."""
    if not _CORRELATION_AVAILABLE:
        return
    try:
        situations = run_correlation(session, hours=6.0, cluster_km=75.0, min_layers=2)
        if situations:
            print(f"[CORRELATION] {len(situations)} developing situations detected")
            # Send Telegram for critical/high severity situations
            if _TELEGRAM_AVAILABLE:
                for sit in situations:
                    if sit.get('severity') in ('critical', 'high') and sit.get('predicted_importance', 0) >= 7:
                        try:
                            send_situation_alert(sit)
                        except Exception:
                            pass
    except Exception as e:
        print(f"[run_signal_correlation] Error: {e}")


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
        ("/ingest/nws",            {}),
        ("/ingest/fema",           {}),
        ("/ingest/usgs",           {"min_magnitude": 2.0, "lookback_hours": 48, "max_events": 100}),
        ("/ingest/google-news",    {}),
        ("/ingest/mbta",           {}),
        ("/ingest/ma-public-data", {}),
        ("/ingest/power-outages",  {}),
        ("/ingest/ma-environment", {}),
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

    # ── Reddit MA subreddits (every 30 min = every other cycle) ──────────
    if _cycle_count % 2 == 0:
        ma_subreddits = ["boston", "massachusetts", "worcesterma", "springfieldma",
                         "capecod", "westernmass", "northshore", "southshorema"]
        try:
            r = httpx.post(f"{BASE}/investigations/{inv_id}/ingest/reddit",
                           headers=HEADERS,
                           json={"subreddits": ma_subreddits, "per_subreddit_limit": 10, "sort": "new"},
                           timeout=90)
            if r.status_code == 200:
                d = r.json()
                log.info(f"  reddit MA: ingested={d.get('ingested_count', 0)}")
            else:
                log.warning(f"  reddit MA: HTTP {r.status_code}")
        except Exception as e:
            log.error(f"  reddit MA: {e}")

    # ── Local news for major US cities (every 30 min = every other cycle) ─
    if _cycle_count % 2 == 0:
        major_cities = [
            ("Boston", "Massachusetts", 42.3601, -71.0589),
            ("Worcester", "Massachusetts", 42.2626, -71.8023),
            ("Springfield", "Massachusetts", 42.1015, -72.5898),
            ("Cambridge", "Massachusetts", 42.3736, -71.1097),
            ("Lowell", "Massachusetts", 42.6334, -71.3162),
            ("New Bedford", "Massachusetts", 41.6362, -70.9342),
            ("Fall River", "Massachusetts", 41.7015, -71.1550),
            ("Lynn", "Massachusetts", 42.4668, -70.9495),
            ("Lawrence", "Massachusetts", 42.7070, -71.1631),
            ("Brockton", "Massachusetts", 42.0834, -71.0184),
            ("Quincy", "Massachusetts", 42.2529, -71.0023),
            ("Framingham", "Massachusetts", 42.2793, -71.4162),
            ("Plymouth", "Massachusetts", 41.9584, -70.6673),
            ("Pittsfield", "Massachusetts", 42.4501, -73.2453),
            ("Cape Cod", "Massachusetts", 41.6688, -70.2962),
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

    # Feature: evaluate watch rules and subscriptions via direct DB session
    try:
        from sts_monitor.database import get_session
        with get_session() as session:
            # Feature: evaluate watch rules against newly stored events
            try:
                new_event_dicts = []  # HTTP-based ingest — no direct row refs here
                evaluate_watch_rules(session, new_event_dicts)
            except Exception as _e:
                print(f"[INGEST] watch rule eval error: {_e}")

            # Every 3rd cycle (~45 min): run subscriptions + correlation
            if _cycle_count % 3 == 0:
                try:
                    run_subscription_fetches(session)
                except Exception as _e:
                    print(f"[INGEST] subscription fetch error: {_e}")
                try:
                    run_signal_correlation(session)
                except Exception as _e:
                    print(f"[INGEST] correlation error: {_e}")
    except Exception as _e:
        print(f"[INGEST] DB session error for feature tasks: {_e}")

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
