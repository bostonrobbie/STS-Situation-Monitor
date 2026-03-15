"""Tests for autopilot, multi_llm, helpers, and intel_briefs — targeting 100% coverage."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch, PropertyMock
from uuid import uuid4

import pytest
import sqlalchemy

import sts_monitor.models  # noqa: F401 — ensure all ORM classes registered
from sts_monitor.database import Base


# Private in-memory engine and session factory for test isolation
_test_engine = sqlalchemy.create_engine(
    "sqlite://", connect_args={"check_same_thread": False},
)
_TestSessionLocal = sqlalchemy.orm.sessionmaker(
    bind=_test_engine, autoflush=False, autocommit=False,
)


def SessionLocal():
    """Return a session bound to the test engine."""
    return _TestSessionLocal()


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch):
    import sts_monitor.rate_limit as rl
    import sts_monitor.database as db_mod
    monkeypatch.setattr(rl, "RATE_LIMIT_ENABLED", False)
    # Patch the production SessionLocal so any production code that calls
    # get_session() also uses our test engine.
    monkeypatch.setattr(db_mod, "SessionLocal", _TestSessionLocal)
    Base.metadata.drop_all(bind=_test_engine)
    Base.metadata.create_all(bind=_test_engine)

# ── helpers.py coverage ───────────────────────────────────────────────────


class TestComputeReportLineageValidation:
    def test_no_claims(self):
        from sts_monitor.helpers import compute_report_lineage_validation

        session = SessionLocal()
        result = compute_report_lineage_validation(session, 99999)
        assert result["valid"] is False
        assert result["claims_total"] == 0
        session.close()

    def test_claims_with_evidence(self):
        from sts_monitor.helpers import compute_report_lineage_validation

        from sts_monitor.models import (
            InvestigationORM, ReportORM, ClaimORM, ClaimEvidenceORM, ObservationORM,
        )
        session = SessionLocal()
        inv = InvestigationORM(id=str(uuid4()), topic="lineage test")
        session.add(inv)
        session.flush()
        report = ReportORM(
            investigation_id=inv.id, summary="test", confidence=0.8,
            accepted_json="[]", dropped_json="[]",
        )
        session.add(report)
        session.flush()
        obs = ObservationORM(
            investigation_id=inv.id, source="s", claim="c", url="u",
            captured_at=datetime.now(UTC), reliability_hint=0.8,
        )
        session.add(obs)
        session.flush()
        # Add 3 claims, 3 with evidence  -> coverage = 1.0
        for i in range(3):
            c = ClaimORM(
                investigation_id=inv.id, report_id=report.id,
                claim_text=f"claim {i}", stance="supported", confidence=0.8,
            )
            session.add(c)
            session.flush()
            session.add(ClaimEvidenceORM(
                claim_id=c.id, observation_id=obs.id, weight=0.5, rationale="test",
            ))
        session.commit()
        result = compute_report_lineage_validation(session, report.id)
        assert result["valid"] is True
        assert result["coverage"] == 1.0
        assert result["claims_total"] == 3
        session.close()

    def test_claims_without_evidence(self):
        from sts_monitor.helpers import compute_report_lineage_validation

        from sts_monitor.models import InvestigationORM, ReportORM, ClaimORM
        session = SessionLocal()
        inv = InvestigationORM(id=str(uuid4()), topic="lineage test 2")
        session.add(inv)
        session.flush()
        report = ReportORM(
            investigation_id=inv.id, summary="test", confidence=0.8,
            accepted_json="[]", dropped_json="[]",
        )
        session.add(report)
        session.flush()
        for i in range(3):
            session.add(ClaimORM(
                investigation_id=inv.id, report_id=report.id,
                claim_text=f"claim {i}", stance="supported", confidence=0.8,
            ))
        session.commit()
        result = compute_report_lineage_validation(session, report.id)
        assert result["valid"] is False
        assert result["coverage"] == 0.0
        session.close()


class TestRecordAudit:
    def test_record_audit(self):
        from sts_monitor.helpers import record_audit
        from sts_monitor.security import AuthContext

        from sts_monitor.models import AuditLogORM
        session = SessionLocal()
        actor = AuthContext(label="test-user", role="admin")
        record_audit(
            session, actor=actor, action="test.action",
            resource_type="investigation", resource_id="123",
            detail={"foo": "bar"},
        )
        session.commit()
        logs = session.query(AuditLogORM).all()
        assert len(logs) == 1
        assert logs[0].action == "test.action"
        session.close()


class TestIsValidStructuredLLMPayload:
    def test_valid_payload(self):
        from sts_monitor.helpers import is_valid_structured_llm_payload
        payload = {
            "topic": "test",
            "overall_assessment": "ok",
            "overall_confidence": 0.8,
            "key_claims": [{"status": "supported", "evidence": ["e1"]}],
            "disputed_claims": [],
            "gaps": [],
            "next_actions": [],
        }
        assert is_valid_structured_llm_payload(payload) is True

    def test_missing_fields(self):
        from sts_monitor.helpers import is_valid_structured_llm_payload
        assert is_valid_structured_llm_payload({"topic": "test"}) is False

    def test_bad_confidence(self):
        from sts_monitor.helpers import is_valid_structured_llm_payload
        payload = {
            "topic": "t", "overall_assessment": "o", "overall_confidence": 2.0,
            "key_claims": [{"status": "supported", "evidence": ["e"]}],
            "disputed_claims": [], "gaps": [], "next_actions": [],
        }
        assert is_valid_structured_llm_payload(payload) is False

    def test_empty_key_claims(self):
        from sts_monitor.helpers import is_valid_structured_llm_payload
        payload = {
            "topic": "t", "overall_assessment": "o", "overall_confidence": 0.5,
            "key_claims": [],
            "disputed_claims": [], "gaps": [], "next_actions": [],
        }
        assert is_valid_structured_llm_payload(payload) is False

    def test_bad_claim_status(self):
        from sts_monitor.helpers import is_valid_structured_llm_payload
        payload = {
            "topic": "t", "overall_assessment": "o", "overall_confidence": 0.5,
            "key_claims": [{"status": "invalid_status", "evidence": ["e"]}],
            "disputed_claims": [], "gaps": [], "next_actions": [],
        }
        assert is_valid_structured_llm_payload(payload) is False

    def test_claim_not_dict(self):
        from sts_monitor.helpers import is_valid_structured_llm_payload
        payload = {
            "topic": "t", "overall_assessment": "o", "overall_confidence": 0.5,
            "key_claims": ["not_a_dict"],
            "disputed_claims": [], "gaps": [], "next_actions": [],
        }
        assert is_valid_structured_llm_payload(payload) is False

    def test_claim_empty_evidence(self):
        from sts_monitor.helpers import is_valid_structured_llm_payload
        payload = {
            "topic": "t", "overall_assessment": "o", "overall_confidence": 0.5,
            "key_claims": [{"status": "supported", "evidence": []}],
            "disputed_claims": [], "gaps": [], "next_actions": [],
        }
        assert is_valid_structured_llm_payload(payload) is False

    def test_disputed_not_list(self):
        from sts_monitor.helpers import is_valid_structured_llm_payload
        payload = {
            "topic": "t", "overall_assessment": "o", "overall_confidence": 0.5,
            "key_claims": [{"status": "supported", "evidence": ["e"]}],
            "disputed_claims": "not_a_list", "gaps": [], "next_actions": [],
        }
        assert is_valid_structured_llm_payload(payload) is False


class TestParseLLMStructuredSummary:
    def test_valid_json(self):
        from sts_monitor.helpers import parse_llm_structured_summary
        payload = {
            "topic": "t", "overall_assessment": "o", "overall_confidence": 0.5,
            "key_claims": [{"status": "supported", "evidence": ["e"]}],
            "disputed_claims": [], "gaps": [], "next_actions": [],
        }
        ok, data, err = parse_llm_structured_summary(json.dumps(payload))
        assert ok is True
        assert data is not None
        assert err is None

    def test_invalid_json(self):
        from sts_monitor.helpers import parse_llm_structured_summary
        ok, data, err = parse_llm_structured_summary("not json")
        assert ok is False
        assert "not valid JSON" in err

    def test_json_not_object(self):
        from sts_monitor.helpers import parse_llm_structured_summary
        ok, data, err = parse_llm_structured_summary("[1,2,3]")
        assert ok is False
        assert "not an object" in err

    def test_invalid_fields(self):
        from sts_monitor.helpers import parse_llm_structured_summary
        ok, data, err = parse_llm_structured_summary('{"topic": "test"}')
        assert ok is False
        assert "required structured fields" in err


class TestBuildReportSections:
    def test_build_sections(self):
        from sts_monitor.helpers import build_report_sections
        accepted = [{"claim": "a1"}, {"claim": "a2"}]
        dropped = [{"claim": "d1"}]
        disputed = ["disp1"]
        result = build_report_sections("topic", accepted, dropped, disputed)
        assert "likely_true" in result
        assert "disputed" in result
        assert "unknown" in result
        assert "monitor_next" in result


class TestConnectorDiagnostics:
    @patch("sts_monitor.helpers.httpx.get")
    def test_all_ok(self, mock_get):
        from sts_monitor.helpers import connector_diagnostics
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp
        result = connector_diagnostics()
        assert result["ok"] is True

    @patch("sts_monitor.helpers.httpx.get", side_effect=Exception("timeout"))
    def test_all_fail(self, mock_get):
        from sts_monitor.helpers import connector_diagnostics
        result = connector_diagnostics()
        assert result["ok"] is False
        for check in result["checks"].values():
            assert check["ok"] is False


class TestComputeReadinessScore:
    def test_all_ok(self):
        from sts_monitor.helpers import compute_readiness_score
        result = compute_readiness_score(db_ok=True, llm_ok=True, workspace_ok=True, connectors_ok=True, queue_ok=True)
        assert result["score"] == 100
        assert result["level"] == "ready"

    def test_degraded(self):
        from sts_monitor.helpers import compute_readiness_score
        result = compute_readiness_score(db_ok=True, llm_ok=False, workspace_ok=True, connectors_ok=True, queue_ok=False)
        assert result["level"] == "degraded"

    def test_blocked(self):
        from sts_monitor.helpers import compute_readiness_score
        result = compute_readiness_score(db_ok=False, llm_ok=False, workspace_ok=False, connectors_ok=False, queue_ok=False)
        assert result["level"] == "blocked"


class TestQueueHealthSnapshot:
    def test_healthy(self):
        from sts_monitor.helpers import queue_health_snapshot

        session = SessionLocal()
        result = queue_health_snapshot(session)
        assert result["ok"] is True
        session.close()

    def test_with_failures(self):
        from sts_monitor.helpers import queue_health_snapshot

        from sts_monitor.models import JobORM
        session = SessionLocal()
        session.add(JobORM(
            job_type="ingest", status="failed",
            payload_json="{}",
            run_at=datetime.now(UTC),
        ))
        session.commit()
        result = queue_health_snapshot(session)
        assert result["ok"] is False
        assert "failures" in result["detail"]
        session.close()


class TestWorkspaceHealthSnapshot:
    def test_valid_workspace(self, tmp_path):
        from sts_monitor.helpers import workspace_health_snapshot
        result = workspace_health_snapshot(tmp_path)
        assert result["ok"] is True
        assert result["disk_free_mb"] is not None

    def test_nonexistent_workspace(self, tmp_path):
        from sts_monitor.helpers import workspace_health_snapshot
        result = workspace_health_snapshot(tmp_path / "nonexistent")
        assert result["ok"] is False


class TestPersistClaimLineage:
    def test_persist_with_matching_obs(self):
        from sts_monitor.helpers import persist_claim_lineage

        from sts_monitor.models import InvestigationORM, ObservationORM, ReportORM, ClaimORM
        session = SessionLocal()
        inv = InvestigationORM(id=str(uuid4()), topic="lineage")
        session.add(inv)
        session.flush()
        obs = ObservationORM(
            investigation_id=inv.id, source="s", claim="the claim text here",
            url="u", captured_at=datetime.now(UTC), reliability_hint=0.8,
        )
        session.add(obs)
        report = ReportORM(
            investigation_id=inv.id, summary="s", confidence=0.8,
            accepted_json="[]", dropped_json="[]",
        )
        session.add(report)
        session.flush()
        sections = {
            "likely_true": ["the claim text here"],
            "disputed": ["disputed claim"],
            "unknown": [],
            "monitor_next": [],
        }
        persist_claim_lineage(
            session=session, investigation_id=inv.id, report_id=report.id,
            report_sections=sections, observations=[obs],
        )
        session.commit()
        claims = session.query(ClaimORM).filter_by(report_id=report.id).all()
        assert len(claims) >= 2
        session.close()


# ── multi_llm.py coverage ────────────────────────────────────────────────


class TestMultiLLMClassifyTier:
    def test_exact_match(self):
        from sts_monitor.multi_llm import _classify_model_tier
        assert _classify_model_tier("phi3") == "fast"
        assert _classify_model_tier("llama3.1:8b") == "medium"
        assert _classify_model_tier("llama3.1:70b") == "large"

    def test_prefix_match(self):
        from sts_monitor.multi_llm import _classify_model_tier
        assert _classify_model_tier("phi3-custom") == "fast"

    def test_size_indicator_large(self):
        from sts_monitor.multi_llm import _classify_model_tier
        assert _classify_model_tier("custom-70b-model") == "large"

    def test_size_indicator_fast(self):
        from sts_monitor.multi_llm import _classify_model_tier
        assert _classify_model_tier("custom-mini-model") == "fast"

    def test_default_medium(self):
        from sts_monitor.multi_llm import _classify_model_tier
        assert _classify_model_tier("unknown-model") == "medium"


class TestMultiLLMRouter:
    def test_get_model_default(self):
        from sts_monitor.multi_llm import MultiLLMRouter
        router = MultiLLMRouter(default_model="test-model")
        model = router.get_model_for_task("general")
        assert model == "test-model"

    @patch("httpx.get")
    def test_scan_models(self, mock_get):
        from sts_monitor.multi_llm import MultiLLMRouter
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [
                {"name": "phi3", "size": 2 * 1024**3, "details": {"parameter_size": "3B"}},
                {"name": "llama3.1:70b", "size": 40 * 1024**3, "details": {"parameter_size": "70B"}},
            ]
        }
        mock_get.return_value = mock_resp
        router = MultiLLMRouter()
        models = router.scan_models()
        assert len(models) == 2

    @patch("httpx.get")
    def test_scan_fails(self, mock_get):
        from sts_monitor.multi_llm import MultiLLMRouter
        mock_get.side_effect = Exception("conn error")
        router = MultiLLMRouter()
        models = router.scan_models()
        assert models == []

    @patch("httpx.get")
    def test_scan_bad_status(self, mock_get):
        from sts_monitor.multi_llm import MultiLLMRouter
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp
        router = MultiLLMRouter()
        assert router.scan_models() == []

    def test_get_model_with_models(self):
        from sts_monitor.multi_llm import MultiLLMRouter, ModelInfo
        router = MultiLLMRouter(default_model="fallback")
        router._models = {
            "fast-model": ModelInfo(name="fast-model", tier="fast", available=True, tasks=["entity_extraction"]),
            "large-model": ModelInfo(name="large-model", tier="large", available=True, tasks=["deep_reasoning"]),
        }
        router._last_scan = 9999999999.0
        assert router.get_model_for_task("entity_extraction") == "fast-model"
        assert router.get_model_for_task("deep_reasoning") == "large-model"

    def test_get_model_fallback_tier(self):
        from sts_monitor.multi_llm import MultiLLMRouter, ModelInfo
        router = MultiLLMRouter(default_model="fallback")
        router._models = {
            "medium-model": ModelInfo(name="medium-model", tier="medium", available=True),
        }
        router._last_scan = 9999999999.0
        # Request "fast" but only medium available
        assert router.get_model_for_task("entity_extraction") == "medium-model"

    @patch("sts_monitor.llm.LocalLLMClient")
    def test_call(self, mock_client_cls):
        from sts_monitor.multi_llm import MultiLLMRouter, ModelInfo
        mock_client = MagicMock()
        mock_client.summarize.return_value = "result"
        mock_client_cls.return_value = mock_client
        router = MultiLLMRouter(default_model="test")
        router._models = {
            "test": ModelInfo(name="test", tier="medium", available=True, avg_latency_ms=100),
        }
        router._last_scan = 9999999999.0
        result = router.call("general", "prompt")
        assert result == "result"

    @patch("sts_monitor.llm.LocalLLMClient")
    def test_call_updates_latency(self, mock_client_cls):
        from sts_monitor.multi_llm import MultiLLMRouter, ModelInfo
        mock_client = MagicMock()
        mock_client.summarize.return_value = "result"
        mock_client_cls.return_value = mock_client
        router = MultiLLMRouter(default_model="test")
        router._models = {
            "test": ModelInfo(name="test", tier="medium", available=True, avg_latency_ms=0),
        }
        router._last_scan = 9999999999.0
        router.call("general", "prompt")
        assert router._models["test"].avg_latency_ms > 0

    def test_get_status(self):
        from sts_monitor.multi_llm import MultiLLMRouter, ModelInfo
        router = MultiLLMRouter(default_model="test")
        router._models = {
            "fast": ModelInfo(name="fast", tier="fast", available=True),
        }
        router._last_scan = 9999999999.0
        status = router.get_status()
        assert "models" in status
        assert status["models_available"] == 1

    def test_model_info_to_dict(self):
        from sts_monitor.multi_llm import ModelInfo
        m = ModelInfo(name="test", size_gb=2.0, parameter_count="3B", tier="fast",
                      available=True, avg_latency_ms=150.123, tasks=["entity_extraction"])
        d = m.to_dict()
        assert d["name"] == "test"
        assert d["avg_latency_ms"] == 150.1

    def test_get_router(self):
        from sts_monitor.multi_llm import get_router, _router
        import sts_monitor.multi_llm as mlm
        old = mlm._router
        mlm._router = None
        r = get_router()
        assert r is not None
        mlm._router = old


# ── intel_briefs.py coverage ─────────────────────────────────────────────


class TestGenerateIntelBrief:
    def test_basic_brief(self):
        from sts_monitor.intel_briefs import generate_intel_brief
        obs = [
            {"source": "reuters", "claim": "test claim", "captured_at": datetime.now(UTC)},
            {"source": "bbc", "claim": "another claim", "captured_at": datetime.now(UTC)},
        ]
        entities = [{"normalized": "USA", "entity_type": "GPE"}]
        result = generate_intel_brief("inv1", "test topic", obs, entities)
        assert result["investigation_id"] == "inv1"
        assert result["total_observations"] == 2

    def test_with_timestamps_string(self):
        from sts_monitor.intel_briefs import generate_intel_brief
        obs = [
            {"source": "reuters", "claim": "test", "captured_at": "2025-01-01T00:00:00Z"},
            {"source": "bbc", "claim": "test2", "captured_at": "2025-01-02T00:00:00Z"},
        ]
        result = generate_intel_brief("inv1", "topic", obs, [])
        assert result["time_span"] != ""

    def test_with_bad_timestamp(self):
        from sts_monitor.intel_briefs import generate_intel_brief
        obs = [{"source": "s", "claim": "c", "captured_at": "not-a-date"}]
        result = generate_intel_brief("inv1", "topic", obs, [])
        assert result["total_observations"] == 1

    def test_with_alerts(self):
        from sts_monitor.intel_briefs import generate_intel_brief
        obs = [{"source": "s", "claim": "c", "captured_at": datetime.now(UTC)}]
        alerts = [{"severity": "critical", "message": "alert!"}]
        result = generate_intel_brief("inv1", "topic", obs, [], alerts=alerts)
        assert any("critical" in str(f) for f in result["key_findings"])

    def test_with_comparative(self):
        from sts_monitor.intel_briefs import generate_intel_brief
        obs = [{"source": "s", "claim": "c", "captured_at": datetime.now(UTC)}]
        comparative = {"contradiction_count": 3, "total_sources": 5, "narrative_divergence_score": 0.6}
        result = generate_intel_brief("inv1", "topic", obs, [], comparative=comparative)
        findings = [f["finding"] for f in result["key_findings"]]
        assert any("contradiction" in f.lower() for f in findings)

    def test_with_pattern_analysis(self):
        from sts_monitor.intel_briefs import generate_intel_brief
        obs = [{"source": "s", "claim": "c", "captured_at": datetime.now(UTC)}]
        pattern = {"top_match": {"pattern": "escalation", "similarity": 0.8}}
        result = generate_intel_brief("inv1", "topic", obs, [], pattern_analysis=pattern)
        findings = [f["finding"] for f in result["key_findings"]]
        assert any("pattern" in f.lower() for f in findings)

    def test_with_disputed_observations(self):
        from sts_monitor.intel_briefs import generate_intel_brief
        obs = [
            {"source": "s", "claim": "attack confirmed", "captured_at": datetime.now(UTC)},
            {"source": "s", "claim": "conflict in region", "captured_at": datetime.now(UTC)},
            {"source": "s", "claim": "crisis deepens", "captured_at": datetime.now(UTC)},
        ]
        result = generate_intel_brief("inv1", "topic", obs, [])
        assert result["total_observations"] == 3

    def test_with_llm_client(self):
        from sts_monitor.intel_briefs import generate_intel_brief
        mock_llm = MagicMock()
        mock_llm.summarize.return_value = "LLM summary"
        obs = [{"source": "s", "claim": "c", "captured_at": datetime.now(UTC)}]
        result = generate_intel_brief("inv1", "topic", obs, [], llm_client=mock_llm)
        assert result["executive_summary"] == "LLM summary"

    def test_with_llm_client_fails(self):
        from sts_monitor.intel_briefs import generate_intel_brief
        mock_llm = MagicMock()
        mock_llm.summarize.side_effect = Exception("LLM error")
        obs = [{"source": "s", "claim": "c", "captured_at": datetime.now(UTC)}]
        result = generate_intel_brief("inv1", "topic", obs, [])
        assert "executive_summary" in result

    def test_with_source_scores(self):
        from sts_monitor.intel_briefs import generate_intel_brief
        obs = [{"source": "s", "claim": "c", "captured_at": datetime.now(UTC)}]
        scores = {"leaderboard": [{"source": "reuters", "grade": "A", "accuracy_rate": 0.95, "total_observations": 10}]}
        result = generate_intel_brief("inv1", "topic", obs, [], source_scores=scores)
        assert len(result["source_assessment"]) == 1

    def test_recommendations_low_source(self):
        from sts_monitor.intel_briefs import generate_intel_brief
        obs = [{"source": "s", "claim": "c", "captured_at": datetime.now(UTC)}]
        result = generate_intel_brief("inv1", "topic", obs, [])
        assert any("source" in r.lower() for r in result["recommendations"])

    def test_recommendations_escalation(self):
        from sts_monitor.intel_briefs import generate_intel_brief
        obs = [{"source": "s", "claim": "c", "captured_at": datetime.now(UTC)}]
        pattern = {"escalation_score": 0.7, "top_match": None}
        result = generate_intel_brief("inv1", "topic", obs, [], pattern_analysis=pattern)
        assert any("escalation" in r.lower() for r in result["recommendations"])

    def test_recommendations_silence(self):
        from sts_monitor.intel_briefs import generate_intel_brief
        obs = [{"source": "s", "claim": "c", "captured_at": datetime.now(UTC)}]
        comparative = {"silence_count": 2}
        result = generate_intel_brief("inv1", "topic", obs, [], comparative=comparative)
        assert any("silence" in r.lower() for r in result["recommendations"])


class TestBriefToMarkdown:
    def test_full_brief(self):
        from sts_monitor.intel_briefs import brief_to_markdown
        brief = {
            "period": "Daily",
            "topic": "test",
            "classification": "UNCLASSIFIED",
            "generated_at": "2025-01-01T00:00:00",
            "time_span": "24 hours",
            "executive_summary": "Summary here.",
            "key_findings": [{"finding": "Finding 1", "confidence": "high"}],
            "total_observations": 10,
            "source_count": 3,
            "sources": ["reuters", "bbc", "cnn"],
            "top_entities": [{"entity": "USA (GPE)", "mentions": 5}],
            "source_assessment": [{"source": "reuters", "grade": "A", "accuracy": 0.95}],
            "recommendations": ["Keep monitoring"],
            "alerts": [{"severity": "high", "message": "Alert!"}],
        }
        md = brief_to_markdown(brief)
        assert "# Daily Brief: test" in md
        assert "Finding 1" in md
        assert "reuters" in md

    def test_minimal_brief(self):
        from sts_monitor.intel_briefs import brief_to_markdown
        brief = {"period": "Weekly", "topic": "t"}
        md = brief_to_markdown(brief)
        assert "Weekly" in md


# ── notifications.py coverage ────────────────────────────────────────────


class TestNotifications:
    def test_alert_notification_dataclass(self):
        from sts_monitor.notifications import AlertNotification
        n = AlertNotification(title="Test", message="msg", severity="high",
                              investigation_id="inv1", metadata={"k": "v"})
        assert n.title == "Test"

    @patch.dict("os.environ", {"STS_SMTP_HOST": "", "STS_SMTP_TO": ""})
    def test_send_email_no_config(self):
        from sts_monitor.notifications import send_email, AlertNotification
        n = AlertNotification(title="Test", message="msg")
        assert send_email(n) is False

    @patch("sts_monitor.notifications.smtplib.SMTP")
    @patch.dict("os.environ", {})
    def test_send_email_success(self, mock_smtp):
        import sts_monitor.notifications as notif
        old_host, old_to = notif.SMTP_HOST, notif.SMTP_TO
        notif.SMTP_HOST = "smtp.test.com"
        notif.SMTP_TO = "test@test.com"
        notif.SMTP_PORT = 587
        notif.SMTP_USER = "user"
        notif.SMTP_PASSWORD = "pass"
        try:
            mock_server = MagicMock()
            mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
            n = notif.AlertNotification(
                title="Test", message="msg", severity="critical",
                investigation_id="inv1", metadata={"key": "val"},
            )
            result = notif.send_email(n)
            assert result is True
        finally:
            notif.SMTP_HOST = old_host
            notif.SMTP_TO = old_to

    @patch("sts_monitor.notifications.smtplib.SMTP", side_effect=Exception("SMTP error"))
    def test_send_email_failure(self, mock_smtp):
        import sts_monitor.notifications as notif
        old_host, old_to = notif.SMTP_HOST, notif.SMTP_TO
        notif.SMTP_HOST = "smtp.test.com"
        notif.SMTP_TO = "test@test.com"
        try:
            n = notif.AlertNotification(title="Test", message="msg")
            result = notif.send_email(n)
            assert result is False
        finally:
            notif.SMTP_HOST = old_host
            notif.SMTP_TO = old_to

    def test_send_slack_no_config(self):
        import sts_monitor.notifications as notif
        old = notif.SLACK_WEBHOOK_URL
        notif.SLACK_WEBHOOK_URL = ""
        try:
            n = notif.AlertNotification(title="Test", message="msg")
            assert notif.send_slack(n) is False
        finally:
            notif.SLACK_WEBHOOK_URL = old

    @patch("sts_monitor.notifications.httpx.post")
    def test_send_slack_success(self, mock_post):
        import sts_monitor.notifications as notif
        old_url = notif.SLACK_WEBHOOK_URL
        old_channel = notif.SLACK_CHANNEL
        notif.SLACK_WEBHOOK_URL = "https://hooks.slack.com/test"
        notif.SLACK_CHANNEL = "#alerts"
        try:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp
            n = notif.AlertNotification(title="Test", message="msg", investigation_id="inv1")
            assert notif.send_slack(n) is True
        finally:
            notif.SLACK_WEBHOOK_URL = old_url
            notif.SLACK_CHANNEL = old_channel

    @patch("sts_monitor.notifications.httpx.post", side_effect=Exception("HTTP error"))
    def test_send_slack_failure(self, mock_post):
        import sts_monitor.notifications as notif
        old = notif.SLACK_WEBHOOK_URL
        notif.SLACK_WEBHOOK_URL = "https://hooks.slack.com/test"
        try:
            n = notif.AlertNotification(title="Test", message="msg")
            assert notif.send_slack(n) is False
        finally:
            notif.SLACK_WEBHOOK_URL = old

    def test_send_webhook_no_config(self):
        import sts_monitor.notifications as notif
        old = notif.WEBHOOK_URL
        notif.WEBHOOK_URL = ""
        try:
            n = notif.AlertNotification(title="Test", message="msg")
            assert notif.send_webhook(n) is False
        finally:
            notif.WEBHOOK_URL = old

    @patch("sts_monitor.notifications.httpx.post")
    def test_send_webhook_success(self, mock_post):
        import sts_monitor.notifications as notif
        old = notif.WEBHOOK_URL
        notif.WEBHOOK_URL = "https://example.com/webhook"
        try:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp
            n = notif.AlertNotification(title="Test", message="msg", metadata={"k": "v"})
            assert notif.send_webhook(n) is True
        finally:
            notif.WEBHOOK_URL = old

    @patch("sts_monitor.notifications.httpx.post", side_effect=Exception("err"))
    def test_send_webhook_failure(self, mock_post):
        import sts_monitor.notifications as notif
        old = notif.WEBHOOK_URL
        notif.WEBHOOK_URL = "https://example.com/webhook"
        try:
            n = notif.AlertNotification(title="Test", message="msg")
            assert notif.send_webhook(n) is False
        finally:
            notif.WEBHOOK_URL = old

    def test_notify_all_no_channels(self):
        import sts_monitor.notifications as notif
        old_smtp = notif.SMTP_HOST
        old_slack = notif.SLACK_WEBHOOK_URL
        old_webhook = notif.WEBHOOK_URL
        notif.SMTP_HOST = ""
        notif.SMTP_TO = ""
        notif.SLACK_WEBHOOK_URL = ""
        notif.WEBHOOK_URL = ""
        try:
            n = notif.AlertNotification(title="Test", message="msg")
            result = notif.notify_all(n)
            assert result == {}
        finally:
            notif.SMTP_HOST = old_smtp
            notif.SLACK_WEBHOOK_URL = old_slack
            notif.WEBHOOK_URL = old_webhook

    @patch("sts_monitor.notifications.httpx.post")
    def test_notify_all_with_channels(self, mock_post):
        import sts_monitor.notifications as notif
        old_slack = notif.SLACK_WEBHOOK_URL
        old_webhook = notif.WEBHOOK_URL
        notif.SLACK_WEBHOOK_URL = "https://slack.test"
        notif.WEBHOOK_URL = "https://webhook.test"
        try:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp
            n = notif.AlertNotification(title="Test", message="msg")
            result = notif.notify_all(n)
            assert "slack" in result
            assert "webhook" in result
        finally:
            notif.SLACK_WEBHOOK_URL = old_slack
            notif.WEBHOOK_URL = old_webhook


# ── autopilot.py coverage ────────────────────────────────────────────────


class TestAutopilotCycleLog:
    def test_cycle_log_defaults(self):
        from sts_monitor.autopilot import CycleLog
        log = CycleLog(
            investigation_id="inv1",
            investigation_topic="test",
            started_at="2025-01-01T00:00:00",
        )
        assert log.observations_ingested == 0
        assert log.connectors_used == []
        assert log.error is None


class TestAutopilotState:
    def test_state_to_dict(self):
        from sts_monitor.autopilot import AutopilotState
        state = AutopilotState()
        d = state.to_dict()
        assert "running" in d
        assert "total_cycles" in d


class TestCheckLLMAvailable:
    @patch("httpx.get")
    def test_llm_available(self, mock_get):
        from sts_monitor.autopilot import _check_llm_available
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp
        assert _check_llm_available() is True

    @patch("httpx.get", side_effect=Exception("err"))
    def test_llm_unavailable(self, mock_get):
        from sts_monitor.autopilot import _check_llm_available
        assert _check_llm_available() is False


class TestCollectFromRealConnectors:
    @patch("sts_monitor.connectors.GDELTConnector")
    @patch("sts_monitor.connectors.USGSEarthquakeConnector")
    @patch("sts_monitor.connectors.NWSAlertConnector")
    @patch("sts_monitor.connectors.ReliefWebConnector")
    @patch("sts_monitor.connectors.RedditConnector")
    def test_all_succeed(self, mock_reddit, mock_rw, mock_nws, mock_usgs, mock_gdelt):
        from sts_monitor.autopilot import _collect_from_real_connectors
        from sts_monitor.pipeline import Observation
        obs = Observation(source="s", claim="c", url="u", captured_at=datetime.now(UTC), reliability_hint=0.5)
        for mock in [mock_gdelt, mock_usgs, mock_nws, mock_rw, mock_reddit]:
            mock_instance = MagicMock()
            mock_instance.collect.return_value = MagicMock(observations=[obs])
            mock.return_value = mock_instance

        all_obs, used, failed = _collect_from_real_connectors("topic")
        assert len(all_obs) >= 5
        assert "gdelt" in used

    @patch("sts_monitor.connectors.GDELTConnector")
    @patch("sts_monitor.connectors.USGSEarthquakeConnector")
    @patch("sts_monitor.connectors.NWSAlertConnector")
    @patch("sts_monitor.connectors.ReliefWebConnector")
    @patch("sts_monitor.connectors.RedditConnector")
    def test_all_fail(self, mock_reddit, mock_rw, mock_nws, mock_usgs, mock_gdelt):
        from sts_monitor.autopilot import _collect_from_real_connectors
        for mock in [mock_gdelt, mock_usgs, mock_nws, mock_rw, mock_reddit]:
            mock.return_value.collect.side_effect = Exception("fail")

        all_obs, used, failed = _collect_from_real_connectors("topic")
        assert len(all_obs) == 0
        assert len(failed) == 5

    @patch("sts_monitor.connectors.GDELTConnector")
    @patch("sts_monitor.connectors.USGSEarthquakeConnector")
    @patch("sts_monitor.connectors.NWSAlertConnector")
    @patch("sts_monitor.connectors.ReliefWebConnector")
    @patch("sts_monitor.connectors.RedditConnector")
    def test_empty_results(self, mock_reddit, mock_rw, mock_nws, mock_usgs, mock_gdelt):
        from sts_monitor.autopilot import _collect_from_real_connectors
        for mock in [mock_gdelt, mock_usgs, mock_nws, mock_rw, mock_reddit]:
            mock.return_value.collect.return_value = MagicMock(observations=[])

        all_obs, used, failed = _collect_from_real_connectors("topic")
        assert len(all_obs) == 0
        assert "gdelt:empty" in failed


class TestStartStopAutopilot:
    def test_start_no_event_loop(self):
        from sts_monitor.autopilot import start_autopilot, _state
        _state.running = False
        import sts_monitor.autopilot as ap
        ap._task = None
        result = start_autopilot()
        assert result["status"] == "started"
        assert _state.enabled is True

    def test_stop(self):
        from sts_monitor.autopilot import stop_autopilot
        result = stop_autopilot()
        assert result["status"] == "stopped"

    def test_start_already_running(self):
        from sts_monitor.autopilot import start_autopilot
        import sts_monitor.autopilot as ap
        mock_task = MagicMock()
        mock_task.done.return_value = False
        ap._task = mock_task
        result = start_autopilot()
        assert result["status"] == "already_running"
        ap._task = None


class TestRunCycleForInvestigation:
    @patch("sts_monitor.autopilot._collect_from_real_connectors")
    def test_cycle_with_simulated_fallback(self, mock_collect):
        from sts_monitor.autopilot import _run_cycle_for_investigation

        from sts_monitor.models import InvestigationORM
        mock_collect.return_value = ([], [], ["gdelt:fail"])
        session = SessionLocal()
        inv = InvestigationORM(id=str(uuid4()), topic="cycle test")
        session.add(inv)
        session.commit()
        inv_id = inv.id
        session.close()
        log = _run_cycle_for_investigation(inv_id, "cycle test", False)
        assert log.observations_ingested > 0
        assert "simulated" in log.connectors_used

    @patch("sts_monitor.autopilot.AUTOPILOT_USE_REAL_CONNECTORS", False)
    def test_cycle_no_real_connectors(self):
        from sts_monitor.autopilot import _run_cycle_for_investigation

        from sts_monitor.models import InvestigationORM
        session = SessionLocal()
        inv = InvestigationORM(id=str(uuid4()), topic="no real test")
        session.add(inv)
        session.commit()
        inv_id = inv.id
        session.close()
        log = _run_cycle_for_investigation(inv_id, "no real test", False)
        assert log.observations_ingested > 0


# ── plugins.py coverage ──────────────────────────────────────────────────


class TestPlugins:
    def test_register_and_list(self):
        from sts_monitor.plugins import PluginRegistry
        registry = PluginRegistry()

        class FakeConnector:
            name = "fake"
            def collect(self, query=""):
                pass

        registry.register_connector("fake", FakeConnector, description="test", version="1.0")
        assert "fake" in registry.registered
        assert registry.registered["fake"]["name"] == "fake"

    def test_get_connector(self):
        from sts_monitor.plugins import PluginRegistry

        class FakeConnector:
            name = "fake"
            def collect(self, query=""):
                pass

        registry = PluginRegistry()
        registry.register_connector("fake", FakeConnector)
        assert registry.get_connector("fake") is FakeConnector
        assert registry.get_connector("nonexistent") is None

    def test_create_connector(self):
        from sts_monitor.plugins import PluginRegistry

        class FakeConnector:
            name = "fake"
            def collect(self, query=""):
                pass

        registry = PluginRegistry()
        registry.register_connector("fake", FakeConnector)
        instance = registry.create_connector("fake")
        assert isinstance(instance, FakeConnector)

    def test_create_connector_not_found(self):
        from sts_monitor.plugins import PluginRegistry
        registry = PluginRegistry()
        with pytest.raises(KeyError):
            registry.create_connector("nonexistent")

    def test_discover_entrypoints(self):
        from sts_monitor.plugins import PluginRegistry
        registry = PluginRegistry()
        count = registry.discover_entrypoints()
        assert isinstance(count, int)

    def test_discover_plugin_dir_nonexistent(self):
        from sts_monitor.plugins import PluginRegistry
        from pathlib import Path
        registry = PluginRegistry()
        count = registry.discover_plugin_dir(Path("/nonexistent/path"))
        assert count == 0

    def test_discover_plugin_dir_with_files(self, tmp_path):
        from sts_monitor.plugins import PluginRegistry
        # Write a plugin file
        plugin_file = tmp_path / "test_plugin.py"
        plugin_file.write_text("""
class TestConnector:
    name = "test_file_plugin"
    def collect(self, query=""):
        return None
""")
        registry = PluginRegistry()
        count = registry.discover_plugin_dir(tmp_path)
        assert count >= 1

    def test_discover_plugin_dir_skip_underscore(self, tmp_path):
        from sts_monitor.plugins import PluginRegistry
        (tmp_path / "_internal.py").write_text("x = 1")
        registry = PluginRegistry()
        count = registry.discover_plugin_dir(tmp_path)
        assert count == 0

    def test_discover_all(self):
        from sts_monitor.plugins import PluginRegistry
        registry = PluginRegistry()
        result = registry.discover_all()
        assert "entrypoints" in result
        assert "plugin_dir" in result

    def test_global_registry(self):
        from sts_monitor.plugins import plugin_registry, PluginRegistry
        assert isinstance(plugin_registry, PluginRegistry)


# ── cross_investigation.py coverage ──────────────────────────────────────


class TestCrossInvestigation:
    def test_less_than_two_investigations(self):
        from sts_monitor.cross_investigation import detect_cross_investigation_links

        session = SessionLocal()
        result = detect_cross_investigation_links(session)
        assert result.investigations_analyzed == 0
        assert result.total_links == 0
        session.close()

    def test_two_investigations_shared_entity(self):
        from sts_monitor.cross_investigation import detect_cross_investigation_links

        from sts_monitor.models import InvestigationORM, ObservationORM, EntityMentionORM
        session = SessionLocal()
        inv1 = InvestigationORM(id=str(uuid4()), topic="inv1")
        inv2 = InvestigationORM(id=str(uuid4()), topic="inv2")
        session.add_all([inv1, inv2])
        session.flush()
        obs1 = ObservationORM(
            investigation_id=inv1.id, source="reuters", claim="USA conflict",
            url="u1", captured_at=datetime.now(UTC), reliability_hint=0.8,
        )
        obs2 = ObservationORM(
            investigation_id=inv2.id, source="reuters", claim="USA economy",
            url="u2", captured_at=datetime.now(UTC), reliability_hint=0.8,
        )
        session.add_all([obs1, obs2])
        session.flush()
        session.add(EntityMentionORM(
            observation_id=obs1.id, investigation_id=inv1.id,
            entity_text="USA", entity_type="GPE", normalized="usa",
            confidence=0.9, start_pos=0, end_pos=3,
        ))
        session.add(EntityMentionORM(
            observation_id=obs2.id, investigation_id=inv2.id,
            entity_text="USA", entity_type="GPE", normalized="usa",
            confidence=0.9, start_pos=0, end_pos=3,
        ))
        session.commit()
        result = detect_cross_investigation_links(session)
        assert result.investigations_analyzed == 2
        d = result.to_dict()
        assert "shared_entities" in d
        session.close()

    def test_shared_sources(self):
        from sts_monitor.cross_investigation import detect_cross_investigation_links

        from sts_monitor.models import InvestigationORM, ObservationORM
        session = SessionLocal()
        inv1 = InvestigationORM(id=str(uuid4()), topic="inv1")
        inv2 = InvestigationORM(id=str(uuid4()), topic="inv2")
        session.add_all([inv1, inv2])
        session.flush()
        for inv in [inv1, inv2]:
            session.add(ObservationORM(
                investigation_id=inv.id, source="reuters", claim="test",
                url="u", captured_at=datetime.now(UTC), reliability_hint=0.8,
            ))
        session.commit()
        result = detect_cross_investigation_links(session)
        assert len(result.shared_sources) >= 1
        session.close()

    def test_crosslink_to_dict(self):
        from sts_monitor.cross_investigation import CrossLink
        link = CrossLink(
            entity_text="test", entity_type="GPE",
            investigation_ids=["a", "b"], investigation_topics=["t1", "t2"],
            mention_counts={"a": 1, "b": 2}, total_mentions=3,
            confidence=0.5, link_type="shared_entity",
            first_seen=datetime.now(UTC), last_seen=datetime.now(UTC),
            sample_claims=["claim1"],
        )
        d = link.to_dict()
        assert d["entity"] == "test"
        assert d["total_mentions"] == 3


# ── retention.py coverage ────────────────────────────────────────────────


class TestRetention:
    def test_dry_run(self):
        from sts_monitor.retention import run_retention
        result = run_retention(dry_run=True)
        assert result["dry_run"] is True

    def test_actual_run(self):
        from sts_monitor.retention import run_retention
        result = run_retention(max_age_days=0, job_max_age_days=0, alert_max_age_days=0)
        assert result["dry_run"] is False

    def test_with_old_data(self):
        from sts_monitor.retention import run_retention

        from sts_monitor.models import InvestigationORM, ObservationORM, JobORM, AlertEventORM
        from datetime import timedelta
        session = SessionLocal()
        # Closed investigation with old data
        inv = InvestigationORM(id=str(uuid4()), topic="old", status="closed")
        session.add(inv)
        session.flush()
        old_date = datetime.now(UTC) - timedelta(days=200)
        session.add(ObservationORM(
            investigation_id=inv.id, source="s", claim="c", url="u",
            captured_at=old_date, reliability_hint=0.5,
        ))
        session.add(JobORM(
            job_type="ingest", status="completed",
            payload_json="{}",
            run_at=old_date,
            created_at=old_date,
        ))
        session.add(AlertEventORM(
            investigation_id=inv.id, severity="info", message="old alert",
            triggered_at=old_date,
        ))
        session.commit()
        session.close()
        result = run_retention(max_age_days=90, job_max_age_days=30, alert_max_age_days=60)
        assert result["observations_removed"] >= 1
        assert result["jobs_removed"] >= 1
        assert result["alert_events_removed"] >= 1

    def test_open_investigations_protected(self):
        from sts_monitor.retention import run_retention

        from sts_monitor.models import InvestigationORM, ObservationORM
        from datetime import timedelta
        session = SessionLocal()
        inv = InvestigationORM(id=str(uuid4()), topic="active", status="open")
        session.add(inv)
        session.flush()
        old_date = datetime.now(UTC) - timedelta(days=200)
        session.add(ObservationORM(
            investigation_id=inv.id, source="s", claim="c", url="u",
            captured_at=old_date, reliability_hint=0.5,
        ))
        session.commit()
        session.close()
        result = run_retention(max_age_days=90, dry_run=True)
        assert result["observations_removed"] == 0
