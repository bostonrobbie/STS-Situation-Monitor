#!/usr/bin/env bash
set -euo pipefail

python -m pip install -e .[dev]
ruff check .
pytest -q
python scripts/simulate_full_workflow.py >/dev/null
