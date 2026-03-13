from __future__ import annotations

from typing import Any

import httpx


def parse_csv_env(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def send_alert_webhook(*, webhook_url: str, timeout_s: float, payload: dict[str, Any]) -> dict[str, Any]:
    if not webhook_url:
        return {"sent": False, "status": "disabled"}
    try:
        response = httpx.post(webhook_url, json=payload, timeout=timeout_s)
        return {
            "sent": response.status_code < 400,
            "status": f"http-{response.status_code}",
        }
    except Exception as exc:
        return {"sent": False, "status": f"error: {exc}"}
