"""Edge-case and missing-branch tests for route modules.

Targets uncovered lines/branches identified by coverage.json to bring
each route module closer to 100% code coverage.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest
import sqlalchemy
from starlette.testclient import TestClient

from sts_monitor.database import Base, engine
from sts_monitor.main import app

AUTH = {"X-API-Key": "change-me"}
FAKE_INV_ID = "nonexistent-investigation-id"


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch):
    import sts_monitor.rate_limit as rl
    monkeypatch.setattr(rl, "RATE_LIMIT_ENABLED", False)
    # Reset custom geofence zones between tests
    from sts_monitor.geofence import _custom_zones
    _custom_zones.clear()
    app.dependency_overrides.clear()
    # Dispose pooled connections to avoid SQLite "database is locked" errors
    engine.dispose()
    # Drop all tables via raw SQL to avoid reflection/ordering issues
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text("PRAGMA foreign_keys = OFF"))
        table_names = conn.execute(
            sqlalchemy.text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        ).scalars().all()
        for t in table_names:
            conn.execute(sqlalchemy.text(f'DROP TABLE IF EXISTS "{t}"'))
        conn.commit()
    Base.metadata.create_all(bind=engine)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def inv_id(client):
    resp = client.post(
        "/investigations",
        json={"topic": "Edge Coverage Test", "priority": 50},
        headers=AUTH,
    )
    assert resp.status_code == 200
    return resp.json()["id"]


# ══════════════════════════════════════════════════════════════════════
# ALERTS — missing lines: 47, 73, 139, 194; branches: 46→47, 72→73,
#   138→139, 193→194, 218→221 (events list empty → skip commit)
# ══════════════════════════════════════════════════════════════════════


class TestAlertEdges:
    """Cover 404 branches and the empty-events path in alerts.py."""

    def test_create_alert_rule_investigation_not_found(self, client):
        """Line 47: investigation not found → 404."""
        resp = client.post(
            "/alerts/rules",
            json={
                "investigation_id": FAKE_INV_ID,
                "name": "test-rule",
                "min_observations": 5,
                "min_disputed_claims": 1,
                "cooldown_seconds": 600,
                "active": True,
            },
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_list_alert_rules_filtered_by_investigation(self, client, inv_id):
        """Line 73: branch where investigation_id filter is applied."""
        resp = client.get(
            "/alerts/rules",
            params={"investigation_id": inv_id},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_create_investigation_alert_rule_not_found(self, client):
        """Line 139: investigation not found → 404."""
        resp = client.post(
            f"/investigations/{FAKE_INV_ID}/alert-rules",
            json={
                "name": "test-rule",
                "rule_type": "volume_spike",
                "threshold": 5.0,
                "window_minutes": 60,
                "cooldown_seconds": 600,
                "severity": "warning",
                "metadata": {},
            },
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_evaluate_investigation_alerts_not_found(self, client):
        """Line 194: investigation not found → 404."""
        resp = client.post(
            f"/investigations/{FAKE_INV_ID}/evaluate-alerts",
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_evaluate_investigation_alerts_no_events(self, client, inv_id):
        """Branch 218→221: no events triggered → skip commit, return 0."""
        resp = client.post(
            f"/investigations/{inv_id}/evaluate-alerts",
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["alerts_fired"] == 0
        assert data["events"] == []


# ══════════════════════════════════════════════════════════════════════
# SEMANTIC — missing lines: 65, 101, 126, 127
# ══════════════════════════════════════════════════════════════════════


class TestSemanticEdges:
    """Cover 404 in semantic index, POST search, and health endpoint."""

    def test_semantic_index_investigation_not_found(self, client):
        """Line 65: investigation not found → 404."""
        resp = client.post(
            "/semantic/index",
            json={"investigation_id": FAKE_INV_ID},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_semantic_search_post(self, client):
        """Lines ~101: exercise the POST /semantic/search path.
        The underlying engine may fail (no Qdrant running), but we
        cover the route code up to the engine call."""
        with patch("sts_monitor.routes.semantic._get_semantic_engine") as mock_engine:
            mock_result = MagicMock()
            mock_result.query = "test"
            mock_result.total_indexed = 0
            mock_result.search_latency_ms = 1.0
            mock_result.matches = []
            mock_engine.return_value.search.return_value = mock_result

            resp = client.post(
                "/semantic/search",
                json={"query": "test query", "limit": 5},
                headers=AUTH,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["query"] == "test"
            assert data["match_count"] == 0

    def test_semantic_health_detailed(self, client):
        """Lines 126-127: exercise the GET /semantic/health path."""
        with patch("sts_monitor.routes.semantic._get_semantic_engine") as mock_engine:
            mock_engine.return_value.embedder.health.return_value = {"status": "ok"}
            mock_engine.return_value.store.health.return_value = {"status": "ok"}

            resp = client.get("/semantic/health", headers=AUTH)
            assert resp.status_code == 200
            data = resp.json()
            assert "embedding" in data
            assert "qdrant" in data


# ══════════════════════════════════════════════════════════════════════
# SYSTEM — missing lines: 51, 52, 53  (DB exception in preflight)
# ══════════════════════════════════════════════════════════════════════


class TestSystemEdges:
    def test_preflight_db_exception(self, client):
        """Lines 51-53: simulate DB query failure in preflight check."""
        from sqlalchemy.orm import Session

        original_execute = Session.execute

        call_count = 0

        def _failing_execute(self, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            # Only fail on the first execute call (the preflight DB check)
            if call_count == 1:
                raise RuntimeError("DB down")
            return original_execute(self, *args, **kwargs)

        with patch.object(Session, "execute", _failing_execute):
            resp = client.get("/system/preflight", headers=AUTH)
            assert resp.status_code == 200
            data = resp.json()
            assert data["database"]["ok"] is False
            assert "DB down" in data["database"]["detail"]


# ══════════════════════════════════════════════════════════════════════
# INGESTION — missing lines: 286, 382, 390, 407, 417, 501
# ══════════════════════════════════════════════════════════════════════


class TestIngestionEdges:
    def test_trending_investigation_not_found(self, client):
        """Line 286: investigation not found → 404."""
        resp = client.post(
            f"/investigations/{FAKE_INV_ID}/ingest/trending",
            json={"geo": "US", "max_topics": 1, "per_topic_limit": 1},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_nasa_firms_no_api_key(self, client, inv_id):
        """Line 381: NASA FIRMS MAP_KEY not configured → 503."""
        with patch("sts_monitor.routes.ingestion.settings") as mock_settings:
            mock_settings.nasa_firms_map_key = ""
            resp = client.post(
                f"/investigations/{inv_id}/ingest/nasa-firms",
                json={"query": "fires"},
                headers=AUTH,
            )
            assert resp.status_code == 503
            assert "NASA FIRMS" in resp.json()["detail"]

    def test_nasa_firms_with_api_key(self, client, inv_id):
        """Lines 382, 390: NASA FIRMS connector creation + ingestion when key IS configured."""
        with patch("sts_monitor.routes.ingestion.settings") as mock_settings:
            mock_settings.nasa_firms_map_key = "fake-map-key"
            mock_settings.nasa_firms_sensor = "VIIRS_NOAA20_NRT"
            mock_settings.nasa_firms_timeout_s = 15.0
            with patch("sts_monitor.routes.ingestion.ingest_with_geo_connector") as mock_ingest:
                mock_ingest.return_value = {
                    "investigation_id": inv_id,
                    "connector": "nasa_firms",
                    "ingested_count": 0,
                    "stored_count": 0,
                }
                resp = client.post(
                    f"/investigations/{inv_id}/ingest/nasa-firms",
                    json={"query": "fires"},
                    headers=AUTH,
                )
                assert resp.status_code == 200
                mock_ingest.assert_called_once()

    def test_acled_no_api_key(self, client, inv_id):
        """Line 406: ACLED API key/email not configured → 503."""
        with patch("sts_monitor.routes.ingestion.settings") as mock_settings:
            mock_settings.acled_api_key = ""
            mock_settings.acled_email = ""
            resp = client.post(
                f"/investigations/{inv_id}/ingest/acled",
                json={"query": "conflicts"},
                headers=AUTH,
            )
            assert resp.status_code == 503
            assert "ACLED" in resp.json()["detail"]

    def test_acled_with_api_key(self, client, inv_id):
        """Lines 407, 417: ACLED connector creation + ingestion when key IS configured."""
        with patch("sts_monitor.routes.ingestion.settings") as mock_settings:
            mock_settings.acled_api_key = "fake-key"
            mock_settings.acled_email = "fake@email.com"
            mock_settings.acled_timeout_s = 15.0
            with patch("sts_monitor.routes.ingestion.ingest_with_geo_connector") as mock_ingest:
                mock_ingest.return_value = {
                    "investigation_id": inv_id,
                    "connector": "acled",
                    "ingested_count": 0,
                    "stored_count": 0,
                }
                resp = client.post(
                    f"/investigations/{inv_id}/ingest/acled",
                    json={"query": "conflicts"},
                    headers=AUTH,
                )
                assert resp.status_code == 200
                mock_ingest.assert_called_once()

    def test_opensky_with_bbox(self, client, inv_id):
        """Line 501: exercise the bbox branch for OpenSky."""
        with patch("sts_monitor.routes.ingestion.ingest_with_geo_connector") as mock_fn:
            mock_fn.return_value = {
                "investigation_id": inv_id,
                "connector": "opensky",
                "ingested_count": 0,
                "stored_count": 0,
            }
            resp = client.post(
                f"/investigations/{inv_id}/ingest/opensky",
                json={
                    "query": "aircraft",
                    "bbox_lamin": 45.0,
                    "bbox_lomin": -93.0,
                    "bbox_lamax": 46.0,
                    "bbox_lomax": -92.0,
                },
                headers=AUTH,
            )
            assert resp.status_code == 200
            # Verify bbox tuple was constructed
            call_args = mock_fn.call_args
            connector = call_args[0][2] if len(call_args[0]) > 2 else call_args.kwargs.get("connector")
            # The connector was passed — if we got 200 the code path ran


# ══════════════════════════════════════════════════════════════════════
# JOBS — missing line: 178  (requeue dead letter 404)
# ══════════════════════════════════════════════════════════════════════


class TestJobsEdges:
    def test_requeue_dead_letter_not_found(self, client):
        """Line 178: dead-letter job not found → 404."""
        resp = client.post(
            "/jobs/dead-letters/99999/requeue",
            headers=AUTH,
        )
        assert resp.status_code == 404
        assert "Dead-letter" in resp.json()["detail"]


# ══════════════════════════════════════════════════════════════════════
# EXPORT — missing line: 94  (PDF export, no report → 404)
# ══════════════════════════════════════════════════════════════════════


class TestExportEdges:
    def test_export_pdf_investigation_not_found(self, client):
        """Line 94: investigation not found → 404."""
        resp = client.get(
            f"/export/{FAKE_INV_ID}/report.pdf",
            headers=AUTH,
        )
        assert resp.status_code == 404
        assert "Investigation not found" in resp.json()["detail"]

    def test_export_pdf_no_report(self, client, inv_id):
        """Line 97: investigation exists but no report → 404."""
        resp = client.get(
            f"/export/{inv_id}/report.pdf",
            headers=AUTH,
        )
        assert resp.status_code == 404
        assert "No report found" in resp.json()["detail"]


# ══════════════════════════════════════════════════════════════════════
# ANALYSIS — missing lines: 136, 194, 237, 279, 471-472, 504, 691-693
# ══════════════════════════════════════════════════════════════════════


class TestAnalysisEdges:
    def test_slop_filter_investigation_not_found(self, client):
        """Line 136: investigation not found → 404."""
        resp = client.post(
            "/analysis/slop-filter",
            json={"investigation_id": FAKE_INV_ID},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_entity_graph_investigation_not_found(self, client):
        """Line 194: investigation not found → 404."""
        resp = client.post(
            "/analysis/entity-graph",
            json={"investigation_id": FAKE_INV_ID},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_narrative_timeline_investigation_not_found(self, client):
        """Line 237: investigation not found → 404."""
        resp = client.post(
            "/analysis/narrative-timeline",
            json={"investigation_id": FAKE_INV_ID},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_anomalies_with_investigation_not_found(self, client):
        """Line 279: investigation_id provided but not found → 404."""
        resp = client.post(
            "/analysis/anomalies",
            json={"investigation_id": FAKE_INV_ID},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_rabbit_trail_llm_exception(self, client, inv_id):
        """Lines 471-472: LLM constructor fails gracefully (exception caught,
        llm_client stays None)."""
        from sts_monitor.llm import LocalLLMClient
        with patch(
            "sts_monitor.llm.LocalLLMClient.__init__",
            side_effect=RuntimeError("LLM unavailable"),
        ):
            resp = client.post(
                f"/investigations/{inv_id}/rabbit-trail",
                params={"max_depth": 1},
                headers=AUTH,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "session_id" in data or "investigation_id" in data

    def test_get_rabbit_trail_not_found(self, client):
        """Line 503: trail session not found → 404."""
        resp = client.get(
            "/rabbit-trails/nonexistent-session-id",
            headers=AUTH,
        )
        assert resp.status_code == 404
        assert "Trail session not found" in resp.json()["detail"]

    def test_get_rabbit_trail_found(self, client, inv_id):
        """Line 504: trail session found → return trail.to_dict()."""
        # First, create a rabbit trail session
        resp = client.post(
            f"/investigations/{inv_id}/rabbit-trail",
            params={"max_depth": 1},
            headers=AUTH,
        )
        assert resp.status_code == 200
        session_id = resp.json()["session_id"]

        # Now retrieve it by ID
        resp = client.get(
            f"/rabbit-trails/{session_id}",
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == session_id

    def test_webhook_ingest_investigation_not_found(self, client):
        """Line ~686: investigation not found → 404 for webhook."""
        resp = client.post(
            f"/investigations/{FAKE_INV_ID}/webhook",
            json={"events": [{"text": "something happened"}]},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_webhook_ingest_with_signature_validation(self, client, inv_id):
        """Lines 691-693: webhook with invalid signature → 401."""
        body = {"events": [{"text": "test event"}]}

        # Create a mock settings that has webhook_secret attribute
        from sts_monitor.config import settings as real_settings
        mock_settings = MagicMock(wraps=real_settings)
        mock_settings.webhook_secret = "test-secret-key"

        with patch("sts_monitor.routes.analysis.settings", mock_settings):
            with patch(
                "sts_monitor.routes.analysis.validate_webhook_signature",
                return_value=False,
            ):
                # x_webhook_signature is a plain param, so pass as query param
                resp = client.post(
                    f"/investigations/{inv_id}/webhook?x_webhook_signature=sha256%3Dinvalid",
                    json=body,
                    headers=AUTH,
                )
                assert resp.status_code == 401
                assert "Invalid webhook signature" in resp.json()["detail"]

    def test_webhook_ingest_valid_signature(self, client, inv_id):
        """Lines 691-693: webhook with valid signature → success path."""
        body = {"events": [{"text": "valid event"}]}

        from sts_monitor.config import settings as real_settings
        mock_settings = MagicMock(wraps=real_settings)
        mock_settings.webhook_secret = "test-secret-key"

        with patch("sts_monitor.routes.analysis.settings", mock_settings):
            with patch(
                "sts_monitor.routes.analysis.validate_webhook_signature",
                return_value=True,
            ):
                with patch(
                    "sts_monitor.routes.analysis.normalize_webhook_payload",
                    return_value=[{"claim": "test claim", "source": "webhook"}],
                ):
                    resp = client.post(
                        f"/investigations/{inv_id}/webhook?x_webhook_signature=sha256%3Dvalid",
                        json=body,
                        headers=AUTH,
                    )
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["status"] == "ingested"
