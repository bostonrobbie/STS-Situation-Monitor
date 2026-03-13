from sts_monitor.tools.simulation_runner import format_simulation_report, summarize_simulation


def test_summarize_simulation_collects_failures_and_snapshots() -> None:
    result = {
        "passed": False,
        "checks": [
            {"name": "preflight", "ok": True, "body": {"llm": {"reachable": False}, "readiness": {"level": "degraded", "score": 55}}},
            {"name": "dashboard summary", "ok": True, "body": {"investigations": 1, "observations": 10}},
            {"name": "run pipeline", "ok": False},
        ],
    }

    summary = summarize_simulation(result)

    assert summary["passed"] is False
    assert summary["total_checks"] == 3
    assert summary["failed_checks"] == ["run pipeline"]
    assert summary["dashboard"] == {"investigations": 1, "observations": 10}


def test_format_simulation_report_includes_status_and_tip() -> None:
    report = format_simulation_report({"passed": True, "checks": []})

    assert "Status: PASS" in report
    assert "Tip: run `python scripts/demo_simulated_functioning.py --json`" in report
