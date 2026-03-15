"""Discovery, collection plan, and feed routes."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from sts_monitor.collection_plan import build_collection_plan, get_curated_feeds, list_feed_categories
from sts_monitor.config import settings
from sts_monitor.connectors import (
    GDELTConnector, USGSEarthquakeConnector, NASAFIRMSConnector,
    ACLEDConnector, NWSAlertConnector, FEMADisasterConnector,
    ReliefWebConnector, OpenSkyConnector, WebcamConnector,
)
from sts_monitor.database import get_session
from sts_monitor.deps import llm_client
from sts_monitor.discovery import build_discovery_summary
from sts_monitor.event_bus import STSEvent, event_bus
from sts_monitor.helpers import ingest_with_geo_connector, record_audit
from sts_monitor.models import (
    CollectionPlanORM,
    ConvergenceZoneORM,
    DiscoveredTopicORM,
    InvestigationORM,
    ObservationORM,
)
from sts_monitor.pipeline import Observation
from sts_monitor.schemas import (
    CollectionPlanCreateRequest,
    DiscoveryRequest,
    PromoteTopicRequest,
)
from sts_monitor.story_discovery import ObservationSnapshot, run_discovery
from sts_monitor.security import AuthContext, require_analyst, require_api_key

router = APIRouter()


@router.get("/discovery/topics")
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


@router.post("/discovery/topics/{topic_id}/promote")
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

    record_audit(
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


@router.post("/discovery/topics/{topic_id}/dismiss")
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


@router.post("/collection-plans")
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
    record_audit(session, actor=auth, action="collection_plan.create",
                 resource_type="collection_plan", resource_id=payload.investigation_id)
    session.commit()
    return {"id": plan.id, "name": plan.name, "connectors": payload.connectors}


@router.get("/collection-plans")
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


@router.post("/collection-plans/{plan_id}/execute")
def execute_collection_plan(
    plan_id: int,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Execute a collection plan -- runs all configured connectors for the plan's query."""
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
            result = ingest_with_geo_connector(
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


@router.get("/feeds/categories")
def list_feed_categories_endpoint(
    _: None = Depends(require_api_key),
) -> list[dict[str, Any]]:
    """List curated RSS feed categories with available feeds."""
    return list_feed_categories()


@router.get("/feeds/by-category")
def get_feeds_by_category(
    categories: str | None = None,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Get curated feeds, optionally filtered by comma-separated categories."""
    cat_list = [c.strip() for c in categories.split(",") if c.strip()] if categories else None
    feeds = get_curated_feeds(cat_list)
    return {"count": len(feeds), "feeds": feeds}


@router.post("/investigations/{investigation_id}/discovery")
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


# ═══════════════════════════════════════════════════════════════════════════
# Automated story discovery
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/discovery/run")
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
