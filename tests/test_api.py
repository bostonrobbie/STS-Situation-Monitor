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
import json

import pytest
from fastapi.testclient import TestClient

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.database import Base, engine
from sts_monitor.main import app
from sts_monitor.pipeline import Observation


client = TestClient(app)
AUTH = {"X-API-Key": "change-me"}

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)



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
        latency_ms = None

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
def test_create_and_run_investigation() -> None:
    created = client.post("/investigations", json={"topic": "Major incident"})
    investigation_id = created.json()["id"]

    run = client.post(f"/investigations/{investigation_id}/run")
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


def test_schedule_tick_enqueues_jobs() -> None:
    created = client.post("/investigations", json={"topic": "River event"}, headers=AUTH)
    investigation_id = created.json()["id"]

    schedule = client.post(
        "/schedules",
        json={
            "name": "river-sim-ingest",
            "job_type": "ingest_simulated",
            "payload": {"investigation_id": investigation_id, "batch_size": 5, "include_noise": False},
            "interval_seconds": 10,
            "priority": 80,
        },
        headers=AUTH,
    )
    assert schedule.status_code == 200

    tick = client.post("/schedules/tick", headers=AUTH)
    assert tick.status_code == 200
    assert tick.json()["enqueued"] >= 1

    schedules = client.get("/schedules", headers=AUTH)
    assert schedules.status_code == 200
    assert schedules.json()[0]["name"] == "river-sim-ingest"


def test_job_priority_processed_first() -> None:
    created = client.post("/investigations", json={"topic": "Airport outage"}, headers=AUTH)
    investigation_id = created.json()["id"]

    low = client.post(
        f"/jobs/enqueue/ingest-simulated/{investigation_id}",
        json={"batch_size": 3, "include_noise": False, "priority": 10},
        headers=AUTH,
    )
    high = client.post(
        f"/jobs/enqueue/ingest-simulated/{investigation_id}",
        json={"batch_size": 3, "include_noise": False, "priority": 90},
        headers=AUTH,
    )
    assert low.status_code == 200
    assert high.status_code == 200

    processed = client.post("/jobs/process-next", headers=AUTH)
    assert processed.status_code == 200
    assert processed.json()["job_id"] == high.json()["job_id"]


def test_dead_letter_and_requeue_flow() -> None:
    created = client.post("/investigations", json={"topic": "Dead letter check"}, headers=AUTH)
    investigation_id = created.json()["id"]

    queued_run = client.post(
        f"/jobs/enqueue/run/{investigation_id}",
        json={"use_llm": False, "priority": 50, "max_attempts": 1},
        headers=AUTH,
    )
    assert queued_run.status_code == 200

    # No observations exist, so this job should fail and eventually dead-letter after retries.
    for _ in range(4):
        client.post("/jobs/process-next", headers=AUTH)

    dead = client.get("/jobs/dead-letters", headers=AUTH)
    assert dead.status_code == 200
    assert len(dead.json()) >= 1
    dead_id = dead.json()[0]["id"]

    requeued = client.post(f"/jobs/dead-letters/{dead_id}/requeue", headers=AUTH)
    assert requeued.status_code == 200

    jobs = client.get("/jobs", headers=AUTH)
    assert jobs.status_code == 200
    requeued_job = next((item for item in jobs.json() if item["id"] == dead_id), None)
    assert requeued_job is not None
    assert requeued_job["attempts"] == 0


def test_process_batch_endpoint() -> None:
    created = client.post("/investigations", json={"topic": "Batch lane"}, headers=AUTH)
    investigation_id = created.json()["id"]

    client.post(
        f"/jobs/enqueue/ingest-simulated/{investigation_id}",
        json={"batch_size": 3, "include_noise": False, "priority": 95},
        headers=AUTH,
    )
    batch = client.post(
        "/jobs/process-batch",
        json={"high_quota": 1, "normal_quota": 0, "low_quota": 0},
        headers=AUTH,
    )
    assert batch.status_code == 200
    assert batch.json()["processed"] >= 1

def test_create_and_run_investigation() -> None:
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
        )

    monkeypatch.setattr(RSSConnector, "collect", fake_collect)

    ingested = client.post(
        f"/investigations/{investigation_id}/ingest/rss",
        json={"feed_urls": ["https://example.com/feed"]},
    )
    assert ingested.status_code == 200
    assert ingested.json()["ingested_count"] == 2

    observations = client.get(f"/investigations/{investigation_id}/observations")
    assert observations.status_code == 200
    assert len(observations.json()) == 2

    run = client.post(f"/investigations/{investigation_id}/run")
    client.post(
        f"/investigations/{investigation_id}/ingest/simulated",
        json={"batch_size": 2, "include_noise": False},
        headers=AUTH,
    )
    run = client.post(f"/investigations/{investigation_id}/run", headers=AUTH)
    assert run.status_code == 200
    payload = run.json()
    assert payload["investigation_id"] == investigation_id
    assert payload["confidence"] == 0.8


def test_simulated_ingest_and_feedback_memory() -> None:
    created = client.post("/investigations", json={"topic": "Grid outage"})
    investigation_id = created.json()["id"]

    ingest = client.post(
        f"/investigations/{investigation_id}/ingest/simulated",
        json={"batch_size": 30, "include_noise": True},
    )
    assert ingest.status_code == 200
    assert ingest.json()["ingested_count"] >= 30

    run = client.post(f"/investigations/{investigation_id}/run", json={"use_llm": False})
    assert run.status_code == 200
    assert "deduplicated_count" in run.json()

    feedback = client.post(
        f"/investigations/{investigation_id}/feedback",
        json={"label": "accurate", "notes": "Good clustering, keep this source weighted high."},
    )
    assert feedback.status_code == 200

    memory = client.get(f"/investigations/{investigation_id}/memory")
    assert memory.status_code == 200
    assert memory.json()["feedback_total"] == 1


def test_llm_fallback_when_generation_fails(monkeypatch) -> None:
    created = client.post("/investigations", json={"topic": "Flooding"})
    investigation_id = created.json()["id"]

    ingest = client.post(
        f"/investigations/{investigation_id}/ingest/simulated",
        json={"batch_size": 5, "include_noise": False},
    )
    assert ingest.status_code == 200

    from sts_monitor.main import llm_client

    def fail_summarize(prompt: str) -> str:
        _ = prompt
        raise RuntimeError("llm offline")

    monkeypatch.setattr(llm_client, "summarize", fail_summarize)

    run = client.post(f"/investigations/{investigation_id}/run", json={"use_llm": True})
    assert run.status_code == 200
    assert "fallback" in run.json()["summary"].lower()


def test_dashboard_summary() -> None:
    response = client.get("/dashboard/summary")
    assert response.status_code == 200
    payload = response.json()
    assert payload["investigations"] == 0
    assert payload["observations"] == 0
    assert payload["reports"] == 0
    assert "confidence" in payload


def test_trending_topics_endpoint(monkeypatch) -> None:
    from sts_monitor.research import TrendingTopic, TrendingResearchScanner

    def fake_fetch_topics(self, geo="US"):
        _ = self
        _ = geo
        return [
            TrendingTopic(topic="Topic A", traffic="100K+", published_at="today", source_url="https://example.com/a"),
            TrendingTopic(topic="Topic B", traffic=None, published_at="today", source_url="https://example.com/b"),
        ]

    monkeypatch.setattr(TrendingResearchScanner, "fetch_topics", fake_fetch_topics)
    response = client.get("/research/trending-topics", headers=AUTH)
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert payload["topics"][0]["topic"] == "Topic A"


def test_ingest_trending_persists_observations(monkeypatch) -> None:
    from sts_monitor.research import TrendingResearchScanner

    created = client.post("/investigations", json={"topic": "Storm"}, headers=AUTH)
    investigation_id = created.json()["id"]

    def fake_collect(self, geo="US"):
        _ = self
        _ = geo
        return (
            [
                Observation(
                    source="trending:US:Topic A",
                    claim="Trending topic 'Topic A': headline",
                    url="https://example.com/a",
                    captured_at=datetime.now(UTC),
                    reliability_hint=0.55,
                )
            ],
            {"geo": "US", "topics_scanned": 1, "failed_topics": []},
        )

    monkeypatch.setattr(TrendingResearchScanner, "collect_observations", fake_collect)

    ingested = client.post(
        f"/investigations/{investigation_id}/ingest/trending",
        json={"geo": "US", "max_topics": 5, "per_topic_limit": 3},
        headers=AUTH,
    )
    assert ingested.status_code == 200
    assert ingested.json()["connector"] == "trending"
    assert ingested.json()["ingested_count"] == 1

    observations = client.get(f"/investigations/{investigation_id}/observations", headers=AUTH)
    assert observations.status_code == 200
    assert len(observations.json()) == 1


def test_preflight_exposes_readiness_and_connector_checks(monkeypatch) -> None:
    import sts_monitor.main as main_mod

    class FakeResponse:
        status_code = 200

    monkeypatch.setattr(main_mod.httpx, "get", lambda *args, **kwargs: FakeResponse())

    response = client.get("/system/preflight")
    assert response.status_code == 200
    payload = response.json()
    assert "connectors" in payload
    assert "readiness" in payload
    assert payload["readiness"]["score"] >= 0




def test_preflight_readiness_degrades_when_queue_unhealthy(monkeypatch) -> None:
    import sts_monitor.main as main_mod

    class FakeHealth:
        reachable = True
        model_available = True
        detail = "ok"
        latency_ms = 10.0

    monkeypatch.setattr(main_mod.llm_client, "health", lambda: FakeHealth())
    monkeypatch.setattr(main_mod, "_connector_diagnostics", lambda: {"ok": True, "checks": {}})
    monkeypatch.setattr(main_mod, "_workspace_health_snapshot", lambda root: {
        "ok": True,
        "workspace_root": str(root),
        "workspace_root_exists": True,
        "disk_free_mb": 1024,
        "min_disk_free_mb": 256,
        "writable_hint": True,
    })
    monkeypatch.setattr(main_mod, "_queue_health_snapshot", lambda session: {
        "ok": False,
        "pending": 200,
        "failed": 1,
        "dead_letter": 0,
        "detail": "job failures or dead letters present",
    })

    response = client.get("/system/preflight")
    assert response.status_code == 200
    payload = response.json()
    assert payload["queue"]["ok"] is False
    assert payload["readiness"]["score"] == 85
    assert payload["readiness"]["level"] == "ready"


def test_preflight_readiness_blocked_when_multiple_systems_down(monkeypatch) -> None:
    import sts_monitor.main as main_mod

    class FakeHealth:
        reachable = False
        model_available = False
        detail = "offline"
        latency_ms = 2.0

    monkeypatch.setattr(main_mod.llm_client, "health", lambda: FakeHealth())
    monkeypatch.setattr(main_mod, "_connector_diagnostics", lambda: {"ok": False, "checks": {}})
    monkeypatch.setattr(main_mod, "_workspace_health_snapshot", lambda root: {
        "ok": False,
        "workspace_root": str(root),
        "workspace_root_exists": False,
        "disk_free_mb": 10,
        "min_disk_free_mb": 256,
        "writable_hint": False,
    })
    monkeypatch.setattr(main_mod, "_queue_health_snapshot", lambda session: {
        "ok": False,
        "pending": 500,
        "failed": 10,
        "dead_letter": 4,
        "detail": "job failures or dead letters present",
    })

    response = client.get("/system/preflight")
    assert response.status_code == 200
    payload = response.json()
    assert payload["llm"]["ok"] is False
    assert payload["workspace"]["ok"] is False
    assert payload["queue"]["ok"] is False
    assert payload["readiness"]["level"] == "blocked"

def test_online_tools_endpoint() -> None:
    response = client.get("/system/online-tools")
    assert response.status_code == 200
    payload = response.json()
    assert "public_base_url" in payload
    assert "trusted_hosts" in payload
    assert "alert_webhook" in payload


def test_run_and_get_report_include_structured_sections() -> None:
    created = client.post("/investigations", json={"topic": "Port outage"}, headers=AUTH)
    investigation_id = created.json()["id"]

    ingest = client.post(
        f"/investigations/{investigation_id}/ingest/simulated",
        json={"batch_size": 5, "include_noise": True},
        headers=AUTH,
    )
    assert ingest.status_code == 200

    run = client.post(f"/investigations/{investigation_id}/run", headers=AUTH)
    assert run.status_code == 200
    run_payload = run.json()
    assert "report_sections" in run_payload
    assert set(run_payload["report_sections"].keys()) == {"likely_true", "disputed", "unknown", "monitor_next"}

    report = client.get(f"/reports/{investigation_id}", headers=AUTH)
    assert report.status_code == 200
    report_payload = report.json()
    assert "report_sections" in report_payload
    assert set(report_payload["report_sections"].keys()) == {"likely_true", "disputed", "unknown", "monitor_next"}


def test_research_sources_discovery_and_alerting_flow(monkeypatch) -> None:
    import sts_monitor.main as main_mod

    monkeypatch.setattr(main_mod, "send_alert_webhook", lambda **kwargs: {"sent": True, "status": "http-200"})

    source_created = client.post(
        "/research/sources",
        json={
            "name": "trusted-rss-source",
            "source_type": "rss",
            "base_url": "https://example.com/feed",
            "trust_score": 0.7,
            "tags": ["regional", "trusted"],
        },
        headers=AUTH,
    )
    assert source_created.status_code == 200

    sources = client.get("/research/sources", headers=AUTH)
    assert sources.status_code == 200
    assert any(item["name"] == "trusted-rss-source" for item in sources.json())

    created = client.post("/investigations", json={"topic": "City protest"}, headers=AUTH)
    investigation_id = created.json()["id"]

    ingest = client.post(
        f"/investigations/{investigation_id}/ingest/simulated",
        json={"batch_size": 30, "include_noise": True},
        headers=AUTH,
    )
    assert ingest.status_code == 200

    discovery = client.post(
        f"/investigations/{investigation_id}/discovery",
        json={"use_llm": False},
        headers=AUTH,
    )
    assert discovery.status_code == 200
    payload = discovery.json()
    assert payload["observation_count"] > 0
    assert isinstance(payload["top_terms"], list)

    rule = client.post(
        "/alerts/rules",
        json={
            "investigation_id": investigation_id,
            "name": "low-threshold-dispute-check",
            "min_observations": 5,
            "min_disputed_claims": 0,
            "cooldown_seconds": 60,
            "active": True,
        },
        headers=AUTH,
    )
    assert rule.status_code == 200

    eval_response = client.post(f"/alerts/evaluate/{investigation_id}", headers=AUTH)
    assert eval_response.status_code == 200
    assert eval_response.json()["triggered"] >= 1
    assert eval_response.json()["events"][0]["webhook"]["sent"] is True

    events = client.get(f"/alerts/events/{investigation_id}", headers=AUTH)
    assert events.status_code == 200
    assert len(events.json()) >= 1

    summary = client.get("/dashboard/summary", headers=AUTH)
    assert summary.status_code == 200
    assert "alert_rules" in summary.json()
    assert "alert_events" in summary.json()


def test_investigation_update_and_observation_filters_and_rss() -> None:
    created = client.post(
        "/investigations",
        json={"topic": "Filter test", "priority": 90, "owner": "analyst-a", "status": "open"},
        headers=AUTH,
    )
    assert created.status_code == 200
    investigation_id = created.json()["id"]
    assert created.json()["priority"] == 90

    updated = client.patch(
        f"/investigations/{investigation_id}",
        json={"status": "monitoring", "owner": "analyst-b", "priority": 75},
        headers=AUTH,
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "monitoring"
    assert updated.json()["owner"] == "analyst-b"

    ingest = client.post(
        f"/investigations/{investigation_id}/ingest/simulated",
        json={"batch_size": 25, "include_noise": True},
        headers=AUTH,
    )
    assert ingest.status_code == 200

    filtered = client.get(
        f"/investigations/{investigation_id}/observations?min_reliability=0.8&limit=50",
        headers=AUTH,
    )
    assert filtered.status_code == 200
    assert all(item["reliability_hint"] >= 0.8 for item in filtered.json())

    run = client.post(f"/investigations/{investigation_id}/run", headers=AUTH)
    assert run.status_code == 200

    claims = client.get(f"/investigations/{investigation_id}/claims", headers=AUTH)
    assert claims.status_code == 200
    assert len(claims.json()) >= 1
    first_claim_id = claims.json()[0]["id"]

    evidence = client.get(f"/claims/{first_claim_id}/evidence", headers=AUTH)
    assert evidence.status_code == 200

    summary = client.get("/dashboard/summary", headers=AUTH)
    assert summary.status_code == 200
    assert "claims" in summary.json()
    assert "claim_evidence" in summary.json()

    rss = client.get(f"/investigations/{investigation_id}/feed.rss", headers=AUTH)
    assert rss.status_code == 200
    assert "application/rss+xml" in rss.headers["content-type"]
    assert "<rss" in rss.text


def test_admin_api_key_lifecycle_and_audit_logs() -> None:
    created_key = client.post(
        "/admin/api-keys",
        json={"label": "ops-analyst", "role": "analyst"},
        headers=AUTH,
    )
    assert created_key.status_code == 200
    payload = created_key.json()
    assert payload["label"] == "ops-analyst"
    key_id = payload["id"]
    derived_auth = {"X-API-Key": payload["api_key"]}

    inv = client.post("/investigations", json={"topic": "Audit run"}, headers=derived_auth)
    assert inv.status_code == 200

    logs = client.get("/audit/logs", headers=AUTH)
    assert logs.status_code == 200
    assert any(item["action"] == "investigation.create" for item in logs.json())

    revoked = client.post(f"/admin/api-keys/{key_id}/revoke", headers=AUTH)
    assert revoked.status_code == 200
    assert revoked.json()["active"] is False

    denied = client.get("/investigations", headers=derived_auth)
    assert denied.status_code == 401


def test_local_json_ingest_and_report_validation_endpoint() -> None:
    created = client.post("/investigations", json={"topic": "Local import"}, headers=AUTH)
    assert created.status_code == 200
    investigation_id = created.json()["id"]

    ingest = client.post(
        f"/investigations/{investigation_id}/ingest/local-json",
        json={
            "observations": [
                {
                    "source": "local:file-a",
                    "claim": "Power outage reported in district 7",
                    "url": "file://snapshot-a.json",
                    "reliability_hint": 0.9,
                },
                {
                    "source": "local:file-b",
                    "claim": "Officials deny widespread outage claims",
                    "url": "file://snapshot-b.json",
                    "reliability_hint": 0.7,
                },
            ]
        },
        headers=AUTH,
    )
    assert ingest.status_code == 200
    assert ingest.json()["ingested_count"] == 2

    run = client.post(f"/investigations/{investigation_id}/run", headers=AUTH)
    assert run.status_code == 200
    assert "lineage_validation" in run.json()

    validation = client.get(f"/reports/{investigation_id}/validation", headers=AUTH)
    assert validation.status_code == 200
    assert "validation" in validation.json()


def test_viewer_role_cannot_mutate() -> None:
    new_key = client.post(
        "/admin/api-keys",
        json={"label": "viewer-user", "role": "viewer"},
        headers=AUTH,
    )
    assert new_key.status_code == 200
    viewer_auth = {"X-API-Key": new_key.json()["api_key"]}

    denied = client.post("/investigations", json={"topic": "Denied for viewer"}, headers=viewer_auth)
    assert denied.status_code == 403


def test_reddit_ingest_endpoint(monkeypatch) -> None:
    created = client.post("/investigations", json={"topic": "Reddit signal"}, headers=AUTH)
    investigation_id = created.json()["id"]

    from sts_monitor.connectors.base import ConnectorResult
    from sts_monitor.connectors.reddit import RedditConnector

    def fake_collect(self, query=None):
        _ = query
        return ConnectorResult(
            connector="reddit",
            observations=[
                Observation(
                    source="reddit:r/news",
                    claim="Witnesses report airport closure",
                    url="https://reddit.test/post-1",
                    captured_at=datetime.now(UTC),
                    reliability_hint=0.58,
                )
            ],
            metadata={"failed_subreddits": [], "subreddit_count": 1, "sort": "new"},
        )

    monkeypatch.setattr(RedditConnector, "collect", fake_collect)

    ingest = client.post(
        f"/investigations/{investigation_id}/ingest/reddit",
        json={"subreddits": ["news"], "query": "airport"},
        headers=AUTH,
    )
    assert ingest.status_code == 200
    body = ingest.json()
    assert body["connector"] == "reddit"
    assert body["ingested_count"] == 1


def test_lineage_gate_can_block_low_coverage(monkeypatch) -> None:
    created = client.post("/investigations", json={"topic": "Lineage gate"}, headers=AUTH)
    investigation_id = created.json()["id"]

    ingest = client.post(
        f"/investigations/{investigation_id}/ingest/local-json",
        json={
            "observations": [
                {
                    "source": "local:file-a",
                    "claim": "Only one known observation",
                    "url": "file://single.json",
                    "reliability_hint": 0.95,
                }
            ]
        },
        headers=AUTH,
    )
    assert ingest.status_code == 200

    from sts_monitor.main import settings

    monkeypatch.setattr(settings, "enforce_report_lineage_gate", True)
    monkeypatch.setattr(settings, "report_min_lineage_coverage", 0.95)

    run = client.post(f"/investigations/{investigation_id}/run", headers=AUTH)
    assert run.status_code == 409
    assert "below required threshold" in run.json()["detail"]


def test_search_profiles_and_query_flow() -> None:
    created = client.post("/investigations", json={"topic": "Searchable topic"}, headers=AUTH)
    assert created.status_code == 200
    investigation_id = created.json()["id"]

    ingest = client.post(
        f"/investigations/{investigation_id}/ingest/local-json",
        json={
            "observations": [
                {
                    "source": "rss:local-feed",
                    "claim": "Airport closure caused major flight disruption",
                    "url": "file://search-1.json",
                    "reliability_hint": 0.9,
                },
                {
                    "source": "reddit:r/news",
                    "claim": "Witnesses report runway reopened",
                    "url": "file://search-2.json",
                    "reliability_hint": 0.7,
                },
            ]
        },
        headers=AUTH,
    )
    assert ingest.status_code == 200

    run = client.post(f"/investigations/{investigation_id}/run", headers=AUTH)
    assert run.status_code == 200

    profile = client.post(
        "/search/profiles",
        json={
            "name": "aviation-watch",
            "investigation_id": investigation_id,
            "include_terms": ["airport"],
            "exclude_terms": ["rumor"],
            "synonyms": {"airport": ["flight", "runway"]},
        },
        headers=AUTH,
    )
    assert profile.status_code == 200

    listed = client.get(f"/search/profiles?investigation_id={investigation_id}", headers=AUTH)
    assert listed.status_code == 200
    assert any(item["name"] == "aviation-watch" for item in listed.json())

    searched = client.post(
        "/search/query",
        json={"query": "airport disruption", "profile_name": "aviation-watch", "investigation_id": investigation_id},
        headers=AUTH,
    )
    assert searched.status_code == 200
    body = searched.json()
    assert body["matched"] >= 1
    assert "facets" in body
    assert any(item["kind"] in {"observation", "claim"} for item in body["results"])


def test_search_suggest_and_kind_toggles() -> None:
    created = client.post("/investigations", json={"topic": "Suggest topic"}, headers=AUTH)
    assert created.status_code == 200
    investigation_id = created.json()["id"]

    ingest = client.post(
        f"/investigations/{investigation_id}/ingest/local-json",
        json={
            "observations": [
                {
                    "source": "rss:city-desk",
                    "claim": "Seismic tremor reported near central district",
                    "url": "file://suggest-1.json",
                    "reliability_hint": 0.8,
                }
            ]
        },
        headers=AUTH,
    )
    assert ingest.status_code == 200

    run = client.post(f"/investigations/{investigation_id}/run", headers=AUTH)
    assert run.status_code == 200

    suggest = client.get(f"/search/suggest?q=earthquake&investigation_id={investigation_id}", headers=AUTH)
    assert suggest.status_code == 200
    assert suggest.json()["count"] >= 1

    claims_only = client.post(
        "/search/query",
        json={
            "query": "seismic",
            "investigation_id": investigation_id,
            "include_observations": False,
            "include_claims": True,
            "min_score": 0.0,
        },
        headers=AUTH,
    )
    assert claims_only.status_code == 200
    assert all(item["kind"] == "claim" for item in claims_only.json()["results"])


def test_related_investigations_endpoint() -> None:
    created_a = client.post("/investigations", json={"topic": "Airport status"}, headers=AUTH)
    created_b = client.post("/investigations", json={"topic": "River flooding"}, headers=AUTH)
    assert created_a.status_code == 200
    assert created_b.status_code == 200

    id_a = created_a.json()["id"]
    id_b = created_b.json()["id"]

    ing_a = client.post(
        f"/investigations/{id_a}/ingest/local-json",
        json={
            "observations": [
                {
                    "source": "rss:airport",
                    "claim": "Airport runway closed after weather disruption",
                    "url": "file://rel-a-1",
                    "reliability_hint": 0.85,
                }
            ]
        },
        headers=AUTH,
    )
    ing_b = client.post(
        f"/investigations/{id_b}/ingest/local-json",
        json={
            "observations": [
                {
                    "source": "rss:flood",
                    "claim": "River flooding continues near downtown",
                    "url": "file://rel-b-1",
                    "reliability_hint": 0.85,
                }
            ]
        },
        headers=AUTH,
    )
    assert ing_a.status_code == 200
    assert ing_b.status_code == 200

    run_a = client.post(f"/investigations/{id_a}/run", headers=AUTH)
    run_b = client.post(f"/investigations/{id_b}/run", headers=AUTH)
    assert run_a.status_code == 200
    assert run_b.status_code == 200

    related = client.post("/search/related-investigations", json={"query": "airport runway", "limit": 5}, headers=AUTH)
    assert related.status_code == 200
    body = related.json()
    assert body["count"] >= 1
    assert body["investigations"][0]["topic"] == "Airport status"


def test_run_with_llm_structured_summary_contract(monkeypatch) -> None:
    created = client.post("/investigations", json={"topic": "Bridge closure"}, headers=AUTH)
    investigation_id = created.json()["id"]

    ingest = client.post(
        f"/investigations/{investigation_id}/ingest/simulated",
        json={"batch_size": 5, "include_noise": False},
        headers=AUTH,
    )
    assert ingest.status_code == 200

    from sts_monitor.main import llm_client

    valid_payload = {
        "topic": "Bridge closure",
        "overall_assessment": "Road access remains disrupted with ongoing mitigation.",
        "overall_confidence": 0.72,
        "key_claims": [
            {
                "statement": "Main bridge lanes are closed.",
                "status": "supported",
                "confidence": 0.8,
                "evidence": [
                    {
                        "source": "simulated:source-1",
                        "url": "https://simulated.local/incident/1",
                        "captured_at": "2026-01-01T00:00:00Z",
                    }
                ],
            }
        ],
        "disputed_claims": [],
        "gaps": ["Need official reopening ETA."],
        "next_actions": ["Recheck transport authority bulletin in 30 minutes."],
    }

    monkeypatch.setattr(llm_client, "summarize", lambda prompt: json.dumps(valid_payload))

    run = client.post(f"/investigations/{investigation_id}/run", json={"use_llm": True}, headers=AUTH)
    assert run.status_code == 200
    body = run.json()
    assert body["llm_schema_valid"] is True
    assert body["llm_fallback_used"] is False
    assert body["llm_structured"]["overall_confidence"] == 0.72


def test_run_with_llm_invalid_schema_falls_back(monkeypatch) -> None:
    created = client.post("/investigations", json={"topic": "Bridge closure"}, headers=AUTH)
    investigation_id = created.json()["id"]

    ingest = client.post(
        f"/investigations/{investigation_id}/ingest/simulated",
        json={"batch_size": 5, "include_noise": False},
        headers=AUTH,
    )
    assert ingest.status_code == 200

    from sts_monitor.main import llm_client

    monkeypatch.setattr(llm_client, "summarize", lambda prompt: '{"not":"schema"}')

    run = client.post(f"/investigations/{investigation_id}/run", json={"use_llm": True}, headers=AUTH)
    assert run.status_code == 200
    body = run.json()
    assert body["llm_schema_valid"] is False
    assert body["llm_fallback_used"] is True
    assert body["llm_structured"] is None
    assert "invalid schema" in body["summary"].lower()




def test_run_with_llm_non_json_falls_back(monkeypatch) -> None:
    created = client.post("/investigations", json={"topic": "Tunnel closure"}, headers=AUTH)
    investigation_id = created.json()["id"]

    ingest = client.post(
        f"/investigations/{investigation_id}/ingest/simulated",
        json={"batch_size": 5, "include_noise": False},
        headers=AUTH,
    )
    assert ingest.status_code == 200

    from sts_monitor.main import llm_client

    monkeypatch.setattr(llm_client, "summarize", lambda prompt: "plain text, not json")

    run = client.post(f"/investigations/{investigation_id}/run", json={"use_llm": True}, headers=AUTH)
    assert run.status_code == 200
    body = run.json()
    assert body["llm_schema_valid"] is False
    assert body["llm_fallback_used"] is True
    assert body["llm_schema_error"] == "llm response was not valid JSON"


def test_run_with_llm_bad_confidence_falls_back(monkeypatch) -> None:
    created = client.post("/investigations", json={"topic": "Tunnel closure"}, headers=AUTH)
    investigation_id = created.json()["id"]

    ingest = client.post(
        f"/investigations/{investigation_id}/ingest/simulated",
        json={"batch_size": 5, "include_noise": False},
        headers=AUTH,
    )
    assert ingest.status_code == 200

    from sts_monitor.main import llm_client

    invalid_payload = {
        "topic": "Tunnel closure",
        "overall_assessment": "Partial closure still active",
        "overall_confidence": 1.7,
        "key_claims": [
            {
                "statement": "Northbound lane is blocked.",
                "status": "supported",
                "confidence": 0.8,
                "evidence": [
                    {
                        "source": "simulated:source-1",
                        "url": "https://simulated.local/incident/1",
                        "captured_at": "2026-01-01T00:00:00Z",
                    }
                ],
            }
        ],
        "disputed_claims": [],
        "gaps": [],
        "next_actions": [],
    }

    monkeypatch.setattr(llm_client, "summarize", lambda prompt: json.dumps(invalid_payload))

    run = client.post(f"/investigations/{investigation_id}/run", json={"use_llm": True}, headers=AUTH)
    assert run.status_code == 200
    body = run.json()
    assert body["llm_schema_valid"] is False
    assert body["llm_fallback_used"] is True
    assert body["llm_structured"] is None

def test_preflight_exposes_queue_and_workspace_details() -> None:
    response = client.get("/system/preflight")
    assert response.status_code == 200
    payload = response.json()
    assert "queue" in payload
    assert "workspace" in payload
    assert payload["queue"]["pending"] >= 0
    assert payload["workspace"]["min_disk_free_mb"] == 256
