#!/usr/bin/env bash
set -euo pipefail

python -m pip install -e .[dev]
python -m pip check
python scripts/check_migration_graph.py
python scripts/verify_config_surface.py
python scripts/check_authz_surface.py
python scripts/check_readme_endpoint_sync.py
python scripts/check_deployment_surface.py
python scripts/evaluate_truth_harness.py
./scripts/verify_migrations.sh
pytest -q tests/test_migrations.py --no-cov
./scripts/qa_security.sh

echo "Infra QA gate passed."
