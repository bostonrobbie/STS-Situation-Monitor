from sts_monitor.tools.simulation_runner import run_full_workflow_simulation


def test_full_workflow_simulation_passes() -> None:
    result = run_full_workflow_simulation()
    assert result["passed"] is True
    assert len(result["checks"]) >= 10
