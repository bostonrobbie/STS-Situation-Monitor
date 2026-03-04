from __future__ import annotations

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


app = FastAPI(title="STS Situation Monitor", version="0.1.0")

pipeline = SignalPipeline()
investigations: dict[str, Investigation] = {}
reports: dict[str, dict[str, Any]] = {}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/investigations", response_model=Investigation)
def create_investigation(payload: InvestigationCreate) -> Investigation:
    investigation = Investigation(
        id=str(uuid4()),
        topic=payload.topic,
        seed_query=payload.seed_query,
        created_at=datetime.now(UTC),
    )
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
