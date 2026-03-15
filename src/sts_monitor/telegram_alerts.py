# -*- coding: utf-8 -*-
"""
telegram_alerts.py -- Push notifications to Telegram via OpenClaw gateway (port 18789).
"""
from __future__ import annotations
import json
import httpx

GATEWAY_URL = "http://localhost:18789"


def send_alert(title: str, body: str, severity: str = "medium",
               lat: float | None = None, lon: float | None = None) -> bool:
    """Send an alert message via the OpenClaw gateway Telegram integration."""
    severity_prefix = {'critical': '[!!]', 'high': '[!]', 'medium': '[*]', 'low': '[i]'}.get(severity, '[*]')
    location_str = f"\nLocation: {lat:.3f}N, {lon:.3f}E" if lat is not None and lon is not None else ""
    message = f"{severity_prefix} STSIA ALERT [{severity.upper()}]\n\n{title}\n{body}{location_str}"

    # Try multiple gateway endpoint formats
    endpoints = [
        ("/send", {"message": message}),
        ("/telegram/send", {"text": message}),
        ("/notify", {"msg": message, "type": "alert"}),
        ("/api/send", {"content": message}),
    ]

    for path, payload in endpoints:
        try:
            r = httpx.post(f"{GATEWAY_URL}{path}", json=payload, timeout=5.0)
            if r.status_code in (200, 201, 202):
                return True
        except Exception:
            continue

    return False


def send_situation_alert(situation: dict) -> bool:
    """Format and send a Developing Situation alert."""
    severity = situation.get('severity', 'medium')
    title = situation.get('title', 'Unknown situation')
    signal_count = situation.get('signal_count', 0)
    layers = ', '.join(situation.get('layers', []))
    importance = situation.get('predicted_importance', 0)

    body = (
        f"Signal count: {signal_count}\n"
        f"Data sources: {layers}\n"
        f"Importance: {importance}/10\n"
        f"View at: http://localhost:8080/dashboard"
    )

    return send_alert(title, body, severity,
                      situation.get('center_lat'), situation.get('center_lon'))


def send_watch_rule_alert(rule_name: str, event_title: str, magnitude: float | None,
                           lat: float, lon: float) -> bool:
    """Alert when a geo watch rule is triggered."""
    mag_str = f" (M{magnitude:.1f})" if magnitude else ""
    body = f"Watch rule '{rule_name}' triggered\n\nEvent: {event_title}{mag_str}"
    return send_alert(f"Watch Rule: {rule_name}", body, "high", lat, lon)
