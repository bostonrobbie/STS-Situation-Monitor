"""Research, autonomous agent, and source management routes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from sts_monitor.config import settings
from sts_monitor.database import get_session
from sts_monitor.llm import LocalLLMClient
from sts_monitor.models import (
    AuditLogORM,
    ConvergenceZoneORM,
    InvestigationORM,
    JobScheduleORM,
    ResearchSourceORM,
)
from sts_monitor.jobs import create_schedule, enqueue_job
from sts_monitor.online_tools import parse_csv_env
from sts_monitor.schemas import ResearchSourceCreateRequest
from sts_monitor.helpers import seed_default_research_sources
from sts_monitor.security import AuthContext, now_utc, require_analyst, require_api_key

router = APIRouter()


# ── Inline Pydantic models (match main.py definitions) ─────────────────


class ResearchAgentRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=500)
    seed_query: str | None = None
    max_iterations: int = Field(default=5, ge=1, le=20)
    investigation_id: str | None = None
    nitter_categories: list[str] | None = None
    rss_categories: list[str] | None = None


class ScrapeRequest(BaseModel):
    urls: list[str] = Field(min_length=1)
    max_depth: int = Field(default=1, ge=0, le=3)
    max_pages: int = Field(default=20, ge=1, le=50)
    query: str | None = None


class TwitterSearchRequest(BaseModel):
    query: str | None = None
    accounts: list[str] | None = None
    categories: list[str] | None = None


class ScheduleResearchRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=500)
    seed_query: str | None = None
    investigation_id: str | None = None
    interval_seconds: int = Field(default=3600, ge=300, le=86400)
    max_iterations_per_run: int = Field(default=3, ge=1, le=10)


# ── Helper: Research Agent singleton ────────────────────────────────────


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


# ── Helper: audit logging ──────────────────────────────────────────────


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


# ── Routes ──────────────────────────────────────────────────────────────


@router.get("/research/trending-topics")
def list_trending_topics(
    geo: str = "US",
    max_topics: int = 10,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    from sts_monitor.research import TrendingResearchScanner

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


@router.post("/research/agent/start")
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


@router.get("/research/agent/sessions")
def list_research_sessions(
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """List all research agent sessions."""
    agent = _get_or_create_agent()
    return {"sessions": agent.list_sessions()}


@router.get("/research/agent/sessions/{session_id}")
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


@router.post("/research/agent/sessions/{session_id}/stop")
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


@router.post("/research/agent/search")
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


@router.post("/research/agent/scrape")
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


@router.post("/research/agent/twitter")
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


@router.get("/research/agent/twitter/categories")
def list_twitter_categories(
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """List available OSINT Twitter account categories."""
    from sts_monitor.connectors.nitter import OSINT_ACCOUNTS

    return {
        "categories": [
            {"name": cat, "account_count": len(accounts), "accounts": accounts}
            for cat, accounts in OSINT_ACCOUNTS.items()
        ],
    }


# ── Scheduled Research Agent ────────────────────────────────────────────


@router.post("/research/agent/schedule")
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


@router.post("/research/agent/auto-investigate")
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


# ── Research Sources ────────────────────────────────────────────────────


@router.post("/research/sources")
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


@router.get("/research/sources")
def list_research_sources(
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    seed_default_research_sources(session)
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
