from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.database import Base, engine
from sts_monitor.main import app
from sts_monitor.pipeline import Observation


client = TestClient(app)
AUTH = {"X-API-Key": "change-me"}


@pytest.fixture(autouse=True)
def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_auth_required_for_protected_endpoints() -> None:
    response = client.get("/investigations")
    assert response.status_code == 401


def test_preflight_handles_unreachable_llm(monkeypatch) -> None:
    from sts_monitor.main import llm_client

    class FakeHealth:
        reachable = False
        model_available = False
        detail = "connection refused"

    monkeypatch.setattr(llm_client, "health", lambda: FakeHealth())
    response = client.get("/system/preflight")
    assert response.status_code == 200
    payload = response.json()
    assert payload["database"]["ok"] is True
    assert payload["llm"]["ok"] is False


def test_run_requires_observations() -> None:
    created = client.post("/investigations", json={"topic": "Major incident"}, headers=AUTH)
    investigation_id = created.json()["id"]

    run = client.post(f"/investigations/{investigation_id}/run", headers=AUTH)
    assert run.status_code == 400


def test_create_ingest_and_run_investigation(monkeypatch) -> None:
    created = client.post("/investigations", json={"topic": "Major incident"}, headers=AUTH)
    assert created.status_code == 200
    investigation_id = created.json()["id"]

    from sts_monitor.connectors.rss import RSSConnector

    def fake_collect(self, query=None):
        _ = query
        return ConnectorResult(
            connector="rss",
            observations=[
                Observation(
                    source="rss:https://example.com/feed",
                    claim="Trusted update",
                    url="https://example.com/post",
                    captured_at=datetime.now(UTC),
                    reliability_hint=0.8,
                ),
                Observation(
                    source="rss:https://example.com/feed",
                    claim="Low quality rumor",
                    url="https://example.com/post2",
                    captured_at=datetime.now(UTC),
                    reliability_hint=0.2,
                ),
            ],
            metadata={"failed_feeds": []},
        )

    monkeypatch.setattr(RSSConnector, "collect", fake_collect)

    ingested = client.post(
        f"/investigations/{investigation_id}/ingest/rss",
        json={"feed_urls": ["https://example.com/feed"]},
        headers=AUTH,
    )
    assert ingested.status_code == 200
    assert ingested.json()["ingested_count"] == 2

    runs = client.get(f"/investigations/{investigation_id}/ingestion-runs", headers=AUTH)
    assert runs.status_code == 200
    assert runs.json()[0]["connector"] == "rss"

    observations = client.get(f"/investigations/{investigation_id}/observations", headers=AUTH)
    assert observations.status_code == 200
    assert len(observations.json()) == 2

    run = client.post(f"/investigations/{investigation_id}/run", headers=AUTH)
    assert run.status_code == 200
    payload = run.json()
    assert payload["investigation_id"] == investigation_id
    assert payload["confidence"] == 0.8


def test_simulated_ingest_and_feedback_memory() -> None:
    created = client.post("/investigations", json={"topic": "Grid outage"}, headers=AUTH)
    investigation_id = created.json()["id"]

    ingest = client.post(
        f"/investigations/{investigation_id}/ingest/simulated",
        json={"batch_size": 30, "include_noise": True},
        headers=AUTH,
    )
    assert ingest.status_code == 200
    assert ingest.json()["ingested_count"] >= 30

    run = client.post(f"/investigations/{investigation_id}/run", json={"use_llm": False}, headers=AUTH)
    assert run.status_code == 200
    assert "deduplicated_count" in run.json()

    feedback = client.post(
        f"/investigations/{investigation_id}/feedback",
        json={"label": "accurate", "notes": "Good clustering, keep this source weighted high."},
        headers=AUTH,
    )
    assert feedback.status_code == 200

    memory = client.get(f"/investigations/{investigation_id}/memory", headers=AUTH)
    assert memory.status_code == 200
    assert memory.json()["feedback_total"] == 1


def test_llm_fallback_when_generation_fails(monkeypatch) -> None:
    created = client.post("/investigations", json={"topic": "Flooding"}, headers=AUTH)
    investigation_id = created.json()["id"]

    ingest = client.post(
        f"/investigations/{investigation_id}/ingest/simulated",
        json={"batch_size": 5, "include_noise": False},
        headers=AUTH,
    )
    assert ingest.status_code == 200

    from sts_monitor.main import llm_client

    def fail_summarize(prompt: str) -> str:
        _ = prompt
        raise RuntimeError("llm offline")

    monkeypatch.setattr(llm_client, "summarize", fail_summarize)

    run = client.post(f"/investigations/{investigation_id}/run", json={"use_llm": True}, headers=AUTH)
    assert run.status_code == 200
    assert run.json()["llm_fallback_used"] is True


def test_dashboard_summary() -> None:
    response = client.get("/dashboard/summary", headers=AUTH)
    assert response.status_code == 200
    payload = response.json()
    assert payload["investigations"] == 0
    assert payload["observations"] == 0
    assert payload["reports"] == 0


def test_enqueue_and_process_jobs() -> None:
    created = client.post("/investigations", json={"topic": "Port incident"}, headers=AUTH)
    investigation_id = created.json()["id"]

    queued_ingest = client.post(
        f"/jobs/enqueue/ingest-simulated/{investigation_id}",
        json={"batch_size": 10, "include_noise": False},
        headers=AUTH,
    )
    assert queued_ingest.status_code == 200

    processed_ingest = client.post("/jobs/process-next", headers=AUTH)
    assert processed_ingest.status_code == 200
    assert processed_ingest.json()["status"] in {"completed", "failed"}

    queued_run = client.post(
        f"/jobs/enqueue/run/{investigation_id}",
        json={"use_llm": False},
        headers=AUTH,
    )
    assert queued_run.status_code == 200

    processed_run = client.post("/jobs/process-next", headers=AUTH)
    assert processed_run.status_code == 200

    jobs = client.get("/jobs", headers=AUTH)
    assert jobs.status_code == 200
    assert len(jobs.json()) >= 2
