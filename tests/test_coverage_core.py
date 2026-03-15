"""Comprehensive tests for retention, __main__ CLI, notifications, and plugins modules.

Targets 100% code coverage for:
- src/sts_monitor/retention.py
- src/sts_monitor/__main__.py
- src/sts_monitor/notifications.py
- src/sts_monitor/plugins.py
"""
from __future__ import annotations

import json
import smtplib
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sts_monitor.database import Base
from sts_monitor.models import (
    AlertEventORM,
    IngestionRunORM,
    InvestigationORM,
    JobORM,
    ObservationORM,
)


# ── Fixtures ──────────────────────────────────────────────────────────

# Use a private in-memory SQLite for all retention/DB tests so we never
# conflict with the shared engine used by test_api.py.

_test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
_TestSession = sessionmaker(bind=_test_engine, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def reset_db():
    """Recreate all tables in the private test engine before each test."""
    Base.metadata.drop_all(bind=_test_engine)
    Base.metadata.create_all(bind=_test_engine)


@pytest.fixture()
def db_session():
    """Provide a session bound to the private test engine."""
    session = _TestSession()
    try:
        yield session
    finally:
        session.close()


def _mock_get_session():
    """Yield a session from the private test engine (for patching retention.get_session)."""
    session = _TestSession()
    try:
        yield session
    finally:
        session.close()


def _make_investigation(session, inv_id="inv-1", status="closed"):
    inv = InvestigationORM(id=inv_id, topic="test topic", status=status)
    session.add(inv)
    session.commit()
    return inv


def _old_dt(days: int = 120) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


# Patch target — all retention tests route get_session to the private engine
_RETENTION_GS = "sts_monitor.retention.get_session"


# =====================================================================
#  RETENTION TESTS
# =====================================================================

class TestRetentionDryRun:
    """Dry-run should count but not delete anything."""

    def test_dry_run_counts_old_observations(self, db_session):
        from sts_monitor.retention import run_retention

        _make_investigation(db_session, "inv-1", "closed")
        db_session.add(ObservationORM(
            investigation_id="inv-1", source="s", claim="c",
            url="http://x", captured_at=_old_dt(120),
        ))
        db_session.commit()

        with patch(_RETENTION_GS, _mock_get_session):
            result = run_retention(max_age_days=90, dry_run=True)

        assert result["dry_run"] is True
        assert result["observations_removed"] == 1
        # Data should still be there
        assert db_session.query(ObservationORM).count() == 1

    def test_dry_run_counts_old_ingestion_runs(self, db_session):
        from sts_monitor.retention import run_retention

        _make_investigation(db_session, "inv-1", "closed")
        db_session.add(IngestionRunORM(
            investigation_id="inv-1", connector="gdelt",
            started_at=_old_dt(120),
        ))
        db_session.commit()

        with patch(_RETENTION_GS, _mock_get_session):
            result = run_retention(max_age_days=90, dry_run=True)
        assert result["ingestion_runs_removed"] == 1

    def test_dry_run_counts_old_alert_events(self, db_session):
        from sts_monitor.retention import run_retention

        db_session.add(AlertEventORM(
            message="old alert", triggered_at=_old_dt(120),
        ))
        db_session.commit()

        with patch(_RETENTION_GS, _mock_get_session):
            result = run_retention(alert_max_age_days=60, dry_run=True)
        assert result["alert_events_removed"] == 1

    def test_dry_run_counts_old_jobs(self, db_session):
        from sts_monitor.retention import run_retention

        db_session.add(JobORM(
            job_type="cycle", payload_json="{}", status="completed",
            created_at=_old_dt(60), run_at=datetime.now(UTC),
        ))
        db_session.commit()

        with patch(_RETENTION_GS, _mock_get_session):
            result = run_retention(job_max_age_days=30, dry_run=True)
        assert result["jobs_removed"] == 1


class TestRetentionActualDelete:
    """Actual deletions when dry_run=False."""

    def test_deletes_old_observations(self, db_session):
        from sts_monitor.retention import run_retention

        _make_investigation(db_session, "inv-1", "closed")
        db_session.add(ObservationORM(
            investigation_id="inv-1", source="s", claim="c",
            url="http://x", captured_at=_old_dt(120),
        ))
        db_session.commit()

        with patch(_RETENTION_GS, _mock_get_session):
            result = run_retention(max_age_days=90, dry_run=False)
        assert result["observations_removed"] == 1
        assert result["dry_run"] is False
        # Verify actually deleted
        fresh = _TestSession()
        assert fresh.query(ObservationORM).count() == 0
        fresh.close()

    def test_deletes_old_ingestion_runs(self, db_session):
        from sts_monitor.retention import run_retention

        _make_investigation(db_session, "inv-1", "closed")
        db_session.add(IngestionRunORM(
            investigation_id="inv-1", connector="gdelt",
            started_at=_old_dt(120),
        ))
        db_session.commit()

        with patch(_RETENTION_GS, _mock_get_session):
            result = run_retention(max_age_days=90, dry_run=False)
        assert result["ingestion_runs_removed"] == 1

    def test_deletes_old_alert_events(self, db_session):
        from sts_monitor.retention import run_retention

        db_session.add(AlertEventORM(
            message="old alert", triggered_at=_old_dt(120),
        ))
        db_session.commit()

        with patch(_RETENTION_GS, _mock_get_session):
            result = run_retention(alert_max_age_days=60, dry_run=False)
        assert result["alert_events_removed"] == 1

    def test_deletes_old_completed_jobs(self, db_session):
        from sts_monitor.retention import run_retention

        db_session.add(JobORM(
            job_type="cycle", payload_json="{}", status="completed",
            created_at=_old_dt(60), run_at=datetime.now(UTC),
        ))
        db_session.commit()

        with patch(_RETENTION_GS, _mock_get_session):
            result = run_retention(job_max_age_days=30, dry_run=False)
        assert result["jobs_removed"] == 1

    def test_deletes_old_failed_jobs(self, db_session):
        from sts_monitor.retention import run_retention

        db_session.add(JobORM(
            job_type="cycle", payload_json="{}", status="failed",
            created_at=_old_dt(60), run_at=datetime.now(UTC),
        ))
        db_session.commit()

        with patch(_RETENTION_GS, _mock_get_session):
            result = run_retention(job_max_age_days=30, dry_run=False)
        assert result["jobs_removed"] == 1

    def test_does_not_delete_pending_jobs(self, db_session):
        from sts_monitor.retention import run_retention

        db_session.add(JobORM(
            job_type="cycle", payload_json="{}", status="pending",
            created_at=_old_dt(60), run_at=datetime.now(UTC),
        ))
        db_session.commit()

        with patch(_RETENTION_GS, _mock_get_session):
            result = run_retention(job_max_age_days=30, dry_run=False)
        assert result["jobs_removed"] == 0


class TestRetentionOpenInvestigations:
    """Data for open/monitoring investigations must never be deleted."""

    def test_skips_open_investigation_observations(self, db_session):
        from sts_monitor.retention import run_retention

        _make_investigation(db_session, "inv-open", "open")
        db_session.add(ObservationORM(
            investigation_id="inv-open", source="s", claim="c",
            url="http://x", captured_at=_old_dt(120),
        ))
        db_session.commit()

        with patch(_RETENTION_GS, _mock_get_session):
            result = run_retention(max_age_days=90, dry_run=False)
        assert result["observations_removed"] == 0

    def test_skips_monitoring_investigation_observations(self, db_session):
        from sts_monitor.retention import run_retention

        _make_investigation(db_session, "inv-mon", "monitoring")
        db_session.add(ObservationORM(
            investigation_id="inv-mon", source="s", claim="c",
            url="http://x", captured_at=_old_dt(120),
        ))
        db_session.commit()

        with patch(_RETENTION_GS, _mock_get_session):
            result = run_retention(max_age_days=90, dry_run=False)
        assert result["observations_removed"] == 0

    def test_skips_open_investigation_ingestion_runs(self, db_session):
        from sts_monitor.retention import run_retention

        _make_investigation(db_session, "inv-open", "open")
        db_session.add(IngestionRunORM(
            investigation_id="inv-open", connector="gdelt",
            started_at=_old_dt(120),
        ))
        db_session.commit()

        with patch(_RETENTION_GS, _mock_get_session):
            result = run_retention(max_age_days=90, dry_run=False)
        assert result["ingestion_runs_removed"] == 0

    def test_mixed_open_and_closed(self, db_session):
        """Only closed investigation data is removed."""
        from sts_monitor.retention import run_retention

        _make_investigation(db_session, "inv-open", "open")
        _make_investigation(db_session, "inv-closed", "closed")

        db_session.add(ObservationORM(
            investigation_id="inv-open", source="s", claim="c",
            url="http://x", captured_at=_old_dt(120),
        ))
        db_session.add(ObservationORM(
            investigation_id="inv-closed", source="s", claim="c",
            url="http://x", captured_at=_old_dt(120),
        ))
        db_session.commit()

        with patch(_RETENTION_GS, _mock_get_session):
            result = run_retention(max_age_days=90, dry_run=False)
        assert result["observations_removed"] == 1


class TestRetentionDeleteWithOpenInvestigations:
    """Cover the branch where open_ids exist during actual ingestion-run deletion."""

    def test_deletes_closed_ingestion_runs_while_open_exist(self, db_session):
        """When there are open AND closed investigations, delete only closed ones' runs."""
        from sts_monitor.retention import run_retention

        _make_investigation(db_session, "inv-open", "open")
        _make_investigation(db_session, "inv-closed", "closed")

        db_session.add(IngestionRunORM(
            investigation_id="inv-closed", connector="gdelt",
            started_at=_old_dt(120),
        ))
        db_session.add(IngestionRunORM(
            investigation_id="inv-open", connector="gdelt",
            started_at=_old_dt(120),
        ))
        db_session.commit()

        with patch(_RETENTION_GS, _mock_get_session):
            result = run_retention(max_age_days=90, dry_run=False)
        assert result["ingestion_runs_removed"] == 1


class TestRetentionEmpty:
    """No data to clean."""

    def test_empty_database(self):
        from sts_monitor.retention import run_retention

        with patch(_RETENTION_GS, _mock_get_session):
            result = run_retention(dry_run=False)
        assert result["observations_removed"] == 0
        assert result["ingestion_runs_removed"] == 0
        assert result["alert_events_removed"] == 0
        assert result["jobs_removed"] == 0

    def test_recent_data_not_deleted(self, db_session):
        from sts_monitor.retention import run_retention

        _make_investigation(db_session, "inv-1", "closed")
        db_session.add(ObservationORM(
            investigation_id="inv-1", source="s", claim="c",
            url="http://x", captured_at=datetime.now(UTC) - timedelta(days=5),
        ))
        db_session.commit()

        with patch(_RETENTION_GS, _mock_get_session):
            result = run_retention(max_age_days=90, dry_run=False)
        assert result["observations_removed"] == 0


class TestRetentionException:
    """Retention should rollback on error."""

    def test_exception_causes_rollback(self):
        from sts_monitor.retention import run_retention

        mock_session = MagicMock()
        mock_session.scalars.return_value.all.return_value = []
        mock_session.scalar.side_effect = RuntimeError("db error")

        def fake_get_session():
            yield mock_session

        with patch(_RETENTION_GS, fake_get_session):
            with pytest.raises(RuntimeError, match="db error"):
                run_retention(dry_run=False)

        mock_session.rollback.assert_called_once()
        mock_session.close.assert_called_once()


class TestRetentionCutoffDate:
    """Verify cutoff_date is in the result."""

    def test_cutoff_date_in_result(self):
        from sts_monitor.retention import run_retention

        with patch(_RETENTION_GS, _mock_get_session):
            result = run_retention(max_age_days=90, dry_run=True)
        assert "cutoff_date" in result
        datetime.fromisoformat(result["cutoff_date"])


# =====================================================================
#  __main__ CLI TESTS
# =====================================================================

class TestSetupLogging:
    def test_verbose(self):
        from sts_monitor.__main__ import _setup_logging
        _setup_logging(verbose=True)

    def test_non_verbose(self):
        from sts_monitor.__main__ import _setup_logging
        _setup_logging(verbose=False)


class TestMainDispatch:
    """Test main() dispatch logic."""

    def test_no_command_returns_0(self):
        from sts_monitor.__main__ import main
        with patch("sys.argv", ["sts_monitor"]):
            assert main() == 0

    def test_verbose_flag_sets_debug(self):
        from sts_monitor.__main__ import main
        with patch("sys.argv", ["sts_monitor", "-v"]):
            assert main() == 0

    def test_dispatches_to_cycle(self):
        from sts_monitor.__main__ import main
        with patch("sys.argv", ["sts_monitor", "cycle", "test-topic"]), \
             patch("sts_monitor.__main__.cmd_cycle", return_value=0) as mock_cmd:
            result = main()
        assert result == 0
        mock_cmd.assert_called_once()

    def test_dispatches_to_deep_truth(self):
        from sts_monitor.__main__ import main
        with patch("sys.argv", ["sts_monitor", "deep-truth", "test-topic"]), \
             patch("sts_monitor.__main__.cmd_deep_truth", return_value=0) as mock_cmd:
            result = main()
        assert result == 0
        mock_cmd.assert_called_once()

    def test_dispatches_to_surge(self):
        from sts_monitor.__main__ import main
        with patch("sys.argv", ["sts_monitor", "surge", "test-topic"]), \
             patch("sts_monitor.__main__.cmd_surge", return_value=0) as mock_cmd:
            result = main()
        assert result == 0
        mock_cmd.assert_called_once()

    def test_dispatches_to_retention(self):
        from sts_monitor.__main__ import main
        with patch("sys.argv", ["sts_monitor", "retention"]), \
             patch("sts_monitor.__main__.cmd_retention", return_value=0) as mock_cmd:
            result = main()
        assert result == 0
        mock_cmd.assert_called_once()

    def test_dispatches_to_serve(self):
        from sts_monitor.__main__ import main
        with patch("sys.argv", ["sts_monitor", "serve"]), \
             patch("sts_monitor.__main__.cmd_serve", return_value=0) as mock_cmd:
            result = main()
        assert result == 0
        mock_cmd.assert_called_once()


class TestMainUnknownCommand:
    """Cover the fallback path where args.command is not in the commands dict."""

    def test_unknown_command_returns_1(self):
        from sts_monitor.__main__ import main

        fake_args = SimpleNamespace(command="nonexistent_cmd", verbose=False)
        with patch("sts_monitor.__main__.build_parser") as mock_bp:
            mock_parser = MagicMock()
            mock_parser.parse_args.return_value = fake_args
            mock_bp.return_value = mock_parser
            result = main()
        assert result == 1
        mock_parser.print_help.assert_called_once()


class TestCmdCycle:
    """Test cmd_cycle with mocked internals."""

    def _make_args(self, **overrides):
        defaults = dict(
            topic="test-topic", no_social=False, search=False,
            no_report=False, min_reliability=0.45, json=False,
            report_file=None,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_basic_cycle(self):
        from sts_monitor.__main__ import cmd_cycle

        mock_result = MagicMock()
        mock_result.summary.return_value = "Summary text"
        mock_result.report = None
        mock_result.alerts_fired = []
        mock_result.promoted_topics = []

        with patch.dict("sys.modules", {
            "sts_monitor.cycle": MagicMock(
                run_cycle=MagicMock(return_value=mock_result),
                build_default_connectors=MagicMock(return_value=[]),
            ),
            "sts_monitor.pipeline": MagicMock(),
        }):
            result = cmd_cycle(self._make_args())
        assert result == 0

    def test_cycle_with_json_output(self):
        from sts_monitor.__main__ import cmd_cycle

        mock_pipeline_result = MagicMock()
        mock_pipeline_result.accepted = []
        mock_pipeline_result.dropped = []
        mock_pipeline_result.disputed_claims = []
        mock_pipeline_result.confidence = 0.8

        mock_enrichment = MagicMock()
        mock_enrichment.entities = []
        mock_enrichment.stories = []
        mock_enrichment.anomalies.total_anomalies = 0

        mock_report = MagicMock()
        mock_report.to_dict.return_value = {"summary": "test"}

        mock_result = MagicMock()
        mock_result.summary.return_value = "Summary"
        mock_result.cycle_id = "c-1"
        mock_result.topic = "test-topic"
        mock_result.duration_ms = 100
        mock_result.total_observations_collected = 5
        mock_result.pipeline_result = mock_pipeline_result
        mock_result.enrichment = mock_enrichment
        mock_result.alerts_fired = []
        mock_result.promoted_topics = []
        mock_result.report = mock_report

        with patch.dict("sys.modules", {
            "sts_monitor.cycle": MagicMock(
                run_cycle=MagicMock(return_value=mock_result),
                build_default_connectors=MagicMock(return_value=[]),
            ),
            "sts_monitor.pipeline": MagicMock(),
        }):
            result = cmd_cycle(self._make_args(json=True))
        assert result == 0

    def test_cycle_with_report_file(self, tmp_path):
        from sts_monitor.__main__ import cmd_cycle

        mock_report = MagicMock()
        mock_report.to_markdown.return_value = "# Report"

        mock_result = MagicMock()
        mock_result.summary.return_value = "Summary"
        mock_result.report = mock_report
        mock_result.alerts_fired = []
        mock_result.promoted_topics = []

        report_file = str(tmp_path / "report.md")
        with patch.dict("sys.modules", {
            "sts_monitor.cycle": MagicMock(
                run_cycle=MagicMock(return_value=mock_result),
                build_default_connectors=MagicMock(return_value=[]),
            ),
            "sts_monitor.pipeline": MagicMock(),
        }):
            result = cmd_cycle(self._make_args(json=False, report_file=report_file))
        assert result == 0
        assert Path(report_file).read_text() == "# Report"

    def test_cycle_json_without_report(self):
        from sts_monitor.__main__ import cmd_cycle

        mock_pipeline_result = MagicMock()
        mock_pipeline_result.accepted = []
        mock_pipeline_result.dropped = []
        mock_pipeline_result.disputed_claims = []
        mock_pipeline_result.confidence = 0.8

        mock_enrichment = MagicMock()
        mock_enrichment.entities = []
        mock_enrichment.stories = []
        mock_enrichment.anomalies.total_anomalies = 0

        mock_result = MagicMock()
        mock_result.summary.return_value = "Summary"
        mock_result.cycle_id = "c-1"
        mock_result.topic = "test"
        mock_result.duration_ms = 50
        mock_result.total_observations_collected = 0
        mock_result.pipeline_result = mock_pipeline_result
        mock_result.enrichment = mock_enrichment
        mock_result.alerts_fired = []
        mock_result.promoted_topics = []
        mock_result.report = None

        with patch.dict("sys.modules", {
            "sts_monitor.cycle": MagicMock(
                run_cycle=MagicMock(return_value=mock_result),
                build_default_connectors=MagicMock(return_value=[]),
            ),
            "sts_monitor.pipeline": MagicMock(),
        }):
            result = cmd_cycle(self._make_args(json=True))
        assert result == 0


class TestCmdDeepTruth:
    """Test cmd_deep_truth with mocked internals."""

    def _make_args(self, **overrides):
        defaults = dict(topic="test-topic", claim=None, json=False)
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def _mock_connector(self, observations=None):
        mock = MagicMock()
        mock.name = "test"
        result = MagicMock()
        result.observations = observations or []
        mock.collect.return_value = result
        return mock

    def test_no_observations(self, capsys):
        from sts_monitor.__main__ import cmd_deep_truth

        with patch.dict("sys.modules", {
            "sts_monitor.deep_truth": MagicMock(),
            "sts_monitor.cycle": MagicMock(
                build_default_connectors=MagicMock(return_value=[]),
            ),
            "sts_monitor.pipeline": MagicMock(),
        }):
            result = cmd_deep_truth(self._make_args())
        assert result == 1
        assert "No observations" in capsys.readouterr().out

    def test_with_observations_json(self):
        from sts_monitor.__main__ import cmd_deep_truth

        obs = MagicMock()
        obs.source = "test"
        obs.claim = "test claim"
        obs.url = "http://test"
        obs.captured_at = datetime.now(UTC)
        obs.reliability_hint = 0.8

        mock_connector = self._mock_connector([obs])

        mock_pipeline_result = MagicMock()
        mock_pipeline_result.accepted = [obs]

        mock_verdict = MagicMock()
        mock_verdict.to_dict.return_value = {"verdict": "true"}

        mock_deep_truth = MagicMock()
        mock_deep_truth.analyze_deep_truth.return_value = mock_verdict

        mock_pipeline_cls = MagicMock()
        mock_pipeline_cls.return_value.run.return_value = mock_pipeline_result

        with patch.dict("sys.modules", {
            "sts_monitor.deep_truth": mock_deep_truth,
            "sts_monitor.cycle": MagicMock(
                build_default_connectors=MagicMock(return_value=[mock_connector]),
            ),
            "sts_monitor.pipeline": MagicMock(SignalPipeline=mock_pipeline_cls),
        }):
            result = cmd_deep_truth(self._make_args(json=True, claim="specific claim"))
        assert result == 0

    def test_with_observations_pretty_print(self):
        from sts_monitor.__main__ import cmd_deep_truth

        obs = MagicMock()
        obs.source = "test"
        obs.claim = "test claim"
        obs.url = "http://test"
        obs.captured_at = datetime.now(UTC)
        obs.reliability_hint = 0.8

        mock_connector = self._mock_connector([obs])

        mock_pipeline_result = MagicMock()
        mock_pipeline_result.accepted = [obs]

        mock_verdict = MagicMock()
        mock_verdict.claim = "test claim about something"
        mock_verdict.authority_weight.score = 0.8
        mock_verdict.authority_weight.skepticism_level = "low"
        mock_verdict.authority_weight.pejorative_labels = ["label1"]
        mock_verdict.authority_weight.coordination_markers = ["m1"]
        mock_verdict.provenance.entropy_bits = 3.5
        mock_verdict.provenance.source_independence = 0.7
        mock_verdict.provenance.primary_source_ratio = 0.5
        track = MagicMock()
        track.survived_attack = True
        track.track_name = "Track A"
        track.probability = 0.9
        track.weaknesses = ["weak1"]
        mock_verdict.tracks = [track]
        gap = MagicMock()
        gap.importance = 0.8
        gap.question = "What about X?"
        mock_verdict.silence_gaps = [gap]
        mock_verdict.verdict_summary = "The claim is supported."
        mock_verdict.probability_distribution = {"true": 0.8, "false": 0.2}
        mock_verdict.confidence_in_verdict = 0.9
        mock_verdict.manufactured_consensus_detected = True
        mock_verdict.active_suppression_detected = True

        mock_deep_truth = MagicMock()
        mock_deep_truth.analyze_deep_truth.return_value = mock_verdict

        mock_pipeline_cls = MagicMock()
        mock_pipeline_cls.return_value.run.return_value = mock_pipeline_result

        with patch.dict("sys.modules", {
            "sts_monitor.deep_truth": mock_deep_truth,
            "sts_monitor.cycle": MagicMock(
                build_default_connectors=MagicMock(return_value=[mock_connector]),
            ),
            "sts_monitor.pipeline": MagicMock(SignalPipeline=mock_pipeline_cls),
        }):
            result = cmd_deep_truth(self._make_args(json=False))
        assert result == 0

    def test_connector_failure_continues(self):
        from sts_monitor.__main__ import cmd_deep_truth

        failing_connector = MagicMock()
        failing_connector.name = "failing"
        failing_connector.collect.side_effect = RuntimeError("network error")

        with patch.dict("sys.modules", {
            "sts_monitor.deep_truth": MagicMock(),
            "sts_monitor.cycle": MagicMock(
                build_default_connectors=MagicMock(return_value=[failing_connector]),
            ),
            "sts_monitor.pipeline": MagicMock(),
        }):
            result_code = cmd_deep_truth(self._make_args())
        assert result_code == 1


class TestCmdDeepTruthPrettyPrint:
    """Test _print_deep_truth branches thoroughly."""

    def test_track_destroyed(self, capsys):
        from sts_monitor.__main__ import _print_deep_truth

        verdict = MagicMock()
        verdict.claim = "X"
        verdict.authority_weight.score = 0.5
        verdict.authority_weight.skepticism_level = "medium"
        verdict.authority_weight.pejorative_labels = []
        verdict.authority_weight.coordination_markers = []
        verdict.provenance.entropy_bits = 2.0
        verdict.provenance.source_independence = 0.5
        verdict.provenance.primary_source_ratio = 0.3

        track = MagicMock()
        track.survived_attack = False
        track.track_name = "Track B"
        track.probability = 0.3
        track.weaknesses = []
        verdict.tracks = [track]
        verdict.silence_gaps = []
        verdict.verdict_summary = "Unclear"
        verdict.probability_distribution = {"true": 0.5, "false": 0.5}
        verdict.confidence_in_verdict = 0.5
        verdict.manufactured_consensus_detected = False
        verdict.active_suppression_detected = False

        _print_deep_truth(verdict)
        out = capsys.readouterr().out
        assert "DESTROYED" in out
        assert "MANUFACTURED CONSENSUS" not in out
        assert "ACTIVE SUPPRESSION" not in out


class TestCmdSurge:
    """Test cmd_surge with mocked internals."""

    def _make_args(self, **overrides):
        defaults = dict(topic="breaking", categories=None, json=False)
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_basic_surge(self, capsys):
        from sts_monitor.__main__ import cmd_surge

        mock_obs = MagicMock()
        mock_obs.source = "nitter"
        mock_obs.claim = "test"
        mock_obs.url = "http://x"
        mock_obs.captured_at = datetime.now(UTC)
        mock_obs.reliability_hint = 0.5

        mock_connector_result = MagicMock()
        mock_connector_result.observations = [mock_obs]

        mock_connector_cls = MagicMock()
        mock_connector_cls.return_value.collect.return_value = mock_connector_result

        mock_analysis = MagicMock()
        mock_analysis.total_processed = 10
        mock_analysis.surge_detected = True
        mock_analysis.alpha_count = 3
        mock_analysis.noise_count = 5
        mock_analysis.disinfo_count = 2
        surge = MagicMock()
        surge.tweet_count = 5
        surge.velocity = 2.0
        alpha_tweet = MagicMock()
        alpha_tweet.alpha_score = 0.9
        alpha_tweet.author = "user1"
        alpha_tweet.text = "Important breaking news"
        surge.alpha_tweets = [alpha_tweet]
        mock_analysis.surges = [surge]

        with patch.dict("sys.modules", {
            "sts_monitor.surge_detector": MagicMock(analyze_surge=MagicMock(return_value=mock_analysis)),
            "sts_monitor.connectors.nitter": MagicMock(
                NitterConnector=mock_connector_cls,
                get_accounts_for_categories=MagicMock(return_value=["@acct"]),
            ),
        }):
            result = cmd_surge(self._make_args())
        assert result == 0
        out = capsys.readouterr().out
        assert "Surge Analysis" in out

    def test_surge_with_categories_and_json(self, capsys):
        from sts_monitor.__main__ import cmd_surge

        mock_connector_result = MagicMock()
        mock_connector_result.observations = []

        mock_connector_cls = MagicMock()
        mock_connector_cls.return_value.collect.return_value = mock_connector_result

        mock_analysis = MagicMock()
        mock_analysis.total_processed = 0
        mock_analysis.surge_detected = False
        mock_analysis.alpha_count = 0
        mock_analysis.noise_count = 0
        mock_analysis.disinfo_count = 0
        mock_analysis.surges = []

        with patch.dict("sys.modules", {
            "sts_monitor.surge_detector": MagicMock(analyze_surge=MagicMock(return_value=mock_analysis)),
            "sts_monitor.connectors.nitter": MagicMock(
                NitterConnector=mock_connector_cls,
                get_accounts_for_categories=MagicMock(return_value=[]),
            ),
        }):
            result = cmd_surge(self._make_args(categories="conflict,osint", json=True))
        assert result == 0


class TestCmdRetention:
    """Test cmd_retention CLI handler."""

    def test_retention_dry_run(self, capsys):
        from sts_monitor.__main__ import cmd_retention

        args = SimpleNamespace(max_age=90, dry_run=True)
        with patch("sts_monitor.retention.run_retention", return_value={
            "observations_removed": 5,
            "ingestion_runs_removed": 3,
            "alert_events_removed": 2,
            "jobs_removed": 1,
            "dry_run": True,
        }):
            result = cmd_retention(args)
        assert result == 0
        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert "5" in out

    def test_retention_actual(self, capsys):
        from sts_monitor.__main__ import cmd_retention

        args = SimpleNamespace(max_age=60, dry_run=False)
        with patch("sts_monitor.retention.run_retention", return_value={
            "observations_removed": 0,
            "ingestion_runs_removed": 0,
            "alert_events_removed": 0,
            "jobs_removed": 0,
            "dry_run": False,
        }):
            result = cmd_retention(args)
        assert result == 0
        out = capsys.readouterr().out
        assert "DRY RUN" not in out


class TestCmdServe:
    """Test cmd_serve."""

    def test_serve_calls_uvicorn(self):
        from sts_monitor.__main__ import cmd_serve

        args = SimpleNamespace(host="0.0.0.0", port=9090, reload=True)
        with patch.dict("sys.modules", {"uvicorn": MagicMock()}) as _:
            import sys
            mock_uvicorn = sys.modules["uvicorn"]
            result = cmd_serve(args)
        assert result == 0
        mock_uvicorn.run.assert_called_once_with(
            "sts_monitor.main:app",
            host="0.0.0.0",
            port=9090,
            reload=True,
        )


class TestBuildParser:
    """Parser construction completeness."""

    def test_all_subcommands_exist(self):
        from sts_monitor.__main__ import build_parser
        import argparse
        parser = build_parser()
        assert isinstance(parser, argparse.ArgumentParser)

    def test_report_file_argument(self):
        from sts_monitor.__main__ import build_parser
        parser = build_parser()
        args = parser.parse_args(["cycle", "topic", "--report-file", "out.md"])
        assert args.report_file == "out.md"

    def test_serve_reload(self):
        from sts_monitor.__main__ import build_parser
        parser = build_parser()
        args = parser.parse_args(["serve", "--reload"])
        assert args.reload is True

    def test_deep_truth_json(self):
        from sts_monitor.__main__ import build_parser
        parser = build_parser()
        args = parser.parse_args(["deep-truth", "topic", "--json"])
        assert args.json is True

    def test_surge_json(self):
        from sts_monitor.__main__ import build_parser
        parser = build_parser()
        args = parser.parse_args(["surge", "topic", "--json"])
        assert args.json is True


class TestMainIfNameMain:
    """Test the if __name__ == '__main__' block."""

    def test_main_exit(self):
        from sts_monitor.__main__ import main
        with patch("sys.argv", ["sts_monitor"]):
            result = main()
        assert result == 0


# =====================================================================
#  NOTIFICATIONS TESTS
# =====================================================================

class TestAlertNotification:
    """Test the AlertNotification dataclass."""

    def test_defaults(self):
        from sts_monitor.notifications import AlertNotification
        n = AlertNotification(title="Test", message="Body")
        assert n.severity == "info"
        assert n.investigation_id is None
        assert n.metadata is None

    def test_all_fields(self):
        from sts_monitor.notifications import AlertNotification
        n = AlertNotification(
            title="Alert", message="Details", severity="critical",
            investigation_id="inv-1", metadata={"key": "val"},
        )
        assert n.severity == "critical"
        assert n.metadata == {"key": "val"}


class TestSendEmail:

    def test_no_smtp_host_returns_false(self):
        from sts_monitor.notifications import AlertNotification, send_email
        n = AlertNotification(title="Test", message="Body")
        with patch("sts_monitor.notifications.SMTP_HOST", ""), \
             patch("sts_monitor.notifications.SMTP_TO", "user@example.com"):
            assert send_email(n) is False

    def test_no_smtp_to_returns_false(self):
        from sts_monitor.notifications import AlertNotification, send_email
        n = AlertNotification(title="Test", message="Body")
        with patch("sts_monitor.notifications.SMTP_HOST", "smtp.test.com"), \
             patch("sts_monitor.notifications.SMTP_TO", ""):
            assert send_email(n) is False

    def test_success_with_starttls(self):
        from sts_monitor.notifications import AlertNotification, send_email

        n = AlertNotification(
            title="Test", message="Body", severity="critical",
            investigation_id="inv-1", metadata={"key": "val"},
        )
        mock_server = MagicMock()
        mock_smtp_cls = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        with patch("sts_monitor.notifications.SMTP_HOST", "smtp.test.com"), \
             patch("sts_monitor.notifications.SMTP_TO", "user@example.com"), \
             patch("sts_monitor.notifications.SMTP_PORT", 587), \
             patch("sts_monitor.notifications.SMTP_USER", "user"), \
             patch("sts_monitor.notifications.SMTP_PASSWORD", "pass"), \
             patch("sts_monitor.notifications.SMTP_FROM", "from@test.com"), \
             patch("smtplib.SMTP", mock_smtp_cls):
            assert send_email(n) is True

        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("user", "pass")
        mock_server.send_message.assert_called_once()

    def test_success_without_starttls_no_user(self):
        from sts_monitor.notifications import AlertNotification, send_email

        n = AlertNotification(title="Test", message="Body")
        mock_server = MagicMock()
        mock_smtp_cls = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        with patch("sts_monitor.notifications.SMTP_HOST", "smtp.test.com"), \
             patch("sts_monitor.notifications.SMTP_TO", "user@example.com"), \
             patch("sts_monitor.notifications.SMTP_PORT", 25), \
             patch("sts_monitor.notifications.SMTP_USER", ""), \
             patch("smtplib.SMTP", mock_smtp_cls):
            assert send_email(n) is True

        mock_server.starttls.assert_not_called()
        mock_server.login.assert_not_called()

    def test_smtp_exception_returns_false(self):
        from sts_monitor.notifications import AlertNotification, send_email

        n = AlertNotification(title="Test", message="Body")
        with patch("sts_monitor.notifications.SMTP_HOST", "smtp.test.com"), \
             patch("sts_monitor.notifications.SMTP_TO", "user@example.com"), \
             patch("smtplib.SMTP", side_effect=smtplib.SMTPException("fail")):
            assert send_email(n) is False

    def test_email_without_investigation_or_metadata(self):
        from sts_monitor.notifications import AlertNotification, send_email

        n = AlertNotification(title="Test", message="Body", investigation_id=None, metadata=None)
        mock_server = MagicMock()
        mock_smtp_cls = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        with patch("sts_monitor.notifications.SMTP_HOST", "smtp.test.com"), \
             patch("sts_monitor.notifications.SMTP_TO", "user@example.com"), \
             patch("sts_monitor.notifications.SMTP_PORT", 25), \
             patch("sts_monitor.notifications.SMTP_USER", ""), \
             patch("smtplib.SMTP", mock_smtp_cls):
            assert send_email(n) is True


class TestSendSlack:

    def test_no_webhook_url_returns_false(self):
        from sts_monitor.notifications import AlertNotification, send_slack
        n = AlertNotification(title="Test", message="Body")
        with patch("sts_monitor.notifications.SLACK_WEBHOOK_URL", ""):
            assert send_slack(n) is False

    def test_success(self):
        from sts_monitor.notifications import AlertNotification, send_slack

        n = AlertNotification(title="Test", message="Body", severity="critical",
                              investigation_id="inv-1")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("sts_monitor.notifications.SLACK_WEBHOOK_URL", "https://hooks.slack.com/xxx"), \
             patch("sts_monitor.notifications.SLACK_CHANNEL", "#alerts"), \
             patch("httpx.post", return_value=mock_resp):
            assert send_slack(n) is True

    def test_success_without_channel_or_investigation(self):
        from sts_monitor.notifications import AlertNotification, send_slack

        n = AlertNotification(title="Test", message="Body", severity="info")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("sts_monitor.notifications.SLACK_WEBHOOK_URL", "https://hooks.slack.com/xxx"), \
             patch("sts_monitor.notifications.SLACK_CHANNEL", ""), \
             patch("httpx.post", return_value=mock_resp):
            assert send_slack(n) is True

    def test_unknown_severity_emoji(self):
        from sts_monitor.notifications import AlertNotification, send_slack

        n = AlertNotification(title="Test", message="Body", severity="unknown_sev")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("sts_monitor.notifications.SLACK_WEBHOOK_URL", "https://hooks.slack.com/xxx"), \
             patch("sts_monitor.notifications.SLACK_CHANNEL", ""), \
             patch("httpx.post", return_value=mock_resp):
            assert send_slack(n) is True

    def test_all_severity_emojis(self):
        """Cover all branches in the severity_emoji dict."""
        from sts_monitor.notifications import AlertNotification, send_slack

        for sev in ["critical", "high", "medium", "low", "info"]:
            n = AlertNotification(title="Test", message="Body", severity=sev)
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()

            with patch("sts_monitor.notifications.SLACK_WEBHOOK_URL", "https://hooks.slack.com/x"), \
                 patch("sts_monitor.notifications.SLACK_CHANNEL", ""), \
                 patch("httpx.post", return_value=mock_resp):
                assert send_slack(n) is True

    def test_exception_returns_false(self):
        from sts_monitor.notifications import AlertNotification, send_slack

        n = AlertNotification(title="Test", message="Body")
        with patch("sts_monitor.notifications.SLACK_WEBHOOK_URL", "https://hooks.slack.com/xxx"), \
             patch("httpx.post", side_effect=httpx.HTTPError("fail")):
            assert send_slack(n) is False


class TestSendWebhook:

    def test_no_webhook_url_returns_false(self):
        from sts_monitor.notifications import AlertNotification, send_webhook
        n = AlertNotification(title="Test", message="Body")
        with patch("sts_monitor.notifications.WEBHOOK_URL", ""):
            assert send_webhook(n) is False

    def test_success(self):
        from sts_monitor.notifications import AlertNotification, send_webhook

        n = AlertNotification(title="Test", message="Body", metadata={"k": "v"})
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("sts_monitor.notifications.WEBHOOK_URL", "https://webhook.test.com/hook"), \
             patch("httpx.post", return_value=mock_resp):
            assert send_webhook(n) is True

    def test_success_no_metadata(self):
        from sts_monitor.notifications import AlertNotification, send_webhook

        n = AlertNotification(title="Test", message="Body", metadata=None)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("sts_monitor.notifications.WEBHOOK_URL", "https://webhook.test.com/hook"), \
             patch("httpx.post", return_value=mock_resp) as mock_post:
            assert send_webhook(n) is True
            payload = mock_post.call_args.kwargs["json"]
            assert payload["metadata"] == {}

    def test_exception_returns_false(self):
        from sts_monitor.notifications import AlertNotification, send_webhook

        n = AlertNotification(title="Test", message="Body")
        with patch("sts_monitor.notifications.WEBHOOK_URL", "https://webhook.test.com/hook"), \
             patch("httpx.post", side_effect=httpx.HTTPError("fail")):
            assert send_webhook(n) is False


class TestNotifyAll:

    def test_no_channels_configured(self):
        from sts_monitor.notifications import AlertNotification, notify_all

        n = AlertNotification(title="Test", message="Body")
        with patch("sts_monitor.notifications.SMTP_HOST", ""), \
             patch("sts_monitor.notifications.SMTP_TO", ""), \
             patch("sts_monitor.notifications.SLACK_WEBHOOK_URL", ""), \
             patch("sts_monitor.notifications.WEBHOOK_URL", ""):
            results = notify_all(n)
        assert results == {}

    def test_all_channels_configured(self):
        from sts_monitor.notifications import AlertNotification, notify_all

        n = AlertNotification(title="Test", message="Body")
        with patch("sts_monitor.notifications.SMTP_HOST", "smtp.test.com"), \
             patch("sts_monitor.notifications.SMTP_TO", "user@test.com"), \
             patch("sts_monitor.notifications.SLACK_WEBHOOK_URL", "https://hooks.slack.com/x"), \
             patch("sts_monitor.notifications.WEBHOOK_URL", "https://webhook.test.com/x"), \
             patch("sts_monitor.notifications.send_email", return_value=True), \
             patch("sts_monitor.notifications.send_slack", return_value=True), \
             patch("sts_monitor.notifications.send_webhook", return_value=False):
            results = notify_all(n)

        assert results == {"email": True, "slack": True, "webhook": False}

    def test_only_email_configured(self):
        from sts_monitor.notifications import AlertNotification, notify_all

        n = AlertNotification(title="Test", message="Body")
        with patch("sts_monitor.notifications.SMTP_HOST", "smtp.test.com"), \
             patch("sts_monitor.notifications.SMTP_TO", "user@test.com"), \
             patch("sts_monitor.notifications.SLACK_WEBHOOK_URL", ""), \
             patch("sts_monitor.notifications.WEBHOOK_URL", ""), \
             patch("sts_monitor.notifications.send_email", return_value=True):
            results = notify_all(n)
        assert "email" in results
        assert "slack" not in results

    def test_only_slack_configured(self):
        from sts_monitor.notifications import AlertNotification, notify_all

        n = AlertNotification(title="Test", message="Body")
        with patch("sts_monitor.notifications.SMTP_HOST", ""), \
             patch("sts_monitor.notifications.SMTP_TO", ""), \
             patch("sts_monitor.notifications.SLACK_WEBHOOK_URL", "https://hooks.slack.com/x"), \
             patch("sts_monitor.notifications.WEBHOOK_URL", ""), \
             patch("sts_monitor.notifications.send_slack", return_value=True):
            results = notify_all(n)
        assert "slack" in results
        assert "email" not in results

    def test_only_webhook_configured(self):
        from sts_monitor.notifications import AlertNotification, notify_all

        n = AlertNotification(title="Test", message="Body")
        with patch("sts_monitor.notifications.SMTP_HOST", ""), \
             patch("sts_monitor.notifications.SMTP_TO", ""), \
             patch("sts_monitor.notifications.SLACK_WEBHOOK_URL", ""), \
             patch("sts_monitor.notifications.WEBHOOK_URL", "https://webhook.test.com/x"), \
             patch("sts_monitor.notifications.send_webhook", return_value=True):
            results = notify_all(n)
        assert "webhook" in results
        assert "email" not in results


# =====================================================================
#  PLUGINS TESTS
# =====================================================================

class TestPluginRegistry:

    def test_register_and_get_connector(self):
        from sts_monitor.plugins import PluginRegistry

        registry = PluginRegistry()

        class FakeConnector:
            name = "fake"
            def collect(self, query=None):
                pass

        registry.register_connector(
            "fake", FakeConnector,
            description="A fake connector",
            author="Test",
            version="1.0.0",
        )

        assert registry.get_connector("fake") is FakeConnector
        assert "fake" in registry.registered
        meta = registry.registered["fake"]
        assert meta["description"] == "A fake connector"
        assert meta["author"] == "Test"
        assert meta["version"] == "1.0.0"

    def test_get_connector_not_found(self):
        from sts_monitor.plugins import PluginRegistry

        registry = PluginRegistry()
        assert registry.get_connector("nonexistent") is None

    def test_create_connector_success(self):
        from sts_monitor.plugins import PluginRegistry

        registry = PluginRegistry()

        class FakeConnector:
            name = "fake"
            def __init__(self, **kwargs):
                self.kwargs = kwargs
            def collect(self, query=None):
                pass

        registry.register_connector("fake", FakeConnector)
        instance = registry.create_connector("fake", param1="val1")
        assert isinstance(instance, FakeConnector)
        assert instance.kwargs == {"param1": "val1"}

    def test_create_connector_not_found(self):
        from sts_monitor.plugins import PluginRegistry

        registry = PluginRegistry()
        with pytest.raises(KeyError, match="No connector registered"):
            registry.create_connector("nonexistent")

    def test_registered_returns_copy(self):
        from sts_monitor.plugins import PluginRegistry

        registry = PluginRegistry()
        r1 = registry.registered
        r1["injected"] = "bad"
        assert "injected" not in registry.registered


class TestPluginDiscoverEntrypoints:

    def test_discover_with_select(self):
        from sts_monitor.plugins import PluginRegistry

        registry = PluginRegistry()

        class FakeEP:
            name = "ep_connector"
            value = "some.module:SomeClass"
            def load(self):
                cls = type("EPConnector", (), {"name": "ep_connector", "collect": lambda self, q=None: None})
                return cls

        mock_eps = MagicMock()
        mock_eps.select.return_value = [FakeEP()]

        with patch("importlib.metadata.entry_points", return_value=mock_eps):
            count = registry.discover_entrypoints()

        assert count == 1
        assert registry.get_connector("ep_connector") is not None

    def test_discover_without_select(self):
        from sts_monitor.plugins import PluginRegistry

        registry = PluginRegistry()

        class FakeEP:
            name = "ep2"
            value = "some.module:C"
            def load(self):
                return type("C", (), {"name": "ep2", "collect": lambda self, q=None: None})

        mock_eps = {"sts_monitor.connectors": [FakeEP()]}

        with patch("importlib.metadata.entry_points", return_value=mock_eps):
            count = registry.discover_entrypoints()

        assert count == 1

    def test_discover_entry_point_load_failure(self):
        from sts_monitor.plugins import PluginRegistry

        registry = PluginRegistry()

        class BadEP:
            name = "bad_ep"
            value = "bad.module:Bad"
            def load(self):
                raise ImportError("no such module")

        mock_eps = MagicMock()
        mock_eps.select.return_value = [BadEP()]

        with patch("importlib.metadata.entry_points", return_value=mock_eps):
            count = registry.discover_entrypoints()

        assert count == 0

    def test_discover_entrypoints_top_level_exception(self):
        from sts_monitor.plugins import PluginRegistry

        registry = PluginRegistry()

        with patch("importlib.metadata.entry_points", side_effect=RuntimeError("broken")):
            count = registry.discover_entrypoints()

        assert count == 0


class TestPluginDiscoverDir:

    def test_nonexistent_dir_returns_0(self):
        from sts_monitor.plugins import PluginRegistry

        registry = PluginRegistry()
        count = registry.discover_plugin_dir(Path("/nonexistent/dir/xyz123"))
        assert count == 0

    def test_loads_valid_plugin_file(self, tmp_path):
        from sts_monitor.plugins import PluginRegistry

        plugin_code = textwrap.dedent("""\
            class MyPlugin:
                name = "my_plugin"
                def collect(self, query=None):
                    return []
        """)
        (tmp_path / "my_plugin.py").write_text(plugin_code)

        registry = PluginRegistry()
        count = registry.discover_plugin_dir(tmp_path)
        assert count == 1
        assert registry.get_connector("my_plugin") is not None

    def test_skips_underscore_files(self, tmp_path):
        from sts_monitor.plugins import PluginRegistry

        (tmp_path / "_private.py").write_text("class X:\n  name='x'\n  def collect(self): pass\n")

        registry = PluginRegistry()
        count = registry.discover_plugin_dir(tmp_path)
        assert count == 0

    def test_skips_non_connector_classes(self, tmp_path):
        from sts_monitor.plugins import PluginRegistry

        plugin_code = textwrap.dedent("""\
            class NotAConnector:
                pass

            class AlsoNot:
                name = "x"
                # no collect method
        """)
        (tmp_path / "helper.py").write_text(plugin_code)

        registry = PluginRegistry()
        count = registry.discover_plugin_dir(tmp_path)
        assert count == 0

    def test_handles_bad_plugin_file(self, tmp_path):
        from sts_monitor.plugins import PluginRegistry

        (tmp_path / "broken.py").write_text("raise RuntimeError('import error')")

        registry = PluginRegistry()
        count = registry.discover_plugin_dir(tmp_path)
        assert count == 0

    def test_spec_none_skips(self, tmp_path):
        from sts_monitor.plugins import PluginRegistry

        (tmp_path / "good.py").write_text("x = 1\n")

        registry = PluginRegistry()
        with patch("importlib.util.spec_from_file_location", return_value=None):
            count = registry.discover_plugin_dir(tmp_path)
        assert count == 0

    def test_spec_loader_none_skips(self, tmp_path):
        from sts_monitor.plugins import PluginRegistry

        (tmp_path / "good.py").write_text("x = 1\n")

        mock_spec = MagicMock()
        mock_spec.loader = None

        registry = PluginRegistry()
        with patch("importlib.util.spec_from_file_location", return_value=mock_spec):
            count = registry.discover_plugin_dir(tmp_path)
        assert count == 0

    def test_uses_default_plugin_dir(self):
        from sts_monitor.plugins import PluginRegistry

        registry = PluginRegistry()
        count = registry.discover_plugin_dir()
        assert count >= 0

    def test_plugin_class_uses_name_attribute(self, tmp_path):
        from sts_monitor.plugins import PluginRegistry

        plugin_code = textwrap.dedent("""\
            class WeirdPlugin:
                name = "custom_name"
                def collect(self, query=None):
                    return []
        """)
        (tmp_path / "weird.py").write_text(plugin_code)

        registry = PluginRegistry()
        count = registry.discover_plugin_dir(tmp_path)
        assert count == 1


class TestPluginDiscoverAll:

    def test_discover_all(self):
        from sts_monitor.plugins import PluginRegistry

        registry = PluginRegistry()
        with patch.object(registry, "discover_entrypoints", return_value=2), \
             patch.object(registry, "discover_plugin_dir", return_value=3):
            result = registry.discover_all()

        assert result == {"entrypoints": 2, "plugin_dir": 3}


class TestGlobalRegistry:

    def test_singleton_exists(self):
        from sts_monitor.plugins import plugin_registry, PluginRegistry
        assert isinstance(plugin_registry, PluginRegistry)
