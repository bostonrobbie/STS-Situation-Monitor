#!/usr/bin/env python
from __future__ import annotations

from pathlib import Path
import sys

COMPOSE_PUBLIC = Path("docker-compose.public.yml")
CADDYFILE = Path("ops/Caddyfile")


def ensure_contains(path: Path, required: list[str]) -> list[str]:
    text = path.read_text(encoding="utf-8")
    missing = [item for item in required if item not in text]
    return missing


def main() -> int:
    if not COMPOSE_PUBLIC.exists():
        print("Missing docker-compose.public.yml", file=sys.stderr)
        return 1
    if not CADDYFILE.exists():
        print("Missing ops/Caddyfile", file=sys.stderr)
        return 1

    compose_missing = ensure_contains(
        COMPOSE_PUBLIC,
        [
            "caddy:",
            "api:",
            '"80:80"',
            '"443:443"',
            "STS_DOMAIN",
            "./ops/Caddyfile:/etc/caddy/Caddyfile:ro",
        ],
    )
    if compose_missing:
        print(f"docker-compose.public.yml missing required markers: {compose_missing}", file=sys.stderr)
        return 1

    caddy_missing = ensure_contains(
        CADDYFILE,
        [
            "{$STS_DOMAIN}",
            "reverse_proxy api:8080",
        ],
    )
    if caddy_missing:
        print(f"ops/Caddyfile missing required markers: {caddy_missing}", file=sys.stderr)
        return 1

    print("Deployment surface check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
