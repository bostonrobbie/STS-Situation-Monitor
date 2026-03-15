"""Semantic search routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from sts_monitor.config import settings
from sts_monitor.database import get_session
from sts_monitor.models import InvestigationORM, ObservationORM
from sts_monitor.security import AuthContext, require_api_key
from sts_monitor.semantic_index import semantic_search as _semantic_search_fn, get_semantic_health

router = APIRouter()


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


class SemanticIndexRequest(BaseModel):
    investigation_id: str


class SemanticSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    limit: int = Field(default=20, ge=1, le=100)
    score_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    investigation_id: str | None = None


@router.post("/semantic/initialize")
def semantic_initialize(
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Initialize semantic search (create Qdrant collection, check embedding model)."""
    engine = _get_semantic_engine()
    return engine.initialize()


@router.post("/semantic/index")
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


@router.post("/semantic/search")
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


@router.get("/semantic/health")
def semantic_health(
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Check health of embedding model and Qdrant."""
    engine = _get_semantic_engine()
    return {
        "embedding": engine.embedder.health(),
        "qdrant": engine.store.health(),
    }


@router.get("/semantic-search")
def run_semantic_search(
    query: str,
    limit: int = 10,
    investigation_id: str | None = None,
    _: AuthContext = Depends(require_api_key),
) -> dict[str, Any]:
    """Search observations by semantic meaning using embeddings."""
    results = _semantic_search_fn(query, limit=limit, investigation_id=investigation_id)
    return {"query": query, "results": results, "count": len(results)}


@router.get("/semantic-search/health")
def semantic_search_health(
    _: AuthContext = Depends(require_api_key),
) -> dict[str, Any]:
    """Get health status of semantic search infrastructure."""
    return get_semantic_health()
