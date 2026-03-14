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
from sts_monitor.models import FeedbackORM, IngestionRunORM, InvestigationORM, ObservationORM, ReportORM
from sts_monitor.pipeline import Observation, SignalPipeline
from sts_monitor.security import require_api_key
from sts_monitor.simulation import generate_simulated_observations
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
import shutil
from datetime import UTC, datetime, timedelta
from xml.sax.saxutils import escape
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncio

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sts_monitor.config import settings
from sts_monitor.discovery import build_discovery_summary
from sts_monitor.connectors import (
    RSSConnector, RedditConnector, GDELTConnector, USGSEarthquakeConnector,
    NASAFIRMSConnector, ACLEDConnector, NWSAlertConnector, FEMADisasterConnector,
    ReliefWebConnector, OpenSkyConnector, WebcamConnector,
    ADSBExchangeConnector, MarineTrafficConnector, TelegramConnector,
    InternetArchiveConnector,
)
from sts_monitor.privacy import PrivacyConfig, get_privacy_status
from sts_monitor.connectors.webcams import list_camera_regions, get_cameras_near, CURATED_CAMERAS
from sts_monitor.database import Base, engine, get_session
from sts_monitor.jobs import create_schedule, enqueue_job, process_job_batch, process_next_job, requeue_dead_letter, tick_schedules
from sts_monitor.llm import LocalLLMClient
from sts_monitor.models import APIKeyORM, AlertEventORM, AlertRuleORM, AuditLogORM, ClaimEvidenceORM, ClaimORM, CollectionPlanORM, ConvergenceZoneORM, DashboardConfigORM, DiscoveredTopicORM, EntityMentionORM, FeedbackORM, GeoEventORM, IngestionRunORM, InvestigationORM, JobORM, JobScheduleORM, ObservationORM, ReportORM, ResearchSourceORM, SearchProfileORM, StoryORM, StoryObservationORM
from sts_monitor.online_tools import parse_csv_env, send_alert_webhook
from sts_monitor.pipeline import Observation, SignalPipeline
from sts_monitor.search import apply_context_boosts, build_query_plan, normalize_datetime, score_text, top_terms
from sts_monitor.clustering import ObservationRef, cluster_observations, enrich_stories_with_entities
from sts_monitor.collection_plan import CollectionRequirement, build_collection_plan, get_curated_feeds, list_feed_categories
from sts_monitor.convergence import GeoPoint, detect_convergence
from sts_monitor.entities import extract_entities
from sts_monitor.event_bus import STSEvent, event_bus
from sts_monitor.research import TrendingResearchScanner
from sts_monitor.story_discovery import ObservationSnapshot, run_discovery
from sts_monitor.security import AuthContext, hash_api_key, now_utc, require_admin, require_analyst, require_api_key
from sts_monitor.simulation import generate_simulated_observations
from sts_monitor.corroboration import analyze_corroboration
from sts_monitor.slop_detector import filter_slop, score_observation
from sts_monitor.entity_graph import build_entity_graph
from sts_monitor.narrative import build_narrative_timeline
from sts_monitor.anomaly_detector import run_anomaly_detection
from sts_monitor.autopilot import AUTOPILOT_ENABLED, get_state as get_autopilot_state, start_autopilot, stop_autopilot
from sts_monitor.investigation_templates import list_templates, get_template, apply_template
from sts_monitor.source_scoring import compute_source_scores, get_source_leaderboard
from sts_monitor.comparative import run_comparative_analysis
from sts_monitor.rabbit_trail import run_rabbit_trail, store_trail_session, get_trail_session, list_trail_sessions
from sts_monitor.alert_engine import evaluate_rules, get_default_rules, AlertRule
from sts_monitor.cross_investigation import detect_cross_investigation_links
from sts_monitor.claim_verification import verify_investigation_claims
from sts_monitor.geofence import get_all_zones, add_zone, remove_zone, check_observations_against_zones, get_zone_activity_summary, GeoZone
from sts_monitor.source_network import analyze_source_network
from sts_monitor.pattern_matching import analyze_patterns
from sts_monitor.intel_briefs import generate_intel_brief, brief_to_markdown
from sts_monitor.webhook_ingest import normalize_webhook_payload, validate_webhook_signature
from sts_monitor.multi_llm import get_router as get_llm_router
from sts_monitor.semantic_index import semantic_search as _semantic_search_fn, index_observations_batch, get_semantic_health


class InvestigationCreate(BaseModel):
    topic: str = Field(min_length=3, max_length=300)
    seed_query: str | None = None
    priority: int = Field(default=50, ge=1, le=100)
    owner: str | None = Field(default=None, max_length=120)
    status: str = Field(default="open", pattern="^(open|monitoring|resolved|closed)$")
    sla_due_at: datetime | None = None


class InvestigationUpdateRequest(BaseModel):
    priority: int | None = Field(default=None, ge=1, le=100)
    owner: str | None = Field(default=None, max_length=120)
    status: str | None = Field(default=None, pattern="^(open|monitoring|resolved|closed)$")
    sla_due_at: datetime | None = None


class Investigation(BaseModel):
    id: str
    topic: str
    seed_query: str | None = None
    priority: int
    owner: str | None = None
    status: str
    sla_due_at: datetime | None = None
    created_at: datetime


class RSSIngestRequest(BaseModel):
    feed_urls: list[str] = Field(min_length=1)
    query: str | None = None
    per_feed_limit: int = Field(default=10, ge=1, le=50)


class SimulatedIngestRequest(BaseModel):
    batch_size: int = Field(default=20, ge=1, le=500)
    include_noise: bool = True


class RedditIngestRequest(BaseModel):
    subreddits: list[str] = Field(min_length=1, max_length=20)
    query: str | None = None
    per_subreddit_limit: int = Field(default=25, ge=1, le=100)
    sort: str = Field(default="new", pattern="^(new|hot|top)$")


class TrendingResearchRequest(BaseModel):
    geo: str = Field(default="US", min_length=2, max_length=3)
    max_topics: int = Field(default=10, ge=1, le=50)
    per_topic_limit: int = Field(default=5, ge=1, le=20)


class GDELTIngestRequest(BaseModel):
    query: str | None = None
    timespan: str = Field(default="3h", pattern=r"^\d+[hd]$")
    max_records: int = Field(default=75, ge=1, le=250)
    mode: str = Field(default="ArtList", pattern="^(ArtList|TimelineVol|TimelineSourceCountry)$")
    source_country: str | None = None
    source_lang: str | None = None


class USGSIngestRequest(BaseModel):
    query: str | None = None
    min_magnitude: float = Field(default=4.0, ge=0.0, le=10.0)
    lookback_hours: int = Field(default=24, ge=1, le=720)
    max_events: int = Field(default=100, ge=1, le=500)
    use_summary_feed: bool = False
    summary_feed: str = Field(default="significant_hour", pattern="^(significant_hour|m4\\.5_day|m2\\.5_day|all_hour)$")


class NASAFIRMSIngestRequest(BaseModel):
    query: str | None = None
    country_code: str | None = Field(default=None, min_length=3, max_length=3)
    days: int = Field(default=1, ge=1, le=10)
    min_confidence: str = Field(default="nominal", pattern="^(low|nominal|high)$")


class ACLEDIngestRequest(BaseModel):
    query: str | None = None
    lookback_days: int = Field(default=7, ge=1, le=365)
    limit: int = Field(default=100, ge=1, le=5000)
    country: str | None = None
    region: int | None = None
    event_type: str | None = None


class NWSIngestRequest(BaseModel):
    query: str | None = None
    severity_filter: str = Field(default="Extreme,Severe")
    status: str = Field(default="actual", pattern="^(actual|exercise|system|test|draft)$")
    urgency: str | None = None
    area: str | None = Field(default=None, min_length=2, max_length=2)


class FEMAIngestRequest(BaseModel):
    query: str | None = None
    lookback_days: int = Field(default=30, ge=1, le=365)
    limit: int = Field(default=100, ge=1, le=1000)
    state: str | None = Field(default=None, min_length=2, max_length=2)
    declaration_type: str | None = Field(default=None, pattern="^(DR|EM|FM|FS)$")


class ReliefWebIngestRequest(BaseModel):
    query: str | None = None
    lookback_days: int = Field(default=7, ge=1, le=365)
    limit: int = Field(default=50, ge=1, le=500)
    country: str | None = None
    disaster_type: str | None = None
    content_format: str | None = None


class OpenSkyIngestRequest(BaseModel):
    query: str | None = None
    bbox_lamin: float | None = None
    bbox_lomin: float | None = None
    bbox_lamax: float | None = None
    bbox_lomax: float | None = None


class WebcamIngestRequest(BaseModel):
    query: str | None = None
    regions: list[str] | None = None
    nearby_lat: float | None = None
    nearby_lon: float | None = None
    nearby_radius_km: int = Field(default=50, ge=1, le=500)


class ADSBIngestRequest(BaseModel):
    query: str | None = None
    lat: float = Field(default=0.0, ge=-90.0, le=90.0)
    lon: float = Field(default=0.0, ge=-180.0, le=180.0)
    dist_nm: int = Field(default=250, ge=10, le=500)
    military_only: bool = False


class MarineIngestRequest(BaseModel):
    query: str | None = None
    bbox_lat_min: float = Field(default=-90.0, ge=-90.0, le=90.0)
    bbox_lat_max: float = Field(default=90.0, ge=-90.0, le=90.0)
    bbox_lon_min: float = Field(default=-180.0, ge=-180.0, le=180.0)
    bbox_lon_max: float = Field(default=180.0, ge=-180.0, le=180.0)
    vessel_types: list[str] | None = None


class TelegramIngestRequest(BaseModel):
    query: str | None = None
    channels: list[str] | None = None
    max_posts_per_channel: int = Field(default=20, ge=1, le=100)


class ArchiveIngestRequest(BaseModel):
    query: str | None = None
    urls: list[str] = Field(min_length=1, max_length=20)
    max_snapshots: int = Field(default=5, ge=1, le=20)


class CollectionPlanCreateRequest(BaseModel):
    investigation_id: str
    name: str = Field(min_length=3, max_length=200)
    connectors: list[str] = Field(min_length=1)
    query: str = Field(min_length=2, max_length=500)
    priority: int = Field(default=50, ge=1, le=100)
    interval_seconds: int = Field(default=3600, ge=60, le=86_400)
    filters: dict[str, Any] = Field(default_factory=dict)
    auto_generate: bool = False


class PromoteTopicRequest(BaseModel):
    priority: int = Field(default=50, ge=1, le=100)
    owner: str | None = None


class RunRequest(BaseModel):
    use_llm: bool = False


class FeedbackRequest(BaseModel):
    label: str = Field(min_length=2, max_length=50)
    notes: str = Field(min_length=2, max_length=5000)


class LocalObservationInput(BaseModel):
    source: str = Field(min_length=2, max_length=600)
    claim: str = Field(min_length=2, max_length=10000)
    url: str = Field(min_length=3, max_length=1200)
    reliability_hint: float = Field(default=0.5, ge=0.0, le=1.0)
    captured_at: datetime | None = None


class LocalIngestRequest(BaseModel):
    observations: list[LocalObservationInput] = Field(min_length=1, max_length=2000)


class EnqueueRunJobRequest(BaseModel):
    use_llm: bool = False
    priority: int = Field(default=60, ge=1, le=100)
    max_attempts: int = Field(default=3, ge=1, le=10)


class EnqueueSimulatedJobRequest(BaseModel):
    batch_size: int = Field(default=20, ge=1, le=500)
    include_noise: bool = True
    priority: int = Field(default=50, ge=1, le=100)
    max_attempts: int = Field(default=3, ge=1, le=10)


class CreateScheduleRequest(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    job_type: str = Field(pattern="^(ingest_simulated|run_pipeline)$")
    payload: dict[str, Any]
    interval_seconds: int = Field(default=300, ge=10, le=86_400)
    priority: int = Field(default=50, ge=1, le=100)


class ProcessBatchRequest(BaseModel):
    high_quota: int = Field(default=2, ge=0, le=50)
    normal_quota: int = Field(default=2, ge=0, le=50)
    low_quota: int = Field(default=1, ge=0, le=50)


class ResearchSourceCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    source_type: str = Field(min_length=2, max_length=40)
    base_url: str = Field(min_length=5, max_length=1200)
    trust_score: float = Field(default=0.5, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)


class DiscoveryRequest(BaseModel):
    use_llm: bool = False


class AlertRuleCreateRequest(BaseModel):
    investigation_id: str
    name: str = Field(min_length=3, max_length=120)
    min_observations: int = Field(default=20, ge=1, le=10000)
    min_disputed_claims: int = Field(default=1, ge=0, le=1000)
    cooldown_seconds: int = Field(default=900, ge=60, le=86_400)
    active: bool = True


class APIKeyCreateRequest(BaseModel):
    label: str = Field(min_length=3, max_length=120)
    role: str = Field(default="analyst", pattern="^(admin|analyst|viewer)$")


class SearchProfileCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    investigation_id: str | None = None
    include_terms: list[str] = Field(default_factory=list, max_length=100)
    exclude_terms: list[str] = Field(default_factory=list, max_length=100)
    synonyms: dict[str, list[str]] = Field(default_factory=dict)


class SearchQueryRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    investigation_id: str | None = None
    profile_name: str | None = None
    source_prefix: str | None = None
    stance: str | None = Field(default=None, pattern="^(supported|disputed|unknown|monitor)$")
    min_reliability: float = Field(default=0.0, ge=0.0, le=1.0)
    since: datetime | None = None
    until: datetime | None = None
    include_observations: bool = True
    include_claims: bool = True
    min_score: float = Field(default=0.1, ge=0.0, le=1.0)
    limit: int = Field(default=50, ge=1, le=500)


class RelatedInvestigationsRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=10, ge=1, le=100)
    min_score: float = Field(default=0.1, ge=0.0, le=1.0)


_scheduler_task: asyncio.Task | None = None


async def _background_scheduler():
    """Background loop that ticks job schedules periodically."""
    while True:
        try:
            await asyncio.sleep(30)  # Check every 30 seconds
            with next(get_session()) as session:
                tick_schedules(session)
        except asyncio.CancelledError:
            break
        except Exception:
            pass  # Scheduler errors are non-fatal


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _scheduler_task
    Base.metadata.create_all(bind=engine)
    _scheduler_task = asyncio.create_task(_background_scheduler())
    # Start autopilot if enabled
    if AUTOPILOT_ENABLED:
        start_autopilot()
    yield
    # Shutdown autopilot
    stop_autopilot()
    _scheduler_task.cancel()
    try:
        await _scheduler_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="STS Situation Monitor", version="0.5.0", lifespan=lifespan)
app = FastAPI(title="STS Situation Monitor", version="0.4.0", lifespan=lifespan)
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
app = FastAPI(title="STS Situation Monitor", version="0.1.0")
app = FastAPI(title="STS Situation Monitor", version="0.7.0", lifespan=lifespan)

# Serve static dashboard
from fastapi.staticfiles import StaticFiles
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

cors_origins = parse_csv_env(settings.cors_origins) or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

trusted_hosts = parse_csv_env(settings.trusted_hosts)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts or ["*"])

# Rate limiting middleware
from sts_monitor.rate_limit import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware)

pipeline = SignalPipeline()
llm_client = LocalLLMClient(
    base_url=settings.local_llm_url,
    model=settings.local_llm_model,
    timeout_s=settings.local_llm_timeout_s,
    max_retries=settings.local_llm_max_retries,
)


@app.get("/")
def root_redirect():
    """Redirect root to dashboard."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/static/globe.html")


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
    session.add(
        IngestionRunORM(
            investigation_id=investigation_id,
            connector=connector,
            started_at=datetime.now(UTC),
            ingested_count=ingested_count,
            failed_count=failed_count,
            status=status,
            detail_json=json.dumps(detail),
        )
    )


def _compute_report_lineage_validation(session: Session, report_id: int) -> dict[str, Any]:
    claims = session.scalars(select(ClaimORM).where(ClaimORM.report_id == report_id)).all()
    total = len(claims)
    if total == 0:
        return {"valid": False, "coverage": 0.0, "claims_total": 0, "claims_with_evidence": 0}

    with_evidence = 0
    for claim in claims:
        exists = session.scalars(select(ClaimEvidenceORM).where(ClaimEvidenceORM.claim_id == claim.id).limit(1)).first()
        if exists:
            with_evidence += 1

    coverage = with_evidence / total
    return {
        "valid": coverage >= 0.7,
        "coverage": round(coverage, 3),
        "claims_total": total,
        "claims_with_evidence": with_evidence,
    }


def _record_audit(
    session: Session,
    *,
    actor: AuthContext,
    action: str,
    resource_type: str,
    resource_id: str | None,
    detail: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLogORM(
            actor_label=actor.label,
            actor_role=actor.role,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail_json=json.dumps(detail or {}),
            created_at=now_utc(),
        )
    )


def _connector_diagnostics() -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}
    endpoints = {
        "google_trends": "https://trends.google.com/trending/rss?geo=US",
        "google_news": "https://news.google.com/rss",
        "reddit_worldnews": "https://www.reddit.com/r/worldnews/new.json?limit=1",
    }
    for name, url in endpoints.items():
        ok = False
        detail = "ok"
        try:
            response = httpx.get(url, timeout=3.0, follow_redirects=True)
            ok = response.status_code < 500
            detail = f"status={response.status_code}"
        except Exception as exc:
            detail = str(exc)
        checks[name] = {"ok": ok, "detail": detail, "url": url}
    return {
        "checks": checks,
        "ok": all(item["ok"] for item in checks.values()),
    }


def _compute_readiness_score(*, db_ok: bool, llm_ok: bool, workspace_ok: bool, connectors_ok: bool, queue_ok: bool) -> dict[str, Any]:
    weighted = [
        (db_ok, 30),
        (workspace_ok, 20),
        (connectors_ok, 20),
        (llm_ok, 15),
        (queue_ok, 15),
    ]
    score = sum(weight for ok, weight in weighted if ok)
    level = "ready" if score >= 80 else "degraded" if score >= 50 else "blocked"
    return {"score": score, "level": level}




def _is_valid_structured_llm_payload(payload: dict[str, Any]) -> bool:
    required_top = {
        "topic",
        "overall_assessment",
        "overall_confidence",
        "key_claims",
        "disputed_claims",
        "gaps",
        "next_actions",
    }
    if not required_top.issubset(payload):
        return False

    confidence = payload.get("overall_confidence")
    if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
        return False

    claims = payload.get("key_claims")
    if not isinstance(claims, list) or not claims:
        return False

    allowed_status = {"supported", "disputed", "unknown", "monitor-next"}
    for claim in claims:
        if not isinstance(claim, dict):
            return False
        if claim.get("status") not in allowed_status:
            return False
        if not isinstance(claim.get("evidence"), list) or not claim["evidence"]:
            return False

    for key in ("disputed_claims", "gaps", "next_actions"):
        if not isinstance(payload.get(key), list):
            return False

    return True


def _parse_llm_structured_summary(raw_summary: str) -> tuple[bool, dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(raw_summary)
    except Exception:
        return False, None, "llm response was not valid JSON"

    if not isinstance(payload, dict):
        return False, None, "llm response JSON was not an object"

    if not _is_valid_structured_llm_payload(payload):
        return False, None, "llm response did not match required structured fields"

    return True, payload, None


def _queue_health_snapshot(session: Session) -> dict[str, Any]:
    pending = session.scalar(select(func.count(JobORM.id)).where(JobORM.status == "pending")) or 0
    failed = session.scalar(select(func.count(JobORM.id)).where(JobORM.status == "failed")) or 0
    dead_letter = session.scalar(select(func.count(JobORM.id)).where(JobORM.dead_lettered.is_(True))) or 0

    ok = failed == 0 and dead_letter == 0 and pending <= 100
    detail = "ok"
    if failed > 0 or dead_letter > 0:
        detail = "job failures or dead letters present"
    elif pending > 100:
        detail = "job backlog is high"

    return {"ok": ok, "pending": pending, "failed": failed, "dead_letter": dead_letter, "detail": detail}


def _workspace_health_snapshot(workspace_root: Path) -> dict[str, Any]:
    exists = workspace_root.exists()
    writable = workspace_root.is_dir() and workspace_root.exists()
    disk_free_mb = None
    if exists:
        usage = shutil.disk_usage(workspace_root)
        disk_free_mb = round(usage.free / (1024 * 1024), 2)

    ok = bool(exists and disk_free_mb is not None and disk_free_mb >= 256)
    return {
        "ok": ok,
        "workspace_root": str(workspace_root),
        "workspace_root_exists": exists,
        "disk_free_mb": disk_free_mb,
        "min_disk_free_mb": 256,
        "writable_hint": writable,
    }

def _build_report_sections(topic: str, accepted: list[dict[str, Any]], dropped: list[dict[str, Any]], disputed_claims: list[str]) -> dict[str, Any]:
    likely_true = [item["claim"] for item in accepted[:5]]
    disputed = disputed_claims[:5]
    unknown = [item["claim"] for item in dropped[:5]]
    monitor_next = [f"Track source drift for {topic}", f"Re-run ingestion for {topic} within 1 hour"]
    sections = {
        "likely_true": likely_true,
        "disputed": disputed,
        "unknown": unknown,
        "monitor_next": monitor_next,
    }
    # report contract validation
    required = ("likely_true", "disputed", "unknown", "monitor_next")
    if not all(key in sections and isinstance(sections[key], list) for key in required):
        raise ValueError("Invalid report sections contract")
    return sections



def _normalize_text(text: str) -> str:
    return " ".join(text.lower().split())


def _persist_claim_lineage(
    *,
    session: Session,
    investigation_id: str,
    report_id: int,
    report_sections: dict[str, list[str]],
    observations: list[ObservationORM],
) -> None:
    stance_map = {
        "likely_true": "supported",
        "disputed": "disputed",
        "unknown": "unknown",
        "monitor_next": "monitor",
    }

    normalized_obs = [(_normalize_text(item.claim), item) for item in observations]

    for section, claims in report_sections.items():
        stance = stance_map.get(section, "unknown")
        base_confidence = 0.8 if stance == "supported" else 0.5
        if stance == "disputed":
            base_confidence = 0.35
        for text in claims[:25]:
            claim_row = ClaimORM(
                investigation_id=investigation_id,
                report_id=report_id,
                claim_text=text,
                stance=stance,
                confidence=base_confidence,
                created_at=datetime.now(UTC),
            )
            session.add(claim_row)
            session.flush()

            normalized_claim = _normalize_text(text)
            linked = 0
            for obs_text, obs in normalized_obs:
                if normalized_claim in obs_text or obs_text in normalized_claim:
                    session.add(
                        ClaimEvidenceORM(
                            claim_id=claim_row.id,
                            observation_id=obs.id,
                            weight=max(0.1, min(1.0, obs.reliability_hint)),
                            rationale="text-match",
                            created_at=datetime.now(UTC),
                        )
                    )
                    linked += 1
                if linked >= 5:
                    break

def _seed_default_research_sources(session: Session) -> None:
    defaults = [
        {"name": "google-trends", "source_type": "trends", "base_url": "https://trends.google.com/trending/rss", "trust_score": 0.6},
        {"name": "google-news-rss", "source_type": "news", "base_url": "https://news.google.com/rss", "trust_score": 0.65},
    ]
    for item in defaults:
        exists = session.scalars(select(ResearchSourceORM).where(ResearchSourceORM.name == item["name"])).first()
        if exists:
            continue
        session.add(
            ResearchSourceORM(
                name=item["name"],
                source_type=item["source_type"],
                base_url=item["base_url"],
                trust_score=item["trust_score"],
                active=True,
                tags_json=json.dumps(["default"]),
                created_at=datetime.now(UTC),
            )
        )


def _evaluate_alert_rules(session: Session, investigation_id: str) -> list[dict[str, Any]]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    db_observations = session.scalars(select(ObservationORM).where(ObservationORM.investigation_id == investigation_id)).all()
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
    pipeline_result = pipeline.run(observations, topic=investigation.topic) if observations else None

    rules = session.scalars(
        select(AlertRuleORM).where(AlertRuleORM.investigation_id == investigation_id).where(AlertRuleORM.active.is_(True))
    ).all()

    emitted: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    obs_count = len(observations)
    disputed_count = len(pipeline_result.disputed_claims) if pipeline_result else 0

    for rule in rules:
        if rule.last_triggered_at and (now - rule.last_triggered_at) < timedelta(seconds=rule.cooldown_seconds):
            continue

        if obs_count < rule.min_observations:
            continue
        if disputed_count < rule.min_disputed_claims:
            continue

        message = (
            f"Rule '{rule.name}' triggered: observations={obs_count} disputed_claims={disputed_count} "
            f"(thresholds: obs>={rule.min_observations}, disputed>={rule.min_disputed_claims})"
        )
        event = AlertEventORM(
            rule_id=rule.id,
            investigation_id=investigation_id,
            triggered_at=now,
            severity="warning",
            message=message,
            detail_json=json.dumps(
                {
                    "observations": obs_count,
                    "disputed_claims": disputed_count,
                    "thresholds": {
                        "min_observations": rule.min_observations,
                        "min_disputed_claims": rule.min_disputed_claims,
                    },
                }
            ),
        )
        session.add(event)
        webhook_delivery = send_alert_webhook(
            webhook_url=settings.alert_webhook_url,
            timeout_s=settings.alert_webhook_timeout_s,
            payload={
                "type": "alert_event",
                "investigation_id": investigation_id,
                "rule_id": rule.id,
                "severity": event.severity,
                "message": message,
            },
        )
        rule.last_triggered_at = now
        rule.updated_at = now
        emitted.append({"rule_id": rule.id, "message": message, "webhook": webhook_delivery})

    session.commit()
    return emitted


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
    workspace = _workspace_health_snapshot(workspace_root)
    filesystem = {
        "database_path_exists": Path(db_path).exists() if db_path else None,
        "cwd": str(Path.cwd()),
        "workspace_root": workspace["workspace_root"],
        "workspace_root_exists": workspace["workspace_root_exists"],
    }

    llm_ok = llm_health.reachable and llm_health.model_available
    connectors = _connector_diagnostics()
    queue = _queue_health_snapshot(session)
    readiness = _compute_readiness_score(
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


@app.get("/system/online-tools")
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


@app.post("/investigations", response_model=Investigation)
def create_investigation(
    payload: InvestigationCreate,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> Investigation:
    investigation = InvestigationORM(
        id=str(uuid4()),
        topic=payload.topic,
        seed_query=payload.seed_query,
        priority=payload.priority,
        owner=payload.owner,
        status=payload.status,
        sla_due_at=payload.sla_due_at,
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
    auth: AuthContext = Depends(require_analyst),
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
    auth: AuthContext = Depends(require_analyst),
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
    auth: AuthContext = Depends(require_analyst),
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
    auth: AuthContext = Depends(require_analyst),
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


@app.get("/dashboard/summary")
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
    investigations[investigation.id] = investigation
    return investigation


@app.get("/investigations", response_model=list[Investigation])
def list_investigations(session: Session = Depends(get_session)) -> list[Investigation]:
    investigations = session.scalars(select(InvestigationORM).order_by(InvestigationORM.created_at.desc())).all()
    return [Investigation.model_validate(item, from_attributes=True) for item in investigations]




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
    _record_audit(session, actor=auth, action="investigation.create", resource_type="investigation", resource_id=investigation.id, detail={"topic": payload.topic})
    session.commit()
    return Investigation.model_validate(investigation, from_attributes=True)


@app.get("/investigations", response_model=list[Investigation])
def list_investigations(_: None = Depends(require_api_key), session: Session = Depends(get_session)) -> list[Investigation]:
    investigations = session.scalars(select(InvestigationORM).order_by(InvestigationORM.priority.desc(), InvestigationORM.created_at.desc())).all()
    return [Investigation.model_validate(item, from_attributes=True) for item in investigations]


@app.patch("/investigations/{investigation_id}", response_model=Investigation)
def update_investigation(
    investigation_id: str,
    payload: InvestigationUpdateRequest,
    auth: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> Investigation:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    if payload.priority is not None:
        investigation.priority = payload.priority
    if payload.owner is not None:
        investigation.owner = payload.owner
    if payload.status is not None:
        investigation.status = payload.status
    if payload.sla_due_at is not None:
        investigation.sla_due_at = payload.sla_due_at
    _record_audit(session, actor=auth, action="investigation.update", resource_type="investigation", resource_id=investigation_id, detail=payload.model_dump(mode="json"))
    session.commit()
    return Investigation.model_validate(investigation, from_attributes=True)


@app.post("/investigations/{investigation_id}/ingest/rss")
def ingest_rss(
    investigation_id: str,
    payload: RSSIngestRequest,
    auth: AuthContext = Depends(require_analyst),
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

    _record_audit(session, actor=auth, action="ingest.rss", resource_type="investigation", resource_id=investigation_id, detail={"ingested": len(result.observations), "failed_feeds": failed})
    session.commit()
    stored_count = session.scalar(select(func.count(ObservationORM.id)).where(ObservationORM.investigation_id == investigation_id))

    return {
        "investigation_id": investigation_id,
        "connector": result.connector,
        "ingested_count": len(result.observations),
        "stored_count": stored_count or 0,
        "failed_feeds": failed,
    }


@app.post("/investigations/{investigation_id}/ingest/reddit")
def ingest_reddit(
    investigation_id: str,
    payload: RedditIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    connector = RedditConnector(
        subreddits=payload.subreddits,
        per_subreddit_limit=payload.per_subreddit_limit,
        sort=payload.sort,
        timeout_s=settings.reddit_timeout_s,
        user_agent=settings.reddit_user_agent,
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

    failed = result.metadata.get("failed_subreddits", [])
    status = "partial" if failed else "success"
    _record_ingestion_run(
        session=session,
        investigation_id=investigation_id,
        connector="reddit",
        ingested_count=len(result.observations),
        failed_count=len(failed),
        status=status,
        detail=result.metadata,
    )
    _record_audit(session, actor=auth, action="ingest.reddit", resource_type="investigation", resource_id=investigation_id, detail={"ingested": len(result.observations), "failed": len(failed)})

    session.commit()
    stored_count = session.scalar(select(func.count(ObservationORM.id)).where(ObservationORM.investigation_id == investigation_id))

    return {
        "investigation_id": investigation_id,
        "connector": result.connector,
        "ingested_count": len(result.observations),
        "stored_count": stored_count or 0,
        "failed_subreddits": failed,
    }


@app.post("/investigations/{investigation_id}/ingest/simulated")
def ingest_simulated(
    investigation_id: str,
    payload: SimulatedIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    generated = generate_simulated_observations(topic=investigation.topic, batch_size=payload.batch_size, include_noise=payload.include_noise)
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
    _record_audit(session, actor=auth, action="ingest.simulated", resource_type="investigation", resource_id=investigation_id, detail={"ingested": len(generated)})
    session.commit()

    return {"investigation_id": investigation_id, "connector": "simulated", "ingested_count": len(generated)}


@app.post("/investigations/{investigation_id}/ingest/local-json")
def ingest_local_json(
    investigation_id: str,
    payload: LocalIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    for item in payload.observations:
        session.add(
            ObservationORM(
                investigation_id=investigation_id,
                source=item.source,
                claim=item.claim,
                url=item.url,
                captured_at=item.captured_at or now_utc(),
                reliability_hint=item.reliability_hint,
            )
        )

    _record_ingestion_run(
        session=session,
        investigation_id=investigation_id,
        connector="local-json",
        ingested_count=len(payload.observations),
        failed_count=0,
        status="success",
        detail={"count": len(payload.observations)},
    )
    _record_audit(
        session,
        actor=auth,
        action="ingest.local-json",
        resource_type="investigation",
        resource_id=investigation_id,
        detail={"count": len(payload.observations)},
    )
    session.commit()
    return {"investigation_id": investigation_id, "connector": "local-json", "ingested_count": len(payload.observations)}


@app.post("/investigations/{investigation_id}/ingest/trending")
def ingest_trending(
    investigation_id: str,
    payload: TrendingResearchRequest,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    scanner = TrendingResearchScanner(max_topics=payload.max_topics, per_topic_limit=payload.per_topic_limit)
    observations, metadata = scanner.collect_observations(geo=payload.geo)

    for item in observations:
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

    failed_topics = metadata.get("failed_topics", [])
    status = "partial" if failed_topics else "success"
    _record_ingestion_run(
        session=session,
        investigation_id=investigation_id,
        connector="trending",
        ingested_count=len(observations),
        failed_count=len(failed_topics),
        status=status,
        detail=metadata,
    )
    session.commit()

    return {
        "investigation_id": investigation_id,
        "connector": "trending",
        "ingested_count": len(observations),
        "topics_scanned": metadata.get("topics_scanned", 0),
        "failed_topics": failed_topics,
    }


@app.get("/research/trending-topics")
def list_trending_topics(
    geo: str = "US",
    max_topics: int = 10,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    scanner = TrendingResearchScanner(max_topics=max_topics)
    topics = scanner.fetch_topics(geo=geo)
    return {
        "geo": geo.upper(),
        "count": len(topics),
        "topics": [
            {
                "topic": item.topic,
                "traffic": item.traffic,
                "published_at": item.published_at,
                "source_url": item.source_url,
            }
            for item in topics
        ],
    }


# ── Autonomous Research Agent ───────────────────────────────────────────

class ResearchAgentRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=500)
    seed_query: str | None = None
    max_iterations: int = Field(default=5, ge=1, le=20)
    investigation_id: str | None = None
    nitter_categories: list[str] | None = None
    rss_categories: list[str] | None = None


def _get_research_agent():
    from sts_monitor.research_agent import ResearchAgent
    from sts_monitor.connectors.nitter import DEFAULT_NITTER_INSTANCES
    nitter_instances = parse_csv_env(settings.nitter_instances) or list(DEFAULT_NITTER_INSTANCES)
    nitter_cats = parse_csv_env(settings.nitter_categories) or None
    agent_llm = LocalLLMClient(
        base_url=settings.local_llm_url,
        model=settings.local_llm_model,
        timeout_s=settings.agent_llm_timeout_s,
        max_retries=settings.local_llm_max_retries,
    )
    return ResearchAgent(
        llm_client=agent_llm,
        max_iterations=settings.agent_max_iterations,
        max_observations=settings.agent_max_observations,
        nitter_instances=nitter_instances,
        nitter_categories=nitter_cats,
        inter_iteration_delay_s=settings.agent_inter_iteration_delay_s,
        scraper_max_depth=settings.scraper_max_depth,
        scraper_max_pages=settings.scraper_max_pages,
        scraper_delay_s=settings.scraper_delay_s,
        search_max_results=settings.search_max_results,
    )


# Singleton agent instance
_research_agent = None


def _get_or_create_agent():
    global _research_agent
    if _research_agent is None:
        _research_agent = _get_research_agent()
    return _research_agent


@app.post("/research/agent/start")
def start_research_agent(
    body: ResearchAgentRequest,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Launch an autonomous research session (runs in background thread)."""
    agent = _get_research_agent()
    session_id = str(uuid4())

    # Apply per-request settings to a fresh agent instance
    if body.max_iterations:
        agent.max_iterations = body.max_iterations
    if body.nitter_categories:
        agent.nitter_categories = body.nitter_categories
    if body.rss_categories:
        agent.rss_categories = body.rss_categories

    agent.run_async(session_id, body.topic, body.seed_query)
    return {
        "session_id": session_id,
        "topic": body.topic,
        "status": "running",
        "max_iterations": agent.max_iterations,
    }


@app.get("/research/agent/sessions")
def list_research_sessions(
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """List all research agent sessions."""
    agent = _get_or_create_agent()
    return {"sessions": agent.list_sessions()}


@app.get("/research/agent/sessions/{session_id}")
def get_research_session(
    session_id: str,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Get details of a specific research session."""
    agent = _get_or_create_agent()
    session = agent.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    iterations_summary = []
    for it in session.iterations:
        iterations_summary.append({
            "iteration": it.iteration,
            "started_at": it.started_at.isoformat(),
            "observations_collected": it.observations_collected,
            "connectors_used": it.connectors_used,
            "duration_s": it.duration_s,
            "assessment": (it.llm_response or {}).get("assessment", ""),
            "confidence": (it.llm_response or {}).get("confidence", 0),
            "should_continue": (it.llm_response or {}).get("should_continue", False),
        })

    return {
        "session_id": session.session_id,
        "topic": session.topic,
        "status": session.status,
        "started_at": session.started_at.isoformat(),
        "finished_at": session.finished_at.isoformat() if session.finished_at else None,
        "total_observations": len(session.all_observations),
        "total_findings": len(session.all_findings),
        "findings": session.all_findings,
        "iterations": iterations_summary,
        "final_brief": session.final_brief,
        "error": session.error,
    }


@app.post("/research/agent/sessions/{session_id}/stop")
def stop_research_session(
    session_id: str,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Stop a running research session."""
    agent = _get_or_create_agent()
    stopped = agent.stop_session(session_id)
    if not stopped:
        raise HTTPException(status_code=404, detail="Session not found or already stopped")
    return {"session_id": session_id, "status": "stopping"}


@app.post("/research/agent/search")
def agent_web_search(
    query: str,
    max_results: int = 20,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Run a one-off web search via DuckDuckGo."""
    from sts_monitor.connectors.search import SearchConnector
    connector = SearchConnector(max_results=max_results)
    result = connector.collect(query=query)
    return {
        "query": query,
        "result_count": len(result.observations),
        "results": [
            {"source": o.source, "claim": o.claim[:500], "url": o.url}
            for o in result.observations
        ],
    }


class ScrapeRequest(BaseModel):
    urls: list[str] = Field(min_length=1)
    max_depth: int = Field(default=1, ge=0, le=3)
    max_pages: int = Field(default=20, ge=1, le=50)
    query: str | None = None


@app.post("/research/agent/scrape")
def agent_scrape_urls(
    body: ScrapeRequest,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Scrape one or more URLs with optional crawling."""
    from sts_monitor.connectors.web_scraper import WebScraperConnector
    scraper = WebScraperConnector(
        seed_urls=body.urls[:10],
        max_depth=body.max_depth,
        max_pages=body.max_pages,
    )
    result = scraper.collect(query=body.query)
    return {
        "urls_submitted": len(body.urls),
        "pages_scraped": len(result.observations),
        "results": [
            {"source": o.source, "claim": o.claim[:500], "url": o.url}
            for o in result.observations
        ],
        "metadata": result.metadata,
    }


class TwitterSearchRequest(BaseModel):
    query: str | None = None
    accounts: list[str] | None = None
    categories: list[str] | None = None


@app.post("/research/agent/twitter")
def agent_twitter_search(
    body: TwitterSearchRequest,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Search Twitter/X via Nitter RSS proxies."""
    from sts_monitor.connectors.nitter import NitterConnector, get_accounts_for_categories
    accts = list(body.accounts or [])
    if body.categories:
        accts.extend(get_accounts_for_categories(body.categories))
    connector = NitterConnector(accounts=list(set(accts)))
    result = connector.collect(query=body.query)
    return {
        "query": body.query,
        "accounts_monitored": len(accts),
        "tweet_count": len(result.observations),
        "tweets": [
            {"source": o.source, "claim": o.claim[:500], "url": o.url, "captured_at": o.captured_at.isoformat()}
            for o in result.observations
        ],
        "metadata": result.metadata,
    }


@app.get("/research/agent/twitter/categories")
def list_twitter_categories(
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """List available OSINT Twitter account categories."""
    from sts_monitor.connectors.nitter import OSINT_ACCOUNTS, list_osint_categories
    return {
        "categories": [
            {"name": cat, "account_count": len(accounts), "accounts": accounts}
            for cat, accounts in OSINT_ACCOUNTS.items()
        ],
    }


# ── Semantic Search Endpoints ───────────────────────────────────────────


def _get_semantic_engine():
    from sts_monitor.embeddings import OllamaEmbeddingClient, QdrantStore, SemanticSearchEngine
    embedder = OllamaEmbeddingClient(
        base_url=settings.local_llm_url,
        model=settings.embedding_model,
        timeout_s=settings.embedding_timeout_s,
    )
    store = QdrantStore(
        qdrant_url=settings.qdrant_url,
        vector_size=settings.qdrant_vector_size,
        timeout_s=settings.qdrant_timeout_s,
    )
    return SemanticSearchEngine(embedder, store)


@app.post("/semantic/initialize")
def semantic_initialize(
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Initialize semantic search (create Qdrant collection, check embedding model)."""
    engine = _get_semantic_engine()
    return engine.initialize()


class SemanticIndexRequest(BaseModel):
    investigation_id: str


@app.post("/semantic/index")
def semantic_index_investigation(
    body: SemanticIndexRequest,
    session: Session = Depends(get_session),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Index all observations for an investigation into Qdrant."""
    investigation = session.get(InvestigationORM, body.investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    db_obs = session.scalars(
        select(ObservationORM).where(ObservationORM.investigation_id == body.investigation_id)
    ).all()

    obs_dicts = [
        {
            "id": o.id,
            "investigation_id": o.investigation_id,
            "source": o.source,
            "claim": o.claim,
            "url": o.url,
            "captured_at": str(o.captured_at),
            "reliability_hint": o.reliability_hint,
        }
        for o in db_obs
    ]

    engine = _get_semantic_engine()
    return engine.index_observations(obs_dicts)


class SemanticSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    limit: int = Field(default=20, ge=1, le=100)
    score_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    investigation_id: str | None = None


@app.post("/semantic/search")
def semantic_search(
    body: SemanticSearchRequest,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Semantic similarity search across indexed observations."""
    engine = _get_semantic_engine()
    result = engine.search(
        query=body.query,
        limit=body.limit,
        score_threshold=body.score_threshold,
        investigation_id=body.investigation_id,
    )
    return {
        "query": result.query,
        "total_indexed": result.total_indexed,
        "search_latency_ms": result.search_latency_ms,
        "match_count": len(result.matches),
        "matches": [
            {
                "observation_id": m.observation_id,
                "investigation_id": m.investigation_id,
                "source": m.source,
                "claim": m.claim[:500],
                "url": m.url,
                "score": m.score,
                "captured_at": m.captured_at,
            }
            for m in result.matches
        ],
    }


@app.get("/semantic/health")
def semantic_health(
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Check health of embedding model and Qdrant."""
    engine = _get_semantic_engine()
    return {
        "embedding": engine.embedder.health(),
        "qdrant": engine.store.health(),
    }


# ── Auto Report Generation Endpoints ───────────────────────────────────


class GenerateReportRequest(BaseModel):
    investigation_id: str
    use_llm: bool = True


@app.post("/reports/generate")
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


@app.post("/reports/generate/markdown")
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


# ── Scheduled Research Agent ────────────────────────────────────────────


class ScheduleResearchRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=500)
    seed_query: str | None = None
    investigation_id: str | None = None
    interval_seconds: int = Field(default=3600, ge=300, le=86400)
    max_iterations_per_run: int = Field(default=3, ge=1, le=10)


@app.post("/research/agent/schedule")
def schedule_research_agent(
    body: ScheduleResearchRequest,
    session: Session = Depends(get_session),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Schedule recurring autonomous research runs."""
    schedule_name = f"research-agent:{body.topic[:60]}"

    # Check for existing schedule with same name
    existing = session.scalars(
        select(JobScheduleORM).where(JobScheduleORM.name == schedule_name)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Schedule '{schedule_name}' already exists (id={existing.id})")

    payload = {
        "topic": body.topic,
        "seed_query": body.seed_query,
        "investigation_id": body.investigation_id,
        "max_iterations": body.max_iterations_per_run,
    }
    schedule = create_schedule(
        session,
        name=schedule_name,
        job_type="run_research_agent",
        payload=payload,
        interval_seconds=body.interval_seconds,
        priority=60,
    )
    return {
        "schedule_id": schedule.id,
        "name": schedule.name,
        "interval_seconds": schedule.interval_seconds,
        "topic": body.topic,
        "active": schedule.active,
    }


@app.post("/research/agent/auto-investigate")
def auto_investigate_convergence(
    session: Session = Depends(get_session),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Auto-launch research agents for high/critical convergence zones that lack investigations."""
    zones = session.scalars(
        select(ConvergenceZoneORM)
        .where(ConvergenceZoneORM.severity.in_(["high", "critical"]))
        .where(ConvergenceZoneORM.resolved_at.is_(None))
        .where(ConvergenceZoneORM.investigation_id.is_(None))
    ).all()

    launched = []
    for zone in zones:
        signal_types = json.loads(zone.signal_types_json)
        topic = f"Convergence zone at ({zone.center_lat:.2f}, {zone.center_lon:.2f}): {', '.join(signal_types)}"
        seed_query = " ".join(signal_types[:3])

        # Create investigation
        inv_id = str(uuid4())
        investigation = InvestigationORM(
            id=inv_id,
            topic=topic[:300],
            seed_query=seed_query,
            priority=80 if zone.severity == "critical" else 70,
            status="open",
        )
        session.add(investigation)
        zone.investigation_id = inv_id

        # Enqueue research agent job
        job = enqueue_job(
            session,
            job_type="run_research_agent",
            payload={
                "topic": topic,
                "seed_query": seed_query,
                "investigation_id": inv_id,
            },
            priority=80 if zone.severity == "critical" else 70,
        )
        launched.append({
            "zone_id": zone.id,
            "severity": zone.severity,
            "investigation_id": inv_id,
            "job_id": job.id,
            "topic": topic,
        })

    session.commit()
    return {"launched": len(launched), "investigations": launched}


def _persist_geo_events(
    session: Session,
    geo_events: list[dict],
    investigation_id: str | None = None,
) -> int:
    """Persist geo_events from connector metadata into GeoEventORM rows."""
    count = 0
    for ge in geo_events:
        event_time = ge.get("event_time")
        if isinstance(event_time, str):
            try:
                event_time = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                event_time = datetime.now(UTC)
        elif not isinstance(event_time, datetime):
            event_time = datetime.now(UTC)

        row = GeoEventORM(
            layer=ge.get("layer", "unknown"),
            source_id=ge.get("source_id"),
            title=str(ge.get("title", ""))[:500],
            latitude=float(ge["latitude"]),
            longitude=float(ge["longitude"]),
            altitude=ge.get("altitude"),
            magnitude=ge.get("magnitude"),
            properties_json=json.dumps(ge.get("properties", {}), default=str),
            event_time=event_time,
            fetched_at=datetime.now(UTC),
            expires_at=ge.get("expires_at"),
            investigation_id=investigation_id,
        )
        session.add(row)
        count += 1
    return count


def _ingest_with_geo_connector(
    session: Session,
    investigation_id: str,
    connector_name: str,
    connector_obj: Any,
    query: str | None,
    auth: AuthContext | None = None,
) -> dict[str, Any]:
    """Generic helper to run a geo-enabled connector and persist results."""
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    result = connector_obj.collect(query=query or investigation.seed_query or investigation.topic)

    # Deduplicate: skip observations whose URL already exists for this investigation
    existing_urls: set[str] = set()
    if result.observations:
        rows = session.execute(
            select(ObservationORM.url)
            .where(ObservationORM.investigation_id == investigation_id)
            .where(ObservationORM.url.in_([o.url for o in result.observations]))
        ).all()
        existing_urls = {r[0] for r in rows}

    dedup_skipped = 0
    for obs in result.observations:
        if obs.url in existing_urls:
            dedup_skipped += 1
            continue
        existing_urls.add(obs.url)
        session.add(
            ObservationORM(
                investigation_id=investigation_id,
                source=obs.source,
                claim=obs.claim,
                url=obs.url,
                captured_at=obs.captured_at,
                reliability_hint=obs.reliability_hint,
                connector_type=connector_name,
            )
        )

    ingested_count = len(result.observations) - dedup_skipped
    geo_events = result.metadata.get("geo_events", [])
    geo_count = _persist_geo_events(session, geo_events, investigation_id=investigation_id)

    _record_ingestion_run(
        session=session,
        investigation_id=investigation_id,
        connector=connector_name,
        ingested_count=ingested_count,
        failed_count=dedup_skipped,
        status="success" if not result.metadata.get("error") else "partial",
        detail={k: v for k, v in result.metadata.items() if k != "geo_events"},
    )

    if auth:
        _record_audit(
            session, actor=auth, action=f"ingest.{connector_name}",
            resource_type="investigation", resource_id=investigation_id,
            detail={"ingested": ingested_count, "dedup_skipped": dedup_skipped, "geo_events": geo_count},
        )

    session.commit()

    # Auto-extract entities from ingested observations
    entity_count = 0
    try:
        # Re-query to get the ORM objects with IDs assigned
        recent_obs = session.scalars(
            select(ObservationORM)
            .where(
                ObservationORM.investigation_id == investigation_id,
                ObservationORM.connector_type == connector_name,
            )
            .order_by(ObservationORM.captured_at.desc())
            .limit(ingested_count)
        ).all()
        for obs_orm in recent_obs:
            entities = extract_entities(obs_orm.claim)
            for ent in entities:
                session.add(EntityMentionORM(
                    observation_id=obs_orm.id,
                    investigation_id=investigation_id,
                    entity_text=ent.text,
                    entity_type=ent.entity_type,
                    normalized=ent.normalized or ent.text,
                    confidence=ent.confidence,
                    start_pos=ent.start,
                    end_pos=ent.end,
                ))
                entity_count += 1
        if entity_count:
            session.commit()
    except Exception:
        pass  # Entity extraction is best-effort, don't fail ingestion

    # Publish SSE event
    event_bus.publish_sync(STSEvent(
        event_type="ingestion",
        payload={
            "connector": connector_name,
            "investigation_id": investigation_id,
            "ingested_count": ingested_count,
            "dedup_skipped": dedup_skipped,
            "geo_events_count": geo_count,
            "entities_extracted": entity_count,
        },
    ))

    return {
        "investigation_id": investigation_id,
        "connector": connector_name,
        "ingested_count": ingested_count,
        "dedup_skipped": dedup_skipped,
        "geo_events_count": geo_count,
        "entities_extracted": entity_count,
        "error": result.metadata.get("error"),
    }


# ── GDELT Connector Endpoint ──────────────────────────────────────────


@app.post("/investigations/{investigation_id}/ingest/gdelt")
def ingest_gdelt(
    investigation_id: str,
    payload: GDELTIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = GDELTConnector(
        timespan=payload.timespan,
        max_records=payload.max_records,
        mode=payload.mode,
        source_country=payload.source_country,
        source_lang=payload.source_lang,
        timeout_s=settings.gdelt_timeout_s,
    )
    return _ingest_with_geo_connector(
        session, investigation_id, "gdelt", connector, payload.query, auth,
    )


# ── USGS Earthquake Connector Endpoint ────────────────────────────────


@app.post("/investigations/{investigation_id}/ingest/usgs")
def ingest_usgs(
    investigation_id: str,
    payload: USGSIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = USGSEarthquakeConnector(
        min_magnitude=payload.min_magnitude,
        lookback_hours=payload.lookback_hours,
        max_events=payload.max_events,
        use_summary_feed=payload.summary_feed if payload.use_summary_feed else None,
        timeout_s=settings.usgs_timeout_s,
    )
    return _ingest_with_geo_connector(
        session, investigation_id, "usgs", connector, payload.query, auth,
    )


# ── NASA FIRMS Fire Connector Endpoint ────────────────────────────────


@app.post("/investigations/{investigation_id}/ingest/nasa-firms")
def ingest_nasa_firms(
    investigation_id: str,
    payload: NASAFIRMSIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if not settings.nasa_firms_map_key:
        raise HTTPException(status_code=503, detail="NASA FIRMS MAP_KEY not configured")
    connector = NASAFIRMSConnector(
        map_key=settings.nasa_firms_map_key,
        sensor=settings.nasa_firms_sensor,
        country_code=payload.country_code,
        days=payload.days,
        min_confidence=payload.min_confidence,
        timeout_s=settings.nasa_firms_timeout_s,
    )
    return _ingest_with_geo_connector(
        session, investigation_id, "nasa_firms", connector, payload.query, auth,
    )


# ── ACLED Conflict Connector Endpoint ─────────────────────────────────


@app.post("/investigations/{investigation_id}/ingest/acled")
def ingest_acled(
    investigation_id: str,
    payload: ACLEDIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if not settings.acled_api_key or not settings.acled_email:
        raise HTTPException(status_code=503, detail="ACLED API key/email not configured")
    connector = ACLEDConnector(
        api_key=settings.acled_api_key,
        email=settings.acled_email,
        lookback_days=payload.lookback_days,
        limit=payload.limit,
        country=payload.country,
        region=payload.region,
        event_type=payload.event_type,
        timeout_s=settings.acled_timeout_s,
    )
    return _ingest_with_geo_connector(
        session, investigation_id, "acled", connector, payload.query, auth,
    )


# ── NWS Weather Alerts Connector Endpoint ─────────────────────────────


@app.post("/investigations/{investigation_id}/ingest/nws")
def ingest_nws(
    investigation_id: str,
    payload: NWSIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = NWSAlertConnector(
        severity_filter=payload.severity_filter,
        status=payload.status,
        urgency=payload.urgency,
        area=payload.area,
        timeout_s=settings.nws_timeout_s,
    )
    return _ingest_with_geo_connector(
        session, investigation_id, "nws", connector, payload.query, auth,
    )


# ── FEMA Disaster Connector Endpoint ──────────────────────────────────


@app.post("/investigations/{investigation_id}/ingest/fema")
def ingest_fema(
    investigation_id: str,
    payload: FEMAIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = FEMADisasterConnector(
        lookback_days=payload.lookback_days,
        limit=payload.limit,
        state=payload.state,
        declaration_type=payload.declaration_type,
        timeout_s=settings.fema_timeout_s,
    )
    return _ingest_with_geo_connector(
        session, investigation_id, "fema", connector, payload.query, auth,
    )


# ── SSE Streaming Endpoint ────────────────────────────────────────────


@app.get("/events/stream")
async def sse_stream(request: Request, _: None = Depends(require_api_key)):
    """Server-Sent Events stream for real-time updates."""
    queue = event_bus.subscribe()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield event.to_sse()
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            event_bus.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Geo Events API ────────────────────────────────────────────────────


@app.get("/geo/events")
def list_geo_events(
    layer: str | None = None,
    hours: int = 24,
    limit: int = 500,
    investigation_id: str | None = None,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Get recent geo events, optionally filtered by layer."""
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, min(hours, 720)))
    q = select(GeoEventORM).where(GeoEventORM.event_time >= cutoff)
    if layer:
        q = q.where(GeoEventORM.layer == layer)
    if investigation_id:
        q = q.where(GeoEventORM.investigation_id == investigation_id)
    q = q.order_by(GeoEventORM.event_time.desc()).limit(max(1, min(limit, 5000)))

    rows = session.scalars(q).all()
    return {
        "count": len(rows),
        "cutoff": cutoff.isoformat(),
        "events": [
            {
                "id": r.id,
                "layer": r.layer,
                "source_id": r.source_id,
                "title": r.title,
                "latitude": r.latitude,
                "longitude": r.longitude,
                "altitude": r.altitude,
                "magnitude": r.magnitude,
                "properties": json.loads(r.properties_json),
                "event_time": r.event_time.isoformat(),
                "investigation_id": r.investigation_id,
            }
            for r in rows
        ],
    }


@app.get("/geo/layers")
def list_geo_layers(
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    """List available geo layers with event counts."""
    cutoff_24h = datetime.now(UTC) - timedelta(hours=24)
    rows = session.execute(
        select(GeoEventORM.layer, func.count(GeoEventORM.id))
        .where(GeoEventORM.event_time >= cutoff_24h)
        .group_by(GeoEventORM.layer)
    ).all()
    return [{"layer": layer, "event_count_24h": count} for layer, count in rows]


@app.get("/geo/convergence")
def detect_convergence_zones(
    hours: int = 24,
    radius_km: float = 50.0,
    min_signal_types: int = 3,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Detect convergence zones where multiple signal types cluster geographically."""
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, min(hours, 720)))
    rows = session.scalars(
        select(GeoEventORM).where(GeoEventORM.event_time >= cutoff)
    ).all()

    points = [
        GeoPoint(
            latitude=r.latitude,
            longitude=r.longitude,
            layer=r.layer,
            title=r.title,
            event_time=r.event_time,
            source_id=r.source_id or "",
        )
        for r in rows
    ]

    zones = detect_convergence(
        points,
        radius_km=radius_km,
        min_signal_types=min_signal_types,
        time_window_hours=hours,
    )

    # Persist detected zones
    for zone in zones:
        cz = ConvergenceZoneORM(
            center_lat=zone.center_lat,
            center_lon=zone.center_lon,
            radius_km=zone.radius_km,
            signal_count=zone.signal_count,
            signal_types_json=json.dumps(zone.signal_types),
            severity=zone.severity,
            first_detected_at=zone.first_detected_at,
            last_updated_at=zone.last_updated_at,
        )
        session.add(cz)

    if zones:
        session.commit()
        event_bus.publish_sync(STSEvent(
            event_type="convergence",
            payload={"zones_detected": len(zones), "radius_km": radius_km},
        ))

        # Auto-trigger alerts for high-severity convergence zones
        for zone in zones:
            if zone.severity in ("high", "critical"):
                alert_event = AlertEventORM(
                    rule_id=None,
                    investigation_id=None,
                    triggered_at=datetime.now(UTC),
                    severity=zone.severity,
                    message=(
                        f"Convergence zone detected: {zone.signal_count} signals from "
                        f"{len(zone.signal_types)} types at ({zone.center_lat:.3f}, {zone.center_lon:.3f})"
                    ),
                    detail_json=json.dumps({
                        "zone_center": [zone.center_lat, zone.center_lon],
                        "signal_count": zone.signal_count,
                        "signal_types": zone.signal_types,
                        "radius_km": zone.radius_km,
                    }),
                )
                session.add(alert_event)
        session.commit()

    return {
        "geo_events_analyzed": len(points),
        "zones": [
            {
                "center_lat": z.center_lat,
                "center_lon": z.center_lon,
                "radius_km": z.radius_km,
                "signal_count": z.signal_count,
                "signal_types": z.signal_types,
                "severity": z.severity,
                "first_detected_at": z.first_detected_at.isoformat(),
                "last_updated_at": z.last_updated_at.isoformat(),
                "event_count": len(z.events),
            }
            for z in zones
        ],
    }


# ── Enhanced Dashboard API ─────────────────────────────────────────────


@app.get("/dashboard/map-data")
def dashboard_map_data(
    hours: int = 24,
    layers: str | None = None,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """GeoJSON-like endpoint for the map dashboard."""
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, min(hours, 720)))
    q = select(GeoEventORM).where(GeoEventORM.event_time >= cutoff)
    if layers:
        layer_list = [l.strip() for l in layers.split(",") if l.strip()]
        if layer_list:
            q = q.where(GeoEventORM.layer.in_(layer_list))
    q = q.order_by(GeoEventORM.event_time.desc()).limit(2000)
    rows = session.scalars(q).all()

    features = []
    for r in rows:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r.longitude, r.latitude]},
            "properties": {
                "id": r.id,
                "layer": r.layer,
                "title": r.title,
                "magnitude": r.magnitude,
                "event_time": r.event_time.isoformat(),
                "source_id": r.source_id,
                **json.loads(r.properties_json),
            },
        })

    return {"type": "FeatureCollection", "features": features}


@app.get("/dashboard/timeline")
def dashboard_timeline(
    hours: int = 48,
    bucket_hours: int = 1,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Event counts bucketed by time for timeline visualization."""
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, min(hours, 720)))
    rows = session.scalars(
        select(GeoEventORM).where(GeoEventORM.event_time >= cutoff)
    ).all()

    buckets: dict[str, dict[str, int]] = {}
    for r in rows:
        bucket_key = r.event_time.replace(
            minute=0, second=0, microsecond=0,
            hour=(r.event_time.hour // bucket_hours) * bucket_hours,
        ).isoformat()
        if bucket_key not in buckets:
            buckets[bucket_key] = {}
        buckets[bucket_key][r.layer] = buckets[bucket_key].get(r.layer, 0) + 1

    return {
        "bucket_hours": bucket_hours,
        "cutoff": cutoff.isoformat(),
        "buckets": [
            {"time": k, "layers": v, "total": sum(v.values())}
            for k, v in sorted(buckets.items())
        ],
    }


@app.get("/dashboard/playback")
def dashboard_playback(
    hours: int = 48,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Temporal playback data: GeoJSON features with timestamps for time-slider animation.

    Returns events sorted chronologically with their timestamps,
    allowing the frontend to animate events appearing on the map over time.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, min(hours, 720)))
    rows = session.scalars(
        select(GeoEventORM).where(GeoEventORM.event_time >= cutoff)
        .order_by(GeoEventORM.event_time.asc())
    ).all()

    features = []
    time_bounds = {"min": None, "max": None}
    for r in rows:
        ts = r.event_time.isoformat()
        if time_bounds["min"] is None or ts < time_bounds["min"]:
            time_bounds["min"] = ts
        if time_bounds["max"] is None or ts > time_bounds["max"]:
            time_bounds["max"] = ts

        props = json.loads(r.properties_json) if r.properties_json else {}
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [r.longitude, r.latitude],
            },
            "properties": {
                "id": r.id,
                "layer": r.layer,
                "title": r.title,
                "magnitude": r.magnitude,
                "event_time": ts,
                "timestamp_ms": int(r.event_time.timestamp() * 1000),
                **{k: v for k, v in props.items() if isinstance(v, (str, int, float, bool))},
            },
        })

    # Compute hourly summary for the playback scrubber
    hourly: dict[str, int] = {}
    for r in rows:
        hour_key = r.event_time.replace(minute=0, second=0, microsecond=0).isoformat()
        hourly[hour_key] = hourly.get(hour_key, 0) + 1

    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "total_events": len(features),
            "hours": hours,
            "time_bounds": time_bounds,
            "hourly_counts": [{"time": k, "count": v} for k, v in sorted(hourly.items())],
        },
    }


@app.get("/dashboard/live")
def dashboard_live(
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Live dashboard data combining summary stats, recent events, and active zones."""
    now = datetime.now(UTC)
    cutoff_24h = now - timedelta(hours=24)

    # Counts
    investigation_count = session.scalar(select(func.count(InvestigationORM.id))) or 0
    observation_count = session.scalar(select(func.count(ObservationORM.id))) or 0
    geo_event_count = session.scalar(
        select(func.count(GeoEventORM.id)).where(GeoEventORM.event_time >= cutoff_24h)
    ) or 0

    # Recent geo events (return up to 500 for globe rendering)
    recent_geo = session.scalars(
        select(GeoEventORM).where(GeoEventORM.event_time >= cutoff_24h)
        .order_by(GeoEventORM.event_time.desc()).limit(500)
    ).all()

    # Active convergence zones
    active_zones = session.scalars(
        select(ConvergenceZoneORM).where(ConvergenceZoneORM.resolved_at.is_(None))
        .order_by(ConvergenceZoneORM.last_updated_at.desc()).limit(10)
    ).all()

    # Layer breakdown
    layer_counts = session.execute(
        select(GeoEventORM.layer, func.count(GeoEventORM.id))
        .where(GeoEventORM.event_time >= cutoff_24h)
        .group_by(GeoEventORM.layer)
    ).all()

    # Recent alerts
    recent_alerts = session.scalars(
        select(AlertEventORM).order_by(AlertEventORM.triggered_at.desc()).limit(10)
    ).all()

    return {
        "timestamp": now.isoformat(),
        "investigations": investigation_count,
        "observations_total": observation_count,
        "geo_events_24h": geo_event_count,
        "sse_subscribers": event_bus.subscriber_count,
        "layers": {layer: count for layer, count in layer_counts},
        "recent_geo_events": [
            {
                "id": r.id,
                "layer": r.layer,
                "title": r.title,
                "latitude": r.latitude,
                "longitude": r.longitude,
                "altitude": r.altitude,
                "magnitude": r.magnitude,
                "source_id": r.source_id,
                "event_time": r.event_time.isoformat(),
                "properties": json.loads(r.properties_json) if r.properties_json else {},
            }
            for r in recent_geo
        ],
        "convergence_zones": [
            {
                "id": z.id, "center_lat": z.center_lat, "center_lon": z.center_lon,
                "severity": z.severity, "signal_count": z.signal_count,
                "signal_types": json.loads(z.signal_types_json),
                "last_updated_at": z.last_updated_at.isoformat(),
            }
            for z in active_zones
        ],
        "recent_alerts": [
            {
                "id": a.id, "severity": a.severity, "message": a.message,
                "triggered_at": a.triggered_at.isoformat(),
            }
            for a in recent_alerts
        ],
    }


# ── Privacy Status Endpoint ─────────────────────────────────────────────


@app.get("/privacy/status")
def privacy_status(
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Return current privacy/anonymization configuration status."""
    config = PrivacyConfig()
    return get_privacy_status(config)


# ── ReliefWeb Humanitarian Connector Endpoint ──────────────────────────


@app.post("/investigations/{investigation_id}/ingest/reliefweb")
def ingest_reliefweb(
    investigation_id: str,
    payload: ReliefWebIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = ReliefWebConnector(
        lookback_days=payload.lookback_days,
        limit=payload.limit,
        country=payload.country,
        disaster_type=payload.disaster_type,
        content_format=payload.content_format,
        timeout_s=settings.reliefweb_timeout_s,
    )
    return _ingest_with_geo_connector(
        session, investigation_id, "reliefweb", connector, payload.query, auth,
    )


# ── OpenSky Aircraft Connector Endpoint ────────────────────────────────


@app.post("/investigations/{investigation_id}/ingest/opensky")
def ingest_opensky(
    investigation_id: str,
    payload: OpenSkyIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    bbox = None
    if all(v is not None for v in [payload.bbox_lamin, payload.bbox_lomin, payload.bbox_lamax, payload.bbox_lomax]):
        bbox = (payload.bbox_lamin, payload.bbox_lomin, payload.bbox_lamax, payload.bbox_lomax)
    connector = OpenSkyConnector(bbox=bbox, timeout_s=settings.opensky_timeout_s)
    return _ingest_with_geo_connector(
        session, investigation_id, "opensky", connector, payload.query, auth,
    )


# ── Webcam / Camera Endpoints ─────────────────────────────────────────


@app.post("/investigations/{investigation_id}/ingest/webcams")
def ingest_webcams(
    investigation_id: str,
    payload: WebcamIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = WebcamConnector(
        windy_api_key=settings.windy_api_key or None,
        regions=payload.regions,
        nearby_lat=payload.nearby_lat,
        nearby_lon=payload.nearby_lon,
        nearby_radius_km=payload.nearby_radius_km,
    )
    return _ingest_with_geo_connector(
        session, investigation_id, "webcams", connector, payload.query, auth,
    )


@app.get("/cameras/regions")
def api_list_camera_regions(
    _auth: AuthContext = Depends(require_api_key),
) -> list[dict[str, Any]]:
    """List available curated camera regions with counts."""
    return list_camera_regions()


@app.get("/cameras/nearby")
def api_get_cameras_nearby(
    lat: float,
    lon: float,
    radius_km: float = 100,
    _auth: AuthContext = Depends(require_api_key),
) -> list[dict[str, Any]]:
    """Find curated cameras near a coordinate."""
    return get_cameras_near(lat, lon, radius_km)


@app.get("/cameras/all")
def api_get_all_cameras(
    region: str | None = None,
    _auth: AuthContext = Depends(require_api_key),
) -> dict[str, Any]:
    """Get all curated cameras, optionally filtered by region."""
    if region:
        cameras = CURATED_CAMERAS.get(region, [])
        return {"region": region, "cameras": cameras, "count": len(cameras)}
    total = sum(len(c) for c in CURATED_CAMERAS.values())
    return {"regions": list(CURATED_CAMERAS.keys()), "cameras": CURATED_CAMERAS, "total": total}


# ── ADS-B Aircraft Connector Endpoint ──────────────────────────────────


@app.post("/investigations/{investigation_id}/ingest/adsb")
def ingest_adsb(
    investigation_id: str,
    payload: ADSBIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Ingest ADS-B aircraft tracking data (includes military flights)."""
    connector = ADSBExchangeConnector(
        center_lat=payload.lat,
        center_lon=payload.lon,
        radius_nm=payload.dist_nm,
        military_only=payload.military_only,
    )
    return _ingest_with_geo_connector(
        session, investigation_id, "adsb", connector, payload.query, auth,
    )


# ── Marine / AIS Vessel Connector Endpoint ─────────────────────────────


@app.post("/investigations/{investigation_id}/ingest/marine")
def ingest_marine(
    investigation_id: str,
    payload: MarineIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Ingest AIS marine vessel tracking data."""
    connector = MarineTrafficConnector(
        bbox=(
            payload.bbox_lat_min,
            payload.bbox_lon_min,
            payload.bbox_lat_max,
            payload.bbox_lon_max,
        ),
        vessel_types=payload.vessel_types,
    )
    return _ingest_with_geo_connector(
        session, investigation_id, "marine", connector, payload.query, auth,
    )


# ── Telegram Public Channel Connector Endpoint ─────────────────────────


@app.post("/investigations/{investigation_id}/ingest/telegram")
def ingest_telegram(
    investigation_id: str,
    payload: TelegramIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Scrape public Telegram channels for OSINT intelligence."""
    # TelegramConnector expects list[dict] with handle/name/category fields
    channel_dicts = (
        [{"handle": h, "name": h, "category": "custom"} for h in payload.channels]
        if payload.channels
        else None
    )
    connector = TelegramConnector(
        channels=channel_dicts,
        per_channel_limit=payload.max_posts_per_channel,
    )
    return _ingest_with_geo_connector(
        session, investigation_id, "telegram", connector, payload.query, auth,
    )


# ── Internet Archive / Wayback Machine Connector Endpoint ──────────────


@app.post("/investigations/{investigation_id}/ingest/archive")
def ingest_archive(
    investigation_id: str,
    payload: ArchiveIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Retrieve archived/cached versions of URLs from the Wayback Machine."""
    connector = InternetArchiveConnector(
        urls_to_check=payload.urls,
        search_query=payload.query,
        max_snapshots=payload.max_snapshots,
    )
    return _ingest_with_geo_connector(
        session, investigation_id, "archive", connector, payload.query, auth,
    )


# ── Entity Extraction Endpoint ─────────────────────────────────────────


@app.post("/investigations/{investigation_id}/extract-entities")
def extract_investigation_entities(
    investigation_id: str,
    limit: int = 500,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Extract entities from all observations in an investigation."""
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    observations = session.scalars(
        select(ObservationORM)
        .where(ObservationORM.investigation_id == investigation_id)
        .order_by(ObservationORM.captured_at.desc())
        .limit(max(1, min(limit, 2000)))
    ).all()

    total_extracted = 0
    entity_counts: dict[str, int] = {}

    for obs in observations:
        entities = extract_entities(obs.claim)
        for ent in entities:
            existing = session.scalars(
                select(EntityMentionORM).where(
                    EntityMentionORM.observation_id == obs.id,
                    EntityMentionORM.entity_text == ent.text,
                    EntityMentionORM.entity_type == ent.entity_type,
                ).limit(1)
            ).first()
            if existing:
                continue

            session.add(EntityMentionORM(
                observation_id=obs.id,
                investigation_id=investigation_id,
                entity_text=ent.text,
                entity_type=ent.entity_type,
                normalized=ent.normalized or ent.text,
                confidence=ent.confidence,
                start_pos=ent.start,
                end_pos=ent.end,
            ))
            total_extracted += 1
            key = ent.entity_type
            entity_counts[key] = entity_counts.get(key, 0) + 1

    session.commit()
    return {
        "investigation_id": investigation_id,
        "observations_processed": len(observations),
        "entities_extracted": total_extracted,
        "by_type": entity_counts,
    }


@app.get("/investigations/{investigation_id}/entities")
def list_investigation_entities(
    investigation_id: str,
    entity_type: str | None = None,
    min_confidence: float = 0.0,
    limit: int = 200,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """List extracted entities for an investigation with frequency counts."""
    q = select(
        EntityMentionORM.normalized,
        EntityMentionORM.entity_type,
        func.count(EntityMentionORM.id).label("mention_count"),
        func.avg(EntityMentionORM.confidence).label("avg_confidence"),
    ).where(
        EntityMentionORM.investigation_id == investigation_id,
        EntityMentionORM.confidence >= min_confidence,
    ).group_by(
        EntityMentionORM.normalized, EntityMentionORM.entity_type
    )

    if entity_type:
        q = q.where(EntityMentionORM.entity_type == entity_type)

    q = q.order_by(func.count(EntityMentionORM.id).desc()).limit(max(1, min(limit, 1000)))
    rows = session.execute(q).all()

    return {
        "investigation_id": investigation_id,
        "entities": [
            {
                "text": row.normalized,
                "type": row.entity_type,
                "mention_count": row.mention_count,
                "avg_confidence": round(float(row.avg_confidence), 3),
            }
            for row in rows
        ],
    }


# ── Story Clustering Endpoint ──────────────────────────────────────────


@app.post("/investigations/{investigation_id}/cluster-stories")
def cluster_investigation_stories(
    investigation_id: str,
    hours: int = 48,
    min_cluster_size: int = 2,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Cluster observations into stories for an investigation."""
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    observations = session.scalars(
        select(ObservationORM)
        .where(ObservationORM.investigation_id == investigation_id)
        .where(ObservationORM.captured_at >= cutoff)
        .order_by(ObservationORM.captured_at.desc())
        .limit(2000)
    ).all()

    obs_refs = [
        ObservationRef(
            id=o.id, source=o.source, claim=o.claim, url=o.url,
            captured_at=o.captured_at, reliability_hint=o.reliability_hint,
            connector_type=o.connector_type, investigation_id=investigation_id,
        )
        for o in observations
    ]

    stories = cluster_observations(
        obs_refs,
        time_window_hours=hours,
        min_cluster_size=min_cluster_size,
    )

    # Enrich with entities
    enrich_stories_with_entities(stories, extract_entities)

    # Persist stories
    persisted = 0
    for story in stories:
        row = StoryORM(
            investigation_id=investigation_id,
            headline=story.headline[:500],
            key_terms_json=json.dumps(story.key_terms),
            entities_json=json.dumps(story.entities),
            source_count=story.source_count,
            observation_count=story.observation_count,
            avg_reliability=story.avg_reliability,
            trending_score=story.trending_score,
            first_seen=story.first_seen,
            last_seen=story.last_seen,
        )
        session.add(row)
        session.flush()

        for obs in story.observations:
            session.add(StoryObservationORM(story_id=row.id, observation_id=obs.id))
        persisted += 1

    session.commit()

    return {
        "investigation_id": investigation_id,
        "observations_analyzed": len(observations),
        "stories_found": len(stories),
        "stories": [
            {
                "headline": s.headline[:200],
                "key_terms": s.key_terms,
                "entities": s.entities[:10],
                "sources": s.sources,
                "observation_count": s.observation_count,
                "trending_score": s.trending_score,
                "first_seen": s.first_seen.isoformat(),
                "last_seen": s.last_seen.isoformat(),
            }
            for s in stories
        ],
    }


@app.get("/investigations/{investigation_id}/stories")
def list_investigation_stories(
    investigation_id: str,
    limit: int = 50,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(StoryORM)
        .where(StoryORM.investigation_id == investigation_id)
        .order_by(StoryORM.trending_score.desc())
        .limit(max(1, min(limit, 200)))
    ).all()
    return [
        {
            "id": r.id,
            "headline": r.headline,
            "key_terms": json.loads(r.key_terms_json),
            "entities": json.loads(r.entities_json),
            "source_count": r.source_count,
            "observation_count": r.observation_count,
            "trending_score": r.trending_score,
            "first_seen": r.first_seen.isoformat(),
            "last_seen": r.last_seen.isoformat(),
        }
        for r in rows
    ]


# ── Story Discovery Endpoint ──────────────────────────────────────────


@app.post("/discovery/run")
def run_story_discovery(
    hours: int = 24,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Run automated story discovery across all recent observations."""
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, min(hours, 168)))

    observations = session.scalars(
        select(ObservationORM)
        .where(ObservationORM.captured_at >= cutoff)
        .order_by(ObservationORM.captured_at.desc())
        .limit(5000)
    ).all()

    snapshots = [
        ObservationSnapshot(
            claim=o.claim, source=o.source,
            captured_at=o.captured_at, url=o.url,
            reliability_hint=o.reliability_hint,
        )
        for o in observations
    ]

    # Get convergence zones for cross-referencing
    zones = session.scalars(
        select(ConvergenceZoneORM)
        .where(ConvergenceZoneORM.resolved_at.is_(None))
        .order_by(ConvergenceZoneORM.last_updated_at.desc())
        .limit(20)
    ).all()

    zone_dicts = [
        {
            "signal_types": json.loads(z.signal_types_json),
            "severity": z.severity,
            "center_lat": z.center_lat,
            "center_lon": z.center_lon,
            "radius_km": z.radius_km,
        }
        for z in zones
    ]

    topics = run_discovery(snapshots, convergence_zones=zone_dicts)

    # Persist discovered topics
    for topic in topics:
        session.add(DiscoveredTopicORM(
            title=topic.title[:500],
            description=topic.description[:2000],
            score=topic.score,
            source=topic.source,
            key_terms_json=json.dumps(topic.key_terms),
            entities_json=json.dumps(topic.entities),
            sample_urls_json=json.dumps(topic.sample_urls),
            suggested_seed_query=topic.suggested_seed_query[:500],
            suggested_connectors_json=json.dumps(topic.suggested_connectors),
            status="new",
        ))

    session.commit()

    event_bus.publish_sync(STSEvent(
        event_type="discovery",
        payload={"topics_found": len(topics)},
    ))

    return {
        "observations_analyzed": len(observations),
        "topics_discovered": len(topics),
        "topics": [
            {
                "title": t.title,
                "description": t.description[:200],
                "score": t.score,
                "source": t.source,
                "key_terms": t.key_terms,
                "entities": t.entities[:5],
                "suggested_seed_query": t.suggested_seed_query,
                "suggested_connectors": t.suggested_connectors,
            }
            for t in topics
        ],
    }


@app.get("/discovery/topics")
def list_discovered_topics(
    status: str | None = None,
    limit: int = 50,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    q = select(DiscoveredTopicORM)
    if status:
        q = q.where(DiscoveredTopicORM.status == status)
    q = q.order_by(DiscoveredTopicORM.score.desc()).limit(max(1, min(limit, 200)))
    rows = session.scalars(q).all()
    return [
        {
            "id": r.id,
            "title": r.title,
            "description": r.description[:200],
            "score": r.score,
            "source": r.source,
            "key_terms": json.loads(r.key_terms_json),
            "suggested_seed_query": r.suggested_seed_query,
            "suggested_connectors": json.loads(r.suggested_connectors_json),
            "status": r.status,
            "discovered_at": r.discovered_at.isoformat(),
        }
        for r in rows
    ]


@app.post("/discovery/topics/{topic_id}/promote")
def promote_discovered_topic(
    topic_id: int,
    payload: PromoteTopicRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Promote a discovered topic into a full investigation with auto-generated collection plan."""
    topic = session.get(DiscoveredTopicORM, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Discovered topic not found")

    # Create investigation
    inv_id = str(uuid4())
    investigation = InvestigationORM(
        id=inv_id,
        topic=topic.title[:300],
        seed_query=topic.suggested_seed_query or topic.title[:200],
        priority=payload.priority,
        owner=payload.owner,
        status="open",
    )
    session.add(investigation)

    # Auto-generate collection plan
    connectors = json.loads(topic.suggested_connectors_json) or ["gdelt", "rss"]
    plan = CollectionPlanORM(
        investigation_id=inv_id,
        name=f"Auto: {topic.title[:150]}",
        description=topic.description[:500],
        connectors_json=json.dumps(connectors),
        query=topic.suggested_seed_query or topic.title[:200],
        priority=payload.priority,
        interval_seconds=3600,
    )
    session.add(plan)

    # Update topic status
    topic.status = "promoted"
    topic.promoted_investigation_id = inv_id

    _record_audit(
        session, actor=auth, action="discovery.promote",
        resource_type="discovered_topic", resource_id=str(topic_id),
        detail={"investigation_id": inv_id},
    )
    session.commit()

    return {
        "investigation_id": inv_id,
        "topic_id": topic_id,
        "collection_plan_id": plan.id,
        "connectors": connectors,
    }


@app.post("/discovery/topics/{topic_id}/dismiss")
def dismiss_discovered_topic(
    topic_id: int,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    topic = session.get(DiscoveredTopicORM, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Discovered topic not found")
    topic.status = "dismissed"
    session.commit()
    return {"topic_id": topic_id, "status": "dismissed"}


# ── Collection Plan Endpoints ──────────────────────────────────────────


@app.post("/collection-plans")
def create_collection_plan(
    payload: CollectionPlanCreateRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Create a collection plan for an investigation, optionally auto-generating from topic."""
    investigation = session.get(InvestigationORM, payload.investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    if payload.auto_generate:
        requirements = build_collection_plan(
            investigation.topic,
            seed_query=investigation.seed_query,
            priority=payload.priority,
        )
        plans = []
        for req in requirements:
            plan = CollectionPlanORM(
                investigation_id=payload.investigation_id,
                name=req.name[:200],
                description=req.description[:500],
                connectors_json=json.dumps(req.connectors),
                query=req.query[:500],
                priority=req.priority,
                interval_seconds=req.interval_seconds,
                filters_json=json.dumps(req.filters, default=str),
            )
            session.add(plan)
            plans.append(plan)
        session.commit()
        return {
            "investigation_id": payload.investigation_id,
            "plans_created": len(plans),
            "plans": [{"id": p.id, "name": p.name, "connectors": json.loads(p.connectors_json)} for p in plans],
        }

    plan = CollectionPlanORM(
        investigation_id=payload.investigation_id,
        name=payload.name,
        connectors_json=json.dumps(payload.connectors),
        query=payload.query,
        priority=payload.priority,
        interval_seconds=payload.interval_seconds,
        filters_json=json.dumps(payload.filters, default=str),
    )
    session.add(plan)
    _record_audit(session, actor=auth, action="collection_plan.create",
                  resource_type="collection_plan", resource_id=payload.investigation_id)
    session.commit()
    return {"id": plan.id, "name": plan.name, "connectors": payload.connectors}


@app.get("/collection-plans")
def list_collection_plans(
    investigation_id: str | None = None,
    active_only: bool = True,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    q = select(CollectionPlanORM)
    if investigation_id:
        q = q.where(CollectionPlanORM.investigation_id == investigation_id)
    if active_only:
        q = q.where(CollectionPlanORM.active.is_(True))
    q = q.order_by(CollectionPlanORM.priority.desc())
    rows = session.scalars(q).all()
    return [
        {
            "id": r.id,
            "investigation_id": r.investigation_id,
            "name": r.name,
            "connectors": json.loads(r.connectors_json),
            "query": r.query,
            "priority": r.priority,
            "interval_seconds": r.interval_seconds,
            "active": r.active,
            "last_collected_at": r.last_collected_at.isoformat() if r.last_collected_at else None,
            "total_collected": r.total_collected,
        }
        for r in rows
    ]


@app.post("/collection-plans/{plan_id}/execute")
def execute_collection_plan(
    plan_id: int,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Execute a collection plan — runs all configured connectors for the plan's query."""
    plan = session.get(CollectionPlanORM, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Collection plan not found")
    if not plan.active:
        raise HTTPException(status_code=400, detail="Collection plan is not active")

    connectors_list = json.loads(plan.connectors_json)
    results: dict[str, Any] = {}
    total_ingested = 0

    connector_map: dict[str, Any] = {
        "gdelt": lambda: GDELTConnector(),
        "usgs": lambda: USGSEarthquakeConnector(),
        "nasa_firms": lambda: NASAFIRMSConnector(map_key=settings.nasa_firms_map_key or None),
        "acled": lambda: ACLEDConnector(api_key=settings.acled_api_key or None, email=settings.acled_email or None),
        "nws": lambda: NWSAlertConnector(),
        "fema": lambda: FEMADisasterConnector(),
        "reliefweb": lambda: ReliefWebConnector(),
        "opensky": lambda: OpenSkyConnector(),
        "webcams": lambda: WebcamConnector(windy_api_key=settings.windy_api_key or None),
    }

    for connector_name in connectors_list:
        factory = connector_map.get(connector_name)
        if not factory:
            results[connector_name] = {"error": f"Unknown connector: {connector_name}"}
            continue
        try:
            connector_obj = factory()
            result = _ingest_with_geo_connector(
                session, plan.investigation_id, connector_name, connector_obj, plan.query, auth,
            )
            results[connector_name] = result
            total_ingested += result.get("ingested_count", 0)
        except Exception as exc:
            results[connector_name] = {"error": str(exc)}

    # Update plan stats
    plan.last_collected_at = datetime.now(UTC)
    plan.total_collected = (plan.total_collected or 0) + total_ingested
    session.commit()

    return {
        "plan_id": plan_id,
        "connectors_executed": len(connectors_list),
        "total_ingested": total_ingested,
        "results": results,
    }


# ── Curated Feed Library ──────────────────────────────────────────────


@app.get("/feeds/categories")
def list_feed_categories_endpoint(
    _: None = Depends(require_api_key),
) -> list[dict[str, Any]]:
    """List curated RSS feed categories with available feeds."""
    return list_feed_categories()


@app.get("/feeds/by-category")
def get_feeds_by_category(
    categories: str | None = None,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Get curated feeds, optionally filtered by comma-separated categories."""
    cat_list = [c.strip() for c in categories.split(",") if c.strip()] if categories else None
    feeds = get_curated_feeds(cat_list)
    return {"count": len(feeds), "feeds": feeds}


@app.post("/research/sources")
def create_research_source(
    payload: ResearchSourceCreateRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    existing = session.scalars(select(ResearchSourceORM).where(ResearchSourceORM.name == payload.name)).first()
    if existing:
        raise HTTPException(status_code=409, detail="Source with this name already exists")

    row = ResearchSourceORM(
        name=payload.name,
        source_type=payload.source_type,
        base_url=payload.base_url,
        trust_score=payload.trust_score,
        active=True,
        tags_json=json.dumps(payload.tags),
        created_at=datetime.now(UTC),
    )
    session.add(row)
    _record_audit(session, actor=auth, action="research_source.create", resource_type="research_source", resource_id=str(row.name), detail={"source_type": row.source_type})
    session.commit()
    return {"id": row.id, "name": row.name, "source_type": row.source_type, "trust_score": row.trust_score}


@app.get("/research/sources")
def list_research_sources(
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    _seed_default_research_sources(session)
    session.commit()
    rows = session.scalars(select(ResearchSourceORM).order_by(ResearchSourceORM.created_at.desc())).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "source_type": row.source_type,
            "base_url": row.base_url,
            "trust_score": row.trust_score,
            "active": row.active,
            "tags": json.loads(row.tags_json),
        }
        for row in rows
    ]


@app.post("/search/profiles")
def create_search_profile(
    payload: SearchProfileCreateRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if payload.investigation_id and not session.get(InvestigationORM, payload.investigation_id):
        raise HTTPException(status_code=404, detail="Investigation not found")

    existing = session.scalars(select(SearchProfileORM).where(SearchProfileORM.name == payload.name)).first()
    if existing:
        raise HTTPException(status_code=409, detail="Search profile with this name already exists")

    profile = SearchProfileORM(
        name=payload.name.strip(),
        investigation_id=payload.investigation_id,
        include_terms_json=json.dumps(sorted({item.strip().lower() for item in payload.include_terms if item.strip()})),
        exclude_terms_json=json.dumps(sorted({item.strip().lower() for item in payload.exclude_terms if item.strip()})),
        synonyms_json=json.dumps(payload.synonyms),
        created_at=now_utc(),
    )
    session.add(profile)
    _record_audit(session, actor=auth, action="search_profile.create", resource_type="search_profile", resource_id=payload.name, detail={"investigation_id": payload.investigation_id})
    session.commit()

    return {"id": profile.id, "name": profile.name, "investigation_id": profile.investigation_id}


@app.get("/search/profiles")
def list_search_profiles(
    investigation_id: str | None = None,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    query = select(SearchProfileORM).order_by(SearchProfileORM.created_at.desc())
    if investigation_id:
        query = query.where(SearchProfileORM.investigation_id == investigation_id)

    rows = session.scalars(query.limit(200)).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "investigation_id": row.investigation_id,
            "include_terms": json.loads(row.include_terms_json),
            "exclude_terms": json.loads(row.exclude_terms_json),
            "synonyms": json.loads(row.synonyms_json),
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@app.post("/search/query")
def search_query(
    payload: SearchQueryRequest,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    profile: SearchProfileORM | None = None
    if payload.profile_name:
        profile = session.scalars(select(SearchProfileORM).where(SearchProfileORM.name == payload.profile_name)).first()
        if not profile:
            raise HTTPException(status_code=404, detail="Search profile not found")

    synonyms: dict[str, list[str]] = {}
    if profile:
        loaded = json.loads(profile.synonyms_json)
        if isinstance(loaded, dict):
            synonyms = {str(k): [str(v) for v in vals] for k, vals in loaded.items() if isinstance(vals, list)}

    plan = build_query_plan(payload.query, extra_synonyms=synonyms)
    if profile:
        for term in json.loads(profile.include_terms_json):
            plan.include_terms.add(str(term).lower())
        for term in json.loads(profile.exclude_terms_json):
            plan.exclude_terms.add(str(term).lower())

    research_sources = session.scalars(select(ResearchSourceORM).where(ResearchSourceORM.active.is_(True))).all()

    def source_trust_for(*, source: str, url: str | None, fallback: float) -> float:
        candidates = [source.lower()]
        if url:
            candidates.append(url.lower())
        for row in research_sources:
            marker = row.base_url.lower()
            if any(marker in item for item in candidates):
                return float(max(0.0, min(1.0, row.trust_score)))
        return float(max(0.0, min(1.0, fallback)))

    results: list[dict[str, Any]] = []

    if payload.include_observations:
        obs_query = select(ObservationORM)
        if payload.investigation_id:
            obs_query = obs_query.where(ObservationORM.investigation_id == payload.investigation_id)
        if payload.source_prefix:
            obs_query = obs_query.where(ObservationORM.source.like(f"{payload.source_prefix}%"))
        if payload.min_reliability > 0:
            obs_query = obs_query.where(ObservationORM.reliability_hint >= payload.min_reliability)
        if payload.since:
            obs_query = obs_query.where(ObservationORM.captured_at >= payload.since)
        if payload.until:
            obs_query = obs_query.where(ObservationORM.captured_at <= payload.until)

        observations = session.scalars(obs_query.order_by(ObservationORM.captured_at.desc()).limit(max(payload.limit * 4, 200))).all()
        for row in observations:
            lexical = score_text(text=row.claim, plan=plan, base_reliability=row.reliability_hint)
            if lexical <= 0:
                continue
            trust = source_trust_for(source=row.source, url=row.url, fallback=row.reliability_hint)
            score = apply_context_boosts(score=lexical, captured_at=row.captured_at, source_trust=trust)
            if score < payload.min_score:
                continue
            results.append(
                {
                    "kind": "observation",
                    "score": score,
                    "investigation_id": row.investigation_id,
                    "source": row.source,
                    "captured_at": normalize_datetime(row.captured_at).isoformat(),
                    "id": row.id,
                    "text": row.claim,
                    "url": row.url,
                    "reliability": row.reliability_hint,
                    "source_trust": trust,
                    "matched_terms": top_terms(row.claim),
                }
            )

    if payload.include_claims:
        claim_query = select(ClaimORM)
        if payload.investigation_id:
            claim_query = claim_query.where(ClaimORM.investigation_id == payload.investigation_id)
        if payload.stance:
            claim_query = claim_query.where(ClaimORM.stance == payload.stance)
        if payload.since:
            claim_query = claim_query.where(ClaimORM.created_at >= payload.since)
        if payload.until:
            claim_query = claim_query.where(ClaimORM.created_at <= payload.until)

        claims = session.scalars(claim_query.order_by(ClaimORM.created_at.desc()).limit(max(payload.limit * 4, 200))).all()
        for row in claims:
            lexical = score_text(text=row.claim_text, plan=plan, base_reliability=row.confidence)
            if lexical <= 0:
                continue
            trust = source_trust_for(source=f"claim:{row.stance}", url=None, fallback=row.confidence)
            score = apply_context_boosts(score=lexical, captured_at=row.created_at, source_trust=trust)
            if score < payload.min_score:
                continue
            results.append(
                {
                    "kind": "claim",
                    "score": score,
                    "investigation_id": row.investigation_id,
                    "source": f"claim:{row.stance}",
                    "captured_at": normalize_datetime(row.created_at).isoformat(),
                    "id": row.id,
                    "text": row.claim_text,
                    "url": None,
                    "reliability": row.confidence,
                    "source_trust": trust,
                    "stance": row.stance,
                    "matched_terms": top_terms(row.claim_text),
                }
            )

    results.sort(key=lambda item: item["score"], reverse=True)
    results = results[: payload.limit]

    source_facets: dict[str, int] = {}
    investigation_facets: dict[str, int] = {}
    kind_facets: dict[str, int] = {}
    for item in results:
        source_family = item["source"].split(":", 1)[0]
        source_facets[source_family] = source_facets.get(source_family, 0) + 1
        investigation_facets[item["investigation_id"]] = investigation_facets.get(item["investigation_id"], 0) + 1
        kind_facets[item["kind"]] = kind_facets.get(item["kind"], 0) + 1

    return {
        "query": payload.query,
        "profile_name": payload.profile_name,
        "matched": len(results),
        "results": results,
        "facets": {
            "source_family": source_facets,
            "investigation": investigation_facets,
            "kind": kind_facets,
        },
    }


@app.get("/search/suggest")
def suggest_search_terms(
    q: str,
    investigation_id: str | None = None,
    limit: int = 15,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    plan = build_query_plan(q)
    obs_query = select(ObservationORM)
    if investigation_id:
        obs_query = obs_query.where(ObservationORM.investigation_id == investigation_id)

    rows = session.scalars(obs_query.order_by(ObservationORM.captured_at.desc()).limit(400)).all()
    scored: list[tuple[float, str]] = []
    for row in rows:
        score = score_text(text=row.claim, plan=plan, base_reliability=row.reliability_hint)
        if score <= 0:
            continue
        for term in top_terms(row.claim, max_terms=6):
            if term in plan.exclude_terms:
                continue
            scored.append((score, term))

    scored.sort(reverse=True)
    suggestions: list[str] = []
    for _, term in scored:
        if term in suggestions:
            continue
        suggestions.append(term)
        if len(suggestions) >= max(1, min(limit, 50)):
            break

    return {"query": q, "suggestions": suggestions, "count": len(suggestions)}


@app.post("/search/related-investigations")
def related_investigations(
    payload: RelatedInvestigationsRequest,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    plan = build_query_plan(payload.query)

    investigations = session.scalars(select(InvestigationORM)).all()
    rows: list[dict[str, Any]] = []

    for inv in investigations:
        obs = session.scalars(
            select(ObservationORM)
            .where(ObservationORM.investigation_id == inv.id)
            .order_by(ObservationORM.captured_at.desc())
            .limit(300)
        ).all()
        claims = session.scalars(
            select(ClaimORM)
            .where(ClaimORM.investigation_id == inv.id)
            .order_by(ClaimORM.created_at.desc())
            .limit(300)
        ).all()

        scores: list[float] = []
        terms: list[str] = []

        for row in obs:
            lexical = score_text(text=row.claim, plan=plan, base_reliability=row.reliability_hint)
            if lexical <= 0:
                continue
            score = apply_context_boosts(score=lexical, captured_at=row.captured_at, source_trust=row.reliability_hint)
            if score < payload.min_score:
                continue
            scores.append(score)
            terms.extend(top_terms(row.claim, max_terms=4))

        for row in claims:
            lexical = score_text(text=row.claim_text, plan=plan, base_reliability=row.confidence)
            if lexical <= 0:
                continue
            score = apply_context_boosts(score=lexical, captured_at=row.created_at, source_trust=row.confidence)
            if score < payload.min_score:
                continue
            scores.append(score)
            terms.extend(top_terms(row.claim_text, max_terms=4))

        if not scores:
            continue

        unique_terms: list[str] = []
        for term in terms:
            if term in unique_terms:
                continue
            unique_terms.append(term)
            if len(unique_terms) >= 8:
                break

        rows.append(
            {
                "investigation_id": inv.id,
                "topic": inv.topic,
                "match_count": len(scores),
                "max_score": round(max(scores), 4),
                "avg_score": round(sum(scores) / len(scores), 4),
                "top_terms": unique_terms,
            }
        )

    rows.sort(key=lambda item: (item["max_score"], item["avg_score"], item["match_count"]), reverse=True)
    rows = rows[: payload.limit]

    return {"query": payload.query, "count": len(rows), "investigations": rows}


@app.post("/investigations/{investigation_id}/discovery")
def discovery_summary(
    investigation_id: str,
    payload: DiscoveryRequest | None = None,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    rows = session.scalars(
        select(ObservationORM)
        .where(ObservationORM.investigation_id == investigation_id)
        .order_by(ObservationORM.captured_at.desc())
        .limit(500)
    ).all()
    observations = [
        Observation(
            source=item.source,
            claim=item.claim,
            url=item.url,
            captured_at=item.captured_at,
            reliability_hint=item.reliability_hint,
        )
        for item in rows
    ]
    summary = build_discovery_summary(observations)

    llm_brief: str | None = None
    if payload and payload.use_llm and observations:
        prompt = (
            f"Topic: {investigation.topic}\n"
            f"Top terms: {summary.top_terms}\n"
            f"Source breakdown: {summary.source_breakdown}\n"
            "Write a concise discovery brief with what to monitor next."
        )
        try:
            llm_brief = llm_client.summarize(prompt)
        except Exception as exc:
            llm_brief = f"LLM unavailable: {exc}"

    return {
        "investigation_id": investigation_id,
        "observation_count": len(observations),
        "top_terms": summary.top_terms,
        "source_breakdown": summary.source_breakdown,
        "sample_claims": summary.sample_claims,
        "llm_brief": llm_brief,
    }


@app.post("/alerts/rules")
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
    _record_audit(session, actor=auth, action="alert_rule.create", resource_type="investigation", resource_id=payload.investigation_id, detail={"rule": payload.name})
    session.commit()
    return {"id": rule.id, "name": rule.name, "active": rule.active}


@app.get("/alerts/rules")
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


@app.post("/alerts/evaluate/{investigation_id}")
def evaluate_alerts(
    investigation_id: str,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    emitted = _evaluate_alert_rules(session=session, investigation_id=investigation_id)
    _record_audit(session, actor=auth, action="alerts.evaluate", resource_type="investigation", resource_id=investigation_id, detail={"triggered": len(emitted)})
    session.commit()
    return {"investigation_id": investigation_id, "triggered": len(emitted), "events": emitted}


@app.get("/alerts/events/{investigation_id}")
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
        select(IngestionRunORM).where(IngestionRunORM.investigation_id == investigation_id).order_by(IngestionRunORM.started_at.desc()).limit(50)
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
    source: str | None = None,
    min_reliability: float | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 200,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    query = select(ObservationORM).where(ObservationORM.investigation_id == investigation_id)
    if source:
        query = query.where(ObservationORM.source == source)
    if min_reliability is not None:
        query = query.where(ObservationORM.reliability_hint >= max(0.0, min(1.0, min_reliability)))
    if since:
        query = query.where(ObservationORM.captured_at >= since)
    if until:
        query = query.where(ObservationORM.captured_at <= until)

    observations = session.scalars(query.order_by(ObservationORM.captured_at.desc()).limit(max(1, min(limit, 1000)))).all()

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
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    db_observations = session.scalars(select(ObservationORM).where(ObservationORM.investigation_id == investigation_id)).all()
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
    llm_structured_payload: dict[str, Any] | None = None
    llm_schema_valid = False
    llm_schema_error: str | None = None
    should_use_llm = bool(payload and payload.use_llm)
    llm_fallback_used = False
    if should_use_llm:
        prompt = _build_report_text(investigation.topic, result.summary, result.confidence, result.disputed_claims)
        try:
            llm_raw = llm_client.summarize(prompt)
            llm_schema_valid, llm_structured_payload, llm_schema_error = _parse_llm_structured_summary(llm_raw)
            if llm_schema_valid and llm_structured_payload:
                llm_summary = str(llm_structured_payload.get("overall_assessment", "")).strip() or result.summary
            else:
                llm_fallback_used = True
                llm_summary = f"LLM output invalid schema, fallback to deterministic summary: {llm_schema_error}"
        except Exception as exc:
            llm_fallback_used = True
            llm_schema_error = str(exc)
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

    report_sections = _build_report_sections(
        topic=investigation.topic,
        accepted=accepted,
        dropped=dropped,
        disputed_claims=result.disputed_claims,
    )
    _persist_claim_lineage(
        session=session,
        investigation_id=investigation_id,
        report_id=report.id,
        report_sections=report_sections,
        observations=db_observations,
    )
    _record_audit(session, actor=auth, action="pipeline.run", resource_type="investigation", resource_id=investigation_id, detail={"report_id": report.id})
    session.commit()

    lineage_validation = _compute_report_lineage_validation(session, report.id)
    gate_enforced = settings.enforce_report_lineage_gate
    gate_passed = lineage_validation["coverage"] >= settings.report_min_lineage_coverage
    if gate_enforced and not gate_passed:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Report lineage coverage {lineage_validation['coverage']} below required "
                f"threshold {settings.report_min_lineage_coverage}"
            ),
        )

    return {
        "investigation_id": investigation_id,
        "generated_at": report.generated_at.isoformat(),
        "summary": report.summary,
        "confidence": report.confidence,
        "accepted": accepted,
        "dropped": dropped,
        "disputed_claims": result.disputed_claims,
        "report_sections": report_sections,
        "deduplicated_count": len(result.deduplicated),
        "llm_fallback_used": llm_fallback_used,
        "llm_schema_valid": llm_schema_valid,
        "llm_schema_error": llm_schema_error,
        "llm_structured": llm_structured_payload,
        "lineage_validation": lineage_validation,
        "lineage_gate": {"enforced": gate_enforced, "passed": gate_passed, "min_coverage": settings.report_min_lineage_coverage},
    }


@app.post("/investigations/{investigation_id}/feedback")
def submit_feedback(
    investigation_id: str,
    payload: FeedbackRequest,
    auth: AuthContext = Depends(require_analyst),
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
    _record_audit(session, actor=auth, action="feedback.submit", resource_type="investigation", resource_id=investigation_id, detail={"label": payload.label})
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

    report = session.scalars(select(ReportORM).where(ReportORM.investigation_id == investigation_id).order_by(ReportORM.generated_at.desc()).limit(1)).first()
    if not report:
        raise HTTPException(status_code=404, detail="No report available")

    accepted = json.loads(report.accepted_json)
    dropped = json.loads(report.dropped_json)
    # Reports from /reports/generate store a dict (full report), not a list of observations
    if isinstance(accepted, dict):
        report_sections = {
            "likely_true": accepted.get("key_findings", [])[:5],
            "disputed": [],
            "unknown": [],
            "monitor_next": accepted.get("recommendations", [])[:5],
        }
    else:
        report_sections = _build_report_sections(
            topic=investigation.topic,
            accepted=accepted,
            dropped=dropped if isinstance(dropped, list) else [],
            disputed_claims=[],
        )
    lineage_validation = _compute_report_lineage_validation(session, report.id)
    gate_enforced = settings.enforce_report_lineage_gate
    gate_passed = lineage_validation["coverage"] >= settings.report_min_lineage_coverage
    if gate_enforced and not gate_passed:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Report lineage coverage {lineage_validation['coverage']} below required "
                f"threshold {settings.report_min_lineage_coverage}"
            ),
        )

    return {
        "investigation_id": investigation_id,
        "generated_at": report.generated_at.isoformat(),
        "summary": report.summary,
        "confidence": report.confidence,
        "accepted": accepted,
        "dropped": dropped,
        "report_sections": report_sections,
        "lineage_validation": lineage_validation,
        "lineage_gate": {"enforced": gate_enforced, "passed": gate_passed, "min_coverage": settings.report_min_lineage_coverage},
    }


@app.get("/reports/{investigation_id}/validation")
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
    validation = _compute_report_lineage_validation(session, report.id)
    return {"investigation_id": investigation_id, "report_id": report.id, "validation": validation}


@app.get("/investigations/{investigation_id}/feed.rss")
def investigation_rss_feed(
    investigation_id: str,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> Response:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    reports = session.scalars(
        select(ReportORM).where(ReportORM.investigation_id == investigation_id).order_by(ReportORM.generated_at.desc()).limit(20)
    ).all()

    items = []
    for report in reports:
        pub_date = report.generated_at.strftime("%a, %d %b %Y %H:%M:%S GMT")
        items.append(
            f"<item><title>{escape(investigation.topic)} report ({report.confidence})</title>"
            f"<description>{escape(report.summary[:500])}</description>"
            f"<pubDate>{pub_date}</pubDate><guid>report-{report.id}</guid></item>"
        )

    xml = (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<rss version='2.0'><channel>"
        f"<title>STS Investigation Feed: {escape(investigation.topic)}</title>"
        f"<link>{escape(settings.public_base_url.rstrip('/'))}/investigations/{investigation_id}/feed.rss</link>"
        f"<description>Situation reports for {escape(investigation.topic)}</description>"
        + "".join(items)
        + "</channel></rss>"
    )
    return Response(content=xml, media_type="application/rss+xml")


@app.get("/investigations/{investigation_id}/claims")
def list_claims(
    investigation_id: str,
    report_id: int | None = None,
    stance: str | None = None,
    limit: int = 200,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    query = select(ClaimORM).where(ClaimORM.investigation_id == investigation_id)
    if report_id is not None:
        query = query.where(ClaimORM.report_id == report_id)
    if stance:
        query = query.where(ClaimORM.stance == stance)

    rows = session.scalars(query.order_by(ClaimORM.created_at.desc()).limit(max(1, min(limit, 1000)))).all()
    return [
        {
            "id": row.id,
            "investigation_id": row.investigation_id,
            "report_id": row.report_id,
            "claim_text": row.claim_text,
            "stance": row.stance,
            "confidence": row.confidence,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@app.get("/claims/{claim_id}/evidence")
def claim_evidence(
    claim_id: int,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    claim = session.get(ClaimORM, claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    rows = session.scalars(
        select(ClaimEvidenceORM)
        .where(ClaimEvidenceORM.claim_id == claim_id)
        .order_by(ClaimEvidenceORM.weight.desc(), ClaimEvidenceORM.created_at.desc())
        .limit(100)
    ).all()

    return [
        {
            "id": row.id,
            "claim_id": row.claim_id,
            "observation_id": row.observation_id,
            "weight": row.weight,
            "rationale": row.rationale,
            "created_at": row.created_at.isoformat(),
            "observation": {
                "source": row.observation.source,
                "claim": row.observation.claim,
                "url": row.observation.url,
                "captured_at": row.observation.captured_at.isoformat(),
                "reliability_hint": row.observation.reliability_hint,
            },
        }
        for row in rows
    ]


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
        payload={"investigation_id": investigation_id, "batch_size": payload.batch_size, "include_noise": payload.include_noise},
        priority=payload.priority,
        max_attempts=payload.max_attempts,
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
        max_attempts=payload.max_attempts,
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
    enqueued = tick_schedules(session, default_max_attempts=settings.job_max_attempts)
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
    result = process_next_job(session=session, pipeline=pipeline, llm_client=llm_client, retry_backoff_s=settings.job_retry_backoff_s)
    if result is None:
        return {"status": "idle", "message": "No pending jobs"}
    return result


@app.post("/jobs/process-batch")
def process_batch(
    payload: ProcessBatchRequest,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    results = process_job_batch(
        session=session,
        pipeline=pipeline,
        llm_client=llm_client,
        high_quota=payload.high_quota,
        normal_quota=payload.normal_quota,
        low_quota=payload.low_quota,
        retry_backoff_s=settings.job_retry_backoff_s,
    )
    return {"processed": len(results), "results": results}


@app.get("/jobs/dead-letters")
def list_dead_letters(
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = session.scalars(select(JobORM).where(JobORM.dead_lettered.is_(True)).order_by(JobORM.updated_at.desc()).limit(200)).all()
    return [
        {
            "id": row.id,
            "job_type": row.job_type,
            "priority": row.priority,
            "attempts": row.attempts,
            "max_attempts": row.max_attempts,
            "last_error": row.last_error,
            "updated_at": row.updated_at.isoformat(),
        }
        for row in rows
    ]


@app.post("/jobs/dead-letters/{job_id}/requeue")
def requeue_dead_letter_job(
    job_id: int,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    job = requeue_dead_letter(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Dead-letter job not found")
    return {"job_id": job.id, "status": job.status}


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
            "max_attempts": row.max_attempts,
            "dead_lettered": row.dead_lettered,
            "last_error": row.last_error,
            "run_at": row.run_at.isoformat(),
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }
        for row in rows
    ]


@app.post("/admin/api-keys")
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


@app.get("/admin/api-keys")
def list_api_keys(
    _: AuthContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = session.scalars(select(APIKeyORM).order_by(APIKeyORM.created_at.desc())).all()
    return [
        {"id": row.id, "label": row.label, "role": row.role, "active": row.active, "created_at": row.created_at.isoformat()}
        for row in rows
    ]


@app.post("/admin/api-keys/{key_id}/revoke")
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


@app.get("/audit/logs")
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


@app.get("/dashboard/summary")
def dashboard_summary(_: None = Depends(require_api_key), session: Session = Depends(get_session)) -> dict[str, Any]:
    investigation_count = session.scalar(select(func.count(InvestigationORM.id))) or 0
    observation_count = session.scalar(select(func.count(ObservationORM.id))) or 0
    report_count = session.scalar(select(func.count(ReportORM.id))) or 0
    feedback_count = session.scalar(select(func.count(FeedbackORM.id))) or 0
    ingestion_runs = session.scalar(select(func.count(IngestionRunORM.id))) or 0
    jobs_pending = session.scalar(select(func.count(JobORM.id)).where(JobORM.status == "pending")) or 0
    jobs_failed = session.scalar(select(func.count(JobORM.id)).where(JobORM.status == "failed")) or 0
    jobs_dead_letter = session.scalar(select(func.count(JobORM.id)).where(JobORM.dead_lettered.is_(True))) or 0
    schedules_active = session.scalar(select(func.count(JobScheduleORM.id)).where(JobScheduleORM.active.is_(True))) or 0
    alert_rules_count = session.scalar(select(func.count(AlertRuleORM.id))) or 0
    alert_events_count = session.scalar(select(func.count(AlertEventORM.id))) or 0
    claims_count = session.scalar(select(func.count(ClaimORM.id))) or 0
    claim_evidence_count = session.scalar(select(func.count(ClaimEvidenceORM.id))) or 0
    api_keys_count = session.scalar(select(func.count(APIKeyORM.id)).where(APIKeyORM.active.is_(True))) or 0
    audit_logs_count = session.scalar(select(func.count(AuditLogORM.id))) or 0

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
        "jobs_dead_letter": jobs_dead_letter,
        "schedules_active": schedules_active,
        "alert_rules": alert_rules_count,
        "alert_events": alert_events_count,
        "claims": claims_count,
        "claim_evidence": claim_evidence_count,
        "api_keys": api_keys_count,
        "audit_logs": audit_logs_count,
        "geo_events": session.scalar(select(func.count(GeoEventORM.id))) or 0,
        "convergence_zones": session.scalar(select(func.count(ConvergenceZoneORM.id)).where(ConvergenceZoneORM.resolved_at.is_(None))) or 0,
        "entities": session.scalar(select(func.count(EntityMentionORM.id))) or 0,
        "stories": session.scalar(select(func.count(StoryORM.id))) or 0,
        "discovered_topics": session.scalar(select(func.count(DiscoveredTopicORM.id)).where(DiscoveredTopicORM.status == "new")) or 0,
        "collection_plans": session.scalar(select(func.count(CollectionPlanORM.id)).where(CollectionPlanORM.active.is_(True))) or 0,
        "latest_reports": latest_reports,
    }


# ── Corroboration endpoints ────────────────────────────────────────────


class CorroborationRequest(BaseModel):
    investigation_id: str
    similarity_threshold: float = Field(default=0.25, ge=0.1, le=0.8)


@app.post("/analysis/corroboration")
def analyze_observation_corroboration(
    payload: CorroborationRequest,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Analyze cross-source corroboration for an investigation's observations."""
    investigation = session.get(InvestigationORM, payload.investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    rows = session.scalars(
        select(ObservationORM)
        .where(ObservationORM.investigation_id == payload.investigation_id)
        .order_by(ObservationORM.captured_at.desc())
        .limit(500)
    ).all()

    observations = [
        {
            "id": r.id,
            "source": r.source,
            "claim": r.claim,
            "url": r.url,
            "captured_at": r.captured_at,
            "reliability_hint": r.reliability_hint,
        }
        for r in rows
    ]

    result = analyze_corroboration(observations, payload.similarity_threshold)

    return {
        "investigation_id": payload.investigation_id,
        "total_claims": result.total_claims,
        "well_corroborated": result.well_corroborated,
        "partially_corroborated": result.partially_corroborated,
        "single_source": result.single_source,
        "contested": result.contested,
        "overall_corroboration_rate": result.overall_corroboration_rate,
        "scores": [
            {
                "claim_summary": s.claim_summary[:200],
                "score": s.score,
                "verdict": s.verdict,
                "independent_sources": s.independent_sources,
                "source_families": s.source_families,
                "connector_types": s.connector_types,
                "source_tiers": s.source_tiers,
                "temporal_spread_hours": s.temporal_spread_hours,
                "first_reported_by": s.first_reported_by,
                "breakdown": s.breakdown,
            }
            for s in result.scores[:50]
        ],
    }


# ── Slop / propaganda filter endpoints ─────────────────────────────────


class SlopFilterRequest(BaseModel):
    investigation_id: str
    drop_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    flag_threshold: float = Field(default=0.4, ge=0.0, le=1.0)


@app.post("/analysis/slop-filter")
def analyze_slop(
    payload: SlopFilterRequest,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Analyze observations for slop, propaganda, engagement bait, and bot patterns."""
    investigation = session.get(InvestigationORM, payload.investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    rows = session.scalars(
        select(ObservationORM)
        .where(ObservationORM.investigation_id == payload.investigation_id)
        .order_by(ObservationORM.captured_at.desc())
        .limit(500)
    ).all()

    observations = [
        {
            "id": r.id,
            "source": r.source,
            "claim": r.claim,
            "url": r.url,
            "captured_at": r.captured_at,
            "reliability_hint": r.reliability_hint,
        }
        for r in rows
    ]

    result = filter_slop(observations, payload.drop_threshold, payload.flag_threshold)

    return {
        "investigation_id": payload.investigation_id,
        "total": result.total,
        "credible": result.credible,
        "suspicious": result.suspicious,
        "slop": result.slop,
        "propaganda": result.propaganda,
        "dropped_count": result.dropped_count,
        "pattern_stats": result.pattern_stats,
        "scores": [
            {
                "observation_id": s.observation_id,
                "source": s.source,
                "claim_preview": s.claim_preview,
                "slop_score": s.slop_score,
                "credibility_score": s.credibility_score,
                "verdict": s.verdict,
                "flags": s.flags,
                "factor_scores": s.factor_scores,
                "recommended_action": s.recommended_action,
            }
            for s in result.scores[:100]
        ],
    }


# ── Entity relationship graph endpoints ────────────────────────────────


class EntityGraphRequest(BaseModel):
    investigation_id: str
    min_mentions: int = Field(default=2, ge=1, le=50)
    min_edge_weight: int = Field(default=2, ge=1, le=20)
    max_nodes: int = Field(default=150, ge=10, le=500)


@app.post("/analysis/entity-graph")
def build_investigation_entity_graph(
    payload: EntityGraphRequest,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Build an entity relationship graph for an investigation."""
    investigation = session.get(InvestigationORM, payload.investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    rows = session.scalars(
        select(ObservationORM)
        .where(ObservationORM.investigation_id == payload.investigation_id)
        .order_by(ObservationORM.captured_at.desc())
        .limit(1000)
    ).all()

    observations = [
        {
            "claim": r.claim,
            "source": r.source,
            "url": r.url,
            "captured_at": r.captured_at,
            "reliability_hint": r.reliability_hint,
            "investigation_id": r.investigation_id,
        }
        for r in rows
    ]

    graph = build_entity_graph(
        observations,
        min_mentions=payload.min_mentions,
        min_edge_weight=payload.min_edge_weight,
        max_nodes=payload.max_nodes,
    )

    return {
        "investigation_id": payload.investigation_id,
        **graph.to_dict(),
    }


# ── Narrative timeline endpoints ───────────────────────────────────────


class NarrativeTimelineRequest(BaseModel):
    investigation_id: str
    window_minutes: int = Field(default=30, ge=5, le=360)


@app.post("/analysis/narrative-timeline")
def build_investigation_narrative(
    payload: NarrativeTimelineRequest,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Build a narrative timeline for an investigation."""
    investigation = session.get(InvestigationORM, payload.investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    rows = session.scalars(
        select(ObservationORM)
        .where(ObservationORM.investigation_id == payload.investigation_id)
        .order_by(ObservationORM.captured_at.asc())
        .limit(1000)
    ).all()

    observations = [
        {
            "id": r.id,
            "claim": r.claim,
            "source": r.source,
            "url": r.url,
            "captured_at": r.captured_at,
            "reliability_hint": r.reliability_hint,
        }
        for r in rows
    ]

    timeline = build_narrative_timeline(
        observations,
        topic=investigation.topic,
        investigation_id=payload.investigation_id,
        window_minutes=payload.window_minutes,
    )

    return timeline.to_dict()


# ── Anomaly detection endpoints ────────────────────────────────────────


class AnomalyDetectionRequest(BaseModel):
    investigation_id: str | None = None
    detection_hours: int = Field(default=6, ge=1, le=72)
    baseline_hours: int = Field(default=72, ge=12, le=720)
    min_z_score: float = Field(default=2.0, ge=1.0, le=5.0)


@app.post("/analysis/anomalies")
def detect_anomalies(
    payload: AnomalyDetectionRequest,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Detect anomalies and pattern breaks across observations."""
    query = select(ObservationORM).order_by(ObservationORM.captured_at.desc()).limit(2000)
    if payload.investigation_id:
        investigation = session.get(InvestigationORM, payload.investigation_id)
        if not investigation:
            raise HTTPException(status_code=404, detail="Investigation not found")
        query = query.where(ObservationORM.investigation_id == payload.investigation_id)

    rows = session.scalars(query).all()

    observations = [
        {
            "id": r.id,
            "source": r.source,
            "claim": r.claim,
            "url": r.url,
            "captured_at": r.captured_at,
            "reliability_hint": r.reliability_hint,
            "investigation_id": r.investigation_id,
        }
        for r in rows
    ]

    report = run_anomaly_detection(
        observations,
        detection_hours=payload.detection_hours,
        baseline_hours=payload.baseline_hours,
        min_z_score=payload.min_z_score,
    )

    return report.to_dict()


# ── Combined intelligence analysis ─────────────────────────────────────


@app.post("/analysis/full")
def full_intelligence_analysis(
    payload: CorroborationRequest,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Run full intelligence analysis: corroboration + slop filter + anomalies + narrative."""
    investigation = session.get(InvestigationORM, payload.investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    rows = session.scalars(
        select(ObservationORM)
        .where(ObservationORM.investigation_id == payload.investigation_id)
        .order_by(ObservationORM.captured_at.desc())
        .limit(1000)
    ).all()

    observations = [
        {
            "id": r.id,
            "source": r.source,
            "claim": r.claim,
            "url": r.url,
            "captured_at": r.captured_at,
            "reliability_hint": r.reliability_hint,
            "investigation_id": r.investigation_id,
        }
        for r in rows
    ]

    # Run all analyses
    corroboration = analyze_corroboration(observations, payload.similarity_threshold)
    slop_result = filter_slop(observations)
    anomalies = run_anomaly_detection(observations)
    timeline = build_narrative_timeline(
        observations,
        topic=investigation.topic,
        investigation_id=payload.investigation_id,
    )
    graph = build_entity_graph(observations)

    return {
        "investigation_id": payload.investigation_id,
        "topic": investigation.topic,
        "observation_count": len(observations),
        "corroboration": {
            "total_claims": corroboration.total_claims,
            "well_corroborated": corroboration.well_corroborated,
            "single_source": corroboration.single_source,
            "rate": corroboration.overall_corroboration_rate,
        },
        "quality": {
            "credible": slop_result.credible,
            "suspicious": slop_result.suspicious,
            "slop": slop_result.slop,
            "propaganda": slop_result.propaganda,
            "pattern_stats": slop_result.pattern_stats,
        },
        "anomalies": {
            "total": anomalies.total_anomalies,
            "by_severity": anomalies.by_severity,
            "by_type": anomalies.by_type,
        },
        "narrative": {
            "time_span_hours": timeline.time_span_hours,
            "phases": timeline.phases,
            "pivots": timeline.pivots,
            "summary": timeline.summary,
        },
        "entity_graph": {
            "nodes": graph.node_count,
            "edges": graph.edge_count,
            "communities": graph.communities,
            "bridge_entities": graph.bridge_entities,
            "top_entities": graph.top_entities[:5],
        },
    }


# ── WebSocket endpoint ───────────────────────────────────────────────

from fastapi import WebSocket
from sts_monitor.websocket import websocket_endpoint as _ws_handler, ws_manager


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await _ws_handler(websocket)


@app.get("/ws/status")
def ws_status(_: None = Depends(require_api_key)) -> dict[str, Any]:
    """Inspect WebSocket connection status."""
    return {
        "active_connections": ws_manager.active_count,
        "connections": ws_manager.connections_info,
    }


# ── Export endpoints ─────────────────────────────────────────────────

from sts_monitor.export import export_observations_csv, export_claims_csv, export_report_markdown, export_report_pdf_bytes


@app.get("/export/{investigation_id}/observations.csv")
def export_observations(
    investigation_id: str,
    source: str | None = None,
    min_reliability: float | None = None,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> Response:
    """Export observations as CSV."""
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


@app.get("/export/{investigation_id}/claims.csv")
def export_claims(
    investigation_id: str,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> Response:
    """Export claims as CSV."""
    inv = session.get(InvestigationORM, investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation not found")
    csv_data = export_claims_csv(session, investigation_id)
    return Response(content=csv_data, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="claims_{investigation_id}.csv"'
    })


@app.get("/export/{investigation_id}/report.md")
def export_report_md(
    investigation_id: str,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> Response:
    """Export latest report as Markdown."""
    inv = session.get(InvestigationORM, investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation not found")
    md = export_report_markdown(session, investigation_id)
    if not md:
        raise HTTPException(status_code=404, detail="No report found")
    return Response(content=md, media_type="text/markdown", headers={
        "Content-Disposition": f'attachment; filename="report_{investigation_id}.md"'
    })


@app.get("/export/{investigation_id}/report.pdf")
def export_report_pdf(
    investigation_id: str,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> Response:
    """Export latest report as PDF."""
    inv = session.get(InvestigationORM, investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation not found")
    pdf = export_report_pdf_bytes(session, investigation_id)
    if not pdf:
        raise HTTPException(status_code=404, detail="No report found")
    return Response(content=pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="report_{investigation_id}.pdf"'
    })


# ── JWT auth endpoints ───────────────────────────────────────────────

from sts_monitor.auth_jwt import hash_password, verify_password, create_token, decode_token, UserContext


class UserRegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=60)
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default="analyst", pattern="^(admin|analyst|viewer)$")


class UserLoginRequest(BaseModel):
    username: str
    password: str


# Store user accounts in a simple JSON structure within the DB
# We'll use the existing APIKeyORM table with a convention:
# label = "user:<username>", key_hash = bcrypt(password), role = role


@app.post("/auth/register")
def register_user(
    payload: UserRegisterRequest,
    auth: AuthContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Register a new user account (admin only)."""
    label = f"user:{payload.username}"
    existing = session.scalars(
        select(APIKeyORM).where(APIKeyORM.label == label)
    ).first()
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


@app.post("/auth/login")
def login_user(
    payload: UserLoginRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Authenticate and receive a JWT token."""
    label = f"user:{payload.username}"
    user = session.scalars(
        select(APIKeyORM).where(APIKeyORM.label == label).where(APIKeyORM.active.is_(True))
    ).first()

    if not user or not verify_password(payload.password, user.key_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_token(user.id, payload.username, user.role)
    return {"token": token, "username": payload.username, "role": user.role}


@app.get("/auth/me")
def auth_me(
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Get current user info from JWT token."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    token_data = decode_token(auth_header[7:])
    return {"user_id": token_data["sub"], "username": token_data["username"], "role": token_data["role"]}


# ── Plugin endpoints ─────────────────────────────────────────────────

from sts_monitor.plugins import plugin_registry


@app.get("/plugins")
def list_plugins(_: None = Depends(require_api_key)) -> dict[str, Any]:
    """List all registered connector plugins."""
    return {"plugins": plugin_registry.registered}


@app.post("/plugins/discover")
def discover_plugins(auth: AuthContext = Depends(require_admin)) -> dict[str, Any]:
    """Trigger plugin discovery from entry points and plugin directory."""
    counts = plugin_registry.discover_all()
    return {"discovered": counts, "total_registered": len(plugin_registry.registered)}


# ── Notification test endpoint ───────────────────────────────────────

from sts_monitor.notifications import AlertNotification, notify_all


@app.post("/notifications/test")
def test_notification(
    auth: AuthContext = Depends(require_admin),
) -> dict[str, Any]:
    """Send a test notification to all configured channels."""
    notification = AlertNotification(
        title="STS Monitor Test Notification",
        message="This is a test notification from the STS Situation Monitor.",
        severity="info",
    )
    results = notify_all(notification)
    return {"channels": results, "configured": len(results)}


# ── Knowledge Graph endpoints ────────────────────────────────────────

from sts_monitor.knowledge_graph import build_knowledge_graph


class KnowledgeGraphRequest(BaseModel):
    investigation_ids: list[str] | None = Field(default=None, description="Limit to specific investigations (null = all)")
    include_observations: bool = Field(default=False, description="Include individual observations as nodes")
    max_entities: int = Field(default=200, ge=10, le=1000)
    min_entity_mentions: int = Field(default=2, ge=1, le=50)
    max_stories: int = Field(default=100, ge=10, le=500)


@app.post("/knowledge-graph")
def get_knowledge_graph(
    payload: KnowledgeGraphRequest,
    _: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Build a unified knowledge graph across investigations.

    Returns a graph of entities, stories, claims, convergence zones, and
    investigations with edges showing how they connect. Use this to see
    the big picture across all your monitoring threads.
    """
    kg = build_knowledge_graph(
        session=session,
        investigation_ids=payload.investigation_ids,
        include_observations=payload.include_observations,
        max_entities=payload.max_entities,
        min_entity_mentions=payload.min_entity_mentions,
        max_stories=payload.max_stories,
    )
    return kg.to_dict()


@app.get("/knowledge-graph/summary")
def knowledge_graph_summary(
    _: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Quick summary stats for the knowledge graph without full node/edge data."""
    kg = build_knowledge_graph(session=session, max_entities=500, max_stories=200)
    return {
        "node_count": kg.node_count,
        "edge_count": kg.edge_count,
        "stats": kg.stats,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Autopilot endpoints
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/autopilot/status")
def autopilot_status(_: AuthContext = Depends(require_api_key)) -> dict[str, Any]:
    """Get current autopilot status and recent cycle logs."""
    return get_autopilot_state().to_dict()


@app.post("/autopilot/start")
def autopilot_start(_: AuthContext = Depends(require_api_key)) -> dict[str, Any]:
    """Start the autopilot background task."""
    return start_autopilot()


@app.post("/autopilot/stop")
def autopilot_stop(_: AuthContext = Depends(require_api_key)) -> dict[str, Any]:
    """Stop the autopilot background task."""
    return stop_autopilot()


# ═══════════════════════════════════════════════════════════════════════════
# Investigation Templates
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/templates")
def list_investigation_templates(
    category: str | None = None,
    _: AuthContext = Depends(require_api_key),
) -> list[dict[str, Any]]:
    """List available investigation templates."""
    return list_templates(category=category)


@app.get("/templates/{template_key}")
def get_investigation_template(
    template_key: str,
    _: AuthContext = Depends(require_api_key),
) -> dict[str, Any]:
    """Get a specific template configuration."""
    tmpl = get_template(template_key)
    if not tmpl:
        raise HTTPException(status_code=404, detail=f"Template not found: {template_key}")
    return apply_template(template_key)


class TemplateApplyRequest(BaseModel):
    custom_topic: str | None = None


@app.post("/templates/{template_key}/apply")
def apply_investigation_template(
    template_key: str,
    payload: TemplateApplyRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Create a new investigation from a template."""
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


# ═══════════════════════════════════════════════════════════════════════════
# Source Reliability Scoring
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/investigations/{investigation_id}/source-scores")
def get_source_scores(
    investigation_id: str,
    _: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Get source reliability scores for an investigation."""
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    observations = session.query(ObservationORM).filter_by(
        investigation_id=investigation_id
    ).all()
    obs_dicts = [
        {"source": o.source, "claim": o.claim, "captured_at": o.captured_at,
         "reliability_hint": o.reliability_hint, "id": o.id}
        for o in observations
    ]
    return get_source_leaderboard(obs_dicts)


# ═══════════════════════════════════════════════════════════════════════════
# Comparative Analysis
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/investigations/{investigation_id}/comparative")
def comparative_analysis(
    investigation_id: str,
    silence_hours: int = 12,
    _: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Run cross-source comparative analysis — contradictions, agreements, silences."""
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    observations = session.query(ObservationORM).filter_by(
        investigation_id=investigation_id
    ).order_by(ObservationORM.captured_at.desc()).limit(1000).all()

    obs_dicts = [
        {"source": o.source, "claim": o.claim, "captured_at": o.captured_at,
         "reliability_hint": o.reliability_hint, "id": o.id, "url": o.url}
        for o in observations
    ]
    report = run_comparative_analysis(obs_dicts, silence_threshold_hours=silence_hours)
    return report.to_dict()


# ═══════════════════════════════════════════════════════════════════════════
# Rabbit Trail (Deep Autonomous Investigation)
# ═══════════════════════════════════════════════════════════════════════════

@app.post("/investigations/{investigation_id}/rabbit-trail")
def start_rabbit_trail(
    investigation_id: str,
    max_depth: int = 10,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Start a rabbit trail deep investigation on an investigation."""
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    # Gather observations and entities
    observations = session.query(ObservationORM).filter_by(
        investigation_id=investigation_id
    ).order_by(ObservationORM.captured_at.desc()).limit(500).all()

    obs_dicts = [
        {"source": o.source, "claim": o.claim, "captured_at": o.captured_at,
         "reliability_hint": o.reliability_hint, "id": o.id, "url": o.url}
        for o in observations
    ]

    entity_mentions = session.query(EntityMentionORM).filter_by(
        investigation_id=investigation_id
    ).all()
    entities = list({em.normalized or em.entity_text for em in entity_mentions})

    # Try to get LLM client
    llm_client = None
    try:
        from sts_monitor.llm import LocalLLMClient
        llm_client = LocalLLMClient(
            base_url=settings.local_llm_url,
            model=settings.local_llm_model,
            timeout_s=settings.agent_llm_timeout_s,
        )
    except Exception:
        pass

    trail = run_rabbit_trail(
        investigation_id=investigation_id,
        topic=investigation.topic,
        observations=obs_dicts,
        entities=entities,
        max_depth=max_depth,
        llm_client=llm_client,
    )
    store_trail_session(trail)
    return trail.to_dict()


@app.get("/rabbit-trails")
def list_rabbit_trails(
    investigation_id: str | None = None,
    _: AuthContext = Depends(require_api_key),
) -> list[dict[str, Any]]:
    """List all rabbit trail sessions."""
    return list_trail_sessions(investigation_id=investigation_id)


@app.get("/rabbit-trails/{session_id}")
def get_rabbit_trail(
    session_id: str,
    _: AuthContext = Depends(require_api_key),
) -> dict[str, Any]:
    """Get details of a specific rabbit trail session."""
    trail = get_trail_session(session_id)
    if not trail:
        raise HTTPException(status_code=404, detail="Trail session not found")
    return trail.to_dict()


# ═══════════════════════════════════════════════════════════════════════════
# Alert Rules Engine
# ═══════════════════════════════════════════════════════════════════════════

class AlertRuleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    rule_type: str = Field(pattern="^(volume_spike|contradiction_threshold|entity_velocity|silence|narrative_shift)$")
    threshold: float = Field(default=5.0, ge=1)
    window_minutes: int = Field(default=60, ge=5, le=1440)
    cooldown_seconds: int = Field(default=600, ge=60)
    severity: str = Field(default="warning", pattern="^(info|warning|critical)$")
    metadata: dict[str, Any] = Field(default_factory=dict)


@app.post("/investigations/{investigation_id}/alert-rules")
def create_alert_rule(
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


@app.get("/investigations/{investigation_id}/alert-rules")
def list_alert_rules(
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


@app.post("/investigations/{investigation_id}/evaluate-alerts")
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


# ═══════════════════════════════════════════════════════════════════════════
# Timeline (Narrative) View API
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/investigations/{investigation_id}/timeline")
def get_investigation_timeline(
    investigation_id: str,
    window_minutes: int = 30,
    _: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Get narrative timeline for an investigation."""
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    observations = session.query(ObservationORM).filter_by(
        investigation_id=investigation_id
    ).order_by(ObservationORM.captured_at.asc()).limit(1000).all()

    obs_dicts = [
        {"source": o.source, "claim": o.claim, "captured_at": o.captured_at,
         "reliability_hint": o.reliability_hint, "url": o.url, "id": o.id}
        for o in observations
    ]

    from sts_monitor.narrative import build_narrative_timeline
    timeline = build_narrative_timeline(
        observations=obs_dicts,
        topic=investigation.topic,
        investigation_id=investigation_id,
        window_minutes=window_minutes,
    )
    return {
        "topic": timeline.topic,
        "investigation_id": timeline.investigation_id,
        "time_span_hours": timeline.time_span_hours,
        "total_events": timeline.total_events,
        "phases": dict(timeline.phases),
        "pivots": timeline.pivots,
        "summary": timeline.summary,
        "events": [
            {
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                "phase": e.phase,
                "headline": e.headline,
                "detail": e.detail,
                "sources": e.sources,
                "reliability": e.reliability,
                "entities": e.entities,
                "location": e.location,
                "is_pivot": e.is_pivot,
            }
            for e in timeline.events
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
# CROSS-INVESTIGATION LINKS
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/cross-investigation/links")
def get_cross_investigation_links(
    _: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Detect links across all investigations."""
    report = detect_cross_investigation_links(session)
    return report.to_dict()


# ═══════════════════════════════════════════════════════════════════════════
# CLAIM VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════

@app.post("/investigations/{investigation_id}/verify-claims")
def verify_claims(
    investigation_id: str,
    max_claims: int = 20,
    _: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Run LLM-powered claim verification on an investigation's observations."""
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    observations = session.query(ObservationORM).filter_by(
        investigation_id=investigation_id
    ).order_by(ObservationORM.captured_at.desc()).limit(500).all()

    obs_dicts = [
        {"claim": o.claim, "source": o.source, "captured_at": o.captured_at.isoformat() if o.captured_at else None}
        for o in observations
    ]

    llm_client = None
    try:
        from sts_monitor.llm import LocalLLMClient
        llm_client = LocalLLMClient()
    except Exception:
        pass

    report = verify_investigation_claims(
        investigation_id=investigation_id,
        topic=investigation.topic,
        observations=obs_dicts,
        llm_client=llm_client,
        max_claims=max_claims,
    )
    return report.to_dict()


# ═══════════════════════════════════════════════════════════════════════════
# GEOFENCES
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/geofences")
def list_geofences(
    _: AuthContext = Depends(require_api_key),
) -> dict[str, Any]:
    """List all configured geofence zones."""
    zones = get_all_zones()
    return {
        "total": len(zones),
        "zones": [
            {"name": z.name, "lat": z.center_lat, "lon": z.center_lon, "radius_km": z.radius_km, "category": z.category}
            for z in zones
        ],
    }


@app.post("/geofences")
def create_geofence(
    name: str,
    lat: float,
    lon: float,
    radius_km: float = 50.0,
    category: str = "custom",
    _: AuthContext = Depends(require_api_key),
) -> dict[str, Any]:
    """Add a custom geofence zone."""
    zone = GeoZone(name=name, center_lat=lat, center_lon=lon, radius_km=radius_km, category=category)
    add_zone(zone)
    return {"status": "created", "zone": {"name": zone.name, "lat": zone.center_lat, "lon": zone.center_lon, "radius_km": zone.radius_km, "category": zone.category}}


@app.delete("/geofences/{zone_name}")
def delete_geofence(
    zone_name: str,
    _: AuthContext = Depends(require_api_key),
) -> dict[str, str]:
    """Remove a custom geofence zone."""
    removed = remove_zone(zone_name)
    if not removed:
        raise HTTPException(status_code=404, detail="Zone not found or is a builtin zone")
    return {"status": "deleted", "zone": zone_name}


@app.post("/investigations/{investigation_id}/geofence-check")
def check_geofences(
    investigation_id: str,
    _: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Check investigation observations against all geofence zones."""
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    observations = session.query(ObservationORM).filter_by(
        investigation_id=investigation_id
    ).limit(1000).all()

    obs_dicts = [
        {"claim": o.claim, "source": o.source, "latitude": o.latitude, "longitude": o.longitude, "captured_at": o.captured_at}
        for o in observations
    ]

    zones = get_all_zones()
    geo_alerts = check_observations_against_zones(obs_dicts, zones, investigation_id)
    summary = get_zone_activity_summary(obs_dicts)

    return {
        "investigation_id": investigation_id,
        "alerts": [a.to_dict() for a in geo_alerts],
        "zone_activity": summary,
    }


# ═══════════════════════════════════════════════════════════════════════════
# SOURCE NETWORK
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/investigations/{investigation_id}/source-network")
def get_source_network(
    investigation_id: str,
    co_report_window_hours: int = 6,
    _: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Analyze source relationships for an investigation."""
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    observations = session.query(ObservationORM).filter_by(
        investigation_id=investigation_id
    ).limit(1000).all()

    obs_dicts = [
        {"claim": o.claim, "source": o.source, "captured_at": o.captured_at}
        for o in observations
    ]

    report = analyze_source_network(obs_dicts, co_report_window_hours=co_report_window_hours)
    return {
        "investigation_id": investigation_id,
        **report.to_dict(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# PATTERN MATCHING
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/investigations/{investigation_id}/pattern-match")
def get_pattern_match(
    investigation_id: str,
    threshold: float = 0.3,
    _: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Match investigation observations against known crisis patterns."""
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    observations = session.query(ObservationORM).filter_by(
        investigation_id=investigation_id
    ).limit(1000).all()

    obs_dicts = [
        {"claim": o.claim, "source": o.source, "captured_at": o.captured_at}
        for o in observations
    ]

    result = analyze_patterns(obs_dicts, threshold=threshold)
    return {
        "investigation_id": investigation_id,
        "current_signature": result.get("current_signature", {}),
        "matches": result.get("matches", []),
        "top_match": result.get("top_match"),
        "escalation_score": result.get("escalation_score", 0),
    }


# ═══════════════════════════════════════════════════════════════════════════
# INTELLIGENCE BRIEFS
# ═══════════════════════════════════════════════════════════════════════════

@app.post("/investigations/{investigation_id}/intel-brief")
def create_intel_brief(
    investigation_id: str,
    period: str = "Daily",
    _: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Generate an intelligence brief for an investigation."""
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    observations = session.query(ObservationORM).filter_by(
        investigation_id=investigation_id
    ).order_by(ObservationORM.captured_at.desc()).limit(500).all()

    obs_dicts = [
        {"claim": o.claim, "source": o.source, "captured_at": o.captured_at, "latitude": o.latitude, "longitude": o.longitude}
        for o in observations
    ]

    entities = session.query(EntityMentionORM).filter_by(
        investigation_id=investigation_id
    ).limit(500).all()

    entity_dicts = [
        {"entity_text": e.entity_text, "entity_type": e.entity_type, "normalized": e.normalized}
        for e in entities
    ]

    llm_client = None
    try:
        from sts_monitor.llm import LocalLLMClient
        llm_client = LocalLLMClient()
    except Exception:
        pass

    brief = generate_intel_brief(
        investigation_id=investigation_id,
        topic=investigation.topic,
        observations=obs_dicts,
        entities=entity_dicts,
        llm_client=llm_client,
        period_label=period,
    )
    return brief


@app.post("/investigations/{investigation_id}/intel-brief/markdown")
def create_intel_brief_markdown(
    investigation_id: str,
    period: str = "Daily",
    _: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    """Generate an intelligence brief in Markdown format."""
    brief = create_intel_brief(investigation_id, period, _, session)
    md = brief_to_markdown(brief)
    return {"markdown": md}


# ═══════════════════════════════════════════════════════════════════════════
# WEBHOOK INGESTION
# ═══════════════════════════════════════════════════════════════════════════

@app.post("/investigations/{investigation_id}/webhook")
def ingest_webhook(
    investigation_id: str,
    request_body: dict[str, Any],
    x_webhook_signature: str | None = None,
    _: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Ingest observations from external webhook."""
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    # Validate signature if configured
    webhook_secret = settings.webhook_secret if hasattr(settings, "webhook_secret") else ""
    if webhook_secret and x_webhook_signature:
        import json
        payload_bytes = json.dumps(request_body).encode()
        if not validate_webhook_signature(payload_bytes, x_webhook_signature, webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    normalized = normalize_webhook_payload(request_body, source_name=f"webhook:{investigation_id}")
    created = []
    for obs_data in normalized:
        obs = ObservationORM(
            investigation_id=investigation_id,
            claim=obs_data.get("claim", ""),
            source=obs_data.get("source", "webhook"),
            captured_at=obs_data.get("captured_at"),
            url=obs_data.get("url", ""),
            latitude=obs_data.get("latitude"),
            longitude=obs_data.get("longitude"),
        )
        session.add(obs)
        created.append(obs_data.get("claim", "")[:100])

    session.commit()
    return {"status": "ingested", "observations_created": len(created), "claims": created}


# ═══════════════════════════════════════════════════════════════════════════
# SEMANTIC SEARCH
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/semantic-search")
def run_semantic_search(
    query: str,
    limit: int = 10,
    investigation_id: str | None = None,
    _: AuthContext = Depends(require_api_key),
) -> dict[str, Any]:
    """Search observations by semantic meaning using embeddings."""
    results = _semantic_search_fn(query, limit=limit, investigation_id=investigation_id)
    return {"query": query, "results": results, "count": len(results)}


@app.get("/semantic-search/health")
def semantic_health(
    _: AuthContext = Depends(require_api_key),
) -> dict[str, Any]:
    """Get health status of semantic search infrastructure."""
    return get_semantic_health()


# ═══════════════════════════════════════════════════════════════════════════
# MULTI-LLM ROUTER
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/llm/status")
def llm_router_status(
    _: AuthContext = Depends(require_api_key),
) -> dict[str, Any]:
    """Get LLM router status and available models."""
    router = get_llm_router()
    return router.get_status()


@app.post("/llm/scan")
def llm_scan_models(
    _: AuthContext = Depends(require_api_key),
) -> dict[str, Any]:
    """Trigger a model scan and return discovered models."""
    router = get_llm_router()
    models = router.scan_models()
    return {"models_found": len(models), "models": [m.to_dict() for m in models]}
