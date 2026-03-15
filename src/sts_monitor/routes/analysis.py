"""Analysis routes (corroboration, slop, entity-graph, narrative, anomalies, etc.)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from sts_monitor.anomaly_detector import run_anomaly_detection
from sts_monitor.claim_verification import verify_investigation_claims
from sts_monitor.comparative import run_comparative_analysis
from sts_monitor.config import settings
from sts_monitor.corroboration import analyze_corroboration
from sts_monitor.cross_investigation import detect_cross_investigation_links
from sts_monitor.database import get_session
from sts_monitor.entity_graph import build_entity_graph
from sts_monitor.intel_briefs import generate_intel_brief, brief_to_markdown
from sts_monitor.models import (
    EntityMentionORM,
    InvestigationORM,
    ObservationORM,
)
from sts_monitor.narrative import build_narrative_timeline
from sts_monitor.pattern_matching import analyze_patterns
from sts_monitor.rabbit_trail import run_rabbit_trail, store_trail_session, get_trail_session, list_trail_sessions
from sts_monitor.security import AuthContext, require_analyst, require_api_key
from sts_monitor.slop_detector import filter_slop
from sts_monitor.source_network import analyze_source_network
from sts_monitor.source_scoring import get_source_leaderboard
from sts_monitor.webhook_ingest import normalize_webhook_payload, validate_webhook_signature

router = APIRouter()


class CorroborationRequest(BaseModel):
    investigation_id: str
    similarity_threshold: float = Field(default=0.25, ge=0.1, le=0.8)


class SlopFilterRequest(BaseModel):
    investigation_id: str
    drop_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    flag_threshold: float = Field(default=0.4, ge=0.0, le=1.0)


class EntityGraphRequest(BaseModel):
    investigation_id: str
    min_mentions: int = Field(default=2, ge=1, le=50)
    min_edge_weight: int = Field(default=2, ge=1, le=20)
    max_nodes: int = Field(default=150, ge=10, le=500)


class NarrativeTimelineRequest(BaseModel):
    investigation_id: str
    window_minutes: int = Field(default=30, ge=5, le=360)


class AnomalyDetectionRequest(BaseModel):
    investigation_id: str | None = None
    detection_hours: int = Field(default=6, ge=1, le=72)
    baseline_hours: int = Field(default=72, ge=12, le=720)
    min_z_score: float = Field(default=2.0, ge=1.0, le=5.0)


@router.post("/analysis/corroboration")
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


@router.post("/analysis/slop-filter")
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


@router.post("/analysis/entity-graph")
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


@router.post("/analysis/narrative-timeline")
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


@router.post("/analysis/anomalies")
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


@router.post("/analysis/full")
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


@router.get("/investigations/{investigation_id}/source-scores")
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


@router.get("/investigations/{investigation_id}/comparative")
def comparative_analysis(
    investigation_id: str,
    silence_hours: int = 12,
    _: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Run cross-source comparative analysis -- contradictions, agreements, silences."""
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


@router.post("/investigations/{investigation_id}/rabbit-trail")
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


@router.get("/rabbit-trails")
def list_rabbit_trails(
    investigation_id: str | None = None,
    _: AuthContext = Depends(require_api_key),
) -> list[dict[str, Any]]:
    """List all rabbit trail sessions."""
    return list_trail_sessions(investigation_id=investigation_id)


@router.get("/rabbit-trails/{session_id}")
def get_rabbit_trail(
    session_id: str,
    _: AuthContext = Depends(require_api_key),
) -> dict[str, Any]:
    """Get details of a specific rabbit trail session."""
    trail = get_trail_session(session_id)
    if not trail:
        raise HTTPException(status_code=404, detail="Trail session not found")
    return trail.to_dict()


@router.get("/cross-investigation/links")
def get_cross_investigation_links(
    _: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Detect links across all investigations."""
    report = detect_cross_investigation_links(session)
    return report.to_dict()


@router.post("/investigations/{investigation_id}/verify-claims")
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


@router.get("/investigations/{investigation_id}/source-network")
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


@router.get("/investigations/{investigation_id}/pattern-match")
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


@router.post("/investigations/{investigation_id}/intel-brief")
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


@router.post("/investigations/{investigation_id}/intel-brief/markdown")
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


@router.post("/investigations/{investigation_id}/webhook")
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
