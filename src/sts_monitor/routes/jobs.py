"""Job queue and scheduling routes."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from sts_monitor.config import settings
from sts_monitor.database import get_session
from sts_monitor.deps import llm_client, pipeline
from sts_monitor.jobs import (
    create_schedule,
    enqueue_job,
    process_job_batch,
    process_next_job,
    requeue_dead_letter,
    tick_schedules,
)
from sts_monitor.models import InvestigationORM, JobORM, JobScheduleORM
from sts_monitor.schemas import (
    CreateScheduleRequest,
    EnqueueRunJobRequest,
    EnqueueSimulatedJobRequest,
    ProcessBatchRequest,
)
from sts_monitor.security import require_api_key

router = APIRouter()


@router.post("/jobs/enqueue/ingest-simulated/{investigation_id}")
def enqueue_simulated_ingest_job(
    investigation_id: str,
    payload: EnqueueSimulatedJobRequest,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    job = enqueue_job(
        session,
        job_type="ingest_simulated",
        payload={"investigation_id": investigation_id, "batch_size": payload.batch_size, "include_noise": payload.include_noise},
        priority=payload.priority,
        max_attempts=payload.max_attempts,
    )
    return {"job_id": job.id, "status": job.status, "job_type": job.job_type}


@router.post("/jobs/enqueue/run/{investigation_id}")
def enqueue_run_job(
    investigation_id: str,
    payload: EnqueueRunJobRequest,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    job = enqueue_job(
        session,
        job_type="run_pipeline",
        payload={"investigation_id": investigation_id, "use_llm": payload.use_llm},
        priority=payload.priority,
        max_attempts=payload.max_attempts,
    )
    return {"job_id": job.id, "status": job.status, "job_type": job.job_type}


@router.post("/schedules")
def create_job_schedule(
    payload: CreateScheduleRequest,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    schedule = create_schedule(
        session,
        name=payload.name,
        job_type=payload.job_type,
        payload=payload.payload,
        interval_seconds=payload.interval_seconds,
        priority=payload.priority,
    )
    return {"schedule_id": schedule.id, "name": schedule.name, "active": schedule.active}


@router.post("/schedules/tick")
def scheduler_tick(
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    enqueued = tick_schedules(session, default_max_attempts=settings.job_max_attempts)
    return {"enqueued": enqueued}


@router.get("/schedules")
def list_schedules(
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = session.scalars(select(JobScheduleORM).order_by(JobScheduleORM.created_at.desc())).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "job_type": row.job_type,
            "interval_seconds": row.interval_seconds,
            "priority": row.priority,
            "active": row.active,
            "last_enqueued_at": row.last_enqueued_at.isoformat() if row.last_enqueued_at else None,
        }
        for row in rows
    ]


@router.post("/jobs/process-next")
def process_next(
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    result = process_next_job(session=session, pipeline=pipeline, llm_client=llm_client, retry_backoff_s=settings.job_retry_backoff_s)
    if result is None:
        return {"status": "idle", "message": "No pending jobs"}
    return result


@router.post("/jobs/process-batch")
def process_batch(
    payload: ProcessBatchRequest,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    results = process_job_batch(
        session=session,
        pipeline=pipeline,
        llm_client=llm_client,
        high_quota=payload.high_quota,
        normal_quota=payload.normal_quota,
        low_quota=payload.low_quota,
        retry_backoff_s=settings.job_retry_backoff_s,
    )
    return {"processed": len(results), "results": results}


@router.get("/jobs/dead-letters")
def list_dead_letters(
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = session.scalars(select(JobORM).where(JobORM.dead_lettered.is_(True)).order_by(JobORM.updated_at.desc()).limit(200)).all()
    return [
        {
            "id": row.id,
            "job_type": row.job_type,
            "priority": row.priority,
            "attempts": row.attempts,
            "max_attempts": row.max_attempts,
            "last_error": row.last_error,
            "updated_at": row.updated_at.isoformat(),
        }
        for row in rows
    ]


@router.post("/jobs/dead-letters/{job_id}/requeue")
def requeue_dead_letter_job(
    job_id: int,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    job = requeue_dead_letter(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Dead-letter job not found")
    return {"job_id": job.id, "status": job.status}


@router.get("/jobs")
def list_jobs(
    limit: int = 50,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = session.scalars(select(JobORM).order_by(JobORM.created_at.desc()).limit(max(1, min(limit, 200)))).all()
    return [
        {
            "id": row.id,
            "job_type": row.job_type,
            "status": row.status,
            "priority": row.priority,
            "attempts": row.attempts,
            "max_attempts": row.max_attempts,
            "dead_lettered": row.dead_lettered,
            "last_error": row.last_error,
            "run_at": row.run_at.isoformat(),
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }
        for row in rows
    ]
