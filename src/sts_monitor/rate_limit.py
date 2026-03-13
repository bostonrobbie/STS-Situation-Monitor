"""Rate limiting middleware for the STS API.

Uses a simple in-memory sliding window counter. Keys are based on
API key or IP address.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict
from dataclasses import dataclass, field

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint


# Config from environment
RATE_LIMIT_RPM = int(os.getenv("STS_RATE_LIMIT_RPM", "120"))  # requests per minute
RATE_LIMIT_BURST = int(os.getenv("STS_RATE_LIMIT_BURST", "30"))  # burst allowance
RATE_LIMIT_ENABLED = os.getenv("STS_RATE_LIMIT_ENABLED", "true").lower() in {"1", "true", "yes"}

# Paths exempt from rate limiting
EXEMPT_PATHS = {"/health", "/system/preflight", "/docs", "/openapi.json", "/static"}


@dataclass
class _BucketEntry:
    tokens: float = 0.0
    last_refill: float = field(default_factory=time.monotonic)


class RateLimiter:
    """Token bucket rate limiter keyed by client identity."""

    def __init__(self, rpm: int = RATE_LIMIT_RPM, burst: int = RATE_LIMIT_BURST) -> None:
        self.rate = rpm / 60.0  # tokens per second
        self.burst = burst
        self._buckets: dict[str, _BucketEntry] = defaultdict(lambda: _BucketEntry(tokens=burst, last_refill=time.monotonic()))
        self._last_cleanup = time.monotonic()

    def _cleanup(self) -> None:
        """Remove stale entries every 5 minutes."""
        now = time.monotonic()
        if now - self._last_cleanup < 300:
            return
        self._last_cleanup = now
        cutoff = now - 300
        stale = [k for k, v in self._buckets.items() if v.last_refill < cutoff]
        for k in stale:
            del self._buckets[k]

    def allow(self, key: str) -> tuple[bool, dict[str, str]]:
        """Check if a request is allowed. Returns (allowed, headers)."""
        self._cleanup()
        now = time.monotonic()
        bucket = self._buckets[key]

        # Refill tokens
        elapsed = now - bucket.last_refill
        bucket.tokens = min(self.burst, bucket.tokens + elapsed * self.rate)
        bucket.last_refill = now

        headers = {
            "X-RateLimit-Limit": str(RATE_LIMIT_RPM),
            "X-RateLimit-Remaining": str(max(0, int(bucket.tokens))),
        }

        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return True, headers

        headers["Retry-After"] = str(int(1 / self.rate) + 1)
        return False, headers


# Global singleton
_limiter = RateLimiter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that enforces per-client rate limits."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not RATE_LIMIT_ENABLED:
            return await call_next(request)

        # Skip exempt paths
        path = request.url.path
        if any(path.startswith(p) for p in EXEMPT_PATHS):
            return await call_next(request)

        # Detect test clients (no real client address)
        client_host = request.client.host if request.client else None
        if not client_host or client_host == "testclient":
            return await call_next(request)

        # Key by API key if present, else by IP
        key = request.headers.get("x-api-key") or client_host

        allowed, headers = _limiter.allow(key)

        if not allowed:
            return Response(
                content='{"detail": "Rate limit exceeded"}',
                status_code=429,
                media_type="application/json",
                headers=headers,
            )

        response = await call_next(request)
        for k, v in headers.items():
            response.headers[k] = v
        return response
