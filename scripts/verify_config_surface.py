#!/usr/bin/env python
from __future__ import annotations

from pathlib import Path
import re
import sys

ENV_EXAMPLE = Path('.env.example')
CONFIG = Path('src/sts_monitor/config.py')

ENV_RE = re.compile(r'^([A-Z0-9_]+)=', re.M)
GETENV_RE = re.compile(r'os\.getenv\("([A-Z0-9_]+)"')


def main() -> int:
    env_text = ENV_EXAMPLE.read_text(encoding='utf-8')
    cfg_text = CONFIG.read_text(encoding='utf-8')

    env_keys = ENV_RE.findall(env_text)
    cfg_keys = GETENV_RE.findall(cfg_text)

    dupes = sorted({k for k in env_keys if env_keys.count(k) > 1})
    if dupes:
        print(f"Duplicate keys in .env.example: {dupes}", file=sys.stderr)
        return 1

    env_set = set(env_keys)
    cfg_set = set(cfg_keys)

    missing_in_env = sorted(cfg_set - env_set)
    if missing_in_env:
        print(f"Missing keys in .env.example: {missing_in_env}", file=sys.stderr)
        return 1

    extras = sorted(env_set - cfg_set)
    print(f"Config surface check passed. keys={len(cfg_set)} extras_in_env={extras}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
