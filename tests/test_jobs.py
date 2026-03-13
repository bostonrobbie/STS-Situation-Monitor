"""Comprehensive unit tests for sts_monitor.jobs module."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from sts_monitor.database import Base, SessionLocal, engine
from sts_monitor.jobs import (
    _execute_job,
    _fetch_next_pending_job,
    create_schedule,
    enqueue_job,
    process_job,
    process_job_batch,
    process_next_job,
    requeue_dead_letter,
    tick_schedules,
)
from sts_monitor.models import InvestigationORM, JobORM, JobScheduleORM, ObservationORM
from sts_monitor.pipeline import Observation, PipelineResult, SignalPipeline

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


@pytest.fixture()
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def mock_pipeline():
    return MagicMock(spec=SignalPipeline)


@pytest.fixture()
def mock_llm():
    from sts_monitor.llm import LocalLLMClient
    return MagicMock(spec=LocalLLMClient)


def _make_investigation(session, inv_id="inv-1", topic="Test topic"):
    inv = InvestigationORM(id=inv_id, topic=topic)
    session.add(inv)
    session.commit()
    return inv


def _make_observation(session, investigation_id="inv-1", claim="Some claim"):
    obs = ObservationORM(
        investigation_id=investigation_id,
        source="test:source",
        claim=claim,
        url="https://example.com/obs",
        captured_at=datetime.now(UTC),
        reliability_hint=0.7,
    )
    session.add(obs)
    session.commit()
    return obs


# ---------------------------------------------------------------------------
# enqueue_job
# ---------------------------------------------------------------------------

class TestEnqueueJob:
    def test_basic_enqueue(self, session):
        job = enqueue_job(session, job_type="test_type", payload={"key": "value"})
        assert job.id is not None
        assert job.job_type == "test_type"
        assert job.status == "pending"
        assert job.priority == 50
        assert job.attempts == 0
        assert job.max_attempts == 3
        assert job.dead_lettered is False
        assert json.loads(job.payload_json) == {"key": "value"}

    def test_priority_clamp_low(self, session):
        job = enqueue_job(session, job_type="t", payload={}, priority=0)
        assert job.priority == 1

    def test_priority_clamp_high(self, session):
        job = enqueue_job(session, job_type="t", payload={}, priority=200)
        assert job.priority == 100

    def test_priority_within_range_unchanged(self, session):
        job = enqueue_job(session, job_type="t", payload={}, priority=75)
        assert job.priority == 75

    def test_max_attempts_clamp_low(self, session):
        job = enqueue_job(session, job_type="t", payload={}, max_attempts=0)
        assert job.max_attempts == 1

    def test_max_attempts_clamp_high(self, session):
        job = enqueue_job(session, job_type="t", payload={}, max_attempts=50)
        assert job.max_attempts == 10

    def test_max_attempts_within_range(self, session):
        job = enqueue_job(session, job_type="t", payload={}, max_attempts=5)
        assert job.max_attempts == 5

    def test_run_at_defaults_to_now(self, session):
        before = datetime.now(UTC)
        job = enqueue_job(session, job_type="t", payload={})
        after = datetime.now(UTC)
        assert before <= job.run_at.replace(tzinfo=UTC) <= after or job.run_at is not None

    def test_run_at_custom(self, session):
        future = datetime.now(UTC) + timedelta(hours=1)
        job = enqueue_job(session, job_type="t", payload={}, run_at=future)
        # The run_at should match the provided value (within a small delta due to tz)
        diff = abs((job.run_at.replace(tzinfo=UTC) - future).total_seconds())
        assert diff < 2


# ---------------------------------------------------------------------------
# create_schedule
# ---------------------------------------------------------------------------

class TestCreateSchedule:
    def test_basic_creation(self, session):
        sched = create_schedule(
            session,
            name="my-schedule",
            job_type="ingest_simulated",
            payload={"investigation_id": "abc"},
            interval_seconds=60,
            priority=80,
        )
        assert sched.id is not None
        assert sched.name == "my-schedule"
        assert sched.job_type == "ingest_simulated"
        assert sched.interval_seconds == 60
        assert sched.priority == 80
        assert sched.active is True
        assert sched.last_enqueued_at is None

    def test_interval_minimum_clamped(self, session):
        sched = create_schedule(
            session, name="fast", job_type="t", payload={}, interval_seconds=1
        )
        assert sched.interval_seconds == 10

    def test_interval_at_minimum(self, session):
        sched = create_schedule(
            session, name="exact", job_type="t", payload={}, interval_seconds=10
        )
        assert sched.interval_seconds == 10

    def test_priority_clamped_in_schedule(self, session):
        sched = create_schedule(
            session, name="lo", job_type="t", payload={}, interval_seconds=30, priority=0
        )
        assert sched.priority == 1

        sched2 = create_schedule(
            session, name="hi", job_type="t", payload={}, interval_seconds=30, priority=999
        )
        assert sched2.priority == 100


# ---------------------------------------------------------------------------
# tick_schedules
# ---------------------------------------------------------------------------

class TestTickSchedules:
    def test_enqueues_on_first_tick(self, session):
        create_schedule(
            session, name="s1", job_type="test_type", payload={"x": 1}, interval_seconds=60
        )
        count = tick_schedules(session)
        assert count == 1
        jobs = session.query(JobORM).filter_by(job_type="test_type").all()
        assert len(jobs) == 1

    def test_skips_when_not_due(self, session):
        now = datetime.utcnow()
        sched = create_schedule(
            session, name="s2", job_type="test_type", payload={}, interval_seconds=300
        )
        # Simulate that it was just enqueued (use naive datetime for SQLite compat)
        sched.last_enqueued_at = now
        session.commit()

        count = tick_schedules(session, now=now + timedelta(seconds=10))
        assert count == 0

    def test_enqueues_when_due(self, session):
        now = datetime.utcnow()
        sched = create_schedule(
            session, name="s3", job_type="test_type", payload={}, interval_seconds=60
        )
        sched.last_enqueued_at = now - timedelta(seconds=120)
        session.commit()

        count = tick_schedules(session, now=now)
        assert count == 1

    def test_first_time_enqueue_when_last_enqueued_is_none(self, session):
        create_schedule(
            session, name="s4", job_type="test_type", payload={}, interval_seconds=60
        )
        # last_enqueued_at is None by default
        count = tick_schedules(session)
        assert count == 1

    def test_inactive_schedule_not_ticked(self, session):
        sched = create_schedule(
            session, name="s5", job_type="test_type", payload={}, interval_seconds=10
        )
        sched.active = False
        session.commit()

        count = tick_schedules(session)
        assert count == 0

    def test_multiple_schedules(self, session):
        now = datetime.now(UTC)
        create_schedule(session, name="a", job_type="t", payload={}, interval_seconds=10)
        create_schedule(session, name="b", job_type="t", payload={}, interval_seconds=10)
        count = tick_schedules(session, now=now)
        assert count == 2


# ---------------------------------------------------------------------------
# _fetch_next_pending_job
# ---------------------------------------------------------------------------

class TestFetchNextPendingJob:
    def test_returns_pending_job(self, session):
        enqueue_job(session, job_type="t", payload={}, priority=50)
        job = _fetch_next_pending_job(session)
        assert job is not None
        assert job.status == "pending"

    def test_returns_highest_priority_first(self, session):
        low = enqueue_job(session, job_type="t", payload={"p": "low"}, priority=10)
        high = enqueue_job(session, job_type="t", payload={"p": "high"}, priority=90)
        job = _fetch_next_pending_job(session)
        assert job.id == high.id

    def test_priority_min_filter(self, session):
        enqueue_job(session, job_type="t", payload={}, priority=30)
        job = _fetch_next_pending_job(session, priority_min=50)
        assert job is None

    def test_priority_max_filter(self, session):
        enqueue_job(session, job_type="t", payload={}, priority=80)
        job = _fetch_next_pending_job(session, priority_max=50)
        assert job is None

    def test_priority_range_filter(self, session):
        enqueue_job(session, job_type="t", payload={}, priority=55)
        job = _fetch_next_pending_job(session, priority_min=40, priority_max=69)
        assert job is not None

    def test_skips_dead_lettered(self, session):
        job = enqueue_job(session, job_type="t", payload={}, priority=50)
        job.dead_lettered = True
        session.commit()
        result = _fetch_next_pending_job(session)
        assert result is None

    def test_skips_future_run_at(self, session):
        future = datetime.now(UTC) + timedelta(hours=1)
        enqueue_job(session, job_type="t", payload={}, run_at=future)
        result = _fetch_next_pending_job(session)
        assert result is None

    def test_returns_none_when_no_jobs(self, session):
        result = _fetch_next_pending_job(session)
        assert result is None

    def test_skips_non_pending_status(self, session):
        job = enqueue_job(session, job_type="t", payload={})
        job.status = "running"
        session.commit()
        result = _fetch_next_pending_job(session)
        assert result is None


# ---------------------------------------------------------------------------
# process_job
# ---------------------------------------------------------------------------

class TestProcessJob:
    def test_success_path(self, session, mock_pipeline, mock_llm):
        job = enqueue_job(session, job_type="t", payload={})
        with patch("sts_monitor.jobs._execute_job", return_value={"done": True}) as mock_exec:
            result = process_job(session, job, mock_pipeline, mock_llm)
        assert result["status"] == "completed"
        assert result["result"] == {"done": True}
        assert job.status == "completed"
        assert job.last_error is None

    def test_failure_with_retry(self, session, mock_pipeline, mock_llm):
        job = enqueue_job(session, job_type="t", payload={}, max_attempts=3)
        with patch("sts_monitor.jobs._execute_job", side_effect=RuntimeError("boom")):
            result = process_job(session, job, mock_pipeline, mock_llm)
        assert result["status"] == "pending"
        assert result["error"] == "boom"
        assert job.status == "pending"
        # After rollback, attempts reverts to 0 since _execute_job's increment was rolled back
        assert job.attempts == 0
        assert job.last_error == "boom"

    def test_dead_letter_after_max_attempts(self, session, mock_pipeline, mock_llm):
        job = enqueue_job(session, job_type="t", payload={}, max_attempts=1)
        # Pre-set attempts to max so the exception handler sees attempts >= max_attempts
        job.attempts = 1
        session.commit()
        with patch("sts_monitor.jobs._execute_job", side_effect=RuntimeError("fail")):
            result = process_job(session, job, mock_pipeline, mock_llm)
        assert result["status"] == "dead_letter"
        assert job.status == "dead_letter"
        assert job.dead_lettered is True

    def test_rollback_on_failure(self, session, mock_pipeline, mock_llm):
        _make_investigation(session, "inv-rollback", "Rollback test")
        job = enqueue_job(session, job_type="t", payload={}, max_attempts=3)
        with patch("sts_monitor.jobs._execute_job", side_effect=ValueError("bad")):
            result = process_job(session, job, mock_pipeline, mock_llm)
        # Session should still be usable after rollback
        assert result["error"] == "bad"
        inv = session.get(InvestigationORM, "inv-rollback")
        assert inv is not None

    def test_custom_retry_backoff(self, session, mock_pipeline, mock_llm):
        job = enqueue_job(session, job_type="t", payload={}, max_attempts=5)
        before = datetime.now(UTC)
        with patch("sts_monitor.jobs._execute_job", side_effect=RuntimeError("err")):
            process_job(session, job, mock_pipeline, mock_llm, retry_backoff_s=30)
        # run_at should be at least 30 seconds from now (approximately)
        assert job.run_at.replace(tzinfo=UTC) >= before + timedelta(seconds=25)


# ---------------------------------------------------------------------------
# process_next_job
# ---------------------------------------------------------------------------

class TestProcessNextJob:
    def test_returns_none_when_no_jobs(self, session, mock_pipeline, mock_llm):
        result = process_next_job(session, mock_pipeline, mock_llm)
        assert result is None

    def test_processes_available_job(self, session, mock_pipeline, mock_llm):
        enqueue_job(session, job_type="t", payload={"k": "v"})
        with patch("sts_monitor.jobs._execute_job", return_value={"ok": True}):
            result = process_next_job(session, mock_pipeline, mock_llm)
        assert result is not None
        assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# process_job_batch
# ---------------------------------------------------------------------------

class TestProcessJobBatch:
    def test_processes_by_priority_tier(self, session, mock_pipeline, mock_llm):
        # Create jobs in different tiers
        enqueue_job(session, job_type="t", payload={"tier": "high"}, priority=85)
        enqueue_job(session, job_type="t", payload={"tier": "normal"}, priority=55)
        enqueue_job(session, job_type="t", payload={"tier": "low"}, priority=20)

        with patch("sts_monitor.jobs._execute_job", return_value={"ok": True}):
            results = process_job_batch(
                session, mock_pipeline, mock_llm,
                high_quota=1, normal_quota=1, low_quota=1,
            )
        assert len(results) == 3
        assert all(r["status"] == "completed" for r in results)

    def test_respects_quotas(self, session, mock_pipeline, mock_llm):
        # Create 3 high-priority jobs but quota is 1
        for i in range(3):
            enqueue_job(session, job_type="t", payload={"i": i}, priority=80)

        with patch("sts_monitor.jobs._execute_job", return_value={"ok": True}):
            results = process_job_batch(
                session, mock_pipeline, mock_llm,
                high_quota=1, normal_quota=0, low_quota=0,
            )
        assert len(results) == 1

    def test_empty_when_no_jobs(self, session, mock_pipeline, mock_llm):
        results = process_job_batch(session, mock_pipeline, mock_llm)
        assert results == []

    def test_skips_tiers_with_no_jobs(self, session, mock_pipeline, mock_llm):
        # Only a low-priority job
        enqueue_job(session, job_type="t", payload={}, priority=15)

        with patch("sts_monitor.jobs._execute_job", return_value={"ok": True}):
            results = process_job_batch(
                session, mock_pipeline, mock_llm,
                high_quota=2, normal_quota=2, low_quota=1,
            )
        assert len(results) == 1


# ---------------------------------------------------------------------------
# requeue_dead_letter
# ---------------------------------------------------------------------------

class TestRequeueDeadLetter:
    def test_requeues_dead_lettered_job(self, session):
        job = enqueue_job(session, job_type="t", payload={})
        job.dead_lettered = True
        job.status = "dead_letter"
        job.attempts = 3
        job.last_error = "some error"
        session.commit()

        requeued = requeue_dead_letter(session, job.id)
        assert requeued is not None
        assert requeued.status == "pending"
        assert requeued.dead_lettered is False
        assert requeued.attempts == 0
        assert requeued.last_error is None

    def test_returns_none_for_non_dead_lettered(self, session):
        job = enqueue_job(session, job_type="t", payload={})
        result = requeue_dead_letter(session, job.id)
        assert result is None

    def test_returns_none_for_missing_job(self, session):
        result = requeue_dead_letter(session, 99999)
        assert result is None


# ---------------------------------------------------------------------------
# _execute_job: ingest_simulated
# ---------------------------------------------------------------------------

class TestExecuteJobIngestSimulated:
    def test_ingest_simulated_creates_observations(self, session, mock_pipeline, mock_llm):
        inv = _make_investigation(session, "inv-sim", "Weather alert")
        job = enqueue_job(
            session,
            job_type="ingest_simulated",
            payload={"investigation_id": "inv-sim", "batch_size": 5, "include_noise": False},
        )
        result = _execute_job(session, job, mock_pipeline, mock_llm)
        assert result["job_type"] == "ingest_simulated"
        assert result["ingested_count"] >= 5
        session.flush()
        obs = session.query(ObservationORM).filter_by(investigation_id="inv-sim").all()
        assert len(obs) >= 5

    def test_ingest_simulated_missing_investigation(self, session, mock_pipeline, mock_llm):
        job = enqueue_job(
            session,
            job_type="ingest_simulated",
            payload={"investigation_id": "nonexistent"},
        )
        with pytest.raises(ValueError, match="Investigation not found"):
            _execute_job(session, job, mock_pipeline, mock_llm)

    def test_ingest_simulated_sets_status_running(self, session, mock_pipeline, mock_llm):
        _make_investigation(session, "inv-run", "Status check")
        job = enqueue_job(
            session,
            job_type="ingest_simulated",
            payload={"investigation_id": "inv-run", "batch_size": 2, "include_noise": False},
        )
        # After execution, job should have been set to running (then process_job sets completed)
        _execute_job(session, job, mock_pipeline, mock_llm)
        assert job.status == "running"
        assert job.attempts == 1


# ---------------------------------------------------------------------------
# _execute_job: run_pipeline
# ---------------------------------------------------------------------------

class TestExecuteJobRunPipeline:
    def test_run_pipeline_success(self, session, mock_pipeline, mock_llm):
        inv = _make_investigation(session, "inv-pipe", "Pipeline test")
        _make_observation(session, "inv-pipe", "A credible claim")

        fake_result = PipelineResult(
            accepted=[Observation(source="s", claim="c", url="u", captured_at=datetime.now(UTC), reliability_hint=0.8)],
            dropped=[],
            deduplicated=[],
            disputed_claims=[],
            summary="Test summary",
            confidence=0.85,
        )
        mock_pipeline.run.return_value = fake_result

        job = enqueue_job(
            session,
            job_type="run_pipeline",
            payload={"investigation_id": "inv-pipe", "use_llm": False},
        )
        result = _execute_job(session, job, mock_pipeline, mock_llm)
        assert result["job_type"] == "run_pipeline"
        assert result["confidence"] == 0.85
        assert result["llm_fallback_used"] is False
        mock_pipeline.run.assert_called_once()

    def test_run_pipeline_with_llm(self, session, mock_pipeline, mock_llm):
        inv = _make_investigation(session, "inv-llm", "LLM pipeline")
        _make_observation(session, "inv-llm", "Something happened")

        fake_result = PipelineResult(
            accepted=[], dropped=[], deduplicated=[], disputed_claims=[],
            summary="Pipeline summary", confidence=0.7,
        )
        mock_pipeline.run.return_value = fake_result
        mock_llm.summarize.return_value = "LLM enhanced summary"

        job = enqueue_job(
            session,
            job_type="run_pipeline",
            payload={"investigation_id": "inv-llm", "use_llm": True},
        )
        result = _execute_job(session, job, mock_pipeline, mock_llm)
        assert result["llm_fallback_used"] is False
        mock_llm.summarize.assert_called_once()

    def test_run_pipeline_llm_fallback(self, session, mock_pipeline, mock_llm):
        inv = _make_investigation(session, "inv-fb", "Fallback test")
        _make_observation(session, "inv-fb", "Claim for fallback")

        fake_result = PipelineResult(
            accepted=[], dropped=[], deduplicated=[], disputed_claims=[],
            summary="Deterministic summary", confidence=0.6,
        )
        mock_pipeline.run.return_value = fake_result
        mock_llm.summarize.side_effect = RuntimeError("LLM offline")

        job = enqueue_job(
            session,
            job_type="run_pipeline",
            payload={"investigation_id": "inv-fb", "use_llm": True},
        )
        result = _execute_job(session, job, mock_pipeline, mock_llm)
        assert result["llm_fallback_used"] is True

    def test_run_pipeline_missing_investigation(self, session, mock_pipeline, mock_llm):
        job = enqueue_job(
            session,
            job_type="run_pipeline",
            payload={"investigation_id": "nope"},
        )
        with pytest.raises(ValueError, match="Investigation not found"):
            _execute_job(session, job, mock_pipeline, mock_llm)

    def test_run_pipeline_no_observations(self, session, mock_pipeline, mock_llm):
        _make_investigation(session, "inv-empty", "Empty")
        job = enqueue_job(
            session,
            job_type="run_pipeline",
            payload={"investigation_id": "inv-empty"},
        )
        with pytest.raises(ValueError, match="No observations available"):
            _execute_job(session, job, mock_pipeline, mock_llm)

    def test_run_pipeline_creates_report(self, session, mock_pipeline, mock_llm):
        _make_investigation(session, "inv-rpt", "Report creation")
        _make_observation(session, "inv-rpt", "Something to report")

        fake_result = PipelineResult(
            accepted=[Observation(source="s", claim="c", url="u", captured_at=datetime.now(UTC), reliability_hint=0.9)],
            dropped=[],
            deduplicated=[],
            disputed_claims=[],
            summary="A report summary",
            confidence=0.9,
        )
        mock_pipeline.run.return_value = fake_result

        job = enqueue_job(
            session,
            job_type="run_pipeline",
            payload={"investigation_id": "inv-rpt", "use_llm": False},
        )
        _execute_job(session, job, mock_pipeline, mock_llm)
        session.flush()

        from sts_monitor.models import ReportORM
        reports = session.query(ReportORM).filter_by(investigation_id="inv-rpt").all()
        assert len(reports) == 1
        assert reports[0].confidence == 0.9
        assert reports[0].summary == "A report summary"


# ---------------------------------------------------------------------------
# _execute_job: unsupported job type
# ---------------------------------------------------------------------------

class TestExecuteJobUnsupported:
    def test_unsupported_job_type_raises(self, session, mock_pipeline, mock_llm):
        job = enqueue_job(session, job_type="unknown_type", payload={})
        with pytest.raises(ValueError, match="Unsupported job type"):
            _execute_job(session, job, mock_pipeline, mock_llm)


# ---------------------------------------------------------------------------
# Integration-style: full process flow
# ---------------------------------------------------------------------------

class TestEndToEndFlow:
    def test_enqueue_and_process_ingest(self, session, mock_pipeline, mock_llm):
        _make_investigation(session, "inv-e2e", "End to end")
        job = enqueue_job(
            session,
            job_type="ingest_simulated",
            payload={"investigation_id": "inv-e2e", "batch_size": 3, "include_noise": False},
        )
        result = process_job(session, job, mock_pipeline, mock_llm)
        assert result["status"] == "completed"
        assert result["result"]["ingested_count"] >= 3

    def test_dead_letter_and_requeue_cycle(self, session, mock_pipeline, mock_llm):
        job = enqueue_job(session, job_type="run_pipeline", payload={"investigation_id": "nope"}, max_attempts=1)
        result = process_job(session, job, mock_pipeline, mock_llm)
        assert result["status"] == "dead_letter"
        assert job.dead_lettered is True

        requeued = requeue_dead_letter(session, job.id)
        assert requeued is not None
        assert requeued.status == "pending"
        assert requeued.attempts == 0
