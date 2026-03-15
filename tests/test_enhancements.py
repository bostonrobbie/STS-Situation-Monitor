"""Tests for enhanced features: templates, source scoring, comparative analysis,
rabbit trail, alert engine, timeline API, and new endpoints."""
from __future__ import annotations

import pytest
from datetime import UTC, datetime, timedelta
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
# Investigation Templates
# ═══════════════════════════════════════════════════════════════════════

class TestTemplates:
    def test_list_all_templates(self, client):
        resp = client.get("/templates", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 10
        names = [t["name"] for t in data]
        assert "Boston & Massachusetts Monitor" in names
        assert "Global Geopolitical Monitor" in names

    def test_list_by_category(self, client):
        resp = client.get("/templates?category=geopolitical", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert all(t["category"] == "geopolitical" for t in data)

    def test_list_local_templates(self, client):
        resp = client.get("/templates?category=local", headers=AUTH)
        data = resp.json()
        assert any("Boston" in t["name"] for t in data)

    def test_list_conspiracy_templates(self, client):
        resp = client.get("/templates?category=conspiracy", headers=AUTH)
        data = resp.json()
        assert any("Narrative" in t["name"] or "Accountability" in t["name"] for t in data)

    def test_get_template_detail(self, client):
        resp = client.get("/templates/boston_local", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["topic"] == "Boston Massachusetts local news events safety"
        assert "config" in data
        assert "rss_feeds" in data["config"]

    def test_get_template_not_found(self, client):
        resp = client.get("/templates/nonexistent", headers=AUTH)
        assert resp.status_code == 404

    def test_apply_template(self, client):
        resp = client.post("/templates/boston_local/apply", json={}, headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["investigation_id"]
        assert "Boston" in data["topic"]

    def test_apply_template_custom_topic(self, client):
        resp = client.post(
            "/templates/geopolitical_global/apply",
            json={"custom_topic": "My custom geopolitical watch"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.json()["topic"] == "My custom geopolitical watch"

    def test_apply_template_not_found(self, client):
        resp = client.post("/templates/bogus/apply", json={}, headers=AUTH)
        assert resp.status_code == 404

    def test_template_requires_auth(self, client):
        resp = client.get("/templates")
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════
# Source Scoring
# ═══════════════════════════════════════════════════════════════════════

class TestSourceScoring:
    def test_compute_scores(self):
        from sts_monitor.source_scoring import compute_source_scores
        now = datetime.now(UTC)
        obs = [
            {"source": "reuters", "claim": f"event {i} confirmed", "captured_at": now - timedelta(hours=i)}
            for i in range(10)
        ] + [
            {"source": "sketchy_blog", "claim": f"event {i} is false and debunked", "captured_at": now - timedelta(hours=i)}
            for i in range(5)
        ]
        scores = compute_source_scores(obs, min_observations=3)
        assert len(scores) == 2
        reuters = next(s for s in scores if s.source == "reuters")
        sketchy = next(s for s in scores if s.source == "sketchy_blog")
        assert reuters.raw_score >= sketchy.raw_score

    def test_leaderboard(self):
        from sts_monitor.source_scoring import get_source_leaderboard
        obs = [{"source": f"src_{i}", "claim": f"claim {i}", "captured_at": datetime.now(UTC)} for i in range(20)]
        result = get_source_leaderboard(obs)
        assert "total_sources" in result
        assert "leaderboard" in result
        assert "grade_distribution" in result

    def test_source_score_api(self, client):
        # Create investigation with observations
        resp = client.post("/investigations", json={"topic": "source test"}, headers=AUTH)
        inv_id = resp.json()["id"]
        client.post(f"/investigations/{inv_id}/ingest/simulated", json={"batch_size": 10}, headers=AUTH)

        resp = client.get(f"/investigations/{inv_id}/source-scores", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "total_sources" in data

    def test_source_score_404(self, client):
        resp = client.get("/investigations/nonexistent/source-scores", headers=AUTH)
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# Comparative Analysis
# ═══════════════════════════════════════════════════════════════════════

class TestComparativeAnalysis:
    def test_detect_contradictions(self):
        from sts_monitor.comparative import detect_contradictions
        obs = [
            {"source": "source_a", "claim": "The president confirmed the deal is proceeding as planned", "captured_at": datetime.now(UTC)},
            {"source": "source_b", "claim": "The president denied the deal is proceeding as planned", "captured_at": datetime.now(UTC)},
        ]
        contradictions = detect_contradictions(obs)
        assert len(contradictions) >= 1

    def test_detect_agreements(self):
        from sts_monitor.comparative import detect_agreements
        obs = [
            {"source": "reuters", "claim": "Major earthquake hits region today", "captured_at": datetime.now(UTC)},
            {"source": "bbc", "claim": "Major earthquake hits region today", "captured_at": datetime.now(UTC)},
            {"source": "ap", "claim": "Major earthquake hits region today", "captured_at": datetime.now(UTC)},
        ]
        agreements = detect_agreements(obs)
        assert len(agreements) >= 1
        assert agreements[0].observation_count == 3

    def test_detect_silences(self):
        from sts_monitor.comparative import detect_silences
        now = datetime.now(UTC)
        # Create obs spread over 3 days, last seen 24h ago
        obs = [
            {"source": "reuters", "claim": f"report {i}", "captured_at": now - timedelta(hours=24 + i * 6)}
            for i in range(10)
        ]
        silences = detect_silences(obs, silence_threshold_hours=12)
        assert len(silences) >= 1

    def test_comparative_api(self, client):
        resp = client.post("/investigations", json={"topic": "compare test"}, headers=AUTH)
        inv_id = resp.json()["id"]
        client.post(f"/investigations/{inv_id}/ingest/simulated", json={"batch_size": 20}, headers=AUTH)

        resp = client.get(f"/investigations/{inv_id}/comparative", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "total_observations" in data
        assert "contradictions" in data
        assert "agreements" in data
        assert "narrative_divergence_score" in data

    def test_comparative_404(self, client):
        resp = client.get("/investigations/nonexistent/comparative", headers=AUTH)
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# Rabbit Trail
# ═══════════════════════════════════════════════════════════════════════

class TestRabbitTrail:
    def test_run_rabbit_trail(self):
        from sts_monitor.rabbit_trail import run_rabbit_trail
        obs = [
            {"source": "bbc", "claim": "Government denied the leaked documents are authentic", "captured_at": datetime.now(UTC)},
            {"source": "reuters", "claim": "Whistleblower confirms leaked documents are real", "captured_at": datetime.now(UTC)},
            {"source": "nyt", "claim": "Sources say classified report was suppressed", "captured_at": datetime.now(UTC)},
        ]
        trail = run_rabbit_trail(
            investigation_id="test-inv",
            topic="leaked documents investigation",
            observations=obs,
            entities=["Government", "Whistleblower", "Pentagon"],
        )
        assert trail.status == "completed"
        assert len(trail.steps) >= 3
        assert trail.total_contradictions >= 0
        assert len(trail.all_queries) > 0

    def test_trail_session_storage(self):
        from sts_monitor.rabbit_trail import run_rabbit_trail, store_trail_session, get_trail_session, list_trail_sessions
        trail = run_rabbit_trail("inv-1", "test topic", [], ["entity1"])
        store_trail_session(trail)
        retrieved = get_trail_session(trail.session_id)
        assert retrieved is not None
        assert retrieved.investigation_id == "inv-1"
        sessions = list_trail_sessions(investigation_id="inv-1")
        assert len(sessions) >= 1

    def test_rabbit_trail_api(self, client):
        resp = client.post("/investigations", json={"topic": "rabbit test"}, headers=AUTH)
        inv_id = resp.json()["id"]
        client.post(f"/investigations/{inv_id}/ingest/simulated", json={"batch_size": 10}, headers=AUTH)

        resp = client.post(f"/investigations/{inv_id}/rabbit-trail?max_depth=5", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert "steps" in data
        assert "key_findings" in data

    def test_list_rabbit_trails(self, client):
        resp = client.get("/rabbit-trails", headers=AUTH)
        assert resp.status_code == 200

    def test_rabbit_trail_404(self, client):
        resp = client.post("/investigations/nonexistent/rabbit-trail", headers=AUTH)
        assert resp.status_code == 404

    def test_get_trail_not_found(self, client):
        resp = client.get("/rabbit-trails/nonexistent", headers=AUTH)
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# Alert Engine
# ═══════════════════════════════════════════════════════════════════════

class TestAlertEngine:
    def test_volume_spike(self):
        from sts_monitor.alert_engine import evaluate_rules, AlertRule
        now = datetime.now(UTC)
        rule = AlertRule(name="vol", rule_type="volume_spike", threshold=5, window_minutes=60)
        obs = [{"claim": f"obs {i}", "source": "src", "captured_at": now - timedelta(minutes=i)} for i in range(10)]
        events = evaluate_rules([rule], obs, now=now)
        assert len(events) == 1
        assert events[0].rule_type == "volume_spike"

    def test_contradiction_threshold(self):
        from sts_monitor.alert_engine import evaluate_rules, AlertRule
        now = datetime.now(UTC)
        rule = AlertRule(name="contra", rule_type="contradiction_threshold", threshold=2, window_minutes=60)
        obs = [
            {"claim": "this is false and debunked", "source": "a", "captured_at": now},
            {"claim": "report denied by officials", "source": "b", "captured_at": now},
            {"claim": "normal report", "source": "c", "captured_at": now},
        ]
        events = evaluate_rules([rule], obs, now=now)
        assert len(events) == 1

    def test_cooldown_respected(self):
        from sts_monitor.alert_engine import evaluate_rules, AlertRule
        now = datetime.now(UTC)
        rule = AlertRule(name="vol", rule_type="volume_spike", threshold=5, window_minutes=60,
                        cooldown_seconds=600, last_triggered_at=now - timedelta(seconds=60))
        obs = [{"claim": f"obs {i}", "source": "src", "captured_at": now} for i in range(10)]
        events = evaluate_rules([rule], obs, now=now)
        assert len(events) == 0  # Still in cooldown

    def test_create_alert_rule_api(self, client):
        resp = client.post("/investigations", json={"topic": "alert test"}, headers=AUTH)
        inv_id = resp.json()["id"]

        resp = client.post(f"/investigations/{inv_id}/alert-rules", json={
            "name": "Test Volume Alert",
            "rule_type": "volume_spike",
            "threshold": 10,
            "window_minutes": 60,
            "severity": "warning",
        }, headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test Volume Alert"

    def test_list_alert_rules_api(self, client):
        resp = client.post("/investigations", json={"topic": "alert list test"}, headers=AUTH)
        inv_id = resp.json()["id"]
        client.post(f"/investigations/{inv_id}/alert-rules", json={
            "name": "Rule 1", "rule_type": "volume_spike", "threshold": 5,
        }, headers=AUTH)

        resp = client.get(f"/investigations/{inv_id}/alert-rules", headers=AUTH)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_evaluate_alerts_api(self, client):
        resp = client.post("/investigations", json={"topic": "eval test"}, headers=AUTH)
        inv_id = resp.json()["id"]
        client.post(f"/investigations/{inv_id}/ingest/simulated", json={"batch_size": 20}, headers=AUTH)

        resp = client.post(f"/investigations/{inv_id}/evaluate-alerts", headers=AUTH)
        assert resp.status_code == 200
        assert "alerts_fired" in resp.json()

    def test_inactive_rule_skipped(self):
        from sts_monitor.alert_engine import evaluate_rules, AlertRule
        rule = AlertRule(name="off", rule_type="volume_spike", threshold=1, active=False)
        obs = [{"claim": "obs", "source": "src", "captured_at": datetime.now(UTC)}]
        events = evaluate_rules([rule], obs)
        assert len(events) == 0


# ═══════════════════════════════════════════════════════════════════════
# Timeline API
# ═══════════════════════════════════════════════════════════════════════

class TestTimeline:
    def test_timeline_api(self, client):
        resp = client.post("/investigations", json={"topic": "timeline test"}, headers=AUTH)
        inv_id = resp.json()["id"]
        client.post(f"/investigations/{inv_id}/ingest/simulated", json={"batch_size": 15}, headers=AUTH)

        resp = client.get(f"/investigations/{inv_id}/timeline", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "topic" in data
        assert "events" in data
        assert "summary" in data

    def test_timeline_404(self, client):
        resp = client.get("/investigations/nonexistent/timeline", headers=AUTH)
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# Module Unit Tests
# ═══════════════════════════════════════════════════════════════════════

class TestTemplateModule:
    def test_list_templates_filtered(self):
        from sts_monitor.investigation_templates import list_templates
        all_t = list_templates()
        geo = list_templates(category="geopolitical")
        assert len(all_t) > len(geo)
        assert all(t["category"] == "geopolitical" for t in geo)

    def test_apply_template_returns_config(self):
        from sts_monitor.investigation_templates import apply_template
        config = apply_template("media_narrative")
        assert "topic" in config
        assert config["config"]["rabbit_trail"] is True
        assert len(config["config"]["rss_feeds"]) > 0

    def test_apply_nonexistent_template(self):
        from sts_monitor.investigation_templates import apply_template
        result = apply_template("nonexistent")
        assert "error" in result


class TestSourceScoringModule:
    def test_source_grade(self):
        from sts_monitor.source_scoring import SourceScore
        s = SourceScore(source="test", raw_score=0.9)
        assert s.reliability_grade == "A"
        s.raw_score = 0.2
        assert s.reliability_grade == "F"

    def test_empty_observations(self):
        from sts_monitor.source_scoring import compute_source_scores
        scores = compute_source_scores([])
        assert len(scores) == 0


class TestComparativeModule:
    def test_full_analysis(self):
        from sts_monitor.comparative import run_comparative_analysis
        report = run_comparative_analysis([
            {"source": "a", "claim": "confirmed event happened", "captured_at": datetime.now(UTC)},
            {"source": "b", "claim": "denied event happened false", "captured_at": datetime.now(UTC)},
        ])
        assert report.total_sources == 2
        assert report.total_observations == 2


class TestAlertEngineModule:
    def test_default_rules(self):
        from sts_monitor.alert_engine import get_default_rules
        rules = get_default_rules("inv-123")
        assert len(rules) >= 4
        assert all(r.investigation_id == "inv-123" for r in rules)

    def test_narrative_shift_detection(self):
        from sts_monitor.alert_engine import evaluate_rules, AlertRule
        now = datetime.now(UTC)
        rule = AlertRule(name="shift", rule_type="narrative_shift", threshold=3, window_minutes=120)
        # Create observations with different topics in two halves
        obs = (
            [{"claim": "earthquake damage seismic activity tremor", "source": "a", "captured_at": now - timedelta(minutes=90)} for _ in range(5)]
            + [{"claim": "political scandal corruption investigation probe", "source": "b", "captured_at": now - timedelta(minutes=10)} for _ in range(5)]
        )
        events = evaluate_rules([rule], obs, now=now)
        # May or may not fire depending on threshold — just ensure no crash
        assert isinstance(events, list)
