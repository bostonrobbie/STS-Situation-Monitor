#!/usr/bin/env bash
set -euo pipefail

python -m pip install -e .[dev]
python -m pip check
ruff check .
./scripts/verify_migrations.sh
./scripts/qa_infra.sh
pytest
python scripts/simulate_full_workflow.py >/dev/null

echo "QA local gate passed."
