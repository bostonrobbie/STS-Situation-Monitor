"""Webhook ingestion — accept incoming data from external systems.

Provides a generic webhook endpoint that can receive observations from:
- Telegram bots
- Custom scrapers
- RSS push services
- CI/CD pipelines
- Any HTTP client

Validates payload, normalizes to observation format, stores in DB.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)


def validate_webhook_signature(
    payload_body: bytes,
    signature: str,
    secret: str,
) -> bool:
    """Validate HMAC-SHA256 webhook signature."""
    if not secret or not signature:
        return True  # No secret configured = open webhook
    expected = hmac.new(secret.encode(), payload_body, hashlib.sha256).hexdigest()
    prefix = "sha256="
    if signature.startswith(prefix):
        signature = signature[len(prefix):]
    return hmac.compare_digest(expected, signature)


def normalize_webhook_payload(
    payload: dict[str, Any],
    source_name: str = "webhook",
) -> list[dict[str, Any]]:
    """Normalize incoming webhook payload to observation format.

    Supports multiple formats:
    - Single observation: {"claim": "...", "source": "..."}
    - Batch: {"observations": [...]}
    - Telegram-style: {"message": {"text": "..."}}
    - RSS-push: {"items": [{"title": "...", "link": "..."}]}
    - Generic: {"text": "...", "url": "..."}
    """
    results = []

    # Batch format
    if "observations" in payload and isinstance(payload["observations"], list):
        for obs in payload["observations"]:
            results.append(_normalize_single(obs, source_name))
        return results

    # RSS push format
    if "items" in payload and isinstance(payload["items"], list):
        for item in payload["items"]:
            results.append({
                "source": source_name,
                "claim": item.get("title") or item.get("description") or item.get("summary", ""),
                "url": item.get("link") or item.get("url", ""),
                "captured_at": _parse_ts(item.get("pubDate") or item.get("published")),
                "reliability_hint": 0.6,
            })
        return results

    # Telegram format
    if "message" in payload and isinstance(payload["message"], dict):
        msg = payload["message"]
        results.append({
            "source": f"telegram:{msg.get('chat', {}).get('title', source_name)}",
            "claim": msg.get("text", ""),
            "url": "",
            "captured_at": datetime.now(UTC),
            "reliability_hint": 0.4,
            "connector_type": "telegram_webhook",
        })
        return results

    # Single observation format
    results.append(_normalize_single(payload, source_name))
    return results


def _normalize_single(payload: dict[str, Any], source_name: str) -> dict[str, Any]:
    """Normalize a single observation payload."""
    return {
        "source": payload.get("source") or source_name,
        "claim": payload.get("claim") or payload.get("text") or payload.get("title") or payload.get("content", ""),
        "url": payload.get("url") or payload.get("link", ""),
        "captured_at": _parse_ts(payload.get("captured_at") or payload.get("timestamp")),
        "reliability_hint": float(payload.get("reliability_hint") or payload.get("reliability") or 0.5),
        "latitude": payload.get("latitude") or payload.get("lat"),
        "longitude": payload.get("longitude") or payload.get("lon"),
        "connector_type": payload.get("connector_type") or "webhook",
    }


def _parse_ts(val) -> datetime:
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=UTC)
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except Exception:
            pass
    return datetime.now(UTC)
