from __future__ import annotations

from secrets import compare_digest

from fastapi import Header, HTTPException

from sts_monitor.config import settings


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if not settings.enforce_auth:
        return
    if not x_api_key or not compare_digest(x_api_key, settings.auth_api_key):
        raise HTTPException(status_code=401, detail="Unauthorized")
