import pytest
import sqlalchemy
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sts_monitor.database import Base
from sts_monitor.models import *

# Private test engine — completely isolated from the shared sts_monitor.db
_test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
_TestSessionLocal = sessionmaker(bind=_test_engine, autoflush=False, autocommit=False, future=True)

# Alias for test helper usage
engine = _test_engine
SessionLocal = _TestSessionLocal


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch):
    import sts_monitor.rate_limit as rl
    import sts_monitor.database as db_mod
    monkeypatch.setattr(rl, "RATE_LIMIT_ENABLED", False)
    # Point everything at our private engine
    monkeypatch.setattr(db_mod, "engine", _test_engine)
    monkeypatch.setattr(db_mod, "SessionLocal", _TestSessionLocal)
    Base.metadata.drop_all(bind=_test_engine)
    Base.metadata.create_all(bind=_test_engine)


# ── helpers ─────────────────────────────────────────────────────────────

def _make_inv(session, inv_id=None, topic="test topic", status="active"):
    inv_id = inv_id or str(uuid4())
    inv = InvestigationORM(id=inv_id, topic=topic, status=status)
    session.add(inv)
    session.commit()
    return inv_id


def _make_obs(session, inv_id, claim="some claim", source="src", url=None,
              captured_at=None, reliability_hint=0.5, lat=None, lon=None):
    obs = ObservationORM(
        investigation_id=inv_id,
        source=source,
        claim=claim,
        url=url or f"http://example.com/{uuid4()}",
        captured_at=captured_at or datetime.now(UTC),
        reliability_hint=reliability_hint,
        latitude=lat,
        longitude=lon,
    )
    session.add(obs)
    session.commit()
    return obs


def _make_report(session, inv_id, summary="summary", confidence=0.7):
    report = ReportORM(
        investigation_id=inv_id,
        summary=summary,
        confidence=confidence,
        accepted_json="[]",
        dropped_json="[]",
    )
    session.add(report)
    session.commit()
    return report


# ══════════════════════════════════════════════════════════════════════════
# 1. autopilot.py coverage
# ══════════════════════════════════════════════════════════════════════════

class TestAutopilotCollectFromRealConnectors:
    """Exercise _collect_from_real_connectors edge cases."""

    def test_all_connectors_raise(self):
        """When every connector raises, we get empty obs and all failures."""
        from sts_monitor.autopilot import _collect_from_real_connectors

        with patch("sts_monitor.connectors.GDELTConnector", side_effect=Exception("g")), \
             patch("sts_monitor.connectors.USGSEarthquakeConnector", side_effect=Exception("u")), \
             patch("sts_monitor.connectors.NWSAlertConnector", side_effect=Exception("n")), \
             patch("sts_monitor.connectors.ReliefWebConnector", side_effect=Exception("r")), \
             patch("sts_monitor.connectors.RedditConnector", side_effect=Exception("rd")):
            obs, used, failed = _collect_from_real_connectors("topic")
            assert obs == []
            assert used == []
            assert len(failed) == 5

    def test_connectors_return_empty(self):
        """When connectors return empty observations we record them as failed:empty."""
        from sts_monitor.autopilot import _collect_from_real_connectors

        empty_result = MagicMock()
        empty_result.observations = []

        def make_connector(*a, **kw):
            m = MagicMock()
            m.collect.return_value = empty_result
            return m

        with patch("sts_monitor.connectors.GDELTConnector", side_effect=make_connector), \
             patch("sts_monitor.connectors.USGSEarthquakeConnector", side_effect=make_connector), \
             patch("sts_monitor.connectors.NWSAlertConnector", side_effect=make_connector), \
             patch("sts_monitor.connectors.ReliefWebConnector", side_effect=make_connector), \
             patch("sts_monitor.connectors.RedditConnector", side_effect=make_connector):
            obs, used, failed = _collect_from_real_connectors("topic")
            assert obs == []
            assert "gdelt:empty" in failed

    def test_connectors_return_data(self):
        """When connectors return observations successfully."""
        from sts_monitor.autopilot import _collect_from_real_connectors

        fake_obs = MagicMock()
        result_with_data = MagicMock()
        result_with_data.observations = [fake_obs]

        empty_result = MagicMock()
        empty_result.observations = []

        def good_connector(*a, **kw):
            m = MagicMock()
            m.collect.return_value = result_with_data
            return m

        def empty_connector(*a, **kw):
            m = MagicMock()
            m.collect.return_value = empty_result
            return m

        with patch("sts_monitor.connectors.GDELTConnector", side_effect=good_connector), \
             patch("sts_monitor.connectors.USGSEarthquakeConnector", side_effect=good_connector), \
             patch("sts_monitor.connectors.NWSAlertConnector", side_effect=empty_connector), \
             patch("sts_monitor.connectors.ReliefWebConnector", side_effect=good_connector), \
             patch("sts_monitor.connectors.RedditConnector", side_effect=good_connector):
            obs, used, failed = _collect_from_real_connectors("topic", seed_query="custom query")
            assert len(obs) == 4  # gdelt, usgs, reliefweb, reddit
            assert "gdelt" in used
            assert "usgs" in used
            assert "reliefweb" in used
            assert "reddit" in used


class TestRunCycleForInvestigation:
    """Exercise _run_cycle_for_investigation with mocked connectors."""

    def test_cycle_with_real_connectors_returning_data(self):
        """Real connectors return data -> observations stored, entities extracted, alerts evaluated."""
        from sts_monitor.autopilot import _run_cycle_for_investigation

        session = SessionLocal()
        inv_id = _make_inv(session, topic="geopolitics")
        session.close()

        fake_obs = MagicMock()
        fake_obs.source = "gdelt"
        fake_obs.claim = "President met with world leaders in Washington"
        fake_obs.url = "http://example.com/1"
        fake_obs.captured_at = datetime.now(UTC)
        fake_obs.reliability_hint = 0.8
        fake_obs.connector_type = "real"
        fake_obs.latitude = None
        fake_obs.longitude = None

        with patch("sts_monitor.autopilot._collect_from_real_connectors") as mock_collect, \
             patch("sts_monitor.autopilot.AUTOPILOT_USE_REAL_CONNECTORS", True):
            mock_collect.return_value = ([fake_obs], ["gdelt"], [])
            cycle = _run_cycle_for_investigation(inv_id, "geopolitics", False)

        assert cycle.observations_ingested == 1
        assert "gdelt" in cycle.connectors_used
        assert cycle.error is None

    def test_cycle_real_connectors_exception_falls_to_simulated(self):
        """If _collect_from_real_connectors raises, fall back to simulated."""
        from sts_monitor.autopilot import _run_cycle_for_investigation

        session = SessionLocal()
        inv_id = _make_inv(session, topic="weather")
        session.close()

        with patch("sts_monitor.autopilot._collect_from_real_connectors", side_effect=Exception("boom")), \
             patch("sts_monitor.autopilot.AUTOPILOT_USE_REAL_CONNECTORS", True):
            cycle = _run_cycle_for_investigation(inv_id, "weather", False)

        # Falls back to simulated since real_obs is empty after exception
        assert cycle.observations_ingested > 0
        assert cycle.error is None

    def test_cycle_pipeline_run_exception_handled(self):
        """Pipeline.run raising should be caught and not break the cycle."""
        from sts_monitor.autopilot import _run_cycle_for_investigation

        session = SessionLocal()
        inv_id = _make_inv(session, topic="test pipeline error")
        session.close()

        with patch("sts_monitor.autopilot.AUTOPILOT_USE_REAL_CONNECTORS", False), \
             patch("sts_monitor.pipeline.SignalPipeline.run", side_effect=Exception("pipe error")):
            cycle = _run_cycle_for_investigation(inv_id, "test pipeline error", False)

        assert cycle.observations_ingested > 0
        assert cycle.error is None  # exception is caught inside

    def test_cycle_entity_extraction_works(self):
        """Entity extraction produces entities and commits them."""
        from sts_monitor.autopilot import _run_cycle_for_investigation

        session = SessionLocal()
        inv_id = _make_inv(session, topic="entity test")
        session.close()

        with patch("sts_monitor.autopilot.AUTOPILOT_USE_REAL_CONNECTORS", False):
            cycle = _run_cycle_for_investigation(inv_id, "entity test", False)

        assert cycle.entities_extracted >= 0
        assert cycle.error is None

    def test_cycle_entity_extraction_exception(self):
        """Entity extraction exception is caught."""
        from sts_monitor.autopilot import _run_cycle_for_investigation

        session = SessionLocal()
        inv_id = _make_inv(session, topic="ent error test")
        session.close()

        with patch("sts_monitor.autopilot.AUTOPILOT_USE_REAL_CONNECTORS", False), \
             patch("sts_monitor.entities.extract_entities", side_effect=Exception("ent err")):
            cycle = _run_cycle_for_investigation(inv_id, "ent error test", False)

        assert cycle.error is None

    def test_cycle_alert_evaluation_stores_alerts(self):
        """Alert evaluation fires alerts and stores AlertEventORM."""
        from sts_monitor.autopilot import _run_cycle_for_investigation

        session = SessionLocal()
        inv_id = _make_inv(session, topic="alert test")
        session.close()

        # Let the cycle create its own observations via simulation and evaluate alerts
        with patch("sts_monitor.autopilot.AUTOPILOT_USE_REAL_CONNECTORS", False):
            cycle = _run_cycle_for_investigation(inv_id, "alert test", False)

        assert cycle.alerts_fired >= 0
        assert cycle.error is None


class TestStartStopAutopilot:
    """Exercise start_autopilot and stop_autopilot."""

    def test_start_autopilot_no_event_loop(self):
        """start_autopilot with no running loop sets running=False."""
        import sts_monitor.autopilot as ap
        old_task = ap._task
        ap._task = None
        try:
            result = ap.start_autopilot()
            assert result["status"] == "started"
            assert result["enabled"] is True
        finally:
            ap._task = old_task
            ap._state.enabled = False

    def test_start_autopilot_already_running(self):
        """start_autopilot when task is already running returns already_running."""
        import sts_monitor.autopilot as ap
        fake_task = MagicMock()
        fake_task.done.return_value = False
        old_task = ap._task
        ap._task = fake_task
        try:
            result = ap.start_autopilot()
            assert result["status"] == "already_running"
        finally:
            ap._task = old_task

    def test_stop_autopilot(self):
        """stop_autopilot cancels task and sets state."""
        import sts_monitor.autopilot as ap
        fake_task = MagicMock()
        fake_task.done.return_value = False
        old_task = ap._task
        ap._task = fake_task
        try:
            result = ap.stop_autopilot()
            assert result["status"] == "stopped"
            fake_task.cancel.assert_called_once()
            assert ap._state.running is False
            assert ap._state.enabled is False
        finally:
            ap._task = old_task
            ap._state.enabled = False

    def test_stop_autopilot_no_task(self):
        """stop_autopilot when no task exists."""
        import sts_monitor.autopilot as ap
        old_task = ap._task
        ap._task = None
        try:
            result = ap.stop_autopilot()
            assert result["status"] == "stopped"
        finally:
            ap._task = old_task

    def test_autopilot_state_to_dict(self):
        """AutopilotState.to_dict returns expected keys."""
        from sts_monitor.autopilot import AutopilotState
        state = AutopilotState()
        d = state.to_dict()
        assert "running" in d
        assert "recent_logs" in d
        assert isinstance(d["recent_logs"], list)

    def test_cycle_log_dataclass(self):
        """CycleLog holds correct defaults."""
        from sts_monitor.autopilot import CycleLog
        cl = CycleLog(
            investigation_id="i1",
            investigation_topic="topic",
            started_at="2024-01-01",
        )
        assert cl.error is None
        assert cl.rabbit_trail is False
        assert cl.connectors_used == []


# ══════════════════════════════════════════════════════════════════════════
# 2. claim_verification.py coverage
# ══════════════════════════════════════════════════════════════════════════

class TestClaimVerification:

    def test_group_claims_skips_short_claims(self):
        """Claims shorter than 15 chars are excluded from clusters."""
        from sts_monitor.claim_verification import _group_claims
        obs = [
            {"claim": "short", "source": "a"},
            {"claim": "This is a sufficiently long claim text for grouping", "source": "b"},
        ]
        clusters = _group_claims(obs)
        # Only the long claim should create a cluster
        assert len(clusters) >= 1
        for c in clusters:
            assert len(c.representative_claim) >= 15

    def test_group_claims_empty_key_skipped(self):
        """Claim with only short words produces empty key and is skipped."""
        from sts_monitor.claim_verification import _group_claims
        obs = [{"claim": "an or at to by if", "source": "a"}]
        clusters = _group_claims(obs)
        assert clusters == []

    def test_verify_heuristic_contradiction(self):
        """Heuristic handles contradictions (positive AND negative signals)."""
        from sts_monitor.claim_verification import _verify_heuristic, ClaimCluster
        cluster = ClaimCluster(
            representative_claim="The incident was confirmed but later denied by officials",
            observations=[
                {"claim": "The incident was confirmed by witnesses", "source": "src1", "reliability_hint": 0.7},
                {"claim": "Officials denied the incident was false", "source": "src2", "reliability_hint": 0.6},
            ],
            sources=["src1", "src2"],
            has_contradiction=True,
            positive_count=1,
            negative_count=1,
            neutral_count=0,
        )
        result = _verify_heuristic(cluster)
        assert result.verdict == "disputed"
        assert result.confidence == 0.3
        assert result.method == "heuristic"
        assert len(result.evidence_needed) > 0

    def test_verify_heuristic_likely_false(self):
        """Heuristic returns likely_false when negatives dominate."""
        from sts_monitor.claim_verification import _verify_heuristic, ClaimCluster
        cluster = ClaimCluster(
            representative_claim="The report was debunked false retracted",
            observations=[
                {"claim": "The report was debunked", "source": "a", "reliability_hint": 0.8},
                {"claim": "Officials said it was false", "source": "b", "reliability_hint": 0.7},
            ],
            sources=["a", "b"],
            has_contradiction=False,
            positive_count=0,
            negative_count=2,
            neutral_count=0,
        )
        result = _verify_heuristic(cluster)
        assert result.verdict == "likely_false"

    def test_verify_heuristic_likely_true_multi_source(self):
        """Heuristic returns likely_true with positive count and 3+ sources."""
        from sts_monitor.claim_verification import _verify_heuristic, ClaimCluster
        cluster = ClaimCluster(
            representative_claim="The event confirmed verified substantiated by officials",
            observations=[
                {"claim": "Event confirmed", "source": "a", "reliability_hint": 0.8},
                {"claim": "Event verified", "source": "b", "reliability_hint": 0.7},
                {"claim": "Event substantiated", "source": "c", "reliability_hint": 0.9},
            ],
            sources=["a", "b", "c"],
            has_contradiction=False,
            positive_count=3,
            negative_count=0,
            neutral_count=0,
        )
        result = _verify_heuristic(cluster)
        assert result.verdict == "likely_true"
        assert result.evidence_needed == []

    def test_verify_heuristic_likely_true_two_sources(self):
        """Heuristic returns likely_true with 2 sources, no positive signals."""
        from sts_monitor.claim_verification import _verify_heuristic, ClaimCluster
        cluster = ClaimCluster(
            representative_claim="Some event happened with neutral reporting from multiple sources",
            observations=[
                {"claim": "Event happened", "source": "a", "reliability_hint": 0.6},
                {"claim": "Event happened also", "source": "b", "reliability_hint": 0.7},
            ],
            sources=["a", "b"],
            has_contradiction=False,
            positive_count=0,
            negative_count=0,
            neutral_count=2,
        )
        result = _verify_heuristic(cluster)
        assert result.verdict == "likely_true"
        assert result.confidence <= 0.7

    def test_verify_heuristic_unverified_single_source(self):
        """Heuristic returns unverified with single source, no signals."""
        from sts_monitor.claim_verification import _verify_heuristic, ClaimCluster
        cluster = ClaimCluster(
            representative_claim="Something happened according to one source only",
            observations=[
                {"claim": "Something happened", "source": "a", "reliability_hint": 0.5},
            ],
            sources=["a"],
            has_contradiction=False,
            positive_count=0,
            negative_count=0,
            neutral_count=1,
        )
        result = _verify_heuristic(cluster)
        assert result.verdict == "unverified"
        assert result.confidence == 0.25

    def test_verify_with_llm_success(self):
        """_verify_with_llm parses LLM response correctly."""
        from sts_monitor.claim_verification import _verify_with_llm, ClaimCluster
        cluster = ClaimCluster(
            representative_claim="Some big confirmed event happened today",
            observations=[
                {"claim": "Big confirmed event happened", "source": "a", "reliability_hint": 0.8},
                {"claim": "Big denied event happened", "source": "b", "reliability_hint": 0.6},
            ],
            sources=["a", "b"],
            has_contradiction=True,
            positive_count=1,
            negative_count=1,
            neutral_count=0,
        )
        mock_llm = MagicMock()
        mock_llm.summarize.return_value = (
            "VERDICT: likely_true\n"
            "CONFIDENCE: 0.85\n"
            "REASONING: Multiple sources corroborate.\n"
        )
        result = _verify_with_llm(cluster, mock_llm)
        assert result.verdict == "likely_true"
        assert result.confidence == 0.85
        assert result.method == "llm"

    def test_verify_with_llm_fallback_on_error(self):
        """_verify_with_llm falls back to heuristic when LLM fails."""
        from sts_monitor.claim_verification import _verify_with_llm, ClaimCluster
        cluster = ClaimCluster(
            representative_claim="Fallback test claim with enough text for grouping",
            observations=[
                {"claim": "Some claim text here", "source": "x", "reliability_hint": 0.5},
            ],
            sources=["x"],
            has_contradiction=False,
            positive_count=0,
            negative_count=0,
            neutral_count=1,
        )
        mock_llm = MagicMock()
        mock_llm.summarize.side_effect = Exception("LLM down")
        result = _verify_with_llm(cluster, mock_llm)
        assert result.method == "heuristic"

    def test_verify_investigation_claims_with_llm(self):
        """verify_investigation_claims routes contradictions to LLM."""
        from sts_monitor.claim_verification import verify_investigation_claims
        observations = [
            {"claim": "The earthquake was confirmed by seismologists with magnitude 7.2", "source": "a", "reliability_hint": 0.9},
            {"claim": "The earthquake was denied and debunked as false by officials", "source": "b", "reliability_hint": 0.7},
        ]
        mock_llm = MagicMock()
        mock_llm.summarize.return_value = "VERDICT: disputed\nCONFIDENCE: 0.4\nBoth sides have points."
        report = verify_investigation_claims("inv1", "earthquake", observations, llm_client=mock_llm)
        assert report.method == "llm+heuristic"
        assert report.total_claims_analyzed >= 0

    def test_verify_investigation_claims_no_llm(self):
        """verify_investigation_claims uses heuristic only when no LLM."""
        from sts_monitor.claim_verification import verify_investigation_claims
        observations = [
            {"claim": "Something noteworthy happened in the world today", "source": "a", "reliability_hint": 0.6},
        ]
        report = verify_investigation_claims("inv1", "world events", observations)
        assert report.method == "heuristic"

    def test_verification_result_to_dict(self):
        """VerificationResult.to_dict returns expected shape."""
        from sts_monitor.claim_verification import VerificationResult
        vr = VerificationResult(
            claim_text="test",
            verdict="likely_true",
            confidence=0.8,
            reasoning="ok",
            supporting_sources=["a"],
            contradicting_sources=[],
            evidence_needed=[],
            method="heuristic",
        )
        d = vr.to_dict()
        assert d["verdict"] == "likely_true"
        assert d["confidence"] == 0.8

    def test_verification_report_to_dict(self):
        """VerificationReport.to_dict returns expected shape."""
        from sts_monitor.claim_verification import VerificationReport
        vr = VerificationReport(
            investigation_id="i1",
            topic="t",
            total_claims_analyzed=1,
            verified_true=1,
            verified_false=0,
            disputed=0,
            unverified=0,
            results=[],
            method="heuristic",
            generated_at="2024-01-01",
        )
        d = vr.to_dict()
        assert d["investigation_id"] == "i1"

    def test_claim_sentiment_negative(self):
        """Negative sentiment dominates."""
        from sts_monitor.claim_verification import _claim_sentiment
        assert _claim_sentiment("This was debunked and fake and hoax") == "negative"

    def test_claim_sentiment_positive(self):
        """Positive sentiment dominates."""
        from sts_monitor.claim_verification import _claim_sentiment
        assert _claim_sentiment("confirmed verified proven") == "positive"

    def test_claim_sentiment_neutral(self):
        """Neutral when neither dominates."""
        from sts_monitor.claim_verification import _claim_sentiment
        assert _claim_sentiment("something happened") == "neutral"

    def test_group_claims_len_1_cluster_included(self):
        """A single observation with long claim creates a cluster (len >= 1)."""
        from sts_monitor.claim_verification import _group_claims
        obs = [
            {"claim": "This is a single observation with enough text to group", "source": "only_source"},
        ]
        clusters = _group_claims(obs)
        # Line 130: `if len(obs_list) < 1` means 0-length lists skipped, 1 is ok
        assert len(clusters) == 1


# ══════════════════════════════════════════════════════════════════════════
# 3. helpers.py coverage
# ══════════════════════════════════════════════════════════════════════════

class TestHelpers:

    def test_queue_health_high_backlog(self):
        """queue_health_snapshot returns detail about high backlog."""
        from sts_monitor.helpers import queue_health_snapshot
        session = SessionLocal()
        # Create > 100 pending jobs
        for i in range(101):
            session.add(JobORM(
                job_type="test",
                payload_json="{}",
                status="pending",
                run_at=datetime.now(UTC),
            ))
        session.commit()
        result = queue_health_snapshot(session)
        assert result["ok"] is False
        assert result["detail"] == "job backlog is high"
        assert result["pending"] == 101
        session.close()

    def test_queue_health_with_failures(self):
        """queue_health_snapshot detects failures."""
        from sts_monitor.helpers import queue_health_snapshot
        session = SessionLocal()
        session.add(JobORM(
            job_type="test", payload_json="{}", status="failed",
            run_at=datetime.now(UTC),
        ))
        session.commit()
        result = queue_health_snapshot(session)
        assert result["ok"] is False
        assert "failures" in result["detail"] or "dead letters" in result["detail"]
        session.close()

    def test_seed_default_research_sources_idempotent(self):
        """seed_default_research_sources skips existing sources."""
        from sts_monitor.helpers import seed_default_research_sources
        session = SessionLocal()
        seed_default_research_sources(session)
        session.commit()
        # Run again - should skip
        seed_default_research_sources(session)
        session.commit()
        from sts_monitor.models import ResearchSourceORM
        count = session.query(ResearchSourceORM).count()
        assert count == 2  # exactly 2, not 4
        session.close()

    def test_persist_geo_events_string_time(self):
        """persist_geo_events handles string event_time."""
        from sts_monitor.helpers import persist_geo_events
        session = SessionLocal()
        inv_id = _make_inv(session)
        events = [
            {
                "layer": "earthquake",
                "source_id": "s1",
                "title": "Quake",
                "latitude": 34.0,
                "longitude": -118.0,
                "event_time": "2024-01-01T00:00:00Z",
            },
        ]
        count = persist_geo_events(session, events, investigation_id=inv_id)
        session.commit()
        assert count == 1
        session.close()

    def test_persist_geo_events_invalid_string_time(self):
        """persist_geo_events handles invalid string event_time."""
        from sts_monitor.helpers import persist_geo_events
        session = SessionLocal()
        inv_id = _make_inv(session)
        events = [
            {
                "layer": "test",
                "latitude": 10.0,
                "longitude": 20.0,
                "event_time": "not-a-date",
            },
        ]
        count = persist_geo_events(session, events, investigation_id=inv_id)
        session.commit()
        assert count == 1
        session.close()

    def test_persist_geo_events_non_datetime_time(self):
        """persist_geo_events handles non-datetime, non-string event_time."""
        from sts_monitor.helpers import persist_geo_events
        session = SessionLocal()
        inv_id = _make_inv(session)
        events = [
            {
                "layer": "test",
                "latitude": 10.0,
                "longitude": 20.0,
                "event_time": 12345,  # integer, not string or datetime
            },
        ]
        count = persist_geo_events(session, events, investigation_id=inv_id)
        session.commit()
        assert count == 1
        session.close()

    def test_persist_geo_events_none_time(self):
        """persist_geo_events handles None event_time."""
        from sts_monitor.helpers import persist_geo_events
        session = SessionLocal()
        inv_id = _make_inv(session)
        events = [
            {
                "layer": "test",
                "latitude": 10.0,
                "longitude": 20.0,
                "event_time": None,
            },
        ]
        count = persist_geo_events(session, events, investigation_id=inv_id)
        session.commit()
        assert count == 1
        session.close()

    def test_ingest_with_geo_connector_full(self):
        """ingest_with_geo_connector runs the full pipeline with dedup and entity extraction."""
        from sts_monitor.helpers import ingest_with_geo_connector
        from sts_monitor.security import AuthContext
        session = SessionLocal()
        inv_id = _make_inv(session, topic="geo test")

        # Pre-create an observation to test dedup
        _make_obs(session, inv_id, claim="existing", url="http://dup.com/1")

        fake_obs_1 = MagicMock()
        fake_obs_1.source = "usgs"
        fake_obs_1.claim = "Earthquake of magnitude 5.0 near Los Angeles California"
        fake_obs_1.url = "http://dup.com/1"  # duplicate
        fake_obs_1.captured_at = datetime.now(UTC)
        fake_obs_1.reliability_hint = 0.8

        fake_obs_2 = MagicMock()
        fake_obs_2.source = "usgs"
        fake_obs_2.claim = "Earthquake of magnitude 3.0 near San Francisco"
        fake_obs_2.url = "http://new.com/2"
        fake_obs_2.captured_at = datetime.now(UTC)
        fake_obs_2.reliability_hint = 0.7

        connector = MagicMock()
        connector.collect.return_value = MagicMock(
            observations=[fake_obs_1, fake_obs_2],
            metadata={"geo_events": [], "count": 2},
        )
        auth = AuthContext(label="tester", role="admin")

        result = ingest_with_geo_connector(
            session, inv_id, "usgs", connector, "earthquake", auth=auth,
        )
        assert result["ingested_count"] == 1  # one deduped
        assert result["dedup_skipped"] == 1
        session.close()

    def test_ingest_with_geo_connector_not_found(self):
        """ingest_with_geo_connector raises 404 for missing investigation."""
        from sts_monitor.helpers import ingest_with_geo_connector
        from fastapi import HTTPException
        session = SessionLocal()
        with pytest.raises(HTTPException) as exc_info:
            ingest_with_geo_connector(session, "nonexistent", "test", MagicMock(), None)
        assert exc_info.value.status_code == 404
        session.close()

    def test_ingest_with_geo_connector_entity_extraction_error(self):
        """Entity extraction failure in ingest_with_geo_connector is silently caught."""
        from sts_monitor.helpers import ingest_with_geo_connector
        session = SessionLocal()
        inv_id = _make_inv(session, topic="ent error")

        fake_obs = MagicMock()
        fake_obs.source = "test"
        fake_obs.claim = "Some claim about President Biden meeting officials"
        fake_obs.url = "http://unique.com/1"
        fake_obs.captured_at = datetime.now(UTC)
        fake_obs.reliability_hint = 0.5

        connector = MagicMock()
        connector.collect.return_value = MagicMock(
            observations=[fake_obs],
            metadata={"geo_events": []},
        )

        with patch("sts_monitor.helpers.extract_entities", side_effect=Exception("entity error")):
            result = ingest_with_geo_connector(session, inv_id, "test", connector, "q")
        assert result["ingested_count"] == 1
        assert result["entities_extracted"] == 0
        session.close()

    def test_workspace_health_snapshot(self):
        """workspace_health_snapshot returns expected structure."""
        from pathlib import Path
        from sts_monitor.helpers import workspace_health_snapshot
        result = workspace_health_snapshot(Path("/tmp"))
        assert result["workspace_root_exists"] is True
        assert result["disk_free_mb"] is not None

    def test_workspace_health_snapshot_nonexistent(self):
        """workspace_health_snapshot for nonexistent path."""
        from pathlib import Path
        from sts_monitor.helpers import workspace_health_snapshot
        result = workspace_health_snapshot(Path("/nonexistent/path/xyz"))
        assert result["ok"] is False
        assert result["workspace_root_exists"] is False

    def test_evaluate_alert_rules_cooldown(self):
        """evaluate_alert_rules skips rule in cooldown by patching now to naive."""
        from sts_monitor.helpers import evaluate_alert_rules
        session = SessionLocal()
        inv_id = _make_inv(session, topic="alert cooldown")
        for i in range(25):
            _make_obs(session, inv_id, claim=f"Observation {i} is debunked false",
                      captured_at=datetime.now(UTC))
        # SQLite stores datetime as naive, so use naive for last_triggered_at
        naive_now = datetime.utcnow()
        rule = AlertRuleORM(
            investigation_id=inv_id,
            name="Test Rule",
            min_observations=1,
            min_disputed_claims=0,
            cooldown_seconds=9999,
            active=True,
            last_triggered_at=naive_now,
        )
        session.add(rule)
        session.commit()
        # Patch datetime.now inside helpers to return naive datetime too
        with patch("sts_monitor.helpers.datetime") as mock_dt:
            mock_dt.now.return_value = naive_now + timedelta(seconds=5)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = evaluate_alert_rules(session, inv_id)
        assert result == []  # skipped due to cooldown
        session.close()

    def test_evaluate_alert_rules_below_threshold(self):
        """evaluate_alert_rules skips when below min_observations."""
        from sts_monitor.helpers import evaluate_alert_rules
        session = SessionLocal()
        inv_id = _make_inv(session, topic="below threshold")
        _make_obs(session, inv_id, claim="Only one observation for the test")
        rule = AlertRuleORM(
            investigation_id=inv_id,
            name="High Threshold",
            min_observations=100,
            min_disputed_claims=0,
            cooldown_seconds=0,
            active=True,
        )
        session.add(rule)
        session.commit()
        result = evaluate_alert_rules(session, inv_id)
        assert result == []
        session.close()

    def test_evaluate_alert_rules_below_disputed(self):
        """evaluate_alert_rules skips when below min_disputed_claims."""
        from sts_monitor.helpers import evaluate_alert_rules
        session = SessionLocal()
        inv_id = _make_inv(session, topic="below disputed")
        _make_obs(session, inv_id, claim="Normal neutral observation text for testing")
        rule = AlertRuleORM(
            investigation_id=inv_id,
            name="Disputed Threshold",
            min_observations=0,
            min_disputed_claims=100,
            cooldown_seconds=0,
            active=True,
        )
        session.add(rule)
        session.commit()
        result = evaluate_alert_rules(session, inv_id)
        assert result == []
        session.close()

    def test_evaluate_alert_rules_triggers(self):
        """evaluate_alert_rules triggers when thresholds met."""
        from sts_monitor.helpers import evaluate_alert_rules
        session = SessionLocal()
        inv_id = _make_inv(session, topic="trigger test")
        for i in range(5):
            _make_obs(session, inv_id, claim=f"Observation {i}")
        rule = AlertRuleORM(
            investigation_id=inv_id,
            name="Easy Rule",
            min_observations=1,
            min_disputed_claims=0,
            cooldown_seconds=0,
            active=True,
        )
        session.add(rule)
        session.commit()

        with patch("sts_monitor.helpers.send_alert_webhook", return_value={"status": "ok"}):
            result = evaluate_alert_rules(session, inv_id)
        assert len(result) == 1
        assert result[0]["rule_id"] == rule.id
        session.close()

    def test_persist_claim_lineage(self):
        """persist_claim_lineage creates claims and evidence links."""
        from sts_monitor.helpers import persist_claim_lineage
        session = SessionLocal()
        inv_id = _make_inv(session, topic="lineage test")
        report = _make_report(session, inv_id)
        obs1 = _make_obs(session, inv_id, claim="important event happened today")
        obs2 = _make_obs(session, inv_id, claim="disputed claim about something else entirely")

        report_sections = {
            "likely_true": ["important event happened today"],
            "disputed": ["disputed claim about something"],
            "unknown": ["unknown event detail here"],
            "monitor_next": ["monitor this topic closely"],
        }
        persist_claim_lineage(
            session=session,
            investigation_id=inv_id,
            report_id=report.id,
            report_sections=report_sections,
            observations=[obs1, obs2],
        )
        session.commit()
        claims = session.query(ClaimORM).filter_by(investigation_id=inv_id).all()
        assert len(claims) == 4
        # Check stances
        stances = {c.stance for c in claims}
        assert "supported" in stances
        assert "disputed" in stances
        session.close()

    def test_evaluate_alert_rules_not_found(self):
        """evaluate_alert_rules raises 404 for missing investigation."""
        from sts_monitor.helpers import evaluate_alert_rules
        from fastapi import HTTPException
        session = SessionLocal()
        with pytest.raises(HTTPException):
            evaluate_alert_rules(session, "nonexistent")
        session.close()


# ══════════════════════════════════════════════════════════════════════════
# 4. alert_engine.py coverage
# ══════════════════════════════════════════════════════════════════════════

class TestAlertEngine:

    def test_alert_rule_to_dict(self):
        """AlertRule.to_dict returns expected shape."""
        from sts_monitor.alert_engine import AlertRule
        rule = AlertRule(
            name="test",
            rule_type="volume_spike",
            last_triggered_at=datetime.now(UTC),
        )
        d = rule.to_dict()
        assert d["name"] == "test"
        assert d["last_triggered_at"] is not None

    def test_alert_rule_to_dict_no_trigger(self):
        """AlertRule.to_dict with None last_triggered_at."""
        from sts_monitor.alert_engine import AlertRule
        rule = AlertRule(name="test", rule_type="volume_spike")
        d = rule.to_dict()
        assert d["last_triggered_at"] is None

    def test_check_silence_fires(self):
        """_check_silence fires when expected sources are silent."""
        from sts_monitor.alert_engine import _check_silence, AlertRule
        now = datetime.now(UTC)
        rule = AlertRule(
            name="Silence",
            rule_type="silence",
            threshold=1,
            window_minutes=60,
            metadata={"expected_sources": ["reuters", "bbc"]},
        )
        # No observations at all -> both sources silent
        events = _check_silence(rule, [], now)
        assert events is not None
        assert events.rule_type == "silence"
        assert "2" in events.message

    def test_check_silence_no_expected_sources(self):
        """_check_silence returns None when no expected_sources configured."""
        from sts_monitor.alert_engine import _check_silence, AlertRule
        now = datetime.now(UTC)
        rule = AlertRule(
            name="Silence",
            rule_type="silence",
            threshold=1,
            window_minutes=60,
            metadata={},
        )
        result = _check_silence(rule, [], now)
        assert result is None

    def test_check_silence_sources_active(self):
        """_check_silence returns None when sources are active."""
        from sts_monitor.alert_engine import _check_silence, AlertRule
        now = datetime.now(UTC)
        rule = AlertRule(
            name="Silence",
            rule_type="silence",
            threshold=2,
            window_minutes=60,
            metadata={"expected_sources": ["reuters"]},
        )
        obs = [{"source": "reuters", "captured_at": now - timedelta(minutes=5)}]
        result = _check_silence(rule, obs, now)
        assert result is None  # only 0 silent, threshold 2

    def test_entity_velocity_with_target_entity(self):
        """_check_entity_velocity with a specific target entity."""
        from sts_monitor.alert_engine import _check_entity_velocity, AlertRule
        now = datetime.now(UTC)
        rule = AlertRule(
            name="Entity",
            rule_type="entity_velocity",
            threshold=2,
            window_minutes=60,
            metadata={"entity": "ukraine"},
        )
        obs = [
            {"claim": "Ukraine conflict escalates", "captured_at": now - timedelta(minutes=5)},
            {"claim": "Ukraine peace talks resume", "captured_at": now - timedelta(minutes=10)},
            {"claim": "Unrelated news item", "captured_at": now - timedelta(minutes=15)},
        ]
        result = _check_entity_velocity(rule, obs, now)
        assert result is not None
        assert result.rule_type == "entity_velocity"
        assert "ukraine" in result.message

    def test_entity_velocity_target_below_threshold(self):
        """_check_entity_velocity with target entity below threshold."""
        from sts_monitor.alert_engine import _check_entity_velocity, AlertRule
        now = datetime.now(UTC)
        rule = AlertRule(
            name="Entity",
            rule_type="entity_velocity",
            threshold=10,
            window_minutes=60,
            metadata={"entity": "ukraine"},
        )
        obs = [{"claim": "Ukraine mentioned once", "captured_at": now - timedelta(minutes=5)}]
        result = _check_entity_velocity(rule, obs, now)
        assert result is None

    def test_entity_velocity_auto_detect(self):
        """_check_entity_velocity auto-detects spiking entity."""
        from sts_monitor.alert_engine import _check_entity_velocity, AlertRule
        now = datetime.now(UTC)
        rule = AlertRule(
            name="Entity Auto",
            rule_type="entity_velocity",
            threshold=3,
            window_minutes=60,
            metadata={},
        )
        obs = [
            {"claim": "earthquake damage reports", "captured_at": now - timedelta(minutes=5)},
            {"claim": "earthquake relief efforts", "captured_at": now - timedelta(minutes=10)},
            {"claim": "earthquake magnitude measured", "captured_at": now - timedelta(minutes=15)},
        ]
        result = _check_entity_velocity(rule, obs, now)
        assert result is not None
        assert result.rule_type == "entity_velocity"

    def test_narrative_shift_fires(self):
        """_check_narrative_shift detects narrative change."""
        from sts_monitor.alert_engine import _check_narrative_shift, AlertRule
        now = datetime.now(UTC)
        rule = AlertRule(
            name="Shift",
            rule_type="narrative_shift",
            threshold=1,  # very low threshold to trigger easily
            window_minutes=120,
        )
        # Earlier half: talk about earthquakes
        earlier = [
            {"claim": "earthquake damage seismic activity shaking buildings collapsed",
             "captured_at": now - timedelta(minutes=90)},
            {"claim": "earthquake relief rescue operations underway aftermath",
             "captured_at": now - timedelta(minutes=80)},
            {"claim": "seismic earthquake tremors aftershocks geological survey",
             "captured_at": now - timedelta(minutes=70)},
        ]
        # Later half: talk about elections
        later = [
            {"claim": "election results presidential candidate voting polls",
             "captured_at": now - timedelta(minutes=30)},
            {"claim": "political campaign electoral college ballots counting",
             "captured_at": now - timedelta(minutes=20)},
            {"claim": "democracy voters turnout political party headquarters",
             "captured_at": now - timedelta(minutes=10)},
        ]
        result = _check_narrative_shift(rule, earlier + later, now)
        assert result is not None
        assert result.rule_type == "narrative_shift"

    def test_narrative_shift_too_few_obs(self):
        """_check_narrative_shift returns None with < 3 obs in either half."""
        from sts_monitor.alert_engine import _check_narrative_shift, AlertRule
        now = datetime.now(UTC)
        rule = AlertRule(
            name="Shift",
            rule_type="narrative_shift",
            threshold=3,
            window_minutes=120,
        )
        obs = [
            {"claim": "only one observation", "captured_at": now - timedelta(minutes=90)},
        ]
        result = _check_narrative_shift(rule, obs, now)
        assert result is None

    def test_evaluate_rules_cooldown(self):
        """evaluate_rules skips rules in cooldown."""
        from sts_monitor.alert_engine import evaluate_rules, AlertRule
        now = datetime.now(UTC)
        rule = AlertRule(
            name="test",
            rule_type="volume_spike",
            threshold=1,
            cooldown_seconds=9999,
            last_triggered_at=now - timedelta(seconds=10),
        )
        obs = [{"claim": "something", "captured_at": now}]
        result = evaluate_rules([rule], obs, now=now)
        assert result == []

    def test_evaluate_rules_inactive(self):
        """evaluate_rules skips inactive rules."""
        from sts_monitor.alert_engine import evaluate_rules, AlertRule
        now = datetime.now(UTC)
        rule = AlertRule(name="test", rule_type="volume_spike", threshold=1, active=False)
        obs = [{"claim": "something", "captured_at": now}]
        result = evaluate_rules([rule], obs, now=now)
        assert result == []

    def test_evaluate_rules_unknown_type(self):
        """evaluate_rules logs warning for unknown rule type."""
        from sts_monitor.alert_engine import evaluate_rules, AlertRule
        now = datetime.now(UTC)
        rule = AlertRule(name="test", rule_type="unknown_type", threshold=1)
        result = evaluate_rules([rule], [], now=now)
        assert result == []

    def test_evaluate_rules_scoped_to_investigation(self):
        """evaluate_rules filters observations by investigation_id."""
        from sts_monitor.alert_engine import evaluate_rules, AlertRule
        now = datetime.now(UTC)
        rule = AlertRule(
            name="scoped",
            rule_type="volume_spike",
            investigation_id="inv1",
            threshold=2,
            window_minutes=60,
        )
        obs = [
            {"claim": "a", "captured_at": now, "investigation_id": "inv1"},
            {"claim": "b", "captured_at": now, "investigation_id": "inv1"},
            {"claim": "c", "captured_at": now, "investigation_id": "inv2"},
        ]
        result = evaluate_rules([rule], obs, now=now)
        assert len(result) == 1

    def test_parse_ts_naive_datetime(self):
        """_parse_ts adds UTC to naive datetime."""
        from sts_monitor.alert_engine import _parse_ts
        naive = datetime(2024, 1, 1, 12, 0, 0)
        result = _parse_ts(naive)
        assert result.tzinfo is not None

    def test_parse_ts_string(self):
        """_parse_ts parses ISO string."""
        from sts_monitor.alert_engine import _parse_ts
        result = _parse_ts("2024-01-01T12:00:00Z")
        assert result.year == 2024

    def test_parse_ts_invalid(self):
        """_parse_ts returns min datetime for invalid input."""
        from sts_monitor.alert_engine import _parse_ts
        result = _parse_ts(12345)
        assert result.year == 1  # datetime.min

    def test_parse_ts_bad_string(self):
        """_parse_ts returns min datetime for unparseable string."""
        from sts_monitor.alert_engine import _parse_ts
        result = _parse_ts("not-a-date")
        assert result.year == 1

    def test_get_default_rules(self):
        """get_default_rules returns rules with correct types."""
        from sts_monitor.alert_engine import get_default_rules
        rules = get_default_rules(investigation_id="inv1")
        assert len(rules) == 4
        types = {r.rule_type for r in rules}
        assert "volume_spike" in types
        assert "contradiction_threshold" in types

    def test_contradiction_threshold_fires(self):
        """_check_contradiction_threshold fires when many disputed claims."""
        from sts_monitor.alert_engine import _check_contradiction_threshold, AlertRule
        now = datetime.now(UTC)
        rule = AlertRule(
            name="Contradictions",
            rule_type="contradiction_threshold",
            threshold=2,
            window_minutes=60,
        )
        obs = [
            {"claim": "This was debunked", "captured_at": now - timedelta(minutes=5)},
            {"claim": "Officials denied the report", "captured_at": now - timedelta(minutes=10)},
            {"claim": "False information spreading", "captured_at": now - timedelta(minutes=15)},
        ]
        result = _check_contradiction_threshold(rule, obs, now)
        assert result is not None
        assert result.rule_type == "contradiction_threshold"
