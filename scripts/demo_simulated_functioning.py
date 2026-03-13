#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from sts_monitor.tools.simulation_runner import format_simulation_report, run_full_workflow_simulation


def main() -> int:
    parser = argparse.ArgumentParser(description="Run STS simulated workflow demo in this VM")
    parser.add_argument("--json", action="store_true", help="Print raw JSON output instead of human report")
    args = parser.parse_args()

    result = run_full_workflow_simulation()
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(format_simulation_report(result))

    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())
