"""Notification channels: email, Slack, and webhook delivery for alerts.

All channels are fire-and-forget with async delivery. Configure via
environment variables. Channels that lack credentials are silently skipped.
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SMTP_HOST = os.getenv("STS_SMTP_HOST", "")
SMTP_PORT = int(os.getenv("STS_SMTP_PORT", "587"))
SMTP_USER = os.getenv("STS_SMTP_USER", "")
SMTP_PASSWORD = os.getenv("STS_SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("STS_SMTP_FROM", "sts-monitor@localhost")
SMTP_TO = os.getenv("STS_SMTP_TO", "")  # comma-separated recipients

SLACK_WEBHOOK_URL = os.getenv("STS_SLACK_WEBHOOK_URL", "")
SLACK_CHANNEL = os.getenv("STS_SLACK_CHANNEL", "")

WEBHOOK_URL = os.getenv("STS_ALERT_WEBHOOK_URL", "")
WEBHOOK_TIMEOUT_S = float(os.getenv("STS_ALERT_WEBHOOK_TIMEOUT_S", "5"))


@dataclass(slots=True)
class AlertNotification:
    """A notification payload to send across all configured channels."""
    title: str
    message: str
    severity: str = "info"
    investigation_id: str | None = None
    metadata: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def send_email(notification: AlertNotification) -> bool:
    """Send an email notification via SMTP. Returns True on success."""
    if not SMTP_HOST or not SMTP_TO:
        return False

    try:
        msg = EmailMessage()
        msg["Subject"] = f"[STS {notification.severity.upper()}] {notification.title}"
        msg["From"] = SMTP_FROM
        msg["To"] = SMTP_TO

        body_lines = [
            f"Severity: {notification.severity}",
            f"Title: {notification.title}",
            "",
            notification.message,
        ]
        if notification.investigation_id:
            body_lines.append(f"\nInvestigation: {notification.investigation_id}")
        if notification.metadata:
            body_lines.append(f"\nMetadata: {json.dumps(notification.metadata, default=str)}")

        msg.set_content("\n".join(body_lines))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            if SMTP_PORT == 587:
                server.starttls()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)

        logger.info("Email notification sent: %s", notification.title)
        return True
    except Exception as exc:
        logger.warning("Email notification failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

def send_slack(notification: AlertNotification) -> bool:
    """Send a Slack notification via incoming webhook. Returns True on success."""
    if not SLACK_WEBHOOK_URL:
        return False

    severity_emoji = {
        "critical": ":rotating_light:",
        "high": ":warning:",
        "medium": ":large_blue_circle:",
        "low": ":white_circle:",
        "info": ":information_source:",
    }.get(notification.severity, ":bell:")

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{severity_emoji} {notification.title}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": notification.message},
        },
    ]

    if notification.investigation_id:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"Investigation: `{notification.investigation_id}`"}],
        })

    payload: dict[str, Any] = {"blocks": blocks}
    if SLACK_CHANNEL:
        payload["channel"] = SLACK_CHANNEL

    try:
        resp = httpx.post(SLACK_WEBHOOK_URL, json=payload, timeout=5)
        resp.raise_for_status()
        logger.info("Slack notification sent: %s", notification.title)
        return True
    except Exception as exc:
        logger.warning("Slack notification failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Generic webhook
# ---------------------------------------------------------------------------

def send_webhook(notification: AlertNotification) -> bool:
    """Send a generic webhook notification. Returns True on success."""
    if not WEBHOOK_URL:
        return False

    payload = {
        "title": notification.title,
        "message": notification.message,
        "severity": notification.severity,
        "investigation_id": notification.investigation_id,
        "metadata": notification.metadata or {},
    }

    try:
        resp = httpx.post(WEBHOOK_URL, json=payload, timeout=WEBHOOK_TIMEOUT_S)
        resp.raise_for_status()
        logger.info("Webhook notification sent: %s", notification.title)
        return True
    except Exception as exc:
        logger.warning("Webhook notification failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Fan-out
# ---------------------------------------------------------------------------

def notify_all(notification: AlertNotification) -> dict[str, bool]:
    """Send notification to all configured channels. Returns channel→success map."""
    results: dict[str, bool] = {}

    if SMTP_HOST and SMTP_TO:
        results["email"] = send_email(notification)

    if SLACK_WEBHOOK_URL:
        results["slack"] = send_slack(notification)

    if WEBHOOK_URL:
        results["webhook"] = send_webhook(notification)

    if not results:
        logger.debug("No notification channels configured; skipping notification: %s", notification.title)

    return results
