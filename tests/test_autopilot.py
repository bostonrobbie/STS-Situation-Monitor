"""Tests for the autopilot system."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from sts_monitor.database import Base, engine
from sts_monitor.main import app

AUTH = {"X-API-Key": "change-me"}


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch):
    import sts_monitor.rate_limit as rl
    monkeypatch.setattr(rl, "RATE_LIMIT_ENABLED", False)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


@pytest.fixture
def client():
    return TestClient(app)


# ═══════════════════════════════════════════════════════════════════════
# Autopilot Module Unit Tests
# ═══════════════════════════════════════════════════════════════════════

class TestAutopilotModule:
    def test_state_defaults(self):
        from sts_monitor.autopilot import AutopilotState
        state = AutopilotState()
        assert state.running is False
        assert state.total_cycles == 0
        assert state.last_cycle_at is None
        assert state.recent_logs == []

    def test_state_to_dict(self):
        from sts_monitor.autopilot import AutopilotState
        state = AutopilotState()
        d = state.to_dict()
        assert "running" in d
        assert "enabled" in d
        assert "interval_s" in d
        assert "total_cycles" in d
        assert "recent_logs" in d

    def test_state_log_bounded(self):
        from sts_monitor.autopilot import AutopilotState
        state = AutopilotState()
        state.recent_logs = [{"i": i} for i in range(50)]
        d = state.to_dict()
        assert len(d["recent_logs"]) == 20  # capped at 20 in to_dict

    def test_cycle_log_dataclass(self):
        from sts_monitor.autopilot import CycleLog
        log = CycleLog(
            investigation_id="inv-1",
            investigation_topic="test topic",
            started_at="2024-01-01T00:00:00Z",
        )
        assert log.observations_ingested == 0
        assert log.error is None
        assert log.llm_available is False


# ═══════════════════════════════════════════════════════════════════════
# Autopilot API Tests
# ═══════════════════════════════════════════════════════════════════════

class TestAutopilotAPI:
    def test_status_endpoint(self, client):
        resp = client.get("/autopilot/status", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data
        assert "enabled" in data
        assert "interval_s" in data
        assert "total_cycles" in data

    def test_status_requires_auth(self, client):
        resp = client.get("/autopilot/status")
        assert resp.status_code == 401

    def test_start_endpoint(self, client):
        resp = client.post("/autopilot/start", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("started", "already_running")
        # Clean up
        client.post("/autopilot/stop", headers=AUTH)

    def test_stop_endpoint(self, client):
        resp = client.post("/autopilot/stop", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "stopped"
        assert data["running"] is False

    def test_start_stop_cycle(self, client):
        # Start
        resp = client.post("/autopilot/start", headers=AUTH)
        assert resp.status_code == 200

        # Check running
        resp = client.get("/autopilot/status", headers=AUTH)
        data = resp.json()
        assert data["enabled"] is True

        # Stop
        resp = client.post("/autopilot/stop", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["running"] is False

    def test_start_requires_auth(self, client):
        resp = client.post("/autopilot/start")
        assert resp.status_code == 401

    def test_stop_requires_auth(self, client):
        resp = client.post("/autopilot/stop")
        assert resp.status_code == 401

    def test_double_start(self, client):
        """Starting twice is idempotent."""
        resp1 = client.post("/autopilot/start", headers=AUTH)
        resp2 = client.post("/autopilot/start", headers=AUTH)
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        # Both return started (or already_running if loop is active)
        assert resp2.json()["status"] in ("started", "already_running")
        # Clean up
        client.post("/autopilot/stop", headers=AUTH)


# ═══════════════════════════════════════════════════════════════════════
# Autopilot Cycle Execution Tests
# ═══════════════════════════════════════════════════════════════════════

class TestAutopilotCycleExecution:
    def test_run_cycle_for_investigation(self, client):
        """Test that a single cycle can be run for an investigation."""
        from sts_monitor.autopilot import _run_cycle_for_investigation

        # Create an investigation first
        resp = client.post(
            "/investigations",
            json={"topic": "autopilot test topic"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        inv_id = resp.json()["id"]

        # Run a cycle
        log = _run_cycle_for_investigation(inv_id, "autopilot test topic", llm_ok=False)
        assert log.investigation_id == inv_id
        assert log.observations_ingested > 0
        assert log.completed_at is not None
        assert log.duration_s >= 0
        assert log.error is None

    def test_run_cycle_without_llm(self, client):
        """Cycle completes even when LLM is not available."""
        from sts_monitor.autopilot import _run_cycle_for_investigation

        resp = client.post(
            "/investigations",
            json={"topic": "no llm test"},
            headers=AUTH,
        )
        inv_id = resp.json()["id"]

        log = _run_cycle_for_investigation(inv_id, "no llm test", llm_ok=False)
        assert log.error is None
        assert log.llm_available is False
        assert log.observations_ingested > 0

    def test_cycle_creates_observations(self, client):
        """Verify that autopilot cycle actually creates observations in DB."""
        from sts_monitor.autopilot import _run_cycle_for_investigation

        resp = client.post(
            "/investigations",
            json={"topic": "obs creation test"},
            headers=AUTH,
        )
        inv_id = resp.json()["id"]

        log = _run_cycle_for_investigation(inv_id, "obs creation test", llm_ok=False)
        assert log.observations_ingested > 0
        assert log.error is None
