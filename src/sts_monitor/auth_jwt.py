"""JWT-based user authentication with session management.

Adds user accounts with password hashing (bcrypt), JWT token issuance,
and session-based auth alongside the existing API key system.

All heavy imports (jwt, bcrypt) are lazy to avoid issues with broken
system-level cryptography packages.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from sts_monitor.config import settings

# JWT config — reads from env with sensible defaults
JWT_SECRET = os.getenv("STS_JWT_SECRET", "change-me-in-production-" + settings.auth_api_key)
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = int(os.getenv("STS_JWT_EXPIRY_HOURS", "24"))


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    import bcrypt
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against its bcrypt hash."""
    import bcrypt
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def create_token(user_id: int, username: str, role: str) -> str:
    """Create a JWT token for an authenticated user."""
    import jwt
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT token."""
    import jwt
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


@dataclass(slots=True)
class UserContext:
    """Authenticated user context from JWT or API key."""
    user_id: int | None
    username: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_analyst(self) -> bool:
        return self.role in {"admin", "analyst"}
