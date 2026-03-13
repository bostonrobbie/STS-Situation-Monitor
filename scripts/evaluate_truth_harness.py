#!/usr/bin/env python
from __future__ import annotations

import json
from pathlib import Path
import sys

from sts_monitor.pipeline import Observation, SignalPipeline

CASES_FILE = Path("scripts/fixtures/truth_harness_cases.json")


def main() -> int:
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    failures: list[str] = []

    for case in cases:
        pipeline = SignalPipeline(min_reliability=float(case.get("min_reliability", 0.45)))
        observations = [
            Observation(
                source=item["source"],
                claim=item["claim"],
                url=item["url"],
                reliability_hint=float(item.get("reliability_hint", 0.5)),
            )
            for item in case.get("observations", [])
        ]
        result = pipeline.run(observations, topic=case.get("topic", "topic"))

        if "expected_disputed_min" in case and len(result.disputed_claims) < int(case["expected_disputed_min"]):
            failures.append(
                f"{case['name']}: disputed_claims {len(result.disputed_claims)} < {case['expected_disputed_min']}"
            )
        if "expected_accepted" in case and len(result.accepted) != int(case["expected_accepted"]):
            failures.append(f"{case['name']}: accepted {len(result.accepted)} != {case['expected_accepted']}")
        if "expected_dropped" in case and len(result.dropped) != int(case["expected_dropped"]):
            failures.append(f"{case['name']}: dropped {len(result.dropped)} != {case['expected_dropped']}")

    if failures:
        print("Truth harness failed:", file=sys.stderr)
        for item in failures:
            print(f"- {item}", file=sys.stderr)
        return 1

    print(f"Truth harness passed. cases={len(cases)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
