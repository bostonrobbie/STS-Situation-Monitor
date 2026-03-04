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
from sts_monitor.models import FeedbackORM, InvestigationORM, ObservationORM, ReportORM
from sts_monitor.pipeline import Observation, SignalPipeline
from sts_monitor.simulation import generate_simulated_observations
from dataclasses import asdict
from datetime import datetime, UTC
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from sts_monitor.pipeline import Observation, SignalPipeline


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


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="STS Situation Monitor", version="0.4.0", lifespan=lifespan)
pipeline = SignalPipeline()
llm_client = LocalLLMClient(
    base_url=settings.local_llm_url,
    model=settings.local_llm_model,
    timeout_s=settings.local_llm_timeout_s,
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
app = FastAPI(title="STS Situation Monitor", version="0.1.0")

pipeline = SignalPipeline()
investigations: dict[str, Investigation] = {}
reports: dict[str, dict[str, Any]] = {}


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
        },
        "filesystem": filesystem,
    }


@app.post("/investigations", response_model=Investigation)
def create_investigation(payload: InvestigationCreate, session: Session = Depends(get_session)) -> Investigation:
    investigation = InvestigationORM(
@app.post("/investigations", response_model=Investigation)
def create_investigation(payload: InvestigationCreate) -> Investigation:
    investigation = Investigation(
        id=str(uuid4()),
        topic=payload.topic,
        seed_query=payload.seed_query,
        created_at=datetime.now(UTC),
    )
    session.add(investigation)
    session.commit()
    return Investigation.model_validate(investigation, from_attributes=True)


@app.get("/investigations", response_model=list[Investigation])
def list_investigations(session: Session = Depends(get_session)) -> list[Investigation]:
    investigations = session.scalars(select(InvestigationORM).order_by(InvestigationORM.created_at.desc())).all()
    return [Investigation.model_validate(item, from_attributes=True) for item in investigations]


@app.post("/investigations/{investigation_id}/ingest/rss")
def ingest_rss(
    investigation_id: str,
    payload: RSSIngestRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    connector = RSSConnector(feed_urls=payload.feed_urls, per_feed_limit=payload.per_feed_limit)
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

    session.commit()
    stored_count = session.scalar(
        select(func.count(ObservationORM.id)).where(ObservationORM.investigation_id == investigation_id)
    )

    return {
        "investigation_id": investigation_id,
        "connector": result.connector,
        "ingested_count": len(result.observations),
        "stored_count": stored_count or 0,
    }


@app.post("/investigations/{investigation_id}/ingest/simulated")
def ingest_simulated(
    investigation_id: str,
    payload: SimulatedIngestRequest,
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
    session.commit()

    return {
        "investigation_id": investigation_id,
        "connector": "simulated",
        "ingested_count": len(generated),
    }


@app.get("/investigations/{investigation_id}/observations")
def list_observations(investigation_id: str, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
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
    if should_use_llm:
        prompt = _build_report_text(investigation.topic, result.summary, result.confidence, result.disputed_claims)
        try:
            llm_summary = llm_client.summarize(prompt)
        except Exception as exc:
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
    }


@app.post("/investigations/{investigation_id}/feedback")
def submit_feedback(
    investigation_id: str,
    payload: FeedbackRequest,
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
def investigation_memory(investigation_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
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
def get_report(investigation_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
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


@app.get("/dashboard/summary")
def dashboard_summary(session: Session = Depends(get_session)) -> dict[str, Any]:
    investigation_count = session.scalar(select(func.count(InvestigationORM.id))) or 0
    observation_count = session.scalar(select(func.count(ObservationORM.id))) or 0
    report_count = session.scalar(select(func.count(ReportORM.id))) or 0
    feedback_count = session.scalar(select(func.count(FeedbackORM.id))) or 0

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
        "latest_reports": latest_reports,
    }
    investigations[investigation.id] = investigation
    return investigation


@app.get("/investigations", response_model=list[Investigation])
def list_investigations() -> list[Investigation]:
    return list(investigations.values())


@app.post("/investigations/{investigation_id}/run")
def run_pipeline(investigation_id: str) -> dict[str, Any]:
    investigation = investigations.get(investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    sample_observations = [
        Observation(
            source="wire_service",
            claim=f"Breaking update tied to {investigation.topic}",
            url="https://example.com/wire-story",
            reliability_hint=0.82,
        ),
        Observation(
            source="anonymous_forum_post",
            claim=f"Unverified rumor related to {investigation.topic}",
            url="https://example.com/unverified-thread",
            reliability_hint=0.21,
        ),
        Observation(
            source="local_reporter",
            claim=f"Eyewitness media from scene on {investigation.topic}",
            url="https://example.com/local-reporter",
            reliability_hint=0.74,
        ),
    ]

    result = pipeline.run(sample_observations, topic=investigation.topic)
    report = {
        "investigation_id": investigation_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": result.summary,
        "confidence": result.confidence,
        "accepted": [asdict(item) for item in result.accepted],
        "dropped": [asdict(item) for item in result.dropped],
    }
    reports[investigation_id] = report
    return report


@app.get("/reports/{investigation_id}")
def get_report(investigation_id: str) -> dict[str, Any]:
    report = reports.get(investigation_id)
    if not report:
        raise HTTPException(status_code=404, detail="No report available")
    return report
