from __future__ import annotations

import json
from contextlib import asynccontextmanager
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
    WHOAlertsConnector, CISAKEVConnector, MaritimeConnector,
    TwitterOSINTConnector, TelegramOSINTConnector,
)
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


class GeoNewsIngestRequest(BaseModel):
    layer: str = Field(default="news", pattern="^(news|conflict|military|political|conspiracy)$")
    timespan: str = Field(default="24h", pattern=r"^\d+[hd]$")
    max_geo_events: int = Field(default=80, ge=1, le=300)
    include_reddit: bool = True
    query: str | None = None


class WHOAlertsIngestRequest(BaseModel):
    query: str | None = None


class CISAKEVIngestRequest(BaseModel):
    query: str | None = None
    lookback_days: int = Field(default=7, ge=1, le=30)


class MaritimeIngestRequest(BaseModel):
    query: str | None = None


class TwitterOSINTIngestRequest(BaseModel):
    query: str | None = None


class TelegramOSINTIngestRequest(BaseModel):
    query: str | None = None
    channels: list[str] | None = None


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


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    # Clean up simulated data on startup
    try:
        from sqlalchemy import delete as _startup_delete
        from sts_monitor.database import SessionLocal as _SessionLocal
        _startup_session = _SessionLocal()
        try:
            _result = _startup_session.execute(
                _startup_delete(GeoEventORM).where(GeoEventORM.layer == "simulated")
            )
            _startup_session.commit()
            if _result.rowcount:
                import logging as _logging
                _logging.getLogger(__name__).info(f"Startup: removed {_result.rowcount} simulated geo events")
        finally:
            _startup_session.close()
    except Exception:
        pass
    yield


app = FastAPI(title="STS Situation Monitor", version="0.7.0", lifespan=lifespan)

# Static files
from fastapi.staticfiles import StaticFiles as _StaticFiles
import pathlib as _pathlib
_static_dir = _pathlib.Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", _StaticFiles(directory=str(_static_dir)), name="static")

# Middleware
cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
trusted_hosts = [h.strip() for h in settings.trusted_hosts.split(",") if h.strip()] or ["*"]
if cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
if trusted_hosts != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)

pipeline = SignalPipeline()
llm_client = LocalLLMClient(
    base_url=settings.local_llm_url,
    model=settings.local_llm_model,
    timeout_s=settings.local_llm_timeout_s,
    max_retries=settings.local_llm_max_retries,
)


@app.get("/")
def root_redirect():
    """Redirect root to dashboard with API key pre-filled."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard")
def serve_dashboard():
    """Serve dashboard HTML with API key injected as a meta tag (no ?key= needed)."""
    import pathlib as _pl
    from fastapi.responses import HTMLResponse
    html_path = _pl.Path(__file__).parent / "static" / "dashboard.html"
    html = html_path.read_text(encoding="utf-8")
    # Inject the API key as a <meta> tag right after <head>
    meta_tag = f'<meta name="sts-api-key" content="{settings.auth_api_key}">'
    html = html.replace("<head>", f"<head>\n{meta_tag}", 1)
    return HTMLResponse(content=html)



def _build_report_text(topic: str, result_summary: str, confidence: float, disputed_claims: list[str]) -> str:
    disputed_line = "\n".join(f"- {item}" for item in disputed_claims[:10]) or "- none"
    return (
        f"You are an OSINT analyst. Analyze the following situation and respond ONLY with valid JSON, no commentary.\n\n"
        f"Topic: {topic}\n"
        f"Pipeline summary: {result_summary}\n"
        f"Confidence score: {confidence:.2f}\n"
        f"Disputed claim clusters:\n{disputed_line}\n\n"
        "Respond with exactly this JSON structure:\n"
        '{"topic":"<topic>","overall_assessment":"<2-3 sentence assessment>","overall_confidence":<0.0-1.0>,'
        '"key_claims":[{"claim":"<claim text>","status":"supported","evidence":["<source>"]}],'
        '"disputed_claims":["<claim>"],"gaps":["<info gap>"],"next_actions":["<action>"]}'
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
    # Strip markdown code fences if present
    text = raw_summary.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        payload = json.loads(text)
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
            "max_retries": settings.local_llm_max_retries,
        },
        "filesystem": filesystem,
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
    investigation.sla_due_at = payload.sla_due_at
    _record_audit(session, actor=auth, action="investigation.update", resource_type="investigation", resource_id=investigation_id, detail=payload.model_dump())
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

    session.commit()
    stored_count = session.scalar(select(func.count(ObservationORM.id)).where(ObservationORM.investigation_id == investigation_id))

    return {
        "investigation_id": investigation_id,
        "connector": result.connector,
        "ingested_count": len(result.observations),
        "stored_count": stored_count or 0,
        "failed_feeds": failed,
    }


@app.post("/investigations/{investigation_id}/ingest/geo-news")
def ingest_geo_news(
    investigation_id: str,
    payload: GeoNewsIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Ingest geo-tagged news events: news | conflict | military | political | conspiracy."""
    from sts_monitor.connectors.geo_news import GeoNewsConnector
    connector = GeoNewsConnector(
        layer=payload.layer,
        timespan=payload.timespan,
        max_geo_events=payload.max_geo_events,
        include_reddit=payload.include_reddit,
    )
    return _ingest_with_geo_connector(
        session, investigation_id, "geo_news", connector, payload.query, auth,
    )


@app.post("/investigations/{investigation_id}/ingest/who-alerts")
def ingest_who_alerts(
    investigation_id: str,
    payload: WHOAlertsIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Ingest WHO Disease Outbreak News into the health_alert layer."""
    connector = WHOAlertsConnector()
    return _ingest_with_geo_connector(
        session, investigation_id, "who_alerts", connector, payload.query, auth,
    )


@app.post("/investigations/{investigation_id}/ingest/cisa-kev")
def ingest_cisa_kev(
    investigation_id: str,
    payload: CISAKEVIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Ingest CISA Known Exploited Vulnerabilities into the cyber layer."""
    connector = CISAKEVConnector(lookback_days=payload.lookback_days)
    return _ingest_with_geo_connector(
        session, investigation_id, "cisa_kev", connector, payload.query, auth,
    )


@app.post("/investigations/{investigation_id}/ingest/maritime")
def ingest_maritime(
    investigation_id: str,
    payload: MaritimeIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Ingest maritime vessel tracking data."""
    connector = MaritimeConnector()
    return _ingest_with_geo_connector(
        session, investigation_id, "maritime", connector, payload.query, auth,
    )


@app.post("/investigations/{investigation_id}/ingest/twitter-osint")
def ingest_twitter_osint(
    investigation_id: str,
    payload: TwitterOSINTIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Ingest Twitter/X OSINT via Nitter RSS feeds into the social_media layer."""
    connector = TwitterOSINTConnector()
    return _ingest_with_geo_connector(
        session, investigation_id, "twitter_osint", connector, payload.query, auth,
    )


@app.post("/investigations/{investigation_id}/ingest/telegram-osint")
def ingest_telegram_osint(
    investigation_id: str,
    payload: TelegramOSINTIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Ingest public Telegram channel OSINT into the telegram layer."""
    connector = TelegramOSINTConnector(channels=payload.channels)
    return _ingest_with_geo_connector(
        session, investigation_id, "telegram_osint", connector, payload.query, auth,
    )


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
        detail={"include_noise": payload.include_noise, "batch_size": payload.batch_size, "geo_events": len(geo_sample)},
    )
    _record_audit(session, actor=auth, action="ingest.simulated", resource_type="investigation", resource_id=investigation_id, detail={"ingested": len(generated), "geo_events": len(geo_sample)})
    session.commit()

    return {"investigation_id": investigation_id, "connector": "simulated", "ingested_count": len(generated), "geo_events_count": len(geo_sample)}


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


_GEO_CONNECTORS_NO_TEXT_FILTER = frozenset({"usgs", "nws", "fema", "nasa_firms", "opensky", "geo_news"})


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

    # Geo-event connectors (seismic, weather, fire, aircraft) emit location data —
    # they shouldn't apply the investigation's text seed_query as a claim filter.
    if connector_name in _GEO_CONNECTORS_NO_TEXT_FILTER:
        effective_query = query  # caller's explicit query only, never seed_query
    else:
        effective_query = query or investigation.seed_query

    result = connector_obj.collect(query=effective_query)

    for obs in result.observations:
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

    geo_events = result.metadata.get("geo_events", [])
    geo_count = _persist_geo_events(session, geo_events, investigation_id=investigation_id)

    _record_ingestion_run(
        session=session,
        investigation_id=investigation_id,
        connector=connector_name,
        ingested_count=len(result.observations),
        failed_count=0,
        status="success" if not result.metadata.get("error") else "partial",
        detail={k: v for k, v in result.metadata.items() if k != "geo_events"},
    )

    if auth:
        _record_audit(
            session, actor=auth, action=f"ingest.{connector_name}",
            resource_type="investigation", resource_id=investigation_id,
            detail={"ingested": len(result.observations), "geo_events": geo_count},
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
            .limit(len(result.observations))
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
            "ingested_count": len(result.observations),
            "geo_events_count": geo_count,
            "entities_extracted": entity_count,
        },
    ))

    return {
        "investigation_id": investigation_id,
        "connector": connector_name,
        "ingested_count": len(result.observations),
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
            event_time=r.event_time if r.event_time.tzinfo else r.event_time.replace(tzinfo=UTC),
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
    zoom: int = 2,
    bbox: str | None = None,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
    investigation_id: str | None = None,
) -> dict[str, Any]:
    """Get recent geo events, filtered by zoom level significance and optional bounding box."""
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, min(hours, 720)))
    q = select(GeoEventORM).where(GeoEventORM.event_time >= cutoff)
    if layers:
        layer_list = [l.strip() for l in layers.split(",") if l.strip()]
        if layer_list:
            q = q.where(GeoEventORM.layer.in_(layer_list))
    q = q.order_by(GeoEventORM.event_time.desc()).limit(5000)
    rows = session.scalars(q).all()

    # Zoom-based minimum significance (using magnitude field as proxy)
    # magnitude NULL or 0 = always show (earthquakes always relevant)
    if zoom <= 3:
        min_mag = 6.0
    elif zoom <= 5:
        min_mag = 4.5
    elif zoom <= 7:
        min_mag = 3.0
    elif zoom <= 9:
        min_mag = 1.5
    else:
        min_mag = 0.0

    # Bounding box filter: "west,south,east,north"
    bbox_bounds = None
    if bbox:
        try:
            parts = [float(x) for x in bbox.split(",")]
            if len(parts) == 4:
                bbox_bounds = parts  # west, south, east, north
        except (ValueError, TypeError):
            pass

    features = []
    for r in rows:
        # Earthquake/NWS layers: always show regardless of mag (they use real magnitude)
        # local_news: always show when explicitly included (synthetic significance scores, not seismic magnitude)
        # News/conflict/military: filter by zoom
        if r.layer not in ("earthquake", "weather_alert", "fire", "disaster", "aircraft", "camera", "maritime", "local_news"):
            mag = r.magnitude or 0.0
            if mag < min_mag:
                continue

        # Bounding box filter
        if bbox_bounds:
            w, s, e, n = bbox_bounds
            # Handle antimeridian crossing
            if w <= e:
                if not (w <= r.longitude <= e and s <= r.latitude <= n):
                    continue
            else:  # crosses antimeridian
                if not ((r.longitude >= w or r.longitude <= e) and s <= r.latitude <= n):
                    continue

        try:
            props = json.loads(r.properties_json) if r.properties_json else {}
        except Exception:
            props = {}

        props.update({
            "layer": r.layer,
            "title": r.title,
            "magnitude": r.magnitude,
            "event_time": r.event_time.isoformat() if r.event_time else None,
            "source_id": r.source_id,
        })
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r.longitude, r.latitude]},
            "properties": props,
        })

    return {"type": "FeatureCollection", "features": features}


@app.get("/dashboard/flights/live")
def flights_live(auth=Depends(require_api_key)):
    """Real-time military aircraft — calls adsb.lol directly and returns GeoJSON."""
    try:
        r = httpx.get("https://api.adsb.lol/v2/mil", timeout=10,
                      headers={"User-Agent": "STSIA-Monitor/1.0"})
        ac_list = r.json().get("ac", [])
    except Exception as e:
        return {"type": "FeatureCollection", "features": [], "error": str(e)}

    features = []
    for ac in ac_list:
        lat = ac.get("lat")
        lon = ac.get("lon")
        if lat is None or lon is None:
            continue
        flight = (ac.get("flight") or ac.get("hex", "UNKNOWN")).strip()
        aircraft_type = ac.get("t", "?")
        alt = ac.get("alt_baro", 0)
        speed = ac.get("gs", 0)
        heading = ac.get("track", 0)
        squawk = ac.get("squawk", "")
        is_emergency = str(squawk) in ("7500", "7600", "7700")

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "id": ac.get("hex", flight),
                "callsign": flight,
                "type": aircraft_type,
                "altitude_ft": alt,
                "speed_kts": round(float(speed)) if speed else 0,
                "heading": round(float(heading)) if heading else 0,
                "squawk": squawk,
                "is_emergency": is_emergency,
                "layer": "aircraft_emergency" if is_emergency else "aircraft_military",
                "title": f"\u2708 {flight} ({aircraft_type}) alt:{alt}ft {speed:.0f}kts" if speed else f"\u2708 {flight} ({aircraft_type}) alt:{alt}ft",
                "adsb_url": f"https://globe.adsbexchange.com/?icao={ac.get('hex', '')}",
                "magnitude": 9.0 if is_emergency else 7.0,
            },
        })

    return {"type": "FeatureCollection", "features": features, "total": len(features)}


@app.get("/dashboard/cameras")
def get_cameras_endpoint(auth=Depends(require_api_key)):
    """Return all curated cameras as GeoJSON with embed_url and thumbnail."""
    from sts_monitor.connectors.webcams import CURATED_CAMERAS
    now = datetime.now(UTC)
    features = []
    for region, cameras in CURATED_CAMERAS.items():
        for cam in cameras:
            embed_url = cam.get("embed_url") or cam.get("url", "")
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [cam["lon"], cam["lat"]]},
                "properties": {
                    "title": f"Camera: {cam['name']}",
                    "region": region,
                    "camera_type": cam["type"],
                    "embed_url": embed_url,
                    "thumbnail": cam.get("thumbnail"),
                    "source": cam.get("source", "unknown"),
                    "layer": "camera",
                    "event_time": now.isoformat(),
                },
            })
    return {"type": "FeatureCollection", "features": features, "total": len(features)}


_ZONE_BBOXES = {
    "israel": (29.5, 34.0, 33.5, 36.0),
    "ukraine": (44.0, 22.0, 52.0, 40.0),
    "iran": (25.0, 44.0, 40.0, 63.0),
    "taiwan": (21.0, 118.0, 26.0, 123.0),
    "korea": (35.0, 124.0, 43.0, 132.0),
    "sudan": (8.0, 22.0, 24.0, 38.0),
    "myanmar": (10.0, 92.0, 28.5, 101.5),
    "global": (-90.0, -180.0, 90.0, 180.0),
}


@app.get("/dashboard/zone-report/{zone_name}")
def zone_report(zone_name: str, auth=Depends(require_api_key), session: Session = Depends(get_session)):
    """Generate an LLM intelligence SITREP for a specific conflict zone."""
    from sts_monitor.llm_enrichment import generate_zone_report
    zone_lower = zone_name.lower()
    bbox = _ZONE_BBOXES.get(zone_lower, _ZONE_BBOXES["global"])
    lat_min, lon_min, lat_max, lon_max = bbox

    cutoff = datetime.now(UTC) - timedelta(hours=72)
    q = (
        select(GeoEventORM)
        .where(
            GeoEventORM.event_time >= cutoff,
            GeoEventORM.latitude >= lat_min,
            GeoEventORM.latitude <= lat_max,
            GeoEventORM.longitude >= lon_min,
            GeoEventORM.longitude <= lon_max,
        )
        .order_by(GeoEventORM.magnitude.desc().nullslast(), GeoEventORM.event_time.desc())
        .limit(50)
    )
    rows = session.scalars(q).all()

    recent_events = []
    for r in rows:
        try:
            props = json.loads(r.properties_json) if r.properties_json else {}
        except Exception:
            props = {}
        recent_events.append({
            "layer": r.layer,
            "title": r.title,
            "magnitude": r.magnitude or 0,
            **props,
        })

    report_text = generate_zone_report(zone_lower, recent_events)
    return {
        "zone": zone_lower,
        "report": report_text,
        "generated_at": datetime.now(UTC).isoformat(),
        "events_used": len(recent_events),
    }


@app.get("/dashboard/local-news")
def local_news(
    lat: float = 38.9,
    lon: float = -77.0,
    radius_km: float = 250.0,
    layers: str | None = None,
    limit: int = 20,
    auth=Depends(require_api_key),
    session: Session = Depends(get_session),
):
    """Return recent geo events near the given coordinates."""
    # Approximate bbox from radius (1 degree lat ~ 111 km)
    deg_lat = radius_km / 111.0
    deg_lon = radius_km / (111.0 * max(0.01, abs(float(f"{lat:.4f}").__class__(lat)) / 90.0 * 0.7 + 0.3))
    lat_min, lat_max = lat - deg_lat, lat + deg_lat
    lon_min, lon_max = lon - deg_lon, lon + deg_lon

    cutoff = datetime.now(UTC) - timedelta(hours=72)
    q = (
        select(GeoEventORM)
        .where(
            GeoEventORM.event_time >= cutoff,
            GeoEventORM.latitude >= lat_min,
            GeoEventORM.latitude <= lat_max,
            GeoEventORM.longitude >= lon_min,
            GeoEventORM.longitude <= lon_max,
        )
    )
    if layers:
        layer_list = [l.strip() for l in layers.split(",") if l.strip()]
        if layer_list:
            q = q.where(GeoEventORM.layer.in_(layer_list))
    q = q.order_by(GeoEventORM.event_time.desc()).limit(max(1, min(limit, 100)))
    rows = session.scalars(q).all()

    features = []
    for r in rows:
        try:
            props = json.loads(r.properties_json) if r.properties_json else {}
        except Exception:
            props = {}
        props.update({
            "layer": r.layer,
            "title": r.title,
            "magnitude": r.magnitude,
            "event_time": r.event_time.isoformat() if r.event_time else None,
            "source_id": r.source_id,
        })
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r.longitude, r.latitude]},
            "properties": props,
        })

    return {
        "type": "FeatureCollection",
        "features": features,
        "query": {"lat": lat, "lon": lon, "radius_km": radius_km},
    }


@app.get("/dashboard/local-intelligence")
def dashboard_local_intelligence(
    lat: float = 38.9,
    lon: float = -77.0,
    radius_km: float = 50.0,
    city: str = "",
    state: str = "",
    scope: str = "city",
    auth: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
):
    """
    Comprehensive local intelligence: geo events + news fetch + cameras for a location.
    Fetches fresh Google News RSS data for the city and stores it in DB before returning.
    """
    from .connectors.local_discovery import fetch_local_news, fetch_local_reddit, fetch_cameras_for_location, fetch_state_news, _STATE_BBOXES
    import math

    # Determine fetch scope
    state_lower = state.lower()
    _US_STATES = {"massachusetts","new york","california","texas","florida","illinois",
                  "washington","colorado","georgia","pennsylvania","ohio","michigan",
                  "arizona","north carolina","virginia"}
    use_state_scope = (scope == "state") or (city.lower() in _US_STATES) or (state_lower in _STATE_BBOXES and not city)

    # Fetch fresh local news and store in DB
    new_events = []
    if city or (use_state_scope and state):
        if use_state_scope and state_lower in _STATE_BBOXES:
            news = fetch_state_news(state if state else city, lat, lon)
        else:
            news = fetch_local_news(city, state, lat, lon)
        reddit = fetch_local_reddit(city, lat, lon) if city else []
        new_events = news + reddit

    # Store new events
    stored = 0
    for ev in new_events:
        existing = session.query(GeoEventORM).filter_by(source_id=ev["source_id"]).first()
        if not existing:
            session.add(GeoEventORM(
                layer=ev["layer"],
                source_id=ev["source_id"],
                title=ev["title"],
                latitude=ev["lat"],
                longitude=ev["lon"],
                magnitude=ev["magnitude"],
                event_time=ev["event_time"],
                properties_json=json.dumps(ev["properties"]),
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            ))
            stored += 1
    session.commit()

    # Query geo events within radius
    # Approx: 1 degree lat ~ 111km
    lat_delta = radius_km / 111.0
    lon_delta = radius_km / (111.0 * abs(math.cos(math.radians(lat))) + 0.0001)

    # Expand DB query bbox to cover whole state when using state scope
    if use_state_scope and state_lower in _STATE_BBOXES:
        sbox = _STATE_BBOXES[state_lower]
        lat_delta = (sbox[2] - sbox[0]) / 2
        lon_delta = (sbox[3] - sbox[1]) / 2

    cutoff = datetime.now(UTC) - timedelta(hours=72)
    rows = session.query(GeoEventORM).filter(
        GeoEventORM.latitude >= lat - lat_delta,
        GeoEventORM.latitude <= lat + lat_delta,
        GeoEventORM.longitude >= lon - lon_delta,
        GeoEventORM.longitude <= lon + lon_delta,
        GeoEventORM.event_time >= cutoff,
    ).order_by(GeoEventORM.magnitude.desc()).limit(100).all()

    features = []
    for r in rows:
        props = {}
        try:
            props = json.loads(r.properties_json or "{}")
        except Exception:
            pass
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r.longitude, r.latitude]},
            "properties": {
                "id": str(r.id),
                "layer": r.layer,
                "title": r.title or "",
                "magnitude": r.magnitude or 0,
                "event_time": r.event_time.isoformat() if r.event_time else "",
                **props,
            },
        })

    # Get cameras for location
    cameras = fetch_cameras_for_location(city or "unknown", lat, lon) if city else []

    return {
        "type": "FeatureCollection",
        "features": features,
        "cameras": cameras,
        "freshly_ingested": stored,
        "query": {"lat": lat, "lon": lon, "radius_km": radius_km, "city": city, "state": state},
    }


@app.post("/admin/cleanup-simulated")
def cleanup_simulated(auth=Depends(require_api_key), session: Session = Depends(get_session)):
    """Delete all geo_events where layer='simulated'."""
    from sqlalchemy import delete as _delete
    result = session.execute(_delete(GeoEventORM).where(GeoEventORM.layer == "simulated"))
    session.commit()
    return {"deleted": result.rowcount, "layer": "simulated"}


@app.post("/admin/cleanup")
def admin_cleanup(
    rules: dict[str, int],
    auth=Depends(require_api_key),
    session: Session = Depends(get_session),
):
    """Delete expired geo_events per layer. rules = {layer_name: hours_to_keep}. hours=0 deletes all."""
    from sqlalchemy import delete as _delete
    now = datetime.now(UTC)
    total_deleted = 0
    summary = {}
    for layer_name, hours_to_keep in rules.items():
        if hours_to_keep == 0:
            result = session.execute(_delete(GeoEventORM).where(GeoEventORM.layer == layer_name))
        else:
            cutoff = now - timedelta(hours=hours_to_keep)
            result = session.execute(
                _delete(GeoEventORM).where(
                    GeoEventORM.layer == layer_name,
                    GeoEventORM.event_time < cutoff,
                )
            )
        summary[layer_name] = result.rowcount
        total_deleted += result.rowcount
    session.commit()
    return {"total_deleted": total_deleted, "by_layer": summary}


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

    # Recent geo events
    recent_geo = session.scalars(
        select(GeoEventORM).where(GeoEventORM.event_time >= cutoff_24h)
        .order_by(GeoEventORM.event_time.desc()).limit(20)
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

    # Discovered topics count (new status=new)
    discovered_topics_count = session.scalar(
        select(func.count(DiscoveredTopicORM.id)).where(DiscoveredTopicORM.status == "new")
    ) or 0

    return {
        "timestamp": now.isoformat(),
        "investigations": investigation_count,
        "observations_total": observation_count,
        "discovered_topics": discovered_topics_count,
        "geo_events_24h": geo_event_count,
        "sse_subscribers": event_bus.subscriber_count,
        "layers": {layer: count for layer, count in layer_counts},
        "recent_geo_events": [
            {
                "id": r.id, "layer": r.layer, "title": r.title,
                "latitude": r.latitude, "longitude": r.longitude,
                "magnitude": r.magnitude, "event_time": r.event_time.isoformat(),
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
            captured_at=o.captured_at if o.captured_at.tzinfo else o.captured_at.replace(tzinfo=UTC),
            url=o.url,
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
    auth: AuthContext = Depends(require_api_key),
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
    report_sections = _build_report_sections(
        topic=investigation.topic,
        accepted=accepted,
        dropped=dropped,
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


# ── Intelligence Briefing System ─────────────────────────────────────────────

# Module-level briefing cache: {"briefing": str, "generated_at": str, "event_count": int}
_briefing_cache: dict[str, Any] = {}
_BRIEFING_CACHE_SECONDS = 30 * 60  # 30 minutes


@app.get("/dashboard/briefing")
def get_intelligence_briefing(
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Generate (or return cached) daily intelligence briefing using local LLM."""
    from sts_monitor.llm_enrichment import generate_daily_briefing

    now = datetime.now(UTC)

    # Check cache
    if _briefing_cache:
        cached_at_str = _briefing_cache.get("generated_at", "")
        try:
            cached_at = datetime.fromisoformat(cached_at_str)
            if cached_at.tzinfo is None:
                cached_at = cached_at.replace(tzinfo=UTC)
            age_s = (now - cached_at).total_seconds()
            if age_s < _BRIEFING_CACHE_SECONDS:
                return {**_briefing_cache, "cached": True}
        except Exception:
            pass

    # Fetch recent high-significance geo events (48h, mag>=5, top 100)
    cutoff = now - timedelta(hours=48)
    q = (
        select(GeoEventORM)
        .where(
            GeoEventORM.event_time >= cutoff,
            GeoEventORM.magnitude >= 5.0,
        )
        .order_by(GeoEventORM.magnitude.desc())
        .limit(100)
    )
    rows = session.scalars(q).all()

    # Also fetch high-signal observations (last 50, signal_score >= 0.7)
    obs_rows = session.scalars(
        select(ObservationORM)
        .where(
            ObservationORM.captured_at >= cutoff,
            ObservationORM.reliability_hint >= 0.7,
        )
        .order_by(ObservationORM.captured_at.desc())
        .limit(50)
    ).all()

    events: list[dict] = []
    for r in rows:
        try:
            props = json.loads(r.properties_json)
        except Exception:
            props = {}
        events.append({
            "layer": r.layer,
            "title": r.title,
            "latitude": r.latitude,
            "longitude": r.longitude,
            "magnitude": r.magnitude,
            "event_time": r.event_time.isoformat(),
            "properties": props,
        })

    # Supplement with high-reliability observations
    for obs in obs_rows[:20]:
        events.append({
            "layer": obs.connector_type or "news",
            "title": obs.claim[:200],
            "latitude": obs.latitude,
            "longitude": obs.longitude,
            "magnitude": obs.reliability_hint * 10,
            "event_time": obs.captured_at.isoformat(),
            "properties": {"source": obs.source, "url": obs.url},
        })

    briefing_text = generate_daily_briefing(events)
    generated_at = now.isoformat()

    result = {
        "briefing": briefing_text,
        "generated_at": generated_at,
        "event_count": len(events),
        "cached": False,
    }
    _briefing_cache.clear()
    _briefing_cache.update(result)

    return result


@app.post("/dashboard/enrich-events")
def enrich_recent_events(
    n: int = 50,
    _: AuthContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Run LLM entity extraction on the last N geo events and store results."""
    from sts_monitor.llm_enrichment import extract_entities

    cutoff = datetime.now(UTC) - timedelta(hours=48)
    rows = session.scalars(
        select(GeoEventORM)
        .where(GeoEventORM.event_time >= cutoff)
        .order_by(GeoEventORM.magnitude.desc().nullslast())
        .limit(max(1, min(n, 200)))
    ).all()

    enriched = 0
    all_entities: dict[str, list[str]] = {"people": [], "organizations": [], "locations": []}

    for row in rows:
        try:
            entities = extract_entities(row.title)
            try:
                existing = json.loads(row.properties_json)
            except Exception:
                existing = {}
            existing["llm_entities"] = entities
            row.properties_json = json.dumps(existing)
            enriched += 1
            for k in ("people", "organizations", "locations"):
                all_entities[k].extend(entities.get(k, []))
        except Exception:
            continue

    if enriched:
        session.commit()

    # Deduplicate
    for k in all_entities:
        all_entities[k] = list(set(all_entities[k]))[:50]

    return {"enriched": enriched, "entities_found": all_entities}


@app.get("/dashboard/entities")
def get_entity_graph(
    hours: int = 48,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Return entity graph from recent geo_events properties_json."""
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, min(hours, 720)))
    rows = session.scalars(
        select(GeoEventORM)
        .where(GeoEventORM.event_time >= cutoff)
        .order_by(GeoEventORM.event_time.desc())
        .limit(2000)
    ).all()

    entity_counts: dict[str, dict[str, Any]] = {}  # id -> {type, count}
    edge_counts: dict[str, int] = {}  # "a||b" -> count

    for row in rows:
        try:
            props = json.loads(row.properties_json)
        except Exception:
            continue

        llm = props.get("llm_entities", {})
        if not llm:
            continue

        # Collect entities from this event
        event_entities = []
        for etype, elist in [("person", llm.get("people", [])),
                              ("org", llm.get("organizations", [])),
                              ("location", llm.get("locations", []))]:
            for ename in elist:
                eid = f"{etype}:{ename}"
                if eid not in entity_counts:
                    entity_counts[eid] = {"id": eid, "type": etype, "name": ename, "count": 0}
                entity_counts[eid]["count"] += 1
                event_entities.append(eid)

        # Build co-occurrence edges
        for i in range(len(event_entities)):
            for j in range(i + 1, len(event_entities)):
                ea, eb = sorted([event_entities[i], event_entities[j]])
                edge_key = f"{ea}||{eb}"
                edge_counts[edge_key] = edge_counts.get(edge_key, 0) + 1

    # Sort nodes by count
    nodes = sorted(entity_counts.values(), key=lambda x: x["count"], reverse=True)[:100]
    node_ids = {n["id"] for n in nodes}

    edges = [
        {"source": k.split("||")[0], "target": k.split("||")[1], "weight": v}
        for k, v in edge_counts.items()
        if k.split("||")[0] in node_ids and k.split("||")[1] in node_ids
    ]
    edges.sort(key=lambda x: x["weight"], reverse=True)

    return {"nodes": nodes, "edges": edges[:200]}


@app.get("/dashboard/alerts-feed")
def get_alerts_feed(
    limit: int = 20,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Return the last N highest-significance geo events for the alert feed."""
    cutoff = datetime.now(UTC) - timedelta(hours=48)
    rows = session.scalars(
        select(GeoEventORM)
        .where(GeoEventORM.event_time >= cutoff)
        .order_by(GeoEventORM.magnitude.desc().nullslast(), GeoEventORM.event_time.desc())
        .limit(max(1, min(limit, 100)))
    ).all()

    events = []
    for r in rows:
        try:
            props = json.loads(r.properties_json)
        except Exception:
            props = {}
        events.append({
            "id": r.id,
            "layer": r.layer,
            "title": r.title,
            "latitude": r.latitude,
            "longitude": r.longitude,
            "magnitude": r.magnitude,
            "event_time": r.event_time.isoformat(),
            "location_name": props.get("location_name") or props.get("country") or "",
        })

    return {"count": len(events), "events": events}


@app.get("/dashboard/stats-enhanced")
def get_enhanced_stats(
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Enhanced statistics: total events, 24h events, active sources, top alert."""
    now = datetime.now(UTC)
    cutoff_24h = now - timedelta(hours=24)

    total_events = session.scalar(select(func.count(GeoEventORM.id))) or 0
    events_24h = session.scalar(
        select(func.count(GeoEventORM.id)).where(GeoEventORM.event_time >= cutoff_24h)
    ) or 0

    # Active sources (distinct layers in last 24h)
    layer_rows = session.execute(
        select(GeoEventORM.layer, func.count(GeoEventORM.id))
        .where(GeoEventORM.event_time >= cutoff_24h)
        .group_by(GeoEventORM.layer)
    ).all()
    active_sources = len(layer_rows)

    # Top alert: highest magnitude event in last 24h
    top_row = session.scalars(
        select(GeoEventORM)
        .where(GeoEventORM.event_time >= cutoff_24h)
        .order_by(GeoEventORM.magnitude.desc().nullslast())
        .limit(1)
    ).first()

    top_alert = ""
    top_alert_layer = ""
    top_alert_magnitude = 0.0
    if top_row:
        top_alert = (top_row.title or "")[:80]
        top_alert_layer = top_row.layer
        top_alert_magnitude = top_row.magnitude or 0.0

    return {
        "total_events": total_events,
        "events_24h": events_24h,
        "active_sources": active_sources,
        "layer_counts": [{"layer": l, "count": c} for l, c in layer_rows],
        "top_alert": top_alert,
        "top_alert_layer": top_alert_layer,
        "top_alert_magnitude": top_alert_magnitude,
        "generated_at": now.isoformat(),
    }
