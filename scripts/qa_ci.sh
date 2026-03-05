#!/usr/bin/env bash
set -euo pipefail

python -m pip install -e .[dev]
python -m pip check
ruff check .

./scripts/verify_migrations.sh
./scripts/qa_infra.sh

# Tiered execution for fast feedback without coverage gating on partial subsets.
pytest -m "not slow" --no-cov
pytest -m "simulation" --no-cov tests/test_workflow_simulation.py

# Final full pass enforces coverage threshold from pyproject addopts.
pytest

echo "QA CI gate passed."
