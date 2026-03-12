"""Vector embedding and semantic search via Ollama + Qdrant.

Uses the local Ollama instance to generate text embeddings, then stores
and queries them in Qdrant for semantic similarity search across all
observations. This enables "find similar observations" even when keywords
don't match — e.g., searching for "seismic activity" finds observations
about "earthquake tremors" or "building collapse from ground shaking".

Architecture:
  Observation text → Ollama /api/embeddings → vector → Qdrant collection
  Query text → Ollama /api/embeddings → vector → Qdrant search → ranked results
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EmbeddingResult:
    text: str
    vector: list[float]
    model: str
    latency_ms: float


@dataclass(slots=True)
class SemanticMatch:
    observation_id: int
    investigation_id: str
    source: str
    claim: str
    url: str
    score: float  # 0.0-1.0 similarity
    captured_at: str


@dataclass(slots=True)
class SemanticSearchResult:
    query: str
    matches: list[SemanticMatch]
    total_indexed: int
    search_latency_ms: float


# ── Ollama Embedding Client ─────────────────────────────────────────────

class OllamaEmbeddingClient:
    """Generate text embeddings via Ollama's /api/embeddings endpoint."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "nomic-embed-text",
        timeout_s: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s

    def embed(self, text: str) -> EmbeddingResult:
        """Generate embedding for a single text."""
        started = time.perf_counter()
        payload = {"model": self.model, "prompt": text}
        resp = httpx.post(
            f"{self.base_url}/api/embeddings",
            json=payload,
            timeout=self.timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        vector = data.get("embedding", [])
        latency = round((time.perf_counter() - started) * 1000, 2)
        return EmbeddingResult(text=text, vector=vector, model=self.model, latency_ms=latency)

    def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        """Generate embeddings for multiple texts (sequential)."""
        results: list[EmbeddingResult] = []
        for text in texts:
            try:
                results.append(self.embed(text))
            except Exception as exc:
                logger.warning("Embedding failed for text (len=%d): %s", len(text), exc)
        return results

    def health(self) -> dict[str, Any]:
        """Check if embedding model is available."""
        try:
            result = self.embed("health check")
            return {
                "reachable": True,
                "model": self.model,
                "vector_dimensions": len(result.vector),
                "latency_ms": result.latency_ms,
            }
        except Exception as exc:
            return {"reachable": False, "model": self.model, "error": str(exc)}


# ── Qdrant Vector Store ─────────────────────────────────────────────────

COLLECTION_NAME = "sts_observations"


class QdrantStore:
    """Manage vector storage and search in Qdrant."""

    def __init__(
        self,
        qdrant_url: str = "http://localhost:6333",
        collection_name: str = COLLECTION_NAME,
        vector_size: int = 768,  # nomic-embed-text default
        timeout_s: float = 15.0,
    ) -> None:
        self.qdrant_url = qdrant_url.rstrip("/")
        self.collection = collection_name
        self.vector_size = vector_size
        self.timeout_s = timeout_s

    def _url(self, path: str) -> str:
        return f"{self.qdrant_url}{path}"

    def ensure_collection(self) -> bool:
        """Create collection if it doesn't exist. Returns True if created."""
        try:
            resp = httpx.get(
                self._url(f"/collections/{self.collection}"),
                timeout=self.timeout_s,
            )
            if resp.status_code == 200:
                return False  # Already exists
        except Exception:
            pass

        # Create collection
        payload = {
            "vectors": {
                "size": self.vector_size,
                "distance": "Cosine",
            },
        }
        resp = httpx.put(
            self._url(f"/collections/{self.collection}"),
            json=payload,
            timeout=self.timeout_s,
        )
        resp.raise_for_status()
        return True

    def upsert(
        self,
        points: list[dict[str, Any]],
    ) -> int:
        """Upsert points into Qdrant. Each point needs: id, vector, payload."""
        if not points:
            return 0
        # Batch in chunks of 100
        total = 0
        for i in range(0, len(points), 100):
            batch = points[i:i + 100]
            payload = {"points": batch}
            resp = httpx.put(
                self._url(f"/collections/{self.collection}/points"),
                json=payload,
                timeout=self.timeout_s,
            )
            resp.raise_for_status()
            total += len(batch)
        return total

    def search(
        self,
        vector: list[float],
        limit: int = 20,
        score_threshold: float = 0.3,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Search for similar vectors."""
        payload: dict[str, Any] = {
            "vector": vector,
            "limit": limit,
            "score_threshold": score_threshold,
            "with_payload": True,
        }
        if filters:
            payload["filter"] = filters

        resp = httpx.post(
            self._url(f"/collections/{self.collection}/points/search"),
            json=payload,
            timeout=self.timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", [])

    def count(self) -> int:
        """Get total point count in collection."""
        try:
            resp = httpx.get(
                self._url(f"/collections/{self.collection}"),
                timeout=self.timeout_s,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("result", {}).get("points_count", 0)
        except Exception:
            return 0

    def delete_by_investigation(self, investigation_id: str) -> int:
        """Delete all points for an investigation."""
        payload = {
            "filter": {
                "must": [
                    {"key": "investigation_id", "match": {"value": investigation_id}},
                ],
            },
        }
        resp = httpx.post(
            self._url(f"/collections/{self.collection}/points/delete"),
            json=payload,
            timeout=self.timeout_s,
        )
        resp.raise_for_status()
        return 0  # Qdrant doesn't return count

    def health(self) -> dict[str, Any]:
        """Check Qdrant health."""
        try:
            resp = httpx.get(self._url("/"), timeout=self.timeout_s)
            resp.raise_for_status()
            count = self.count()
            return {"reachable": True, "collection": self.collection, "points_count": count}
        except Exception as exc:
            return {"reachable": False, "error": str(exc)}


# ── Semantic Search Engine ──────────────────────────────────────────────

def _observation_point_id(observation_id: int) -> str:
    """Deterministic UUID from observation ID for Qdrant."""
    h = hashlib.md5(f"obs-{observation_id}".encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


class SemanticSearchEngine:
    """High-level semantic search combining Ollama embeddings + Qdrant."""

    def __init__(
        self,
        embedding_client: OllamaEmbeddingClient,
        qdrant_store: QdrantStore,
    ) -> None:
        self.embedder = embedding_client
        self.store = qdrant_store

    def initialize(self) -> dict[str, Any]:
        """Ensure Qdrant collection exists and embedding model is ready."""
        embed_health = self.embedder.health()
        qdrant_health = self.store.health()

        created = False
        if qdrant_health.get("reachable"):
            try:
                created = self.store.ensure_collection()
            except Exception as exc:
                qdrant_health["collection_error"] = str(exc)

        # Auto-detect vector size from embedding model
        if embed_health.get("reachable") and embed_health.get("vector_dimensions"):
            detected_size = embed_health["vector_dimensions"]
            if detected_size != self.store.vector_size:
                self.store.vector_size = detected_size
                if qdrant_health.get("reachable"):
                    try:
                        self.store.ensure_collection()
                    except Exception:
                        pass

        return {
            "embedding": embed_health,
            "qdrant": qdrant_health,
            "collection_created": created,
        }

    def index_observations(
        self,
        observations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Embed and index a batch of observations.

        Each observation dict should have: id, investigation_id, source, claim, url, captured_at, reliability_hint
        """
        if not observations:
            return {"indexed": 0, "failed": 0}

        texts = [obs["claim"][:1000] for obs in observations]  # Cap text length
        embeddings = self.embedder.embed_batch(texts)

        points: list[dict[str, Any]] = []
        for obs, emb in zip(observations, embeddings):
            if not emb.vector:
                continue
            point_id = _observation_point_id(obs["id"])
            points.append({
                "id": point_id,
                "vector": emb.vector,
                "payload": {
                    "observation_id": obs["id"],
                    "investigation_id": obs.get("investigation_id", ""),
                    "source": obs.get("source", ""),
                    "claim": obs.get("claim", "")[:2000],
                    "url": obs.get("url", ""),
                    "captured_at": str(obs.get("captured_at", "")),
                    "reliability_hint": obs.get("reliability_hint", 0.5),
                },
            })

        indexed = 0
        if points:
            indexed = self.store.upsert(points)

        return {
            "indexed": indexed,
            "failed": len(observations) - len(embeddings),
            "total_in_collection": self.store.count(),
        }

    def search(
        self,
        query: str,
        limit: int = 20,
        score_threshold: float = 0.3,
        investigation_id: str | None = None,
    ) -> SemanticSearchResult:
        """Semantic search across indexed observations."""
        started = time.perf_counter()

        # Embed the query
        query_embedding = self.embedder.embed(query)

        # Build filter
        filters = None
        if investigation_id:
            filters = {
                "must": [
                    {"key": "investigation_id", "match": {"value": investigation_id}},
                ],
            }

        # Search Qdrant
        raw_results = self.store.search(
            vector=query_embedding.vector,
            limit=limit,
            score_threshold=score_threshold,
            filters=filters,
        )

        matches: list[SemanticMatch] = []
        for r in raw_results:
            payload = r.get("payload", {})
            matches.append(SemanticMatch(
                observation_id=payload.get("observation_id", 0),
                investigation_id=payload.get("investigation_id", ""),
                source=payload.get("source", ""),
                claim=payload.get("claim", ""),
                url=payload.get("url", ""),
                score=round(r.get("score", 0.0), 4),
                captured_at=payload.get("captured_at", ""),
            ))

        latency = round((time.perf_counter() - started) * 1000, 2)
        return SemanticSearchResult(
            query=query,
            matches=matches,
            total_indexed=self.store.count(),
            search_latency_ms=latency,
        )

    def find_similar(
        self,
        observation_id: int,
        claim_text: str,
        limit: int = 10,
        score_threshold: float = 0.5,
    ) -> list[SemanticMatch]:
        """Find observations semantically similar to a given one."""
        result = self.search(
            query=claim_text,
            limit=limit + 1,  # +1 to exclude self
            score_threshold=score_threshold,
        )
        # Filter out the source observation
        return [m for m in result.matches if m.observation_id != observation_id][:limit]
