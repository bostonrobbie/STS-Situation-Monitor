#!/usr/bin/env bash
set -euo pipefail

DB_PATH=.ci_migration.db
export STS_DATABASE_URL="sqlite:///${DB_PATH}"
alembic upgrade head

python - <<'PY'
from pathlib import Path
p=Path('.ci_migration.db')
if p.exists():
    p.unlink()
PY

echo "Migration check passed."
