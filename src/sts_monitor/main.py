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

import httpx
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sts_monitor.config import settings
from sts_monitor.discovery import build_discovery_summary
from sts_monitor.connectors import RSSConnector, RedditConnector
from sts_monitor.database import Base, engine, get_session
from sts_monitor.jobs import create_schedule, enqueue_job, process_job_batch, process_next_job, requeue_dead_letter, tick_schedules
from sts_monitor.llm import LocalLLMClient
from sts_monitor.models import APIKeyORM, AlertEventORM, AlertRuleORM, AuditLogORM, ClaimEvidenceORM, ClaimORM, FeedbackORM, IngestionRunORM, InvestigationORM, JobORM, JobScheduleORM, ObservationORM, ReportORM, ResearchSourceORM, SearchProfileORM
from sts_monitor.online_tools import parse_csv_env, send_alert_webhook
from sts_monitor.pipeline import Observation, SignalPipeline
from sts_monitor.search import apply_context_boosts, build_query_plan, normalize_datetime, score_text, top_terms
from sts_monitor.research import TrendingResearchScanner
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
    yield


app = FastAPI(title="STS Situation Monitor", version="0.6.0", lifespan=lifespan)

cors_origins = parse_csv_env(settings.cors_origins)
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

trusted_hosts = parse_csv_env(settings.trusted_hosts)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts or ["*"])

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
            "latency_ms": llm_health.latency_ms,
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
        "latest_reports": latest_reports,
    }
