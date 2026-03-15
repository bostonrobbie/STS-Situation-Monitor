"""Tests for embeddings module: Ollama client, Qdrant store, semantic search engine."""
from __future__ import annotations

import pytest

from sts_monitor.embeddings import (
    EmbeddingResult,
    OllamaEmbeddingClient,
    QdrantStore,
    SemanticMatch,
    SemanticSearchEngine,
    SemanticSearchResult,
    _observation_point_id,
)

pytestmark = pytest.mark.unit

FAKE_VECTOR = [0.1] * 768


# ── Shared fake HTTP helpers ───────────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload=None, status_code: int = 200) -> None:
        self._payload = payload or {}
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


# ── _observation_point_id ──────────────────────────────────────────────


class TestObservationPointId:
    def test_deterministic(self):
        a = _observation_point_id(42)
        b = _observation_point_id(42)
        assert a == b

    def test_different_ids(self):
        assert _observation_point_id(1) != _observation_point_id(2)

    def test_uuid_format(self):
        result = _observation_point_id(1)
        parts = result.split("-")
        assert len(parts) == 5
        assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


# ── OllamaEmbeddingClient ─────────────────────────────────────────────


class TestOllamaEmbeddingClient:
    def test_embed_success(self, monkeypatch):
        import sts_monitor.embeddings as mod

        def fake_post(url, *, json, timeout):
            assert "/api/embeddings" in url
            assert json["prompt"] == "hello world"
            return _FakeResponse({"embedding": FAKE_VECTOR})

        monkeypatch.setattr(mod.httpx, "post", fake_post)
        client = OllamaEmbeddingClient()
        result = client.embed("hello world")
        assert isinstance(result, EmbeddingResult)
        assert result.vector == FAKE_VECTOR
        assert result.text == "hello world"
        assert result.model == "nomic-embed-text"
        assert result.latency_ms >= 0

    def test_embed_raises_on_http_error(self, monkeypatch):
        import sts_monitor.embeddings as mod

        def fake_post(url, *, json, timeout):
            return _FakeResponse(status_code=500)

        monkeypatch.setattr(mod.httpx, "post", fake_post)
        client = OllamaEmbeddingClient()
        with pytest.raises(Exception, match="500"):
            client.embed("fail")

    def test_embed_batch_success(self, monkeypatch):
        import sts_monitor.embeddings as mod

        call_count = 0

        def fake_post(url, *, json, timeout):
            nonlocal call_count
            call_count += 1
            return _FakeResponse({"embedding": FAKE_VECTOR})

        monkeypatch.setattr(mod.httpx, "post", fake_post)
        client = OllamaEmbeddingClient()
        results = client.embed_batch(["one", "two", "three"])
        assert len(results) == 3
        assert call_count == 3

    def test_embed_batch_skips_failures(self, monkeypatch):
        import sts_monitor.embeddings as mod

        call_count = 0

        def fake_post(url, *, json, timeout):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise ConnectionError("network down")
            return _FakeResponse({"embedding": FAKE_VECTOR})

        monkeypatch.setattr(mod.httpx, "post", fake_post)
        client = OllamaEmbeddingClient()
        results = client.embed_batch(["a", "b", "c"])
        assert len(results) == 3  # same length as input, failed entry is None
        assert results[0] is not None
        assert results[1] is None  # second one failed
        assert results[2] is not None

    def test_health_reachable(self, monkeypatch):
        import sts_monitor.embeddings as mod

        def fake_post(url, *, json, timeout):
            return _FakeResponse({"embedding": FAKE_VECTOR})

        monkeypatch.setattr(mod.httpx, "post", fake_post)
        client = OllamaEmbeddingClient()
        h = client.health()
        assert h["reachable"] is True
        assert h["model"] == "nomic-embed-text"
        assert h["vector_dimensions"] == 768

    def test_health_unreachable(self, monkeypatch):
        import sts_monitor.embeddings as mod

        def fake_post(url, *, json, timeout):
            raise ConnectionError("down")

        monkeypatch.setattr(mod.httpx, "post", fake_post)
        client = OllamaEmbeddingClient()
        h = client.health()
        assert h["reachable"] is False
        assert "down" in h["error"]

    def test_custom_base_url_strips_trailing_slash(self):
        client = OllamaEmbeddingClient(base_url="http://myhost:1234/")
        assert client.base_url == "http://myhost:1234"


# ── QdrantStore ────────────────────────────────────────────────────────


class TestQdrantStore:
    def test_ensure_collection_already_exists(self, monkeypatch):
        import sts_monitor.embeddings as mod

        def fake_get(url, *, timeout):
            return _FakeResponse({"result": {}}, status_code=200)

        monkeypatch.setattr(mod.httpx, "get", fake_get)
        store = QdrantStore()
        assert store.ensure_collection() is False

    def test_ensure_collection_creates(self, monkeypatch):
        import sts_monitor.embeddings as mod

        def fake_get(url, *, timeout):
            return _FakeResponse(status_code=404)

        put_calls = []

        def fake_put(url, *, json, timeout):
            put_calls.append((url, json))
            return _FakeResponse({"result": True})

        monkeypatch.setattr(mod.httpx, "get", fake_get)
        monkeypatch.setattr(mod.httpx, "put", fake_put)
        store = QdrantStore()
        assert store.ensure_collection() is True
        assert len(put_calls) == 1
        assert put_calls[0][1]["vectors"]["size"] == 768
        assert put_calls[0][1]["vectors"]["distance"] == "Cosine"

    def test_ensure_collection_get_exception_then_create(self, monkeypatch):
        import sts_monitor.embeddings as mod

        def fake_get(url, *, timeout):
            raise ConnectionError("timeout")

        def fake_put(url, *, json, timeout):
            return _FakeResponse({"result": True})

        monkeypatch.setattr(mod.httpx, "get", fake_get)
        monkeypatch.setattr(mod.httpx, "put", fake_put)
        store = QdrantStore()
        assert store.ensure_collection() is True

    def test_upsert_empty(self):
        store = QdrantStore()
        assert store.upsert([]) == 0

    def test_upsert_single_batch(self, monkeypatch):
        import sts_monitor.embeddings as mod

        put_calls = []

        def fake_put(url, *, json, timeout):
            put_calls.append(json)
            return _FakeResponse({"result": {"status": "ok"}})

        monkeypatch.setattr(mod.httpx, "put", fake_put)
        store = QdrantStore()
        points = [{"id": "abc", "vector": FAKE_VECTOR, "payload": {"k": "v"}}]
        assert store.upsert(points) == 1
        assert len(put_calls) == 1
        assert put_calls[0]["points"] == points

    def test_upsert_batching(self, monkeypatch):
        """Over 100 points should be split into batches."""
        import sts_monitor.embeddings as mod

        put_calls = []

        def fake_put(url, *, json, timeout):
            put_calls.append(len(json["points"]))
            return _FakeResponse({"result": {"status": "ok"}})

        monkeypatch.setattr(mod.httpx, "put", fake_put)
        store = QdrantStore()
        points = [{"id": str(i), "vector": [0.0], "payload": {}} for i in range(150)]
        assert store.upsert(points) == 150
        assert put_calls == [100, 50]

    def test_search(self, monkeypatch):
        import sts_monitor.embeddings as mod

        def fake_post(url, *, json, timeout):
            assert "search" in url
            assert json["vector"] == FAKE_VECTOR
            assert json["limit"] == 5
            return _FakeResponse({
                "result": [
                    {"id": "abc", "score": 0.95, "payload": {"claim": "test"}},
                    {"id": "def", "score": 0.80, "payload": {"claim": "other"}},
                ]
            })

        monkeypatch.setattr(mod.httpx, "post", fake_post)
        store = QdrantStore()
        results = store.search(FAKE_VECTOR, limit=5)
        assert len(results) == 2
        assert results[0]["score"] == 0.95

    def test_search_with_filters(self, monkeypatch):
        import sts_monitor.embeddings as mod

        captured_payload = {}

        def fake_post(url, *, json, timeout):
            captured_payload.update(json)
            return _FakeResponse({"result": []})

        monkeypatch.setattr(mod.httpx, "post", fake_post)
        store = QdrantStore()
        my_filter = {"must": [{"key": "investigation_id", "match": {"value": "inv-1"}}]}
        store.search(FAKE_VECTOR, filters=my_filter)
        assert captured_payload["filter"] == my_filter

    def test_count_success(self, monkeypatch):
        import sts_monitor.embeddings as mod

        def fake_get(url, *, timeout):
            return _FakeResponse({"result": {"points_count": 42}})

        monkeypatch.setattr(mod.httpx, "get", fake_get)
        store = QdrantStore()
        assert store.count() == 42

    def test_count_failure_returns_zero(self, monkeypatch):
        import sts_monitor.embeddings as mod

        def fake_get(url, *, timeout):
            raise ConnectionError("down")

        monkeypatch.setattr(mod.httpx, "get", fake_get)
        store = QdrantStore()
        assert store.count() == 0

    def test_delete_by_investigation(self, monkeypatch):
        import sts_monitor.embeddings as mod

        captured = {}

        def fake_post(url, *, json, timeout):
            captured.update(json)
            return _FakeResponse({"result": True})

        monkeypatch.setattr(mod.httpx, "post", fake_post)
        store = QdrantStore()
        store.delete_by_investigation("inv-123")
        assert captured["filter"]["must"][0]["key"] == "investigation_id"
        assert captured["filter"]["must"][0]["match"]["value"] == "inv-123"

    def test_health_reachable(self, monkeypatch):
        import sts_monitor.embeddings as mod

        def fake_get(url, *, timeout):
            if url.endswith("/"):
                return _FakeResponse({"status": "ok"})
            return _FakeResponse({"result": {"points_count": 10}})

        monkeypatch.setattr(mod.httpx, "get", fake_get)
        store = QdrantStore()
        h = store.health()
        assert h["reachable"] is True
        assert h["points_count"] == 10

    def test_health_unreachable(self, monkeypatch):
        import sts_monitor.embeddings as mod

        def fake_get(url, *, timeout):
            raise ConnectionError("nope")

        monkeypatch.setattr(mod.httpx, "get", fake_get)
        store = QdrantStore()
        h = store.health()
        assert h["reachable"] is False

    def test_url_helper(self):
        store = QdrantStore(qdrant_url="http://qdrant:6333/")
        assert store._url("/collections/x") == "http://qdrant:6333/collections/x"


# ── SemanticSearchEngine ───────────────────────────────────────────────


class TestSemanticSearchEngine:
    def _make_engine(self, monkeypatch):
        """Create an engine with fully mocked HTTP layer."""
        import sts_monitor.embeddings as mod

        get_responses = {}
        post_responses = {}
        put_responses = {}

        def fake_get(url, *, timeout=15):
            for pattern, resp in get_responses.items():
                if pattern in url:
                    return resp
            return _FakeResponse({"result": {"points_count": 5}})

        def fake_post(url, *, json, timeout=30):
            for pattern, resp in post_responses.items():
                if pattern in url:
                    return resp(json) if callable(resp) else resp
            return _FakeResponse({"embedding": FAKE_VECTOR})

        def fake_put(url, *, json, timeout=15):
            for pattern, resp in put_responses.items():
                if pattern in url:
                    return resp
            return _FakeResponse({"result": True})

        monkeypatch.setattr(mod.httpx, "get", fake_get)
        monkeypatch.setattr(mod.httpx, "post", fake_post)
        monkeypatch.setattr(mod.httpx, "put", fake_put)

        embedder = OllamaEmbeddingClient()
        store = QdrantStore()
        engine = SemanticSearchEngine(embedder, store)
        return engine, get_responses, post_responses, put_responses

    def test_initialize(self, monkeypatch):
        engine, get_responses, post_responses, put_responses = self._make_engine(monkeypatch)
        # Collection already exists
        get_responses["/collections/sts_observations"] = _FakeResponse({"result": {"points_count": 5}})
        get_responses["localhost:6333/"] = _FakeResponse({"status": "ok"})

        result = engine.initialize()
        assert result["embedding"]["reachable"] is True
        assert result["qdrant"]["reachable"] is True

    def test_initialize_collection_create_error(self, monkeypatch):
        """When ensure_collection raises, the error is captured in qdrant_health."""
        engine, get_responses, post_responses, put_responses = self._make_engine(monkeypatch)
        get_responses["localhost:6333/"] = _FakeResponse({"status": "ok"})

        # Patch ensure_collection to raise after health succeeds
        monkeypatch.setattr(engine.store, "ensure_collection", lambda: (_ for _ in ()).throw(RuntimeError("disk full")))

        result = engine.initialize()
        assert "collection_error" in result["qdrant"]
        assert "disk full" in result["qdrant"]["collection_error"]

    def test_initialize_vector_size_autodetect(self, monkeypatch):
        """When embedding model reports a different vector size, store is updated."""
        import sts_monitor.embeddings as mod

        small_vector = [0.1] * 384  # Different from default 768

        def fake_get(url, *, timeout=15):
            if url.endswith("/"):
                return _FakeResponse({"status": "ok"})
            return _FakeResponse({"result": {"points_count": 0}})

        def fake_post(url, *, json, timeout=30):
            return _FakeResponse({"embedding": small_vector})

        def fake_put(url, *, json, timeout=15):
            return _FakeResponse({"result": True})

        monkeypatch.setattr(mod.httpx, "get", fake_get)
        monkeypatch.setattr(mod.httpx, "post", fake_post)
        monkeypatch.setattr(mod.httpx, "put", fake_put)

        embedder = OllamaEmbeddingClient()
        store = QdrantStore(vector_size=768)
        engine = SemanticSearchEngine(embedder, store)
        engine.initialize()
        assert store.vector_size == 384

    def test_index_observations_empty(self, monkeypatch):
        engine, *_ = self._make_engine(monkeypatch)
        result = engine.index_observations([])
        assert result == {"indexed": 0, "failed": 0}

    def test_index_observations(self, monkeypatch):
        engine, get_responses, post_responses, put_responses = self._make_engine(monkeypatch)

        upserted = []

        def capture_upsert(url, *, json, timeout=15):
            upserted.append(json)
            return _FakeResponse({"result": {"status": "ok"}})

        # Override put for points upsert
        import sts_monitor.embeddings as mod
        original_put = mod.httpx.put

        def smart_put(url, *, json, timeout=15):
            if "/points" in url:
                return capture_upsert(url, json=json, timeout=timeout)
            return original_put(url, json=json, timeout=timeout)

        monkeypatch.setattr(mod.httpx, "put", smart_put)

        observations = [
            {
                "id": 1,
                "investigation_id": "inv-1",
                "source": "twitter",
                "claim": "Something happened",
                "url": "https://example.com/1",
                "captured_at": "2025-01-01T00:00:00Z",
                "reliability_hint": 0.8,
            },
            {
                "id": 2,
                "investigation_id": "inv-1",
                "source": "reddit",
                "claim": "Another thing happened",
                "url": "https://example.com/2",
                "captured_at": "2025-01-01T00:00:00Z",
                "reliability_hint": 0.6,
            },
        ]

        result = engine.index_observations(observations)
        assert result["indexed"] == 2
        assert result["failed"] == 0
        assert len(upserted) == 1  # single batch
        points = upserted[0]["points"]
        assert len(points) == 2
        assert points[0]["payload"]["observation_id"] == 1

    def test_index_observations_skips_empty_vectors(self, monkeypatch):
        """Observations with empty embedding vectors are skipped."""
        import sts_monitor.embeddings as mod

        call_count = [0]

        def fake_post(url, *, json, timeout=30):
            call_count[0] += 1
            # Return empty vector for second call
            if call_count[0] == 2:
                return _FakeResponse({"embedding": []})
            return _FakeResponse({"embedding": FAKE_VECTOR})

        def fake_put(url, *, json, timeout=15):
            return _FakeResponse({"result": {"status": "ok"}})

        def fake_get(url, *, timeout=15):
            return _FakeResponse({"result": {"points_count": 1}})

        monkeypatch.setattr(mod.httpx, "post", fake_post)
        monkeypatch.setattr(mod.httpx, "put", fake_put)
        monkeypatch.setattr(mod.httpx, "get", fake_get)

        embedder = OllamaEmbeddingClient()
        store = QdrantStore()
        engine = SemanticSearchEngine(embedder, store)

        observations = [
            {"id": 1, "investigation_id": "inv-1", "source": "s", "claim": "Good", "url": "u1", "captured_at": "", "reliability_hint": 0.8},
            {"id": 2, "investigation_id": "inv-1", "source": "s", "claim": "Bad embed", "url": "u2", "captured_at": "", "reliability_hint": 0.6},
            {"id": 3, "investigation_id": "inv-1", "source": "s", "claim": "Also good", "url": "u3", "captured_at": "", "reliability_hint": 0.7},
        ]

        result = engine.index_observations(observations)
        assert result["indexed"] == 2  # only 2 had valid vectors

    def test_search(self, monkeypatch):
        engine, get_responses, post_responses, put_responses = self._make_engine(monkeypatch)

        # Mock Qdrant search to return results
        post_responses["search"] = _FakeResponse({
            "result": [
                {
                    "id": "abc",
                    "score": 0.92,
                    "payload": {
                        "observation_id": 1,
                        "investigation_id": "inv-1",
                        "source": "twitter",
                        "claim": "earthquake in LA",
                        "url": "https://example.com/1",
                        "captured_at": "2025-01-01",
                    },
                },
            ]
        })

        result = engine.search("earthquake")
        assert isinstance(result, SemanticSearchResult)
        assert result.query == "earthquake"
        assert len(result.matches) == 1
        assert result.matches[0].observation_id == 1
        assert result.matches[0].score == 0.92
        assert result.search_latency_ms >= 0

    def test_search_with_investigation_filter(self, monkeypatch):
        engine, get_responses, post_responses, put_responses = self._make_engine(monkeypatch)

        captured_search_payload = {}

        def capture_search(json):
            captured_search_payload.update(json)
            return _FakeResponse({"result": []})

        post_responses["search"] = capture_search

        engine.search("test", investigation_id="inv-42")
        assert "filter" in captured_search_payload
        assert captured_search_payload["filter"]["must"][0]["match"]["value"] == "inv-42"

    def test_find_similar_excludes_self(self, monkeypatch):
        engine, get_responses, post_responses, put_responses = self._make_engine(monkeypatch)

        post_responses["search"] = _FakeResponse({
            "result": [
                {"id": "a", "score": 1.0, "payload": {"observation_id": 10, "investigation_id": "", "source": "", "claim": "self", "url": "", "captured_at": ""}},
                {"id": "b", "score": 0.9, "payload": {"observation_id": 20, "investigation_id": "", "source": "", "claim": "similar", "url": "", "captured_at": ""}},
                {"id": "c", "score": 0.8, "payload": {"observation_id": 30, "investigation_id": "", "source": "", "claim": "also similar", "url": "", "captured_at": ""}},
            ]
        })

        similar = engine.find_similar(observation_id=10, claim_text="self")
        assert len(similar) == 2
        assert all(m.observation_id != 10 for m in similar)

    def test_find_similar_respects_limit(self, monkeypatch):
        engine, get_responses, post_responses, put_responses = self._make_engine(monkeypatch)

        post_responses["search"] = _FakeResponse({
            "result": [
                {"id": "a", "score": 1.0, "payload": {"observation_id": 10, "investigation_id": "", "source": "", "claim": "", "url": "", "captured_at": ""}},
                {"id": "b", "score": 0.9, "payload": {"observation_id": 20, "investigation_id": "", "source": "", "claim": "", "url": "", "captured_at": ""}},
                {"id": "c", "score": 0.8, "payload": {"observation_id": 30, "investigation_id": "", "source": "", "claim": "", "url": "", "captured_at": ""}},
                {"id": "d", "score": 0.7, "payload": {"observation_id": 40, "investigation_id": "", "source": "", "claim": "", "url": "", "captured_at": ""}},
            ]
        })

        similar = engine.find_similar(observation_id=10, claim_text="test", limit=1)
        assert len(similar) == 1


# ── Dataclass tests ────────────────────────────────────────────────────


class TestDataclasses:
    def test_embedding_result_fields(self):
        r = EmbeddingResult(text="hi", vector=[0.1, 0.2], model="m", latency_ms=1.0)
        assert r.text == "hi"
        assert r.vector == [0.1, 0.2]

    def test_semantic_match_fields(self):
        m = SemanticMatch(
            observation_id=1, investigation_id="inv-1", source="s",
            claim="c", url="u", score=0.9, captured_at="2025-01-01",
        )
        assert m.score == 0.9

    def test_semantic_search_result_fields(self):
        r = SemanticSearchResult(query="q", matches=[], total_indexed=0, search_latency_ms=0.0)
        assert r.query == "q"
        assert r.matches == []
