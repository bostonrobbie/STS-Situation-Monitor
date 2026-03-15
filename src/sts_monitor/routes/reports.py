"""Report routes."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from sts_monitor.config import settings
from sts_monitor.database import get_session
from sts_monitor.helpers import compute_report_lineage_validation
from sts_monitor.llm import LocalLLMClient
from sts_monitor.models import (
    ConvergenceZoneORM,
    EntityMentionORM,
    InvestigationORM,
    ObservationORM,
    ReportORM,
    StoryORM,
)
from sts_monitor.pipeline import Observation, SignalPipeline
from sts_monitor.security import AuthContext, require_api_key

router = APIRouter()


class GenerateReportRequest(BaseModel):
    investigation_id: str
    use_llm: bool = True


@router.get("/reports/{investigation_id}")
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


@router.post("/reports/generate")
def generate_intelligence_report(
    body: GenerateReportRequest,
    session: Session = Depends(get_session),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Generate a full structured intelligence report for an investigation."""
    from sts_monitor.report_generator import ReportGenerator
    from sts_monitor.entities import extract_entities

    investigation = session.get(InvestigationORM, body.investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    # Gather observations
    db_obs = session.scalars(
        select(ObservationORM).where(ObservationORM.investigation_id == body.investigation_id)
    ).all()
    if not db_obs:
        raise HTTPException(status_code=400, detail="No observations to report on")

    observations = [
        Observation(
            source=o.source, claim=o.claim, url=o.url,
            captured_at=o.captured_at, reliability_hint=o.reliability_hint,
        )
        for o in db_obs
    ]

    # Run pipeline
    pipeline = SignalPipeline()
    pipeline_result = pipeline.run(observations, topic=investigation.topic)

    # Gather entities
    entity_rows = session.scalars(
        select(EntityMentionORM).where(EntityMentionORM.investigation_id == body.investigation_id)
    ).all()
    entities = [
        {"entity_text": e.entity_text, "entity_type": e.entity_type, "confidence": e.confidence}
        for e in entity_rows
    ]
    # If no stored entities, extract on the fly
    if not entities:
        all_text = " ".join(o.claim for o in pipeline_result.accepted[:50])
        extracted = extract_entities(all_text)
        entities = [{"entity_text": e.text, "entity_type": e.entity_type, "confidence": e.confidence} for e in extracted]

    # Gather stories
    story_rows = session.scalars(
        select(StoryORM).where(StoryORM.investigation_id == body.investigation_id)
    ).all()
    stories = [
        {"headline": s.headline, "observation_count": s.observation_count,
         "source_count": s.source_count, "avg_reliability": s.avg_reliability}
        for s in story_rows
    ]

    # Gather convergence zones
    zone_rows = session.scalars(
        select(ConvergenceZoneORM).where(ConvergenceZoneORM.investigation_id == body.investigation_id)
    ).all()
    zones = [
        {"center_lat": z.center_lat, "center_lon": z.center_lon,
         "signal_count": z.signal_count, "severity": z.severity,
         "signal_types": json.loads(z.signal_types_json)}
        for z in zone_rows
    ]

    # Generate report
    llm_client = None
    if body.use_llm:
        llm_client = LocalLLMClient(
            base_url=settings.local_llm_url,
            model=settings.local_llm_model,
            timeout_s=settings.agent_llm_timeout_s,
            max_retries=settings.local_llm_max_retries,
        )

    generator = ReportGenerator(llm_client=llm_client)
    report = generator.generate(
        investigation_id=body.investigation_id,
        topic=investigation.topic,
        pipeline_result=pipeline_result,
        entities=entities,
        stories=stories,
        convergence_zones=zones,
    )

    # Persist as a ReportORM
    report_orm = ReportORM(
        investigation_id=body.investigation_id,
        generated_at=report.generated_at,
        summary=report.executive_summary,
        confidence=pipeline_result.confidence,
        accepted_json=json.dumps(report.to_dict()),
        dropped_json=json.dumps({"generation_method": report.generation_method}),
    )
    session.add(report_orm)
    session.commit()

    return {
        "report_id": report_orm.id,
        **report.to_dict(),
    }


@router.post("/reports/generate/markdown")
def generate_report_markdown(
    body: GenerateReportRequest,
    session: Session = Depends(get_session),
    _: None = Depends(require_api_key),
) -> Response:
    """Generate an intelligence report and return as markdown."""
    from sts_monitor.report_generator import ReportGenerator
    from sts_monitor.entities import extract_entities

    investigation = session.get(InvestigationORM, body.investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    db_obs = session.scalars(
        select(ObservationORM).where(ObservationORM.investigation_id == body.investigation_id)
    ).all()
    if not db_obs:
        raise HTTPException(status_code=400, detail="No observations to report on")

    observations = [
        Observation(
            source=o.source, claim=o.claim, url=o.url,
            captured_at=o.captured_at, reliability_hint=o.reliability_hint,
        )
        for o in db_obs
    ]

    pipeline = SignalPipeline()
    pipeline_result = pipeline.run(observations, topic=investigation.topic)

    # Quick entity extraction
    all_text = " ".join(o.claim for o in pipeline_result.accepted[:50])
    extracted = extract_entities(all_text)
    entities = [{"entity_text": e.text, "entity_type": e.entity_type, "confidence": e.confidence} for e in extracted]

    llm_client = None
    if body.use_llm:
        llm_client = LocalLLMClient(
            base_url=settings.local_llm_url,
            model=settings.local_llm_model,
            timeout_s=settings.agent_llm_timeout_s,
            max_retries=settings.local_llm_max_retries,
        )

    generator = ReportGenerator(llm_client=llm_client)
    report = generator.generate(
        investigation_id=body.investigation_id,
        topic=investigation.topic,
        pipeline_result=pipeline_result,
        entities=entities,
    )

    return Response(content=report.to_markdown(), media_type="text/markdown")


@router.get("/reports/{investigation_id}/validation")
def validate_latest_report_lineage(
    investigation_id: str,
    _: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    report = session.scalars(
        select(ReportORM).where(ReportORM.investigation_id == investigation_id).order_by(ReportORM.generated_at.desc()).limit(1)
    ).first()
    if not report:
        raise HTTPException(status_code=404, detail="No report available")
    validation = compute_report_lineage_validation(session, report.id)
    return {"investigation_id": investigation_id, "report_id": report.id, "validation": validation}
