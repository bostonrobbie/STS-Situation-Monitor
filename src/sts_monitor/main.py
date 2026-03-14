"""STS Situation Monitor — FastAPI application factory.

This module creates the FastAPI app and includes all route modules.
Route handlers are organized into modules under ``sts_monitor.routes``.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from sts_monitor.autopilot import AUTOPILOT_ENABLED, start_autopilot, stop_autopilot
from sts_monitor.config import settings
from sts_monitor.database import Base, engine, get_session
from sts_monitor.jobs import tick_schedules
from sts_monitor.online_tools import parse_csv_env
from sts_monitor.rate_limit import RateLimitMiddleware
from sts_monitor.routes import all_routers

_scheduler_task: asyncio.Task | None = None


async def _background_scheduler():
    """Background loop that ticks job schedules periodically."""
    while True:
        try:
            await asyncio.sleep(30)
            with next(get_session()) as session:
                tick_schedules(session)
        except asyncio.CancelledError:
            break
        except Exception:
            pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _scheduler_task
    Base.metadata.create_all(bind=engine)
    _scheduler_task = asyncio.create_task(_background_scheduler())
    if AUTOPILOT_ENABLED:
        start_autopilot()
    yield
    stop_autopilot()
    _scheduler_task.cancel()
    try:
        await _scheduler_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="STS Situation Monitor", version="0.7.0", lifespan=lifespan)

# Serve static dashboard
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# CORS middleware
cors_origins = parse_csv_env(settings.cors_origins) or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Trusted hosts middleware
trusted_hosts = parse_csv_env(settings.trusted_hosts)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts or ["*"])

# Rate limiting middleware
app.add_middleware(RateLimitMiddleware)

# Include all route modules
for router in all_routers:
    app.include_router(router)

# Re-export shared singletons and helpers for backward compatibility with tests
from sts_monitor.deps import llm_client, pipeline  # noqa: E402, F401
from sts_monitor.pipeline import SignalPipeline  # noqa: E402, F401
from sts_monitor.helpers import (  # noqa: E402, F401
    connector_diagnostics as _connector_diagnostics,
    compute_readiness_score as _compute_readiness_score,
    workspace_health_snapshot as _workspace_health_snapshot,
    queue_health_snapshot as _queue_health_snapshot,
    build_report_text as _build_report_text,
    record_ingestion_run as _record_ingestion_run,
    record_audit as _record_audit,
    evaluate_alert_rules as _evaluate_alert_rules,
    seed_default_research_sources as _seed_default_research_sources,
    is_valid_structured_llm_payload as _is_valid_structured_llm_payload,
    parse_llm_structured_summary as _parse_llm_structured_summary,
    build_report_sections as _build_report_sections,
    persist_claim_lineage as _persist_claim_lineage,
    normalize_text as _normalize_text,
    compute_report_lineage_validation as _compute_report_lineage_validation,
    persist_geo_events as _persist_geo_events,
    ingest_with_geo_connector as _ingest_with_geo_connector,
)
from sts_monitor.online_tools import send_alert_webhook  # noqa: E402, F401
import httpx  # noqa: E402, F401
