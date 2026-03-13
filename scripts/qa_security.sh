#!/usr/bin/env bash
set -euo pipefail

python -m pip install -e .[dev]
python -m pip check
pip-audit --ignore-vuln CVE-2026-1703

# Lightweight repo secret-pattern scan (heuristic).
if rg -n --hidden --glob '!.git/*' --glob '!*.md' --glob '!*.lock' \
  'AKIA[0-9A-Z]{16}|-----BEGIN (RSA|EC|OPENSSH|DSA)? ?PRIVATE KEY-----|xox[baprs]-[0-9A-Za-z-]+' .; then
  echo "Potential secret pattern(s) detected." >&2
  exit 1
fi

echo "Security QA gate passed."
