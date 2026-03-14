"""Dashboard routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sts_monitor.database import get_session
from sts_monitor.models import (
    FeedbackORM,
    IngestionRunORM,
    InvestigationORM,
    ObservationORM,
    ReportORM,
)
from sts_monitor.security import require_api_key

router = APIRouter()


@router.get("/dashboard/summary")
def dashboard_summary(_: None = Depends(require_api_key), session: Session = Depends(get_session)) -> dict[str, Any]:
    investigation_count = session.scalar(select(func.count(InvestigationORM.id))) or 0
    observation_count = session.scalar(select(func.count(ObservationORM.id))) or 0
    report_count = session.scalar(select(func.count(ReportORM.id))) or 0
    feedback_count = session.scalar(select(func.count(FeedbackORM.id))) or 0
    ingestion_runs = session.scalar(select(func.count(IngestionRunORM.id))) or 0

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
        "latest_reports": latest_reports,
    }
