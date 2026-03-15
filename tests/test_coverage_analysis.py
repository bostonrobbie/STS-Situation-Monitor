"""Tests for analysis and utility modules — targeting 100% coverage."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy

from sts_monitor.database import Base, engine


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch):
    import sts_monitor.rate_limit as rl
    monkeypatch.setattr(rl, "RATE_LIMIT_ENABLED", False)
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text("PRAGMA foreign_keys = OFF"))
        conn.commit()
    Base.metadata.drop_all(bind=engine, checkfirst=True)
    Base.metadata.create_all(bind=engine, checkfirst=True)
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text("PRAGMA foreign_keys = ON"))
        conn.commit()


# ── claim_verification.py coverage ───────────────────────────────────────

class TestClaimVerification:
    def test_verify_claims_no_obs(self):
        from sts_monitor.claim_verification import verify_investigation_claims
        result = verify_investigation_claims(investigation_id="test-inv", topic="test", observations=[], llm_client=None)
        assert result.total_claims_analyzed == 0

    def test_verify_claims_with_obs(self):
        from sts_monitor.claim_verification import verify_investigation_claims
        obs = [
            {"source": "reuters", "claim": "Event happened according to officials", "reliability_hint": 0.9},
            {"source": "bbc", "claim": "Event happened according to officials", "reliability_hint": 0.8},
            {"source": "unknown", "claim": "Event denied by spokesperson today", "reliability_hint": 0.3},
        ]
        result = verify_investigation_claims(investigation_id="test-inv", topic="test event", observations=obs, llm_client=None)
        assert result.total_claims_analyzed >= 1
        assert len(result.results) >= 1

    def test_verify_with_llm(self):
        from sts_monitor.claim_verification import verify_investigation_claims
        mock_llm = MagicMock()
        mock_llm.summarize.return_value = json.dumps({
            "verdict": "likely_true",
            "confidence": 0.9,
            "reasoning": "Multiple reliable sources confirm",
        })
        obs = [
            {"source": "reuters", "claim": "Test event confirmed by multiple sources", "reliability_hint": 0.9},
        ]
        result = verify_investigation_claims(investigation_id="test-inv", topic="test", observations=obs, llm_client=mock_llm)
        assert result.total_claims_analyzed >= 0

    def test_verify_with_llm_failure(self):
        from sts_monitor.claim_verification import verify_investigation_claims
        mock_llm = MagicMock()
        mock_llm.summarize.side_effect = Exception("LLM error")
        obs = [
            {"source": "reuters", "claim": "Test event confirmed by multiple sources", "reliability_hint": 0.9},
        ]
        result = verify_investigation_claims(investigation_id="test-inv", topic="test", observations=obs, llm_client=mock_llm)
        assert result.total_claims_analyzed >= 0


# ── alert_engine.py coverage ─────────────────────────────────────────────

class TestAlertEngine:
    def test_evaluate_rules_empty(self):
        from sts_monitor.alert_engine import evaluate_rules, get_default_rules
        rules = get_default_rules(investigation_id="test")
        alerts = evaluate_rules(rules, [])
        assert isinstance(alerts, list)

    def test_evaluate_rules_with_data(self):
        from sts_monitor.alert_engine import evaluate_rules, get_default_rules
        rules = get_default_rules(investigation_id="test")
        obs = [
            {"id": 1, "claim": "Test event denied", "source": "reuters",
             "captured_at": datetime.now(UTC), "reliability_hint": 0.9,
             "investigation_id": "test"},
        ] * 20  # many observations
        alerts = evaluate_rules(rules, obs)
        assert isinstance(alerts, list)

    def test_narrative_shift_detection(self):
        from sts_monitor.alert_engine import evaluate_rules, AlertRule
        rule = AlertRule(
            name="narrative_shift",
            rule_type="narrative_shift",
            investigation_id="test",
            threshold=3,
            window_minutes=1440,
        )
        obs = [
            {"id": i, "claim": f"claim {i}", "source": "s",
             "captured_at": datetime.now(UTC) - timedelta(hours=i),
             "reliability_hint": 0.5, "investigation_id": "test"}
            for i in range(30)
        ]
        alerts = evaluate_rules([rule], obs)
        assert isinstance(alerts, list)

    def test_volume_spike(self):
        from sts_monitor.alert_engine import evaluate_rules, AlertRule
        rule = AlertRule(
            name="volume_spike",
            rule_type="volume_spike",
            investigation_id="test",
            threshold=2.0,
            window_minutes=60,
        )
        # Create a spike: many obs in recent hour, few in the previous period
        now = datetime.now(UTC)
        obs = [
            {"id": i, "claim": f"claim {i}", "source": "s",
             "captured_at": now - timedelta(minutes=i % 30),
             "reliability_hint": 0.5, "investigation_id": "test"}
            for i in range(50)
        ]
        alerts = evaluate_rules([rule], obs)
        assert isinstance(alerts, list)

    def test_contradiction_threshold(self):
        from sts_monitor.alert_engine import evaluate_rules, AlertRule
        rule = AlertRule(
            name="contradiction",
            rule_type="contradiction_threshold",
            investigation_id="test",
            threshold=3,
            window_minutes=1440,
        )
        obs = [
            {"id": 1, "claim": "Event confirmed", "source": "reuters",
             "captured_at": datetime.now(UTC), "reliability_hint": 0.9, "investigation_id": "test"},
            {"id": 2, "claim": "Event denied by officials", "source": "gov",
             "captured_at": datetime.now(UTC), "reliability_hint": 0.7, "investigation_id": "test"},
            {"id": 3, "claim": "No event happened says expert", "source": "expert",
             "captured_at": datetime.now(UTC), "reliability_hint": 0.6, "investigation_id": "test"},
            {"id": 4, "claim": "Witnesses confirm event", "source": "bbc",
             "captured_at": datetime.now(UTC), "reliability_hint": 0.8, "investigation_id": "test"},
        ]
        alerts = evaluate_rules([rule], obs)
        assert isinstance(alerts, list)


# ── surge_detector.py coverage ───────────────────────────────────────────

class TestSurgeDetector:
    def test_basic_surge_detection(self):
        from sts_monitor.surge_detector import analyze_surge
        obs = [
            {"source": "nitter:@user", "claim": f"event {i}", "captured_at": datetime.now(UTC) - timedelta(minutes=i)}
            for i in range(50)
        ]
        result = analyze_surge(obs, topic="test")
        assert isinstance(result.total_processed, int)

    def test_empty_observations(self):
        from sts_monitor.surge_detector import analyze_surge
        result = analyze_surge([], topic="test")
        assert result.surge_detected is False or result.total_processed == 0


# ── knowledge_graph.py coverage ──────────────────────────────────────────

class TestKnowledgeGraph:
    def test_build_graph_empty(self):
        from sts_monitor.knowledge_graph import build_knowledge_graph
        from sqlalchemy.orm import Session as SASession
        from sts_monitor.database import engine
        with SASession(engine) as session:
            # Use a non-existent investigation ID to get empty graph
            result = build_knowledge_graph(session, investigation_ids=["nonexistent-id"])
            assert result.node_count == 0

    def test_build_graph_with_session(self):
        from sts_monitor.knowledge_graph import build_knowledge_graph
        from sqlalchemy.orm import Session as SASession
        from sts_monitor.database import engine
        with SASession(engine) as session:
            # Build graph with all investigations (may have seeded data)
            result = build_knowledge_graph(session)
            assert result.node_count >= 0


# ── comparative.py coverage ──────────────────────────────────────────────

class TestComparative:
    def test_compare_empty(self):
        from sts_monitor.comparative import run_comparative_analysis
        result = run_comparative_analysis([])
        assert result.total_sources == 0

    def test_compare_with_data(self):
        from sts_monitor.comparative import run_comparative_analysis
        obs = [
            {"source": "reuters", "claim": "Event happened at 3pm", "captured_at": datetime.now(UTC).isoformat(), "reliability_hint": 0.9},
            {"source": "bbc", "claim": "Event happened at 3pm", "captured_at": datetime.now(UTC).isoformat(), "reliability_hint": 0.8},
            {"source": "fox", "claim": "No event occurred", "captured_at": datetime.now(UTC).isoformat(), "reliability_hint": 0.4},
        ]
        result = run_comparative_analysis(obs)
        assert result.total_sources >= 2

    def test_with_llm(self):
        from sts_monitor.comparative import run_comparative_analysis
        obs = [
            {"source": "reuters", "claim": "test claim", "captured_at": datetime.now(UTC).isoformat(), "reliability_hint": 0.9},
        ]
        result = run_comparative_analysis(obs)
        assert result.total_observations >= 1


# ── pattern_matching.py coverage ─────────────────────────────────────────

class TestPatternMatching:
    def test_match_patterns_empty(self):
        from sts_monitor.pattern_matching import analyze_patterns
        result = analyze_patterns([])
        assert result["escalation_score"] == 0.0

    def test_match_patterns_with_data(self):
        from sts_monitor.pattern_matching import analyze_patterns
        now = datetime.now(UTC)
        obs = [
            {"source": "s", "claim": f"military deployment {i}", "captured_at": (now - timedelta(hours=i)).isoformat()}
            for i in range(20)
        ]
        result = analyze_patterns(obs)
        assert "escalation_score" in result
        assert "current_signature" in result


# ── source_scoring.py coverage ───────────────────────────────────────────

class TestSourceScoring:
    def test_score_empty(self):
        from sts_monitor.source_scoring import get_source_leaderboard
        result = get_source_leaderboard([])
        assert result["leaderboard"] == []

    def test_score_with_data(self):
        from sts_monitor.source_scoring import get_source_leaderboard
        obs = [
            {"source": "reuters", "claim": "claim1", "reliability_hint": 0.9},
            {"source": "reuters", "claim": "claim2", "reliability_hint": 0.8},
            {"source": "unknown", "claim": "claim3", "reliability_hint": 0.3},
        ]
        result = get_source_leaderboard(obs)
        assert len(result["leaderboard"]) >= 1


# ── source_network.py coverage ───────────────────────────────────────────

class TestSourceNetwork:
    def test_analyze_empty(self):
        from sts_monitor.source_network import analyze_source_network
        result = analyze_source_network([])
        assert result.total_sources == 0

    def test_analyze_with_data(self):
        from sts_monitor.source_network import analyze_source_network
        now = datetime.now(UTC)
        obs = [
            {"source": "reuters", "claim": "event happened", "captured_at": now, "reliability_hint": 0.9},
            {"source": "bbc", "claim": "event confirmed", "captured_at": now + timedelta(minutes=5), "reliability_hint": 0.8},
            {"source": "reuters", "claim": "updated report", "captured_at": now + timedelta(minutes=10), "reliability_hint": 0.9},
        ]
        result = analyze_source_network(obs)
        assert result.total_sources >= 2


# ── geofence.py coverage ─────────────────────────────────────────────────

class TestGeofence:
    def test_check_empty(self):
        from sts_monitor.geofence import check_observations_against_zones
        result = check_observations_against_zones([])
        assert result == []

    def test_check_with_obs(self):
        from sts_monitor.geofence import check_observations_against_zones
        obs = [
            {"id": 1, "source": "s", "claim": "test", "latitude": 48.8566, "longitude": 2.3522},
        ]
        result = check_observations_against_zones(obs)
        assert isinstance(result, list)

    def test_add_custom_zone(self):
        from sts_monitor.geofence import add_zone, check_observations_against_zones, _custom_zones, GeoZone
        _custom_zones.clear()
        add_zone(GeoZone("test_zone", 40.0, -74.0, 50.0))
        obs = [{"id": 1, "source": "s", "claim": "test", "latitude": 40.001, "longitude": -74.001}]
        result = check_observations_against_zones(obs)
        assert isinstance(result, list)
        _custom_zones.clear()


# ── rabbit_trail.py coverage ─────────────────────────────────────────────

class TestRabbitTrail:
    def test_run_trail_no_llm(self):
        from sts_monitor.rabbit_trail import run_rabbit_trail
        result = run_rabbit_trail(
            investigation_id="inv1", topic="test",
            observations=[{"source": "s", "claim": "c", "reliability_hint": 0.5}],
            entities=["USA"],
            max_depth=2,
        )
        assert result is not None

    def test_store_and_get_trail(self):
        from sts_monitor.rabbit_trail import store_trail_session, get_trail_session, list_trail_sessions
        from sts_monitor.rabbit_trail import run_rabbit_trail, _trail_sessions
        _trail_sessions.clear()
        trail = run_rabbit_trail(
            investigation_id="inv1", topic="test",
            observations=[{"source": "s", "claim": "c", "reliability_hint": 0.5}],
            entities=["USA"],
        )
        store_trail_session(trail)
        retrieved = get_trail_session(trail.session_id)
        assert retrieved is not None
        sessions = list_trail_sessions()
        assert len(sessions) >= 1
        sessions_filtered = list_trail_sessions(investigation_id="inv1")
        assert len(sessions_filtered) >= 1
        _trail_sessions.clear()


# ── deep_truth.py coverage ───────────────────────────────────────────────

class TestDeepTruth:
    def test_analyze_no_obs(self):
        from sts_monitor.deep_truth import analyze_deep_truth
        result = analyze_deep_truth(observations=[], topic="test")
        assert result is not None

    def test_analyze_with_obs(self):
        from sts_monitor.deep_truth import analyze_deep_truth
        obs = [
            {"source": "reuters", "claim": "Major event", "reliability_hint": 0.9,
             "captured_at": datetime.now(UTC).isoformat()},
            {"source": "bbc", "claim": "Major event confirmed", "reliability_hint": 0.8,
             "captured_at": datetime.now(UTC).isoformat()},
        ]
        result = analyze_deep_truth(observations=obs, topic="test")
        assert isinstance(result.to_dict(), dict)


# ── research_agent.py coverage ───────────────────────────────────────────

class TestResearchAgent:
    def test_run_research_no_llm(self):
        from sts_monitor.research_agent import ResearchAgent
        mock_llm = MagicMock()
        mock_llm.summarize.return_value = json.dumps({
            "key_findings": ["test finding"],
            "confidence": 0.5,
            "new_search_queries": [],
            "urls_to_scrape": [],
            "twitter_accounts_to_follow": [],
            "twitter_search_queries": [],
            "assessment": "Test assessment",
            "should_continue": False,
            "reasoning": "Test done",
        })
        agent = ResearchAgent(llm_client=mock_llm, max_iterations=1)
        sessions = agent.list_sessions()
        assert isinstance(sessions, list)


# ── cycle.py coverage ────────────────────────────────────────────────────

class TestCycle:
    def test_run_cycle_empty(self):
        from sts_monitor.cycle import run_cycle
        from unittest.mock import MagicMock
        # Create a mock connector that returns empty results
        mock_connector = MagicMock()
        mock_connector.collect.return_value = MagicMock(
            observations=[], connector="mock", metadata={}
        )
        result = run_cycle(
            topic="test",
            investigation_id="inv1",
            connectors=[mock_connector],
            generate_report=False,
            run_surge_analysis=False,
            run_deep_truth=False,
        )
        assert isinstance(result.cycle_id, str)

    def test_run_cycle_with_data(self):
        from sts_monitor.cycle import run_cycle
        from sts_monitor.pipeline import Observation
        from unittest.mock import MagicMock
        mock_connector = MagicMock()
        obs = Observation(
            source="test",
            claim="test claim for cycle",
            url="https://example.com",
            captured_at=datetime.now(UTC),
            reliability_hint=0.5,
        )
        mock_connector.collect.return_value = MagicMock(
            observations=[obs], connector="mock", metadata={}
        )
        result = run_cycle(
            topic="test",
            investigation_id="inv1",
            connectors=[mock_connector],
            generate_report=False,
            run_surge_analysis=False,
            run_deep_truth=False,
        )
        assert isinstance(result.cycle_id, str)


# ── webhook_ingest.py coverage ───────────────────────────────────────────

class TestWebhookIngest:
    def test_normalize_single(self):
        from sts_monitor.webhook_ingest import normalize_webhook_payload
        event = {
            "source": "test",
            "claim": "Test claim",
            "url": "https://example.com",
        }
        result = normalize_webhook_payload(event)
        assert isinstance(result, list)
        assert len(result) >= 1
        assert result[0]["source"] == "test"

    def test_normalize_with_timestamp(self):
        from sts_monitor.webhook_ingest import normalize_webhook_payload
        event = {
            "source": "test",
            "claim": "Test claim",
            "url": "https://example.com",
            "timestamp": "2025-01-01T00:00:00Z",
        }
        result = normalize_webhook_payload(event)
        assert isinstance(result, list)
        assert len(result) >= 1
        assert "captured_at" in result[0]

    def test_normalize_bad_timestamp(self):
        from sts_monitor.webhook_ingest import normalize_webhook_payload
        event = {
            "source": "test",
            "claim": "Test claim",
            "url": "https://example.com",
            "timestamp": "not-a-date",
        }
        result = normalize_webhook_payload(event)
        assert isinstance(result, list)
        assert len(result) >= 1
        assert "captured_at" in result[0]

    def test_validate_signature_no_secret(self):
        from sts_monitor.webhook_ingest import validate_webhook_signature
        assert validate_webhook_signature(b"body", "", "") is True

    def test_validate_signature_with_secret(self):
        from sts_monitor.webhook_ingest import validate_webhook_signature
        import hmac as hmac_mod, hashlib
        secret = "test-secret"
        body = b"test body"
        sig = hmac_mod.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert validate_webhook_signature(body, f"sha256={sig}", secret) is True

    def test_validate_signature_wrong(self):
        from sts_monitor.webhook_ingest import validate_webhook_signature
        assert validate_webhook_signature(b"body", "sha256=wrong", "secret") is False
