"""Export and WebSocket routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from fastapi.responses import Response
from sqlalchemy.orm import Session

from sts_monitor.database import get_session
from sts_monitor.export import export_claims_csv, export_observations_csv, export_report_markdown, export_report_pdf_bytes
from sts_monitor.models import InvestigationORM
from sts_monitor.security import require_api_key
from sts_monitor.websocket import websocket_endpoint as _ws_handler, ws_manager

router = APIRouter()


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await _ws_handler(websocket)


@router.get("/ws/status")
def ws_status(_: None = Depends(require_api_key)) -> dict[str, Any]:
    return {
        "active_connections": ws_manager.active_count,
        "connections": ws_manager.connections_info,
    }


@router.get("/export/{investigation_id}/observations.csv")
def export_observations(
    investigation_id: str,
    source: str | None = None,
    min_reliability: float | None = None,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> Response:
    inv = session.get(InvestigationORM, investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation not found")
    filters = {}
    if source:
        filters["source"] = source
    if min_reliability is not None:
        filters["min_reliability"] = min_reliability
    csv_data = export_observations_csv(session, investigation_id, filters or None)
    return Response(content=csv_data, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="observations_{investigation_id}.csv"'
    })


@router.get("/export/{investigation_id}/claims.csv")
def export_claims(
    investigation_id: str,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> Response:
    inv = session.get(InvestigationORM, investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation not found")
    csv_data = export_claims_csv(session, investigation_id)
    return Response(content=csv_data, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="claims_{investigation_id}.csv"'
    })


@router.get("/export/{investigation_id}/report.md")
def export_report_md(
    investigation_id: str,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> Response:
    inv = session.get(InvestigationORM, investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation not found")
    md = export_report_markdown(session, investigation_id)
    if not md:
        raise HTTPException(status_code=404, detail="No report found")
    return Response(content=md, media_type="text/markdown", headers={
        "Content-Disposition": f'attachment; filename="report_{investigation_id}.md"'
    })


@router.get("/export/{investigation_id}/report.pdf")
def export_report_pdf(
    investigation_id: str,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> Response:
    inv = session.get(InvestigationORM, investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation not found")
    pdf = export_report_pdf_bytes(session, investigation_id)
    if not pdf:
        raise HTTPException(status_code=404, detail="No report found")
    return Response(content=pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="report_{investigation_id}.pdf"'
    })
