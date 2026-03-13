"""JWT-based user authentication with session management.

Uses a pure-Python HMAC-SHA256 JWT implementation to avoid dependency on
the system's broken cryptography/cffi packages. Also provides bcrypt-based
password hashing.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
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


# ── Pure-Python JWT (HMAC-SHA256 only) ───────────────────────────────

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _jwt_encode(payload: dict[str, Any], secret: str) -> str:
    """Encode a JWT token using HMAC-SHA256."""
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":"), default=str).encode())
    signing_input = f"{header_b64}.{payload_b64}"
    signature = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    sig_b64 = _b64url_encode(signature)
    return f"{signing_input}.{sig_b64}"


def _jwt_decode(token: str, secret: str) -> dict[str, Any]:
    """Decode and validate a JWT token using HMAC-SHA256."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid token format")

    header_b64, payload_b64, sig_b64 = parts

    # Verify signature
    signing_input = f"{header_b64}.{payload_b64}"
    expected_sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    actual_sig = _b64url_decode(sig_b64)

    if not hmac.compare_digest(expected_sig, actual_sig):
        raise ValueError("Invalid signature")

    # Decode payload
    payload = json.loads(_b64url_decode(payload_b64))

    # Check expiration
    if "exp" in payload:
        exp = payload["exp"]
        if isinstance(exp, str):
            exp_dt = datetime.fromisoformat(exp)
        elif isinstance(exp, (int, float)):
            exp_dt = datetime.fromtimestamp(exp, tz=UTC)
        else:
            exp_dt = datetime.now(UTC)

        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=UTC)

        if exp_dt < datetime.now(UTC):
            raise ValueError("Token expired")

    return payload


# ── Password hashing ─────────────────────────────────────────────────

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


# ── Token API ────────────────────────────────────────────────────────

def create_token(user_id: int, username: str, role: str) -> str:
    """Create a JWT token for an authenticated user."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": now.timestamp(),
        "exp": (now + timedelta(hours=JWT_EXPIRY_HOURS)).timestamp(),
    }
    return _jwt_encode(payload, JWT_SECRET)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT token."""
    try:
        return _jwt_decode(token, JWT_SECRET)
    except ValueError as exc:
        msg = str(exc)
        if "expired" in msg.lower():
            raise HTTPException(status_code=401, detail="Token expired")
        raise HTTPException(status_code=401, detail="Invalid token")


# ── User context ─────────────────────────────────────────────────────

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
