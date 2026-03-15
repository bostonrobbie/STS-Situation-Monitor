"""
predictive.py — Importance scoring and predictive significance for geo events.
Uses a hybrid rule-based + historical frequency approach.
"""
from __future__ import annotations
import re
from datetime import UTC, datetime, timedelta


# High-importance keywords (raise score)
_HIGH_KW = [
    'explosion', 'attack', 'shooting', 'killed', 'dead', 'casualties',
    'nuclear', 'missile', 'war', 'invasion', 'coup', 'earthquake',
    'tsunami', 'wildfire', 'hurricane', 'pandemic', 'outbreak',
    'terrorism', 'bomb', 'chemical', 'biological', 'cyberattack',
    'airstrike', 'naval', 'troops', 'mobilization', 'sanctions',
]

_MEDIUM_KW = [
    'protest', 'arrest', 'fire', 'flood', 'storm', 'crash', 'accident',
    'evacuation', 'emergency', 'alert', 'warning', 'military', 'conflict',
    'election', 'vote', 'summit', 'treaty', 'deal', 'agreement',
]

_LAYER_WEIGHTS = {
    'conflict': 3.0, 'military': 2.8, 'telegram': 2.5, 'earthquake': 2.5,
    'fire': 2.2, 'health_alert': 2.2, 'cyber': 2.0, 'weather_alert': 1.8,
    'disaster': 1.8, 'news': 1.5, 'political': 1.5, 'humanitarian': 1.4,
    'social_intel': 1.2, 'local_news': 1.0, 'camera': 0.5,
}


def score_event(title: str, layer: str, magnitude: float | None, event_time: datetime | None = None) -> float:
    """
    Compute a predicted importance score 0-10 for a geo event.
    Higher = more likely to be significant/newsworthy.
    """
    base = _LAYER_WEIGHTS.get(layer, 1.0)
    score = base

    # Keyword analysis on title
    title_lower = title.lower()
    if any(kw in title_lower for kw in _HIGH_KW):
        score += 2.5
    elif any(kw in title_lower for kw in _MEDIUM_KW):
        score += 1.2

    # Magnitude component
    if magnitude:
        if magnitude >= 7.0:
            score += 3.0
        elif magnitude >= 5.0:
            score += 1.5
        elif magnitude >= 3.0:
            score += 0.5

    # Recency boost: events in last 2 hours are more important
    if event_time:
        et = event_time if event_time.tzinfo is not None else event_time.replace(tzinfo=UTC)
        age_hours = (datetime.now(UTC) - et).total_seconds() / 3600
        if age_hours < 2:
            score += 1.5
        elif age_hours < 6:
            score += 0.7

    return round(min(score, 10.0), 1)


def batch_score_events(events: list[dict]) -> list[dict]:
    """Add 'predicted_importance' field to each event dict."""
    for evt in events:
        title = evt.get('title', '')
        layer = evt.get('layer', '')
        magnitude = evt.get('magnitude')
        event_time_raw = evt.get('event_time')
        event_time = None
        if isinstance(event_time_raw, str):
            try:
                event_time = datetime.fromisoformat(event_time_raw.replace('Z', '+00:00'))
            except Exception:
                pass
        elif isinstance(event_time_raw, datetime):
            event_time = event_time_raw
        evt['predicted_importance'] = score_event(title, layer, magnitude, event_time)
    return events


def top_events(events: list[dict], n: int = 10) -> list[dict]:
    """Return top N events by predicted importance."""
    scored = batch_score_events(events)
    return sorted(scored, key=lambda e: e.get('predicted_importance', 0), reverse=True)[:n]
