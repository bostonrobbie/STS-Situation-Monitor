"""Investigation routes."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sts_monitor.config import settings
from sts_monitor.database import get_session
from sts_monitor.deps import llm_client, pipeline
from sts_monitor.helpers import build_report_text, record_audit
from sts_monitor.clustering import ObservationRef, cluster_observations, enrich_stories_with_entities
from sts_monitor.entities import extract_entities
from sts_monitor.models import (
    ClaimEvidenceORM,
    ClaimORM,
    EntityMentionORM,
    FeedbackORM,
    IngestionRunORM,
    InvestigationORM,
    ObservationORM,
    ReportORM,
    StoryObservationORM,
    StoryORM,
)
from sts_monitor.pipeline import Observation
from sts_monitor.schemas import (
    FeedbackRequest,
    Investigation,
    InvestigationCreate,
    InvestigationUpdateRequest,
    RunRequest,
)
from sts_monitor.security import AuthContext, require_analyst, require_api_key

router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════
# CRUD
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/investigations", response_model=Investigation)
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


@router.get("/investigations", response_model=list[Investigation])
def list_investigations(_: None = Depends(require_api_key), session: Session = Depends(get_session)) -> list[Investigation]:
    investigations = session.scalars(select(InvestigationORM).order_by(InvestigationORM.created_at.desc())).all()
    return [Investigation.model_validate(item, from_attributes=True) for item in investigations]


@router.patch("/investigations/{investigation_id}", response_model=Investigation)
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
    record_audit(session, actor=auth, action="investigation.update", resource_type="investigation", resource_id=investigation_id, detail=payload.model_dump(mode="json"))
    session.commit()
    return Investigation.model_validate(investigation, from_attributes=True)


# ═══════════════════════════════════════════════════════════════════════════
# Ingestion runs & observations
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/investigations/{investigation_id}/ingestion-runs")
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


@router.get("/investigations/{investigation_id}/observations")
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


# ═══════════════════════════════════════════════════════════════════════════
# Pipeline run
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/investigations/{investigation_id}/run")
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
        prompt = build_report_text(investigation.topic, result.summary, result.confidence, result.disputed_claims)
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


# ═══════════════════════════════════════════════════════════════════════════
# Feedback & memory
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/investigations/{investigation_id}/feedback")
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


@router.get("/investigations/{investigation_id}/memory")
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


# ═══════════════════════════════════════════════════════════════════════════
# RSS feed
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/investigations/{investigation_id}/feed.rss")
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


# ═══════════════════════════════════════════════════════════════════════════
# Claims & evidence
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/investigations/{investigation_id}/claims")
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


@router.get("/claims/{claim_id}/evidence")
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


# ═══════════════════════════════════════════════════════════════════════════
# Timeline
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/investigations/{investigation_id}/timeline")
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
# Entity extraction
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/investigations/{investigation_id}/extract-entities")
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


@router.get("/investigations/{investigation_id}/entities")
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


# ═══════════════════════════════════════════════════════════════════════════
# Story clustering
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/investigations/{investigation_id}/cluster-stories")
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


@router.get("/investigations/{investigation_id}/stories")
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
