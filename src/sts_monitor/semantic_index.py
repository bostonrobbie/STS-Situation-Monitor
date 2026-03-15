"""Semantic indexing — auto-embed observations on ingest and enable meaning-based search.

Wraps the existing OllamaEmbeddingClient + QdrantStore to provide:
- Auto-indexing hook for autopilot and ingest endpoints
- Cross-investigation semantic search
- Similar observation discovery
- Embedding health monitoring
"""
from __future__ import annotations

import logging
from typing import Any

from sts_monitor.config import settings

log = logging.getLogger(__name__)

# Lazy singleton
_engine = None


def _get_engine():
    """Lazy-init the SemanticSearchEngine."""
    global _engine
    if _engine is not None:
        return _engine
    try:
        from sts_monitor.embeddings import OllamaEmbeddingClient, QdrantStore, SemanticSearchEngine
        client = OllamaEmbeddingClient(
            base_url=settings.local_llm_url,
            model=settings.embedding_model,
            timeout_s=settings.embedding_timeout_s,
        )
        store = QdrantStore(
            qdrant_url=settings.qdrant_url,
            vector_size=settings.qdrant_vector_size,
            timeout_s=settings.qdrant_timeout_s,
        )
        _engine = SemanticSearchEngine(embedding_client=client, qdrant_store=store)
        _engine.initialize()
        return _engine
    except Exception as e:
        log.debug("Semantic search unavailable: %s", e)
        return None


def index_observations_batch(observations: list[dict[str, Any]]) -> dict[str, Any]:
    """Index a batch of observations into the vector store.

    Call this after ingest to make observations searchable by meaning.
    Gracefully returns empty result if embedding infrastructure is unavailable.
    """
    engine = _get_engine()
    if not engine:
        return {"indexed": 0, "status": "unavailable", "reason": "embedding infrastructure offline"}

    try:
        result = engine.index_observations(observations)
        return {"indexed": result.get("indexed", 0), "status": "ok", **result}
    except Exception as e:
        log.warning("Semantic indexing failed: %s", e)
        return {"indexed": 0, "status": "error", "reason": str(e)}


def semantic_search(
    query: str,
    limit: int = 20,
    investigation_id: str | None = None,
    score_threshold: float = 0.3,
) -> dict[str, Any]:
    """Search observations by meaning, not just keywords."""
    engine = _get_engine()
    if not engine:
        return {"matches": [], "status": "unavailable"}

    try:
        result = engine.search(
            query=query,
            limit=limit,
            score_threshold=score_threshold,
            investigation_id=investigation_id,
        )
        return {
            "query": result.query,
            "matches": [
                {
                    "observation_id": m.observation_id,
                    "investigation_id": m.investigation_id,
                    "source": m.source,
                    "claim": m.claim,
                    "score": round(m.score, 4),
                    "captured_at": m.captured_at,
                }
                for m in result.matches
            ],
            "total_indexed": result.total_indexed,
            "search_latency_ms": round(result.search_latency_ms, 1),
            "status": "ok",
        }
    except Exception as e:
        log.warning("Semantic search failed: %s", e)
        return {"matches": [], "status": "error", "reason": str(e)}


def find_similar_observations(
    claim_text: str,
    observation_id: int = 0,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Find observations semantically similar to a given claim."""
    engine = _get_engine()
    if not engine:
        return []

    try:
        matches = engine.find_similar(
            observation_id=observation_id,
            claim_text=claim_text,
            limit=limit,
        )
        return [
            {
                "observation_id": m.observation_id,
                "investigation_id": m.investigation_id,
                "source": m.source,
                "claim": m.claim,
                "score": round(m.score, 4),
            }
            for m in matches
        ]
    except Exception as e:
        log.warning("Similar observation search failed: %s", e)
        return []


def get_semantic_health() -> dict[str, Any]:
    """Check health of embedding + vector store infrastructure."""
    engine = _get_engine()
    if not engine:
        return {"available": False, "reason": "not initialized"}
    try:
        emb_health = engine.embedding_client.health()
        qdrant_health = engine.qdrant_store.health()
        return {
            "available": True,
            "embedding": emb_health,
            "vector_store": qdrant_health,
            "total_indexed": engine.qdrant_store.count(),
        }
    except Exception as e:
        return {"available": False, "reason": str(e)}
