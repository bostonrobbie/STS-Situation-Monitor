"""Alert routes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from sts_monitor.alert_engine import evaluate_rules, get_default_rules
from sts_monitor.database import get_session
from sts_monitor.helpers import evaluate_alert_rules, record_audit
from sts_monitor.models import (
    AlertEventORM,
    AlertRuleORM,
    InvestigationORM,
    ObservationORM,
)
from sts_monitor.schemas import AlertRuleCreateRequest
from sts_monitor.security import AuthContext, require_analyst, require_api_key

router = APIRouter()


class AlertRuleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    rule_type: str = Field(pattern="^(volume_spike|contradiction_threshold|entity_velocity|silence|narrative_shift)$")
    threshold: float = Field(default=5.0, ge=1)
    window_minutes: int = Field(default=60, ge=5, le=1440)
    cooldown_seconds: int = Field(default=600, ge=60)
    severity: str = Field(default="warning", pattern="^(info|warning|critical)$")
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.post("/alerts/rules")
def create_alert_rule(
    payload: AlertRuleCreateRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(InvestigationORM, payload.investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    rule = AlertRuleORM(
        investigation_id=payload.investigation_id,
        name=payload.name,
        min_observations=payload.min_observations,
        min_disputed_claims=payload.min_disputed_claims,
        cooldown_seconds=payload.cooldown_seconds,
        active=payload.active,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(rule)
    record_audit(session, actor=auth, action="alert_rule.create", resource_type="investigation", resource_id=payload.investigation_id, detail={"rule": payload.name})
    session.commit()
    return {"id": rule.id, "name": rule.name, "active": rule.active}


@router.get("/alerts/rules")
def list_alert_rules(
    investigation_id: str | None = None,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    query = select(AlertRuleORM).order_by(AlertRuleORM.created_at.desc())
    if investigation_id:
        query = query.where(AlertRuleORM.investigation_id == investigation_id)
    rows = session.scalars(query).all()
    return [
        {
            "id": row.id,
            "investigation_id": row.investigation_id,
            "name": row.name,
            "min_observations": row.min_observations,
            "min_disputed_claims": row.min_disputed_claims,
            "cooldown_seconds": row.cooldown_seconds,
            "active": row.active,
            "last_triggered_at": row.last_triggered_at.isoformat() if row.last_triggered_at else None,
        }
        for row in rows
    ]


@router.post("/alerts/evaluate/{investigation_id}")
def evaluate_alerts(
    investigation_id: str,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    emitted = evaluate_alert_rules(session=session, investigation_id=investigation_id)
    record_audit(session, actor=auth, action="alerts.evaluate", resource_type="investigation", resource_id=investigation_id, detail={"triggered": len(emitted)})
    session.commit()
    return {"investigation_id": investigation_id, "triggered": len(emitted), "events": emitted}


@router.get("/alerts/events/{investigation_id}")
def list_alert_events(
    investigation_id: str,
    limit: int = 50,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(AlertEventORM)
        .where(AlertEventORM.investigation_id == investigation_id)
        .order_by(AlertEventORM.triggered_at.desc())
        .limit(max(1, min(limit, 200)))
    ).all()
    return [
        {
            "id": row.id,
            "rule_id": row.rule_id,
            "investigation_id": row.investigation_id,
            "triggered_at": row.triggered_at.isoformat(),
            "severity": row.severity,
            "message": row.message,
            "detail": json.loads(row.detail_json),
        }
        for row in rows
    ]


@router.post("/investigations/{investigation_id}/alert-rules")
def create_investigation_alert_rule(
    investigation_id: str,
    payload: AlertRuleCreate,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Create an alert rule for an investigation."""
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    rule = AlertRuleORM(
        investigation_id=investigation_id,
        name=payload.name,
        min_observations=int(payload.threshold),
        min_disputed_claims=payload.metadata.get("min_disputed", 1),
        cooldown_seconds=payload.cooldown_seconds,
        active=True,
    )
    session.add(rule)
    session.commit()

    return {
        "id": rule.id,
        "name": rule.name,
        "rule_type": payload.rule_type,
        "threshold": payload.threshold,
        "window_minutes": payload.window_minutes,
        "severity": payload.severity,
        "active": rule.active,
    }


@router.get("/investigations/{investigation_id}/alert-rules")
def list_investigation_alert_rules(
    investigation_id: str,
    _: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    """List alert rules for an investigation."""
    rules = session.query(AlertRuleORM).filter_by(investigation_id=investigation_id).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "min_observations": r.min_observations,
            "min_disputed_claims": r.min_disputed_claims,
            "cooldown_seconds": r.cooldown_seconds,
            "active": r.active,
            "last_triggered_at": r.last_triggered_at.isoformat() if r.last_triggered_at else None,
        }
        for r in rules
    ]


@router.post("/investigations/{investigation_id}/evaluate-alerts")
def evaluate_investigation_alerts(
    investigation_id: str,
    _: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Evaluate all alert rules against current investigation data."""
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    observations = session.query(ObservationORM).filter_by(
        investigation_id=investigation_id
    ).order_by(ObservationORM.captured_at.desc()).limit(1000).all()

    obs_dicts = [
        {"id": o.id, "claim": o.claim, "source": o.source,
         "captured_at": o.captured_at, "reliability_hint": o.reliability_hint,
         "investigation_id": o.investigation_id}
        for o in observations
    ]

    rules = get_default_rules(investigation_id=investigation_id)
    events = evaluate_rules(rules, obs_dicts)

    # Store events
    for evt in events:
        session.add(AlertEventORM(
            investigation_id=investigation_id,
            severity=evt.severity,
            message=evt.message[:500],
            detail_json=json.dumps(evt.details, default=str),
        ))
    if events:
        session.commit()

    return {
        "alerts_fired": len(events),
        "events": [e.to_dict() for e in events],
    }
