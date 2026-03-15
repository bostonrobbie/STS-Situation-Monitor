"""
subscription_connector.py — Auto-fetch intelligence for saved location subscriptions.
Used by auto_ingest.py to keep subscribed locations fresh.
"""
from __future__ import annotations
from datetime import UTC, datetime, timedelta
import json

from sqlalchemy.orm import Session
from sqlalchemy import select


def refresh_all_subscriptions(session: Session, force: bool = False) -> dict:
    """
    Refresh news/events for all active location subscriptions.
    Returns stats dict.
    """
    from sts_monitor.models import LocationSubscriptionORM, GeoEventORM
    from sts_monitor.connectors.local_discovery import (
        fetch_local_news, fetch_state_news, _STATE_BBOXES
    )

    now = datetime.now(UTC)
    refresh_interval = timedelta(minutes=45)
    stats = {"checked": 0, "refreshed": 0, "events_stored": 0, "errors": []}

    subs = session.scalars(
        select(LocationSubscriptionORM).where(LocationSubscriptionORM.is_active == True)
    ).all()

    for sub in subs:
        stats["checked"] += 1
        if not force and sub.last_fetched_at and (now - sub.last_fetched_at) < refresh_interval:
            continue

        try:
            if sub.state.lower() in _STATE_BBOXES and not sub.city:
                events = fetch_state_news(sub.state, sub.lat, sub.lon)
            else:
                events = fetch_local_news(sub.city or sub.display_name, sub.state, sub.lat, sub.lon)

            stored = 0
            for evt in events:
                exists = session.scalar(
                    select(GeoEventORM.id).where(GeoEventORM.source_id == evt['source_id'])
                )
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

            session.commit()
            sub.last_fetched_at = now
            session.commit()
            stats["refreshed"] += 1
            stats["events_stored"] += stored
        except Exception as e:
            stats["errors"].append(f"{sub.display_name}: {e}")

    return stats
