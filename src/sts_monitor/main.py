from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sts_monitor.config import settings
from sts_monitor.connectors import RSSConnector
from sts_monitor.database import Base, engine, get_session
from sts_monitor.llm import LocalLLMClient
from sts_monitor.jobs import create_schedule, enqueue_job, process_next_job, tick_schedules
from sts_monitor.models import FeedbackORM, IngestionRunORM, InvestigationORM, JobORM, JobScheduleORM, ObservationORM, ReportORM
from sts_monitor.pipeline import Observation, SignalPipeline
from sts_monitor.security import require_api_key
from sts_monitor.simulation import generate_simulated_observations


class InvestigationCreate(BaseModel):
    topic: str = Field(min_length=3, max_length=300)
    seed_query: str | None = None


class Investigation(BaseModel):
    id: str
    topic: str
    seed_query: str | None = None
    created_at: datetime


class RSSIngestRequest(BaseModel):
    feed_urls: list[str] = Field(min_length=1)
    query: str | None = None
    per_feed_limit: int = Field(default=10, ge=1, le=50)


class SimulatedIngestRequest(BaseModel):
    batch_size: int = Field(default=20, ge=1, le=500)
    include_noise: bool = True


class RunRequest(BaseModel):
    use_llm: bool = False


class FeedbackRequest(BaseModel):
    label: str = Field(min_length=2, max_length=50)
    notes: str = Field(min_length=2, max_length=5000)



class EnqueueRunJobRequest(BaseModel):
    use_llm: bool = False
    priority: int = Field(default=60, ge=1, le=100)


class EnqueueSimulatedJobRequest(BaseModel):
    batch_size: int = Field(default=20, ge=1, le=500)
    include_noise: bool = True
    priority: int = Field(default=50, ge=1, le=100)


class CreateScheduleRequest(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    job_type: str = Field(pattern="^(ingest_simulated|run_pipeline)$")
    payload: dict[str, Any]
    interval_seconds: int = Field(default=300, ge=10, le=86_400)
    priority: int = Field(default=50, ge=1, le=100)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="STS Situation Monitor", version="0.5.0", lifespan=lifespan)
pipeline = SignalPipeline()
llm_client = LocalLLMClient(
    base_url=settings.local_llm_url,
    model=settings.local_llm_model,
    timeout_s=settings.local_llm_timeout_s,
    max_retries=settings.local_llm_max_retries,
)


def _build_report_text(topic: str, result_summary: str, confidence: float, disputed_claims: list[str]) -> str:
    disputed_line = "\n".join(f"- {item}" for item in disputed_claims[:10]) or "- none"
    return (
        f"Topic: {topic}\n"
        f"Pipeline summary: {result_summary}\n"
        f"Confidence: {confidence}\n"
        f"Disputed claim clusters:\n{disputed_line}\n"
        "Output format: likely true / disputed / unknown / monitor-next"
    )


def _record_ingestion_run(
    session: Session,
    investigation_id: str,
    connector: str,
    ingested_count: int,
    failed_count: int,
    status: str,
    detail: dict[str, Any],
) -> None:
    run = IngestionRunORM(
        investigation_id=investigation_id,
        connector=connector,
        started_at=datetime.now(UTC),
        ingested_count=ingested_count,
        failed_count=failed_count,
        status=status,
        detail_json=json.dumps(detail),
    )
    session.add(run)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/system/preflight")
def preflight(session: Session = Depends(get_session)) -> dict[str, Any]:
    db_ok = True
    db_detail = "ok"
    try:
        session.execute(select(func.count(InvestigationORM.id)))
    except Exception as exc:
        db_ok = False
        db_detail = str(exc)

    llm_health = llm_client.health()
    db_path = settings.database_url.removeprefix("sqlite:///") if settings.database_url.startswith("sqlite") else None
    workspace_root = Path(settings.workspace_root).resolve()
    filesystem = {
        "database_path_exists": Path(db_path).exists() if db_path else None,
        "cwd": str(Path.cwd()),
        "workspace_root": str(workspace_root),
        "workspace_root_exists": workspace_root.exists(),
    }

    return {
        "database": {"ok": db_ok, "detail": db_detail, "url": settings.database_url},
        "llm": {
            "ok": llm_health.reachable and llm_health.model_available,
            "reachable": llm_health.reachable,
            "model_available": llm_health.model_available,
            "detail": llm_health.detail,
            "base_url": settings.local_llm_url,
            "model": settings.local_llm_model,
            "max_retries": settings.local_llm_max_retries,
        },
        "filesystem": filesystem,
        "security": {
            "auth_enforced": settings.enforce_auth,
            "default_api_key_in_use": settings.auth_api_key == "change-me",
        },
    }


@app.post("/investigations", response_model=Investigation)
def create_investigation(
    payload: InvestigationCreate,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> Investigation:
    investigation = InvestigationORM(
        id=str(uuid4()),
        topic=payload.topic,
        seed_query=payload.seed_query,
        created_at=datetime.now(UTC),
    )
    session.add(investigation)
    session.commit()
    return Investigation.model_validate(investigation, from_attributes=True)


@app.get("/investigations", response_model=list[Investigation])
def list_investigations(_: None = Depends(require_api_key), session: Session = Depends(get_session)) -> list[Investigation]:
    investigations = session.scalars(select(InvestigationORM).order_by(InvestigationORM.created_at.desc())).all()
    return [Investigation.model_validate(item, from_attributes=True) for item in investigations]


@app.post("/investigations/{investigation_id}/ingest/rss")
def ingest_rss(
    investigation_id: str,
    payload: RSSIngestRequest,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    connector = RSSConnector(
        feed_urls=payload.feed_urls,
        per_feed_limit=payload.per_feed_limit,
        timeout_s=settings.rss_timeout_s,
        max_retries=settings.rss_max_retries,
    )
    result = connector.collect(query=payload.query or investigation.seed_query or investigation.topic)

    for item in result.observations:
        session.add(
            ObservationORM(
                investigation_id=investigation_id,
                source=item.source,
                claim=item.claim,
                url=item.url,
                captured_at=item.captured_at,
                reliability_hint=item.reliability_hint,
            )
        )

    failed = result.metadata.get("failed_feeds", [])
    status = "partial" if failed else "success"
    _record_ingestion_run(
        session=session,
        investigation_id=investigation_id,
        connector="rss",
        ingested_count=len(result.observations),
        failed_count=len(failed),
        status=status,
        detail=result.metadata,
    )

    session.commit()
    stored_count = session.scalar(
        select(func.count(ObservationORM.id)).where(ObservationORM.investigation_id == investigation_id)
    )

    return {
        "investigation_id": investigation_id,
        "connector": result.connector,
        "ingested_count": len(result.observations),
        "stored_count": stored_count or 0,
        "failed_feeds": failed,
    }


@app.post("/investigations/{investigation_id}/ingest/simulated")
def ingest_simulated(
    investigation_id: str,
    payload: SimulatedIngestRequest,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    generated = generate_simulated_observations(
        topic=investigation.topic,
        batch_size=payload.batch_size,
        include_noise=payload.include_noise,
    )
    for item in generated:
        session.add(
            ObservationORM(
                investigation_id=investigation_id,
                source=item.source,
                claim=item.claim,
                url=item.url,
                captured_at=item.captured_at,
                reliability_hint=item.reliability_hint,
            )
        )

    _record_ingestion_run(
        session=session,
        investigation_id=investigation_id,
        connector="simulated",
        ingested_count=len(generated),
        failed_count=0,
        status="success",
        detail={"include_noise": payload.include_noise, "batch_size": payload.batch_size},
    )
    session.commit()

    return {
        "investigation_id": investigation_id,
        "connector": "simulated",
        "ingested_count": len(generated),
    }


@app.get("/investigations/{investigation_id}/ingestion-runs")
def list_ingestion_runs(
    investigation_id: str,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    runs = session.scalars(
        select(IngestionRunORM)
        .where(IngestionRunORM.investigation_id == investigation_id)
        .order_by(IngestionRunORM.started_at.desc())
        .limit(50)
    ).all()

    return [
        {
            "id": run.id,
            "connector": run.connector,
            "started_at": run.started_at.isoformat(),
            "ingested_count": run.ingested_count,
            "failed_count": run.failed_count,
            "status": run.status,
            "detail": json.loads(run.detail_json),
        }
        for run in runs
    ]


@app.get("/investigations/{investigation_id}/observations")
def list_observations(
    investigation_id: str,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    observations = session.scalars(
        select(ObservationORM)
        .where(ObservationORM.investigation_id == investigation_id)
        .order_by(ObservationORM.captured_at.desc())
    ).all()

    return [
        {
            "id": item.id,
            "source": item.source,
            "claim": item.claim,
            "url": item.url,
            "captured_at": item.captured_at.isoformat(),
            "reliability_hint": item.reliability_hint,
        }
        for item in observations
    ]


@app.post("/investigations/{investigation_id}/run")
def run_pipeline(
    investigation_id: str,
    payload: RunRequest | None = None,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    db_observations = session.scalars(
        select(ObservationORM).where(ObservationORM.investigation_id == investigation_id)
    ).all()
    if not db_observations:
        raise HTTPException(status_code=400, detail="No observations available. Ingest data before running pipeline.")

    observations = [
        Observation(
            source=item.source,
            claim=item.claim,
            url=item.url,
            captured_at=item.captured_at,
            reliability_hint=item.reliability_hint,
        )
        for item in db_observations
    ]

    result = pipeline.run(observations, topic=investigation.topic)
    accepted = [asdict(item) for item in result.accepted]
    dropped = [asdict(item) for item in result.dropped]

    llm_summary: str | None = None
    should_use_llm = bool(payload and payload.use_llm)
    llm_fallback_used = False
    if should_use_llm:
        prompt = _build_report_text(investigation.topic, result.summary, result.confidence, result.disputed_claims)
        try:
            llm_summary = llm_client.summarize(prompt)
        except Exception as exc:
            llm_fallback_used = True
            llm_summary = f"LLM unavailable, fallback to deterministic summary: {exc}"

    report = ReportORM(
        investigation_id=investigation_id,
        generated_at=datetime.now(UTC),
        summary=llm_summary or result.summary,
        confidence=result.confidence,
        accepted_json=json.dumps(accepted, default=str),
        dropped_json=json.dumps(dropped, default=str),
    )
    session.add(report)
    session.commit()

    return {
        "investigation_id": investigation_id,
        "generated_at": report.generated_at.isoformat(),
        "summary": report.summary,
        "confidence": report.confidence,
        "accepted": accepted,
        "dropped": dropped,
        "disputed_claims": result.disputed_claims,
        "deduplicated_count": len(result.deduplicated),
        "llm_fallback_used": llm_fallback_used,
    }


@app.post("/investigations/{investigation_id}/feedback")
def submit_feedback(
    investigation_id: str,
    payload: FeedbackRequest,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    feedback = FeedbackORM(
        investigation_id=investigation_id,
        label=payload.label.strip().lower(),
        notes=payload.notes,
        created_at=datetime.now(UTC),
    )
    session.add(feedback)
    session.commit()
    return {"status": "saved", "feedback_id": feedback.id}


@app.get("/investigations/{investigation_id}/memory")
def investigation_memory(
    investigation_id: str,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    feedback_entries = session.scalars(
        select(FeedbackORM).where(FeedbackORM.investigation_id == investigation_id).order_by(FeedbackORM.created_at.desc())
    ).all()
    by_label: dict[str, int] = {}
    for item in feedback_entries:
        by_label[item.label] = by_label.get(item.label, 0) + 1

    return {
        "investigation_id": investigation_id,
        "feedback_total": len(feedback_entries),
        "labels": by_label,
        "latest_notes": [item.notes for item in feedback_entries[:10]],
    }


@app.get("/reports/{investigation_id}")
def get_report(
    investigation_id: str,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    report = session.scalars(
        select(ReportORM)
        .where(ReportORM.investigation_id == investigation_id)
        .order_by(ReportORM.generated_at.desc())
        .limit(1)
    ).first()
    if not report:
        raise HTTPException(status_code=404, detail="No report available")

    return {
        "investigation_id": investigation_id,
        "generated_at": report.generated_at.isoformat(),
        "summary": report.summary,
        "confidence": report.confidence,
        "accepted": json.loads(report.accepted_json),
        "dropped": json.loads(report.dropped_json),
    }


@app.post("/jobs/enqueue/ingest-simulated/{investigation_id}")
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
        payload={
            "investigation_id": investigation_id,
            "batch_size": payload.batch_size,
            "include_noise": payload.include_noise,
        },
        priority=payload.priority,
    )
    return {"job_id": job.id, "status": job.status, "job_type": job.job_type}


@app.post("/jobs/enqueue/run/{investigation_id}")
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
    )
    return {"job_id": job.id, "status": job.status, "job_type": job.job_type}


@app.post("/schedules")
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


@app.post("/schedules/tick")
def scheduler_tick(
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    enqueued = tick_schedules(session)
    return {"enqueued": enqueued}


@app.get("/schedules")
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


@app.post("/jobs/process-next")
def process_next(
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    result = process_next_job(session=session, pipeline=pipeline, llm_client=llm_client)
    if result is None:
        return {"status": "idle", "message": "No pending jobs"}
    return result


@app.get("/jobs")
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
            "last_error": row.last_error,
            "run_at": row.run_at.isoformat(),
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }
        for row in rows
    ]


@app.get("/dashboard/summary")
def dashboard_summary(_: None = Depends(require_api_key), session: Session = Depends(get_session)) -> dict[str, Any]:
    investigation_count = session.scalar(select(func.count(InvestigationORM.id))) or 0
    observation_count = session.scalar(select(func.count(ObservationORM.id))) or 0
    report_count = session.scalar(select(func.count(ReportORM.id))) or 0
    feedback_count = session.scalar(select(func.count(FeedbackORM.id))) or 0
    ingestion_runs = session.scalar(select(func.count(IngestionRunORM.id))) or 0
    jobs_pending = session.scalar(select(func.count(JobORM.id)).where(JobORM.status == "pending")) or 0
    jobs_failed = session.scalar(select(func.count(JobORM.id)).where(JobORM.status == "failed")) or 0
    schedules_active = session.scalar(select(func.count(JobScheduleORM.id)).where(JobScheduleORM.active.is_(True))) or 0

    latest = session.scalars(select(ReportORM).order_by(ReportORM.generated_at.desc()).limit(5)).all()
    latest_reports = [
        {
            "investigation_id": item.investigation_id,
            "generated_at": item.generated_at.isoformat(),
            "confidence": item.confidence,
            "summary": item.summary,
        }
        for item in latest
    ]

    return {
        "investigations": investigation_count,
        "observations": observation_count,
        "reports": report_count,
        "feedback": feedback_count,
        "ingestion_runs": ingestion_runs,
        "jobs_pending": jobs_pending,
        "jobs_failed": jobs_failed,
        "schedules_active": schedules_active,
        "latest_reports": latest_reports,
    }
