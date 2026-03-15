"""
correlation.py — Signal correlation engine for STSIA.
Detects when multiple data sources report activity in the same area/time window,
creating "Developing Situation" records.
"""
from __future__ import annotations
import json
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import select, func


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def predict_importance(layers: list[str], signal_count: int, max_magnitude: float) -> float:
    """Rule-based importance score 0-10."""
    score = 3.0
    HIGH_LAYERS = {'conflict', 'military', 'telegram', 'earthquake', 'fire', 'health_alert', 'cyber'}
    MED_LAYERS = {'weather_alert', 'disaster', 'news', 'political', 'humanitarian'}
    for layer in layers:
        if layer in HIGH_LAYERS:
            score += 2.0
            break
        elif layer in MED_LAYERS:
            score += 1.0
            break
    score += min(signal_count * 0.5, 3.0)
    if max_magnitude and max_magnitude > 6:
        score += 1.5
    elif max_magnitude and max_magnitude > 4:
        score += 0.8
    return min(round(score, 1), 10.0)


def classify_severity(importance: float) -> str:
    if importance >= 8:
        return 'critical'
    elif importance >= 6.5:
        return 'high'
    elif importance >= 4.5:
        return 'medium'
    return 'low'


def run_correlation(session: Session, hours: float = 6.0, cluster_km: float = 75.0, min_layers: int = 2) -> list[dict]:
    """
    Find geo events from multiple distinct layers within the same area/time window.
    Returns list of new/updated situation dicts.
    """
    from sts_monitor.models import GeoEventORM, SituationORM

    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    rows = session.scalars(
        select(GeoEventORM)
        .where(GeoEventORM.event_time >= cutoff)
        .order_by(GeoEventORM.event_time.desc())
        .limit(2000)
    ).all()

    if not rows:
        return []

    # Cluster events spatially
    clusters: list[dict[str, Any]] = []
    for event in rows:
        placed = False
        for cluster in clusters:
            dist = haversine_km(event.latitude, event.longitude, cluster['clat'], cluster['clon'])
            if dist <= cluster_km:
                cluster['events'].append(event)
                cluster['layers'].add(event.layer)
                # Update centroid
                n = len(cluster['events'])
                cluster['clat'] = (cluster['clat'] * (n-1) + event.latitude) / n
                cluster['clon'] = (cluster['clon'] * (n-1) + event.longitude) / n
                placed = True
                break
        if not placed:
            clusters.append({
                'clat': event.latitude,
                'clon': event.longitude,
                'events': [event],
                'layers': {event.layer},
            })

    # Filter to clusters with 2+ distinct layers
    situations = []
    for cluster in clusters:
        if len(cluster['layers']) < min_layers:
            continue
        events = cluster['events']
        layers = list(cluster['layers'])
        magnitudes = [e.magnitude for e in events if e.magnitude]
        max_mag = max(magnitudes) if magnitudes else 0.0
        importance = predict_importance(layers, len(events), max_mag)
        severity = classify_severity(importance)

        # Check if a situation already exists near this location
        existing = session.scalars(
            select(SituationORM)
            .where(SituationORM.is_active == True)
            .where(SituationORM.center_lat.between(cluster['clat'] - 0.7, cluster['clat'] + 0.7))
            .where(SituationORM.center_lon.between(cluster['clon'] - 0.7, cluster['clon'] + 0.7))
        ).first()

        event_ids = [e.id for e in events[:20]]
        # Generate a short title from the most significant event
        top_event = max(events, key=lambda e: e.magnitude or 0)
        title = top_event.title[:120] if top_event.title else f"Multi-source activity ({len(events)} signals)"

        if existing:
            existing.signal_count = len(events)
            existing.layers_json = json.dumps(layers)
            existing.event_ids_json = json.dumps(event_ids)
            existing.predicted_importance = importance
            existing.severity = severity
            existing.updated_at = datetime.now(UTC)
            sit_dict = _situation_to_dict(existing)
        else:
            sit = SituationORM(
                title=title,
                center_lat=cluster['clat'],
                center_lon=cluster['clon'],
                severity=severity,
                signal_count=len(events),
                layers_json=json.dumps(layers),
                event_ids_json=json.dumps(event_ids),
                predicted_importance=importance,
            )
            session.add(sit)
            session.flush()
            sit_dict = _situation_to_dict(sit)

        situations.append(sit_dict)

    session.commit()
    return situations


def _situation_to_dict(sit: Any) -> dict:
    return {
        'id': sit.id,
        'title': sit.title,
        'summary': sit.summary,
        'center_lat': sit.center_lat,
        'center_lon': sit.center_lon,
        'severity': sit.severity,
        'signal_count': sit.signal_count,
        'layers': json.loads(sit.layers_json or '[]'),
        'predicted_importance': sit.predicted_importance,
        'is_active': sit.is_active,
        'created_at': sit.created_at.isoformat() if sit.created_at else None,
        'updated_at': sit.updated_at.isoformat() if sit.updated_at else None,
    }


def get_active_situations(session: Session, limit: int = 20) -> list[dict]:
    """Return active situations sorted by importance."""
    from sts_monitor.models import SituationORM
    rows = session.scalars(
        select(SituationORM)
        .where(SituationORM.is_active == True)
        .order_by(SituationORM.predicted_importance.desc())
        .limit(limit)
    ).all()
    return [_situation_to_dict(r) for r in rows]
