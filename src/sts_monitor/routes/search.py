"""Search routes."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from sts_monitor.database import get_session
from sts_monitor.helpers import record_audit
from sts_monitor.models import (
    ClaimORM,
    InvestigationORM,
    ObservationORM,
    ResearchSourceORM,
    SearchProfileORM,
)
from sts_monitor.schemas import (
    RelatedInvestigationsRequest,
    SearchProfileCreateRequest,
    SearchQueryRequest,
)
from sts_monitor.search import apply_context_boosts, build_query_plan, normalize_datetime, score_text, top_terms
from sts_monitor.security import AuthContext, now_utc, require_analyst, require_api_key

router = APIRouter()


@router.post("/search/profiles")
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
    record_audit(session, actor=auth, action="search_profile.create", resource_type="search_profile", resource_id=payload.name, detail={"investigation_id": payload.investigation_id})
    session.commit()

    return {"id": profile.id, "name": profile.name, "investigation_id": profile.investigation_id}


@router.get("/search/profiles")
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


@router.post("/search/query")
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


@router.get("/search/suggest")
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


@router.post("/search/related-investigations")
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
