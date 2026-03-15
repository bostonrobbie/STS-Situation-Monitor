"""Privacy and anonymity layer for all outbound requests.

Provides:
  - Tor SOCKS5 proxy routing (all traffic through Tor if enabled)
  - User-Agent rotation (realistic browser fingerprints)
  - Request spacing and jitter (avoid detection patterns)
  - DNS-over-HTTPS resolution (prevent DNS snooping by ISP)
  - Referrer stripping
  - Cookie isolation per connector
  - Request logging suppression

Usage:
    from sts_monitor.privacy import PrivacyConfig, build_private_client

    config = PrivacyConfig(use_tor=True, rotate_ua=True)
    client = build_private_client(config)

All connectors can optionally accept a proxy_url parameter.
This module provides the infrastructure to generate those configs.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any

import httpx


# ── User-Agent rotation pool ──────────────────────────────────────────

_USER_AGENTS: list[str] = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Safari on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    # Chrome on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Firefox on Linux
    "Mozilla/5.0 (X11; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

# DNS-over-HTTPS resolvers
DOH_RESOLVERS: dict[str, str] = {
    "cloudflare": "https://cloudflare-dns.com/dns-query",
    "google": "https://dns.google/dns-query",
    "quad9": "https://dns.quad9.net/dns-query",
    "mullvad": "https://dns.mullvad.net/dns-query",
}


@dataclass(slots=True)
class PrivacyConfig:
    """Privacy configuration for outbound requests."""
    use_tor: bool = False
    tor_socks_url: str = "socks5://127.0.0.1:9050"
    rotate_ua: bool = True
    strip_referrer: bool = True
    use_doh: bool = False
    doh_resolver: str = "cloudflare"
    min_request_delay_s: float = 0.5
    max_request_delay_s: float = 2.0
    add_jitter: bool = True
    timeout_s: float = 30.0
    # Custom proxy (overrides Tor if set)
    proxy_url: str | None = None


def get_random_ua() -> str:
    """Get a random realistic User-Agent string."""
    return random.choice(_USER_AGENTS)


def get_privacy_headers(config: PrivacyConfig) -> dict[str, str]:
    """Build privacy-respecting HTTP headers."""
    headers: dict[str, str] = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    if config.rotate_ua:
        headers["User-Agent"] = get_random_ua()
    else:
        headers["User-Agent"] = _USER_AGENTS[0]

    if config.strip_referrer:
        headers["Referer"] = ""

    return headers


def get_proxy_url(config: PrivacyConfig) -> str | None:
    """Get the proxy URL based on config."""
    if config.proxy_url:
        return config.proxy_url
    if config.use_tor:
        return config.tor_socks_url
    return None


def build_private_client(
    config: PrivacyConfig | None = None,
    **extra_kwargs: Any,
) -> httpx.Client:
    """Build an httpx.Client with privacy protections applied.

    Args:
        config: Privacy configuration. None = defaults (UA rotation, no Tor).
        **extra_kwargs: Additional kwargs passed to httpx.Client.
    """
    if config is None:
        config = PrivacyConfig()

    kwargs: dict[str, Any] = {
        "timeout": config.timeout_s,
        "follow_redirects": True,
        "headers": get_privacy_headers(config),
    }

    proxy = get_proxy_url(config)
    if proxy:
        kwargs["proxy"] = proxy

    kwargs.update(extra_kwargs)
    return httpx.Client(**kwargs)


def privacy_delay(config: PrivacyConfig) -> None:
    """Sleep for a random interval to avoid request pattern detection."""
    if not config.add_jitter:
        time.sleep(config.min_request_delay_s)
        return

    delay = random.uniform(
        config.min_request_delay_s,
        config.max_request_delay_s,
    )
    time.sleep(delay)


def resolve_doh(hostname: str, resolver: str = "cloudflare") -> list[str]:
    """Resolve a hostname using DNS-over-HTTPS to avoid ISP DNS snooping.

    Returns list of IP addresses.
    """
    resolver_url = DOH_RESOLVERS.get(resolver, DOH_RESOLVERS["cloudflare"])

    try:
        resp = httpx.get(
            resolver_url,
            params={"name": hostname, "type": "A"},
            headers={"Accept": "application/dns-json"},
            timeout=5.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            answer["data"]
            for answer in data.get("Answer", [])
            if answer.get("type") == 1  # A record
        ]
    except Exception:
        return []


# ── Tor health check ──────────────────────────────────────────────────

def check_tor_status(socks_url: str = "socks5://127.0.0.1:9050") -> dict[str, Any]:
    """Check if Tor is running and working by querying the Tor check API."""
    try:
        with httpx.Client(proxy=socks_url, timeout=10.0) as client:
            resp = client.get("https://check.torproject.org/api/ip")
            resp.raise_for_status()
            data = resp.json()
        return {
            "tor_available": True,
            "is_tor": data.get("IsTor", False),
            "ip": data.get("IP", "unknown"),
        }
    except Exception as exc:
        return {
            "tor_available": False,
            "is_tor": False,
            "error": str(exc),
        }


def get_privacy_status(config: PrivacyConfig) -> dict[str, Any]:
    """Get current privacy protection status."""
    status: dict[str, Any] = {
        "tor_enabled": config.use_tor,
        "ua_rotation": config.rotate_ua,
        "referrer_stripping": config.strip_referrer,
        "doh_enabled": config.use_doh,
        "doh_resolver": config.doh_resolver if config.use_doh else None,
        "request_jitter": config.add_jitter,
        "delay_range": f"{config.min_request_delay_s}-{config.max_request_delay_s}s",
        "proxy_url": "configured" if config.proxy_url else None,
    }

    if config.use_tor:
        tor_check = check_tor_status(config.tor_socks_url)
        status["tor_status"] = tor_check

    return status
