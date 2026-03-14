"""Shared helper functions for the STS Situation Monitor API."""
from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sts_monitor.config import settings
from sts_monitor.entities import extract_entities
from sts_monitor.event_bus import STSEvent, event_bus
from sts_monitor.models import (
    AlertEventORM,
    AlertRuleORM,
    AuditLogORM,
    ClaimEvidenceORM,
    ClaimORM,
    EntityMentionORM,
    GeoEventORM,
    IngestionRunORM,
    InvestigationORM,
    JobORM,
    ObservationORM,
)
from sts_monitor.online_tools import send_alert_webhook
from sts_monitor.pipeline import Observation, SignalPipeline
from sts_monitor.security import AuthContext, now_utc


def build_report_text(topic: str, result_summary: str, confidence: float, disputed_claims: list[str]) -> str:
    disputed_line = "\n".join(f"- {item}" for item in disputed_claims[:10]) or "- none"
    return (
        f"Topic: {topic}\n"
        f"Pipeline summary: {result_summary}\n"
        f"Confidence: {confidence}\n"
        f"Disputed claim clusters:\n{disputed_line}\n"
        "Output format: likely true / disputed / unknown / monitor-next"
    )


def record_ingestion_run(
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


def compute_report_lineage_validation(session: Session, report_id: int) -> dict[str, Any]:
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


def record_audit(
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


def connector_diagnostics() -> dict[str, Any]:
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


def compute_readiness_score(*, db_ok: bool, llm_ok: bool, workspace_ok: bool, connectors_ok: bool, queue_ok: bool) -> dict[str, Any]:
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


def is_valid_structured_llm_payload(payload: dict[str, Any]) -> bool:
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


def parse_llm_structured_summary(raw_summary: str) -> tuple[bool, dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(raw_summary)
    except Exception:
        return False, None, "llm response was not valid JSON"

    if not isinstance(payload, dict):
        return False, None, "llm response JSON was not an object"

    if not is_valid_structured_llm_payload(payload):
        return False, None, "llm response did not match required structured fields"

    return True, payload, None


def queue_health_snapshot(session: Session) -> dict[str, Any]:
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


def workspace_health_snapshot(workspace_root: Path) -> dict[str, Any]:
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


def build_report_sections(topic: str, accepted: list[dict[str, Any]], dropped: list[dict[str, Any]], disputed_claims: list[str]) -> dict[str, Any]:
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
    required = ("likely_true", "disputed", "unknown", "monitor_next")
    if not all(key in sections and isinstance(sections[key], list) for key in required):
        raise ValueError("Invalid report sections contract")
    return sections


def normalize_text(text: str) -> str:
    return " ".join(text.lower().split())


def persist_claim_lineage(
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

    normalized_obs = [(normalize_text(item.claim), item) for item in observations]

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

            normalized_claim = normalize_text(text)
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


def seed_default_research_sources(session: Session) -> None:
    from sts_monitor.models import ResearchSourceORM
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


def evaluate_alert_rules(session: Session, investigation_id: str) -> list[dict[str, Any]]:
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
    pipeline = SignalPipeline()
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


def persist_geo_events(
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


def ingest_with_geo_connector(
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
    geo_count = persist_geo_events(session, geo_events, investigation_id=investigation_id)

    record_ingestion_run(
        session=session,
        investigation_id=investigation_id,
        connector=connector_name,
        ingested_count=ingested_count,
        failed_count=dedup_skipped,
        status="success" if not result.metadata.get("error") else "partial",
        detail={k: v for k, v in result.metadata.items() if k != "geo_events"},
    )

    if auth:
        record_audit(
            session, actor=auth, action=f"ingest.{connector_name}",
            resource_type="investigation", resource_id=investigation_id,
            detail={"ingested": ingested_count, "dedup_skipped": dedup_skipped, "geo_events": geo_count},
        )

    session.commit()

    # Auto-extract entities from ingested observations
    entity_count = 0
    try:
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
        pass  # Entity extraction is best-effort

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
