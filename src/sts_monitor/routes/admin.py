"""Admin, audit, auth, plugins, notifications, autopilot, templates, knowledge graph, and LLM routes."""
from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from sts_monitor.autopilot import get_state as get_autopilot_state, start_autopilot, stop_autopilot
from sts_monitor.config import settings
from sts_monitor.database import get_session
from sts_monitor.investigation_templates import apply_template, get_template, list_templates
from sts_monitor.knowledge_graph import build_knowledge_graph
from sts_monitor.models import APIKeyORM, AuditLogORM, InvestigationORM
from sts_monitor.multi_llm import get_router as get_llm_router
from sts_monitor.schemas import APIKeyCreateRequest
from sts_monitor.security import AuthContext, hash_api_key, now_utc, require_admin, require_analyst, require_api_key

router = APIRouter()


# ── Admin API Keys ──────────────────────────────────────────────────────


@router.post("/admin/api-keys")
def create_api_key(
    payload: APIKeyCreateRequest,
    _: AuthContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    existing = session.scalars(select(APIKeyORM).where(APIKeyORM.label == payload.label)).first()
    if existing:
        raise HTTPException(status_code=409, detail="API key label already exists")

    raw_key = f"sts_{uuid4().hex}"
    row = APIKeyORM(
        label=payload.label,
        key_hash=hash_api_key(raw_key),
        role=payload.role,
        active=True,
        created_at=now_utc(),
    )
    session.add(row)
    session.commit()
    return {"id": row.id, "label": row.label, "role": row.role, "api_key": raw_key}


@router.get("/admin/api-keys")
def list_api_keys(
    _: AuthContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = session.scalars(select(APIKeyORM).order_by(APIKeyORM.created_at.desc())).all()
    return [
        {"id": row.id, "label": row.label, "role": row.role, "active": row.active, "created_at": row.created_at.isoformat()}
        for row in rows
    ]


@router.post("/admin/api-keys/{key_id}/revoke")
def revoke_api_key(
    key_id: int,
    _: AuthContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    row = session.get(APIKeyORM, key_id)
    if not row:
        raise HTTPException(status_code=404, detail="API key not found")
    row.active = False
    session.commit()
    return {"id": row.id, "active": row.active}


# ── Audit Logs ──────────────────────────────────────────────────────────


@router.get("/audit/logs")
def list_audit_logs(
    action: str | None = None,
    limit: int = 200,
    _: AuthContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    query = select(AuditLogORM)
    if action:
        query = query.where(AuditLogORM.action == action)
    rows = session.scalars(query.order_by(AuditLogORM.created_at.desc()).limit(max(1, min(limit, 1000)))).all()
    return [
        {
            "id": row.id,
            "actor_label": row.actor_label,
            "actor_role": row.actor_role,
            "action": row.action,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "detail": json.loads(row.detail_json),
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


# ── Auth (JWT) ──────────────────────────────────────────────────────────


class UserRegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=60)
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default="analyst", pattern="^(admin|analyst|viewer)$")


class UserLoginRequest(BaseModel):
    username: str
    password: str


@router.post("/auth/register")
def register_user(
    payload: UserRegisterRequest,
    auth: AuthContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    from sts_monitor.auth_jwt import hash_password
    label = f"user:{payload.username}"
    existing = session.scalars(select(APIKeyORM).where(APIKeyORM.label == label)).first()
    if existing:
        raise HTTPException(status_code=409, detail="Username already exists")

    user = APIKeyORM(
        label=label,
        key_hash=hash_password(payload.password),
        role=payload.role,
        active=True,
    )
    session.add(user)
    session.commit()
    return {"username": payload.username, "role": payload.role, "id": user.id}


@router.post("/auth/login")
def login_user(
    payload: UserLoginRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    from sts_monitor.auth_jwt import create_token, verify_password
    label = f"user:{payload.username}"
    user = session.scalars(
        select(APIKeyORM).where(APIKeyORM.label == label).where(APIKeyORM.active.is_(True))
    ).first()

    if not user or not verify_password(payload.password, user.key_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_token(user.id, payload.username, user.role)
    return {"token": token, "username": payload.username, "role": user.role}


@router.get("/auth/me")
def auth_me(
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    from sts_monitor.auth_jwt import decode_token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    token_data = decode_token(auth_header[7:])
    return {"user_id": token_data["sub"], "username": token_data["username"], "role": token_data["role"]}


# ── Plugins ─────────────────────────────────────────────────────────────


@router.get("/plugins")
def list_plugins(_: None = Depends(require_api_key)) -> dict[str, Any]:
    from sts_monitor.plugins import plugin_registry
    return {"plugins": plugin_registry.registered}


@router.post("/plugins/discover")
def discover_plugins(auth: AuthContext = Depends(require_admin)) -> dict[str, Any]:
    from sts_monitor.plugins import plugin_registry
    counts = plugin_registry.discover_all()
    return {"discovered": counts, "total_registered": len(plugin_registry.registered)}


# ── Notifications ───────────────────────────────────────────────────────


@router.post("/notifications/test")
def test_notification(
    auth: AuthContext = Depends(require_admin),
) -> dict[str, Any]:
    from sts_monitor.notifications import AlertNotification, notify_all
    notification = AlertNotification(
        title="STS Monitor Test Notification",
        message="This is a test notification from the STS Situation Monitor.",
        severity="info",
    )
    results = notify_all(notification)
    return {"channels": results, "configured": len(results)}


# ── Knowledge Graph ─────────────────────────────────────────────────────


class KnowledgeGraphRequest(BaseModel):
    investigation_ids: list[str] | None = Field(default=None)
    include_observations: bool = Field(default=False)
    max_entities: int = Field(default=200, ge=10, le=1000)
    min_entity_mentions: int = Field(default=2, ge=1, le=50)
    max_stories: int = Field(default=100, ge=10, le=500)


@router.post("/knowledge-graph")
def get_knowledge_graph(
    payload: KnowledgeGraphRequest,
    _: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    kg = build_knowledge_graph(
        session=session,
        investigation_ids=payload.investigation_ids,
        include_observations=payload.include_observations,
        max_entities=payload.max_entities,
        min_entity_mentions=payload.min_entity_mentions,
        max_stories=payload.max_stories,
    )
    return kg.to_dict()


@router.get("/knowledge-graph/summary")
def knowledge_graph_summary(
    _: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    kg = build_knowledge_graph(session=session, max_entities=500, max_stories=200)
    return {
        "node_count": kg.node_count,
        "edge_count": kg.edge_count,
        "stats": kg.stats,
    }


# ── Autopilot ───────────────────────────────────────────────────────────


@router.get("/autopilot/status")
def autopilot_status(_: AuthContext = Depends(require_api_key)) -> dict[str, Any]:
    return get_autopilot_state().to_dict()


@router.post("/autopilot/start")
def autopilot_start(_: AuthContext = Depends(require_api_key)) -> dict[str, Any]:
    return start_autopilot()


@router.post("/autopilot/stop")
def autopilot_stop(_: AuthContext = Depends(require_api_key)) -> dict[str, Any]:
    return stop_autopilot()


# ── Templates ───────────────────────────────────────────────────────────


@router.get("/templates")
def list_investigation_templates(
    category: str | None = None,
    _: AuthContext = Depends(require_api_key),
) -> list[dict[str, Any]]:
    return list_templates(category=category)


@router.get("/templates/{template_key}")
def get_investigation_template(
    template_key: str,
    _: AuthContext = Depends(require_api_key),
) -> dict[str, Any]:
    tmpl = get_template(template_key)
    if not tmpl:
        raise HTTPException(status_code=404, detail=f"Template not found: {template_key}")
    return apply_template(template_key)


class TemplateApplyRequest(BaseModel):
    custom_topic: str | None = None


@router.post("/templates/{template_key}/apply")
def apply_investigation_template(
    template_key: str,
    payload: TemplateApplyRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    config = apply_template(template_key, custom_topic=payload.custom_topic)
    if "error" in config:
        raise HTTPException(status_code=404, detail=config["error"])

    inv = InvestigationORM(
        id=str(uuid4()),
        topic=config["topic"],
        seed_query=config.get("seed_query"),
        priority=config.get("priority", 50),
        status=config.get("status", "active"),
        owner=auth.label if auth else None,
    )
    session.add(inv)
    session.commit()

    return {
        "investigation_id": inv.id,
        "template": config["template_name"],
        "topic": inv.topic,
        "status": inv.status,
        "config": config.get("config", {}),
    }


# ── LLM Router ──────────────────────────────────────────────────────────


@router.get("/llm/status")
def llm_router_status(
    _: AuthContext = Depends(require_api_key),
) -> dict[str, Any]:
    llm_router = get_llm_router()
    return llm_router.get_status()


@router.post("/llm/scan")
def llm_scan_models(
    _: AuthContext = Depends(require_api_key),
) -> dict[str, Any]:
    llm_router = get_llm_router()
    models = llm_router.scan_models()
    return {"models_found": len(models), "models": [m.to_dict() for m in models]}
