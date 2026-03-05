from sts_monitor.tools.simulation_runner import run_full_workflow_simulation

import pytest

from sts_monitor.tools.simulation_runner import run_full_workflow_simulation

pytestmark = [pytest.mark.simulation, pytest.mark.slow]


def test_full_workflow_simulation_passes() -> None:
    result = run_full_workflow_simulation()
    assert result["passed"] is True
    assert len(result["checks"]) >= 10

    checks = {item["name"]: item for item in result["checks"]}
    assert checks["preflight"]["status"] == 200
    assert checks["run pipeline no llm"]["status"] == 200
    assert checks["run pipeline with llm (fallback expected offline)"]["status"] == 200

    dashboard = checks["dashboard summary"]["body"]
    assert dashboard["observations"] > 0
    assert dashboard["reports"] >= 1

    llm_run = checks["run pipeline with llm (fallback expected offline)"]["body"]
    assert llm_run["llm_fallback_used"] is True
