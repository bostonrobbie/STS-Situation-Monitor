#!/usr/bin/env python3
from __future__ import annotations

import json

from sts_monitor.tools.simulation_runner import run_full_workflow_simulation


if __name__ == "__main__":
    print(json.dumps(run_full_workflow_simulation(), indent=2, default=str))
