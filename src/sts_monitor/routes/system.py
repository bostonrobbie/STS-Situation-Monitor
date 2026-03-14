"""System, health, and privacy routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select, func

from sts_monitor.helpers import (
    connector_diagnostics,
    compute_readiness_score,
    workspace_health_snapshot,
    queue_health_snapshot,
)
from sts_monitor.deps import llm_client
from sts_monitor.config import settings
from sts_monitor.database import get_session
from sts_monitor.security import require_api_key
from sts_monitor.models import InvestigationORM
from sts_monitor.privacy import PrivacyConfig, get_privacy_status
from sts_monitor.online_tools import parse_csv_env

router = APIRouter()

cors_origins = parse_csv_env(settings.cors_origins) or ["*"]
trusted_hosts = parse_csv_env(settings.trusted_hosts)


@router.get("/")
def root_redirect():
    """Redirect root to dashboard."""
    return RedirectResponse(url="/static/globe.html")


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/system/preflight")
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
    workspace = workspace_health_snapshot(workspace_root)
    filesystem = {
        "database_path_exists": Path(db_path).exists() if db_path else None,
        "cwd": str(Path.cwd()),
        "workspace_root": workspace["workspace_root"],
        "workspace_root_exists": workspace["workspace_root_exists"],
    }

    llm_ok = llm_health.reachable and llm_health.model_available
    connectors = connector_diagnostics()
    queue = queue_health_snapshot(session)
    readiness = compute_readiness_score(
        db_ok=db_ok,
        llm_ok=llm_ok,
        workspace_ok=workspace["ok"],
        connectors_ok=connectors["ok"],
        queue_ok=queue["ok"],
    )

    return {
        "database": {"ok": db_ok, "detail": db_detail, "url": settings.database_url},
        "llm": {
            "ok": llm_ok,
            "reachable": llm_health.reachable,
            "model_available": llm_health.model_available,
            "detail": llm_health.detail,
            "latency_ms": getattr(llm_health, "latency_ms", None),
            "base_url": settings.local_llm_url,
            "model": settings.local_llm_model,
            "max_retries": settings.local_llm_max_retries,
        },
        "connectors": connectors,
        "filesystem": filesystem,
        "workspace": workspace,
        "queue": queue,
        "readiness": readiness,
        "security": {
            "auth_enforced": settings.enforce_auth,
            "default_api_key_in_use": settings.auth_api_key == "change-me",
        },
    }


@router.get("/system/online-tools")
def online_tools() -> dict[str, Any]:
    return {
        "public_base_url": settings.public_base_url,
        "cors_origins": cors_origins,
        "trusted_hosts": trusted_hosts or ["*"],
        "alert_webhook": {
            "configured": bool(settings.alert_webhook_url),
            "timeout_s": settings.alert_webhook_timeout_s,
        },
        "exposure_hints": [
            "Run API with host 0.0.0.0 behind a TLS reverse proxy",
            "Use HTTPS and a real DNS record for internet-facing deployments",
            "Set STS_TRUSTED_HOSTS and STS_CORS_ORIGINS to explicit domains",
        ],
    }


@router.get("/privacy/status")
def privacy_status(
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Return current privacy/anonymization configuration status."""
    config = PrivacyConfig()
    return get_privacy_status(config)
