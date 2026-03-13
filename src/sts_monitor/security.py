from __future__ import annotations

from secrets import compare_digest

from fastapi import Header, HTTPException

from sts_monitor.config import settings


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if not settings.enforce_auth:
        return
    if not x_api_key or not compare_digest(x_api_key, settings.auth_api_key):
        raise HTTPException(status_code=401, detail="Unauthorized")
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from secrets import compare_digest

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from sts_monitor.config import settings
from sts_monitor.database import get_session
from sts_monitor.models import APIKeyORM


@dataclass(slots=True)
class AuthContext:
    label: str
    role: str


def hash_api_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def require_api_key(
    x_api_key: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> AuthContext:
    if not settings.enforce_auth:
        return AuthContext(label="anonymous", role="admin")

    if not x_api_key:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Legacy static key remains valid.
    if compare_digest(x_api_key, settings.auth_api_key):
        return AuthContext(label="root-static", role="admin")

    key_hash = hash_api_key(x_api_key)
    row = session.scalars(select(APIKeyORM).where(APIKeyORM.key_hash == key_hash).where(APIKeyORM.active.is_(True))).first()
    if not row:
        raise HTTPException(status_code=401, detail="Unauthorized")

    return AuthContext(label=row.label, role=row.role)


def require_admin(ctx: AuthContext = Depends(require_api_key)) -> AuthContext:
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return ctx


def now_utc() -> datetime:
    return datetime.now(UTC)


def require_analyst(ctx: AuthContext = Depends(require_api_key)) -> AuthContext:
    if ctx.role not in {"admin", "analyst"}:
        raise HTTPException(status_code=403, detail="Analyst role required")
    return ctx
