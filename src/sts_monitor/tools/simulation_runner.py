from __future__ import annotations


def run_full_workflow_simulation() -> dict:
    import json

    from fastapi.testclient import TestClient

    from sts_monitor.database import Base, engine
    from sts_monitor.main import app

    client = TestClient(app)
    auth = {"X-API-Key": "change-me"}

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    out: dict[str, object] = {"checks": []}

    def check(name: str, response, expect: int) -> None:
        ok = response.status_code == expect
        rec = {
            "name": name,
            "status": response.status_code,
            "expected": expect,
            "ok": ok,
        }
        if response.headers.get("content-type", "").startswith("application/json"):
            rec["body"] = response.json()
        out["checks"].append(rec)

    preflight = client.get("/system/preflight")
    check("preflight", preflight, 200)

    created = client.post("/investigations", json={"topic": "Harbor outage"}, headers=auth)
    check("create investigation", created, 200)
    investigation_id = created.json()["id"]

    queue_sim = client.post(
        f"/jobs/enqueue/ingest-simulated/{investigation_id}",
        json={"batch_size": 20, "include_noise": True},
        headers=auth,
    )
    check("enqueue simulated ingest job", queue_sim, 200)

    process_sim = client.post("/jobs/process-next", headers=auth)
    check("process simulated ingest job", process_sim, 200)

    sim = client.post(
        f"/investigations/{investigation_id}/ingest/simulated",
        json={"batch_size": 40, "include_noise": True},
        headers=auth,
    )
    check("simulated ingest", sim, 200)

    rss = client.post(
        f"/investigations/{investigation_id}/ingest/rss",
        json={"feed_urls": ["https://invalid.local/not-a-feed.xml"]},
        headers=auth,
    )
    check("rss ingest with broken feed", rss, 200)

    observations = client.get(f"/investigations/{investigation_id}/observations", headers=auth)
    check("list observations", observations, 200)

    schedule = client.post(
        "/schedules",
        json={
            "name": "harbor-sim-run",
            "job_type": "run_pipeline",
            "payload": {"investigation_id": investigation_id, "use_llm": False},
            "interval_seconds": 10,
            "priority": 70,
        },
        headers=auth,
    )
    check("create schedule", schedule, 200)

    tick = client.post("/schedules/tick", headers=auth)
    check("tick schedules", tick, 200)

    schedules = client.get("/schedules", headers=auth)
    check("list schedules", schedules, 200)

    queue_run = client.post(
        f"/jobs/enqueue/run/{investigation_id}",
        json={"use_llm": False},
        headers=auth,
    )
    check("enqueue run job", queue_run, 200)

    process_run = client.post("/jobs/process-next", headers=auth)
    check("process run job", process_run, 200)

    process_batch = client.post("/jobs/process-batch", json={"high_quota": 1, "normal_quota": 1, "low_quota": 1}, headers=auth)
    check("process job batch", process_batch, 200)

    run_no_llm = client.post(f"/investigations/{investigation_id}/run", json={"use_llm": False}, headers=auth)
    check("run pipeline no llm", run_no_llm, 200)

    run_llm = client.post(f"/investigations/{investigation_id}/run", json={"use_llm": True}, headers=auth)
    check("run pipeline with llm (fallback expected offline)", run_llm, 200)

    feedback = client.post(
        f"/investigations/{investigation_id}/feedback",
        json={"label": "review", "notes": "Need stronger source trust scoring and entity extraction."},
        headers=auth,
    )
    check("submit feedback", feedback, 200)

    memory = client.get(f"/investigations/{investigation_id}/memory", headers=auth)
    check("memory", memory, 200)

    reports = client.get(f"/reports/{investigation_id}", headers=auth)
    check("latest report", reports, 200)

    ingestion_runs = client.get(f"/investigations/{investigation_id}/ingestion-runs", headers=auth)
    check("ingestion run audit", ingestion_runs, 200)

    jobs_list = client.get("/jobs", headers=auth)
    check("list jobs", jobs_list, 200)

    dead_letters = client.get("/jobs/dead-letters", headers=auth)
    check("list dead letters", dead_letters, 200)

    dashboard = client.get("/dashboard/summary", headers=auth)
    check("dashboard summary", dashboard, 200)

    out["passed"] = all(item["ok"] for item in out["checks"])
    out["summary"] = json.dumps({"total_checks": len(out["checks"]), "passed": out["passed"]})
    return out
