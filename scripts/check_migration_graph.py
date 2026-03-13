#!/usr/bin/env python
from __future__ import annotations

from pathlib import Path
import re
import sys

VERSIONS_DIR = Path("alembic/versions")
REV_RE = re.compile(r'^revision\s*=\s*"([^"]+)"', re.M)
DOWN_RE = re.compile(r'^down_revision\s*=\s*(.+)$', re.M)


def parse_value(raw: str) -> str | None:
    raw = raw.strip()
    if raw in {"None", "'None'", '"None"'}:
        return None
    m = re.match(r"^[\'\"]([^\'\"]+)[\'\"]$", raw)
    return m.group(1) if m else raw


def main() -> int:
    files = sorted(VERSIONS_DIR.glob("*.py"))
    if not files:
        print("No migration files found", file=sys.stderr)
        return 1

    revisions: dict[str, str | None] = {}
    for path in files:
        text = path.read_text(encoding="utf-8")
        r = REV_RE.search(text)
        d = DOWN_RE.search(text)
        if not r or not d:
            print(f"Malformed migration metadata in {path}", file=sys.stderr)
            return 1
        rev = r.group(1)
        down = parse_value(d.group(1))
        if rev in revisions:
            print(f"Duplicate revision id: {rev}", file=sys.stderr)
            return 1
        revisions[rev] = down

    children: dict[str, int] = {k: 0 for k in revisions}
    roots = 0
    for rev, down in revisions.items():
        if down is None:
            roots += 1
            continue
        if down not in revisions:
            print(f"Revision {rev} points to missing down_revision {down}", file=sys.stderr)
            return 1
        children[down] += 1

    if roots != 1:
        print(f"Expected exactly 1 root migration, found {roots}", file=sys.stderr)
        return 1

    heads = [rev for rev, count in children.items() if count == 0]
    if len(heads) != 1:
        print(f"Expected exactly 1 head migration, found {len(heads)}: {heads}", file=sys.stderr)
        return 1

    print(f"Migration graph check passed. root_count={roots} head={heads[0]} revisions={len(revisions)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
