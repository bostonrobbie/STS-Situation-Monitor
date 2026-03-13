"""Comprehensive tests for all new features:
- Export endpoints (CSV, PDF, Markdown)
- JWT auth (register, login, me, token validation)
- Rate limiting middleware
- Plugin system
- Notification system
- WebSocket manager
- Frontend SPA
"""
from __future__ import annotations

import json
import time

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


def _create_inv(client, topic="test topic", **kwargs):
    resp = client.post("/investigations", json={"topic": topic, **kwargs}, headers=AUTH)
    assert resp.status_code == 200
    return resp.json()["id"]


def _ingest_and_run(client, inv_id, batch_size=15):
    resp = client.post(
        f"/investigations/{inv_id}/ingest/simulated",
        json={"batch_size": batch_size, "include_noise": True},
        headers=AUTH,
    )
    assert resp.status_code == 200
    resp = client.post(
        f"/investigations/{inv_id}/run",
        json={"use_llm": False},
        headers=AUTH,
    )
    assert resp.status_code == 200
    return resp.json()


# ═══════════════════════════════════════════════════════════════════════
# Export Endpoints
# ═══════════════════════════════════════════════════════════════════════

class TestExportObservationsCSV:
    def test_basic_export(self, client):
        inv_id = _create_inv(client)
        _ingest_and_run(client, inv_id, batch_size=10)
        resp = client.get(f"/export/{inv_id}/observations.csv", headers=AUTH)
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        assert "attachment" in resp.headers.get("content-disposition", "")
        lines = resp.text.strip().split("\n")
        assert len(lines) >= 2  # header + at least one row
        header = lines[0]
        assert "id" in header
        assert "source" in header
        assert "claim" in header
        assert "url" in header

    def test_export_with_source_filter(self, client):
        inv_id = _create_inv(client)
        _ingest_and_run(client, inv_id, batch_size=20)
        # Unfiltered
        resp1 = client.get(f"/export/{inv_id}/observations.csv", headers=AUTH)
        lines1 = resp1.text.strip().split("\n")
        # Filtered by source (shouldn't match much)
        resp2 = client.get(
            f"/export/{inv_id}/observations.csv?source=nonexistent_source",
            headers=AUTH,
        )
        lines2 = resp2.text.strip().split("\n")
        assert len(lines2) <= len(lines1)

    def test_export_with_reliability_filter(self, client):
        inv_id = _create_inv(client)
        _ingest_and_run(client, inv_id, batch_size=20)
        resp = client.get(
            f"/export/{inv_id}/observations.csv?min_reliability=0.9",
            headers=AUTH,
        )
        assert resp.status_code == 200

    def test_export_nonexistent_investigation(self, client):
        resp = client.get("/export/nonexistent/observations.csv", headers=AUTH)
        assert resp.status_code == 404

    def test_export_empty_investigation(self, client):
        inv_id = _create_inv(client)
        resp = client.get(f"/export/{inv_id}/observations.csv", headers=AUTH)
        assert resp.status_code == 200
        lines = resp.text.strip().split("\n")
        assert len(lines) == 1  # header only

    def test_export_no_auth(self, client):
        inv_id = _create_inv(client)
        resp = client.get(f"/export/{inv_id}/observations.csv")
        assert resp.status_code == 401


class TestExportClaimsCSV:
    def test_basic_export(self, client):
        inv_id = _create_inv(client)
        _ingest_and_run(client, inv_id)
        resp = client.get(f"/export/{inv_id}/claims.csv", headers=AUTH)
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]

    def test_nonexistent(self, client):
        resp = client.get("/export/nonexistent/claims.csv", headers=AUTH)
        assert resp.status_code == 404


class TestExportReportMarkdown:
    def test_basic_export(self, client):
        inv_id = _create_inv(client)
        _ingest_and_run(client, inv_id)
        resp = client.get(f"/export/{inv_id}/report.md", headers=AUTH)
        assert resp.status_code == 200
        assert "markdown" in resp.headers["content-type"] or resp.text.startswith("#")
        assert "Situation Report" in resp.text or "Summary" in resp.text

    def test_no_report(self, client):
        inv_id = _create_inv(client)
        resp = client.get(f"/export/{inv_id}/report.md", headers=AUTH)
        assert resp.status_code == 404

    def test_nonexistent(self, client):
        resp = client.get("/export/nonexistent/report.md", headers=AUTH)
        assert resp.status_code == 404


class TestExportReportPDF:
    def test_basic_export(self, client):
        inv_id = _create_inv(client)
        _ingest_and_run(client, inv_id)
        resp = client.get(f"/export/{inv_id}/report.pdf", headers=AUTH)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content[:5] == b"%PDF-"
        assert b"%%EOF" in resp.content

    def test_no_report(self, client):
        inv_id = _create_inv(client)
        resp = client.get(f"/export/{inv_id}/report.pdf", headers=AUTH)
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# JWT Authentication
# ═══════════════════════════════════════════════════════════════════════

class TestAuthRegister:
    def test_register_user(self, client):
        resp = client.post(
            "/auth/register",
            json={"username": "alice", "password": "securepass1", "role": "analyst"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "alice"
        assert data["role"] == "analyst"
        assert "id" in data

    def test_register_admin(self, client):
        resp = client.post(
            "/auth/register",
            json={"username": "boss", "password": "adminpass1", "role": "admin"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.json()["role"] == "admin"

    def test_register_viewer(self, client):
        resp = client.post(
            "/auth/register",
            json={"username": "viewer1", "password": "viewpass12", "role": "viewer"},
            headers=AUTH,
        )
        assert resp.status_code == 200

    def test_register_duplicate(self, client):
        client.post(
            "/auth/register",
            json={"username": "alice", "password": "securepass1", "role": "analyst"},
            headers=AUTH,
        )
        resp = client.post(
            "/auth/register",
            json={"username": "alice", "password": "different1", "role": "analyst"},
            headers=AUTH,
        )
        assert resp.status_code == 409

    def test_register_short_username(self, client):
        resp = client.post(
            "/auth/register",
            json={"username": "ab", "password": "securepass1", "role": "analyst"},
            headers=AUTH,
        )
        assert resp.status_code == 422

    def test_register_short_password(self, client):
        resp = client.post(
            "/auth/register",
            json={"username": "alice", "password": "short", "role": "analyst"},
            headers=AUTH,
        )
        assert resp.status_code == 422

    def test_register_invalid_role(self, client):
        resp = client.post(
            "/auth/register",
            json={"username": "alice", "password": "securepass1", "role": "superuser"},
            headers=AUTH,
        )
        assert resp.status_code == 422

    def test_register_no_auth(self, client):
        resp = client.post(
            "/auth/register",
            json={"username": "alice", "password": "securepass1", "role": "analyst"},
        )
        assert resp.status_code == 401


class TestAuthLogin:
    def _register(self, client, username="alice", password="securepass1"):
        client.post(
            "/auth/register",
            json={"username": username, "password": password, "role": "analyst"},
            headers=AUTH,
        )

    def test_login_success(self, client):
        self._register(client)
        resp = client.post("/auth/login", json={"username": "alice", "password": "securepass1"})
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["username"] == "alice"
        assert data["role"] == "analyst"
        # Token should be a valid JWT (3 dot-separated parts)
        assert len(data["token"].split(".")) == 3

    def test_login_wrong_password(self, client):
        self._register(client)
        resp = client.post("/auth/login", json={"username": "alice", "password": "wrongpass1"})
        assert resp.status_code == 401

    def test_login_nonexistent_user(self, client):
        resp = client.post("/auth/login", json={"username": "nobody", "password": "something1"})
        assert resp.status_code == 401

    def test_login_no_body(self, client):
        resp = client.post("/auth/login")
        assert resp.status_code == 422


class TestAuthMe:
    def _get_token(self, client, username="alice", password="securepass1"):
        client.post(
            "/auth/register",
            json={"username": username, "password": password, "role": "analyst"},
            headers=AUTH,
        )
        resp = client.post("/auth/login", json={"username": username, "password": password})
        return resp.json()["token"]

    def test_valid_token(self, client):
        token = self._get_token(client)
        resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "alice"
        assert data["role"] == "analyst"

    def test_invalid_token(self, client):
        resp = client.get("/auth/me", headers={"Authorization": "Bearer invalid.token.here"})
        assert resp.status_code == 401

    def test_missing_bearer(self, client):
        resp = client.get("/auth/me", headers={"Authorization": "NotBearer abc"})
        assert resp.status_code == 401

    def test_no_auth_header(self, client):
        resp = client.get("/auth/me")
        assert resp.status_code == 401

    def test_empty_token(self, client):
        resp = client.get("/auth/me", headers={"Authorization": "Bearer "})
        assert resp.status_code == 401


class TestJWTTokens:
    """Unit tests for JWT encode/decode."""

    def test_roundtrip(self):
        from sts_monitor.auth_jwt import create_token, decode_token
        token = create_token(42, "testuser", "admin")
        decoded = decode_token(token)
        assert decoded["sub"] == "42"
        assert decoded["username"] == "testuser"
        assert decoded["role"] == "admin"

    def test_tampered_token(self):
        from sts_monitor.auth_jwt import create_token, decode_token
        token = create_token(1, "user", "analyst")
        # Tamper with payload
        parts = token.split(".")
        tampered = parts[0] + "." + parts[1] + "x" + "." + parts[2]
        with pytest.raises(Exception):
            decode_token(tampered)

    def test_expired_token(self):
        from datetime import UTC, datetime, timedelta
        from sts_monitor.auth_jwt import _jwt_encode, decode_token, JWT_SECRET
        payload = {
            "sub": "1", "username": "test", "role": "analyst",
            "exp": (datetime.now(UTC) - timedelta(hours=1)).timestamp(),
        }
        token = _jwt_encode(payload, JWT_SECRET)
        with pytest.raises(Exception):
            decode_token(token)


# ═══════════════════════════════════════════════════════════════════════
# Rate Limiting
# ═══════════════════════════════════════════════════════════════════════

class TestRateLimiting:
    def test_rate_limiter_allows_normal_traffic(self):
        from sts_monitor.rate_limit import RateLimiter
        limiter = RateLimiter(rpm=60, burst=10)
        for _ in range(10):
            allowed, headers = limiter.allow("test-key")
            assert allowed

    def test_rate_limiter_blocks_burst(self):
        from sts_monitor.rate_limit import RateLimiter
        limiter = RateLimiter(rpm=60, burst=5)
        for _ in range(5):
            allowed, _ = limiter.allow("test-key")
            assert allowed
        # 6th request should be blocked
        allowed, headers = limiter.allow("test-key")
        assert not allowed
        assert "Retry-After" in headers

    def test_rate_limiter_different_keys(self):
        from sts_monitor.rate_limit import RateLimiter
        limiter = RateLimiter(rpm=60, burst=3)
        for _ in range(3):
            limiter.allow("key-a")
        allowed_a, _ = limiter.allow("key-a")
        allowed_b, _ = limiter.allow("key-b")
        assert not allowed_a
        assert allowed_b

    def test_rate_limiter_refills(self):
        from sts_monitor.rate_limit import RateLimiter
        # High rate so refill is fast: 60000 rpm = 1000/s
        limiter = RateLimiter(rpm=60000, burst=2)
        # Exhaust burst
        limiter.allow("refill-key")
        limiter.allow("refill-key")
        allowed_empty, _ = limiter.allow("refill-key")
        assert not allowed_empty  # burst exhausted
        # Wait for refill (1ms = 1 token at 1000/s)
        time.sleep(0.05)
        allowed_refilled, _ = limiter.allow("refill-key")
        assert allowed_refilled

    def test_rate_limit_headers(self):
        from sts_monitor.rate_limit import RateLimiter
        limiter = RateLimiter(rpm=120, burst=10)
        _, headers = limiter.allow("test")
        assert "X-RateLimit-Limit" in headers
        assert "X-RateLimit-Remaining" in headers

    def test_middleware_skips_testclient(self, client):
        """TestClient requests should bypass rate limiting."""
        for _ in range(20):
            resp = client.get("/health")
            assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════
# Plugin System
# ═══════════════════════════════════════════════════════════════════════

class TestPluginSystem:
    def test_list_plugins_empty(self, client):
        resp = client.get("/plugins", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == {"plugins": {}}

    def test_discover_plugins(self, client):
        resp = client.post("/plugins/discover", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "discovered" in data
        assert "total_registered" in data

    def test_discover_requires_admin(self, client):
        resp = client.post("/plugins/discover")
        assert resp.status_code == 401

    def test_plugin_registry_register(self):
        from sts_monitor.plugins import PluginRegistry

        class FakeConnector:
            name = "fake"
            def collect(self, query=None):
                return None

        registry = PluginRegistry()
        registry.register_connector("fake", FakeConnector, description="Test plugin")
        assert "fake" in registry.registered
        assert registry.get_connector("fake") is FakeConnector

    def test_plugin_registry_create(self):
        from sts_monitor.plugins import PluginRegistry

        class FakeConnector:
            name = "fake"
            def __init__(self, **kwargs):
                self.kwargs = kwargs
            def collect(self, query=None):
                return None

        registry = PluginRegistry()
        registry.register_connector("fake", FakeConnector)
        instance = registry.create_connector("fake", foo="bar")
        assert instance.kwargs == {"foo": "bar"}

    def test_plugin_registry_missing(self):
        from sts_monitor.plugins import PluginRegistry
        registry = PluginRegistry()
        with pytest.raises(KeyError):
            registry.create_connector("nonexistent")

    def test_plugin_discover_nonexistent_dir(self):
        from sts_monitor.plugins import PluginRegistry
        from pathlib import Path
        registry = PluginRegistry()
        count = registry.discover_plugin_dir(Path("/nonexistent/path"))
        assert count == 0

    def test_plugin_discover_entrypoints(self):
        from sts_monitor.plugins import PluginRegistry
        registry = PluginRegistry()
        # Should not crash even if no entrypoints exist
        count = registry.discover_entrypoints()
        assert isinstance(count, int)


# ═══════════════════════════════════════════════════════════════════════
# Notification System
# ═══════════════════════════════════════════════════════════════════════

class TestNotifications:
    def test_notify_test_endpoint(self, client):
        resp = client.post("/notifications/test", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "channels" in data
        assert "configured" in data
        # No channels configured in test = 0
        assert data["configured"] == 0

    def test_notify_requires_admin(self, client):
        resp = client.post("/notifications/test")
        assert resp.status_code == 401

    def test_email_without_config(self):
        from sts_monitor.notifications import AlertNotification, send_email
        n = AlertNotification(title="test", message="test msg", severity="info")
        result = send_email(n)
        assert result is False

    def test_slack_without_config(self):
        from sts_monitor.notifications import AlertNotification, send_slack
        n = AlertNotification(title="test", message="test msg", severity="info")
        result = send_slack(n)
        assert result is False

    def test_webhook_without_config(self):
        from sts_monitor.notifications import AlertNotification, send_webhook
        n = AlertNotification(title="test", message="test msg", severity="info")
        result = send_webhook(n)
        assert result is False

    def test_notify_all_empty(self):
        from sts_monitor.notifications import AlertNotification, notify_all
        n = AlertNotification(title="test", message="msg", severity="critical")
        result = notify_all(n)
        assert result == {}

    def test_notification_dataclass(self):
        from sts_monitor.notifications import AlertNotification
        n = AlertNotification(
            title="Alert",
            message="Something happened",
            severity="high",
            investigation_id="inv-123",
            metadata={"key": "value"},
        )
        assert n.title == "Alert"
        assert n.investigation_id == "inv-123"


# ═══════════════════════════════════════════════════════════════════════
# WebSocket Manager
# ═══════════════════════════════════════════════════════════════════════

class TestWebSocketManager:
    def test_ws_status_endpoint(self, client):
        resp = client.get("/ws/status", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "active_connections" in data
        assert "connections" in data
        assert data["active_connections"] == 0

    def test_ws_status_no_auth(self, client):
        resp = client.get("/ws/status")
        assert resp.status_code == 401

    def test_websocket_connect(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"action": "ping"}))
            data = json.loads(ws.receive_text())
            assert data["action"] == "pong"
            assert "ts" in data

    def test_websocket_subscribe(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"action": "subscribe", "event_types": ["alert", "observation"]}))
            data = json.loads(ws.receive_text())
            assert data["action"] == "subscribed"
            assert set(data["event_types"]) == {"alert", "observation"}

    def test_websocket_unsubscribe(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"action": "unsubscribe"}))
            data = json.loads(ws.receive_text())
            assert data["action"] == "unsubscribed"

    def test_websocket_status(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"action": "status"}))
            data = json.loads(ws.receive_text())
            assert data["action"] == "status"
            assert "active_connections" in data

    def test_websocket_unknown_action(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"action": "unknown_thing"}))
            data = json.loads(ws.receive_text())
            assert "error" in data
            assert "available_actions" in data

    def test_websocket_invalid_json(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.send_text("not json at all {{{")
            data = json.loads(ws.receive_text())
            assert "error" in data


# ═══════════════════════════════════════════════════════════════════════
# Frontend SPA
# ═══════════════════════════════════════════════════════════════════════

class TestFrontendSPA:
    def test_index_html_serves(self, client):
        resp = client.get("/static/index.html")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_index_has_react(self, client):
        resp = client.get("/static/index.html")
        assert "React" in resp.text or "react" in resp.text

    def test_index_has_all_pages(self, client):
        resp = client.get("/static/index.html")
        for page in ["DashboardPage", "InvestigationsPage", "SearchPage", "MapPage", "ReportsPage", "SettingsPage"]:
            assert page in resp.text, f"Missing page component: {page}"

    def test_index_has_api_calls(self, client):
        resp = client.get("/static/index.html")
        assert "/dashboard/summary" in resp.text
        assert "/investigations" in resp.text
        assert "/export/" in resp.text

    def test_index_has_websocket(self, client):
        resp = client.get("/static/index.html")
        assert "WebSocket" in resp.text or "websocket" in resp.text or "/ws" in resp.text

    def test_root_redirects_to_spa(self, client):
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 307
        assert "/static/" in resp.headers["location"]  # globe.html or index.html

    def test_old_dashboard_still_works(self, client):
        resp = client.get("/static/dashboard.html")
        assert resp.status_code == 200

    def test_globe_still_works(self, client):
        resp = client.get("/static/globe.html")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════
# Export module unit tests
# ═══════════════════════════════════════════════════════════════════════

class TestExportModule:
    def test_build_simple_pdf(self):
        from sts_monitor.export import _build_simple_pdf
        pdf = _build_simple_pdf("Hello World\n\nThis is a test.")
        assert pdf[:5] == b"%PDF-"
        assert b"%%EOF" in pdf

    def test_build_pdf_with_headers(self):
        from sts_monitor.export import _build_simple_pdf
        pdf = _build_simple_pdf("# Title\n## Subtitle\nBody text")
        assert pdf[:5] == b"%PDF-"

    def test_build_pdf_multipage(self):
        from sts_monitor.export import _build_simple_pdf
        long_text = "\n".join(f"Line {i}" for i in range(200))
        pdf = _build_simple_pdf(long_text)
        assert pdf[:5] == b"%PDF-"
        # Should have multiple pages
        assert pdf.count(b"/Type /Page") >= 2

    def test_build_pdf_special_chars(self):
        from sts_monitor.export import _build_simple_pdf
        pdf = _build_simple_pdf("Special: (parens) \\backslash \\ more (test)")
        assert pdf[:5] == b"%PDF-"

    def test_build_pdf_empty(self):
        from sts_monitor.export import _build_simple_pdf
        pdf = _build_simple_pdf("")
        assert pdf[:5] == b"%PDF-"


# ═══════════════════════════════════════════════════════════════════════
# Password hashing unit tests
# ═══════════════════════════════════════════════════════════════════════

class TestPasswordHashing:
    def test_hash_and_verify(self):
        from sts_monitor.auth_jwt import hash_password, verify_password
        hashed = hash_password("mypassword")
        assert hashed != "mypassword"
        assert verify_password("mypassword", hashed)

    def test_wrong_password(self):
        from sts_monitor.auth_jwt import hash_password, verify_password
        hashed = hash_password("correct")
        assert not verify_password("wrong", hashed)

    def test_different_hashes(self):
        from sts_monitor.auth_jwt import hash_password
        h1 = hash_password("same")
        h2 = hash_password("same")
        # bcrypt produces different salts
        assert h1 != h2

    def test_verify_invalid_hash(self):
        from sts_monitor.auth_jwt import verify_password
        assert not verify_password("test", "not-a-valid-hash")


# ═══════════════════════════════════════════════════════════════════════
# Dashboard integration
# ═══════════════════════════════════════════════════════════════════════

class TestDashboardIntegration:
    def test_dashboard_summary(self, client):
        resp = client.get("/dashboard/summary", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        # Should have key counters
        assert "investigations" in data or "total_investigations" in data

    def test_dashboard_after_data(self, client):
        inv_id = _create_inv(client)
        _ingest_and_run(client, inv_id)
        resp = client.get("/dashboard/summary", headers=AUTH)
        assert resp.status_code == 200

    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_preflight(self, client):
        resp = client.get("/system/preflight")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════
# End-to-end workflow: create → ingest → run → export all formats
# ═══════════════════════════════════════════════════════════════════════

class TestEndToEndExportWorkflow:
    def test_full_workflow(self, client):
        """Complete workflow: create → ingest → pipeline → export CSV/MD/PDF."""
        # Create
        inv_id = _create_inv(client, "full export test")

        # Ingest
        _ingest_and_run(client, inv_id, batch_size=20)

        # Export observations CSV
        resp = client.get(f"/export/{inv_id}/observations.csv", headers=AUTH)
        assert resp.status_code == 200
        csv_lines = resp.text.strip().split("\n")
        assert len(csv_lines) > 1

        # Export claims CSV
        resp = client.get(f"/export/{inv_id}/claims.csv", headers=AUTH)
        assert resp.status_code == 200

        # Export report markdown
        resp = client.get(f"/export/{inv_id}/report.md", headers=AUTH)
        assert resp.status_code == 200
        assert len(resp.text) > 50

        # Export report PDF
        resp = client.get(f"/export/{inv_id}/report.pdf", headers=AUTH)
        assert resp.status_code == 200
        assert resp.content[:5] == b"%PDF-"

    def test_auth_workflow(self, client):
        """Register → login → use token → check identity."""
        # Register
        resp = client.post(
            "/auth/register",
            json={"username": "workflow_user", "password": "workflow_pass1", "role": "analyst"},
            headers=AUTH,
        )
        assert resp.status_code == 200

        # Login
        resp = client.post("/auth/login", json={"username": "workflow_user", "password": "workflow_pass1"})
        assert resp.status_code == 200
        token = resp.json()["token"]

        # Use token
        resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["username"] == "workflow_user"
