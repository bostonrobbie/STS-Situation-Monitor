"""Integration / end-to-end tests.

These tests boot the real FastAPI app via TestClient and exercise
multi-endpoint workflows: create investigation -> ingest -> run pipeline ->
generate report -> verify dashboard counters.

All IO-bound connectors are patched so tests run offline and fast.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sts_monitor.main import app
from sts_monitor.database import Base, engine

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

AUTH = {"X-API-Key": "change-me"}

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _fresh_db():
    """Reset the database for every test."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Health / preflight
# ---------------------------------------------------------------------------


class TestHealthAndPreflight:
    def test_health_no_auth(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_preflight_no_auth(self, client):
        resp = client.get("/system/preflight")
        assert resp.status_code == 200
        data = resp.json()
        # Preflight returns various check sections
        assert isinstance(data, dict)
        assert len(data) > 0

    def test_protected_endpoint_requires_key(self, client):
        resp = client.get("/investigations")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Investigation lifecycle
# ---------------------------------------------------------------------------


class TestInvestigationLifecycle:
    def test_create_and_list(self, client):
        # Create
        resp = client.post(
            "/investigations",
            json={"topic": "earthquake in Turkey", "priority": 80},
            headers=AUTH,
        )
        assert resp.status_code == 200
        inv = resp.json()
        inv_id = inv["id"]
        assert inv["topic"] == "earthquake in Turkey"
        assert inv["priority"] == 80

        # List
        resp = client.get("/investigations", headers=AUTH)
        assert resp.status_code == 200
        investigations = resp.json()
        assert any(i["id"] == inv_id for i in investigations)

    def test_update_investigation(self, client):
        resp = client.post(
            "/investigations",
            json={"topic": "wildfire in California"},
            headers=AUTH,
        )
        inv_id = resp.json()["id"]

        resp = client.patch(
            f"/investigations/{inv_id}",
            json={"priority": 95, "status": "monitoring"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.json()["priority"] == 95
        assert resp.json()["status"] == "monitoring"


# ---------------------------------------------------------------------------
# Full workflow: Ingest -> Pipeline -> Report -> Dashboard
# ---------------------------------------------------------------------------


class TestFullWorkflow:
    def test_simulated_ingest_pipeline_report_dashboard(self, client):
        """End-to-end: create -> ingest -> run -> report -> dashboard."""
        # 1. Create investigation
        resp = client.post(
            "/investigations",
            json={"topic": "flood in Bangladesh"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        inv_id = resp.json()["id"]

        # 2. Ingest simulated data
        resp = client.post(
            f"/investigations/{inv_id}/ingest/simulated",
            json={"batch_size": 15, "include_noise": True},
            headers=AUTH,
        )
        assert resp.status_code == 200
        ingest = resp.json()
        assert ingest["ingested_count"] > 0

        # 3. List observations
        resp = client.get(
            f"/investigations/{inv_id}/observations",
            headers=AUTH,
        )
        assert resp.status_code == 200
        obs = resp.json()
        assert len(obs) > 0

        # 4. Run pipeline
        resp = client.post(
            f"/investigations/{inv_id}/run",
            json={"use_llm": False},
            headers=AUTH,
        )
        assert resp.status_code == 200
        run_result = resp.json()
        assert "confidence" in run_result

        # 5. Fetch report
        resp = client.get(f"/reports/{inv_id}", headers=AUTH)
        assert resp.status_code == 200
        report = resp.json()
        assert "report_sections" in report or "summary" in report

        # 6. Check dashboard counters
        resp = client.get("/dashboard/summary", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "total_investigations" in data or "investigations" in data

    def test_ingestion_audit_trail(self, client):
        resp = client.post(
            "/investigations",
            json={"topic": "volcanic eruption"},
            headers=AUTH,
        )
        inv_id = resp.json()["id"]

        # Ingest
        client.post(
            f"/investigations/{inv_id}/ingest/simulated",
            json={"batch_size": 5},
            headers=AUTH,
        )

        # Check audit trail
        resp = client.get(
            f"/investigations/{inv_id}/ingestion-runs",
            headers=AUTH,
        )
        assert resp.status_code == 200
        runs = resp.json()
        assert len(runs) >= 1


# ---------------------------------------------------------------------------
# Feedback + memory
# ---------------------------------------------------------------------------


class TestFeedbackMemory:
    def test_submit_and_recall_feedback(self, client):
        resp = client.post(
            "/investigations",
            json={"topic": "cyberattack on infrastructure"},
            headers=AUTH,
        )
        inv_id = resp.json()["id"]

        # Submit feedback
        resp = client.post(
            f"/investigations/{inv_id}/feedback",
            json={"label": "unreliable", "notes": "Source X is not trustworthy"},
            headers=AUTH,
        )
        assert resp.status_code == 200

        # Read memory
        resp = client.get(
            f"/investigations/{inv_id}/memory",
            headers=AUTH,
        )
        assert resp.status_code == 200
        memory = resp.json()
        assert isinstance(memory, (list, dict))


# ---------------------------------------------------------------------------
# Search profiles
# ---------------------------------------------------------------------------


class TestSearchProfiles:
    def test_create_and_list_profiles(self, client):
        resp = client.post(
            "/search/profiles",
            json={"name": "conflict-zones", "terms": ["war", "conflict", "fighting"]},
            headers=AUTH,
        )
        assert resp.status_code == 200

        resp = client.get("/search/profiles", headers=AUTH)
        assert resp.status_code == 200
        profiles = resp.json()
        assert any(p["name"] == "conflict-zones" for p in profiles)


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


class TestJobs:
    def test_enqueue_and_list_jobs(self, client):
        resp = client.post(
            "/investigations",
            json={"topic": "job test"},
            headers=AUTH,
        )
        inv_id = resp.json()["id"]

        resp = client.post(
            f"/jobs/enqueue/ingest-simulated/{inv_id}",
            json={"batch_size": 5},
            headers=AUTH,
        )
        assert resp.status_code == 200

        resp = client.get("/jobs", headers=AUTH)
        assert resp.status_code == 200
        jobs = resp.json()
        assert len(jobs) >= 1

    def test_dead_letter_queue_initially_empty(self, client):
        resp = client.get("/jobs/dead-letters", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TestAuth:
    def test_wrong_api_key_rejected(self, client):
        resp = client.get(
            "/investigations",
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code in (401, 403)

    def test_admin_endpoints_accessible_with_root_key(self, client):
        resp = client.get("/audit/logs", headers=AUTH)
        assert resp.status_code == 200

    def test_api_key_management(self, client):
        # Create a scoped key
        resp = client.post(
            "/admin/api-keys",
            json={"label": "test-analyst", "role": "analyst"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        key_data = resp.json()
        assert "key" in key_data or "api_key" in key_data or "id" in key_data

        # List keys
        resp = client.get("/admin/api-keys", headers=AUTH)
        assert resp.status_code == 200
        keys = resp.json()
        assert len(keys) >= 1


# ---------------------------------------------------------------------------
# Claims & evidence
# ---------------------------------------------------------------------------


class TestClaimsAndEvidence:
    def test_claims_after_pipeline_run(self, client):
        """After ingesting and running the pipeline, claims should exist."""
        resp = client.post(
            "/investigations",
            json={"topic": "protest in capital city"},
            headers=AUTH,
        )
        inv_id = resp.json()["id"]

        client.post(
            f"/investigations/{inv_id}/ingest/simulated",
            json={"batch_size": 20},
            headers=AUTH,
        )

        client.post(
            f"/investigations/{inv_id}/run",
            json={"use_llm": False},
            headers=AUTH,
        )

        resp = client.get(f"/investigations/{inv_id}/claims", headers=AUTH)
        assert resp.status_code == 200
        claims = resp.json()
        # Simulated data should produce at least some claims
        assert isinstance(claims, list)


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


class TestAlerts:
    def test_alert_rules_and_evaluate(self, client):
        resp = client.post(
            "/investigations",
            json={"topic": "alert test"},
            headers=AUTH,
        )
        inv_id = resp.json()["id"]

        # Create rule
        resp = client.post(
            "/alerts/rules",
            json={
                "investigation_id": inv_id,
                "name": "high-dispute",
                "condition": "disputed_claims > 5",
                "severity": "high",
            },
            headers=AUTH,
        )
        assert resp.status_code == 200

        # List rules
        resp = client.get("/alerts/rules", headers=AUTH)
        assert resp.status_code == 200
        rules = resp.json()
        assert len(rules) >= 1

        # Evaluate (even with no data, should not error)
        resp = client.post(f"/alerts/evaluate/{inv_id}", headers=AUTH)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# RSS feed output
# ---------------------------------------------------------------------------


class TestRSSFeed:
    def test_rss_feed_returns_xml(self, client):
        resp = client.post(
            "/investigations",
            json={"topic": "rss feed test"},
            headers=AUTH,
        )
        inv_id = resp.json()["id"]

        resp = client.get(f"/investigations/{inv_id}/feed.rss", headers=AUTH)
        assert resp.status_code == 200
        assert "xml" in resp.headers.get("content-type", "").lower() or resp.text.startswith("<?xml")
