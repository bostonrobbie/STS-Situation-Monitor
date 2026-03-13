"""Stress / edge-case tests for report generation, embeddings, research agent,
simulation, and privacy modules.

Covers:
  - report_generator.py: deterministic & LLM paths, code-fence stripping
  - embeddings.py: OllamaEmbeddingClient, QdrantStore, SemanticSearchEngine
  - research_agent.py: session lifecycle, thread safety, LLM parsing
  - simulation.py: batch sizes, noise flag, reliability bounds
  - privacy.py: UA rotation, DoH, Tor status check
"""
from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from sts_monitor.pipeline import Observation, PipelineResult
from sts_monitor.report_generator import ReportGenerator, IntelligenceReport
from sts_monitor.embeddings import (
    OllamaEmbeddingClient,
    EmbeddingResult,
    QdrantStore,
    SemanticSearchEngine,
    _observation_point_id,
)
from sts_monitor.research_agent import ResearchAgent, ResearchSession
from sts_monitor.simulation import generate_simulated_observations
from sts_monitor.privacy import (
    PrivacyConfig,
    get_random_ua,
    get_privacy_headers,
    get_proxy_url,
    build_private_client,
    privacy_delay,
    resolve_doh,
    check_tor_status,
    _USER_AGENTS,
)

pytestmark = pytest.mark.unit


# ── helpers ────────────────────────────────────────────────────────────

def _obs(claim: str = "test claim", source: str = "test:src",
         reliability: float = 0.5, url: str = "https://example.com") -> Observation:
    return Observation(
        source=source, claim=claim, url=url,
        captured_at=datetime.now(UTC), reliability_hint=reliability,
    )


def _pipeline_result(
    accepted: list[Observation] | None = None,
    dropped: list[Observation] | None = None,
    disputed: list[str] | None = None,
    confidence: float = 0.6,
) -> PipelineResult:
    return PipelineResult(
        accepted=accepted or [],
        dropped=dropped or [],
        deduplicated=[],
        disputed_claims=disputed or [],
        summary="test",
        confidence=confidence,
    )


def _mock_llm(return_value: str | None = "") -> MagicMock:
    llm = MagicMock()
    llm.summarize = MagicMock(return_value=return_value)
    return llm


# =====================================================================
#  report_generator.py
# =====================================================================

class TestDeterministicReportEmpty:
    """Deterministic report with zero observations."""

    def test_empty_observations(self):
        gen = ReportGenerator()
        pr = _pipeline_result()
        report = gen.generate("inv-1", "topic", pr)
        assert isinstance(report, IntelligenceReport)
        assert report.generation_method == "deterministic"
        assert report.key_findings == []
        assert len(report.intelligence_gaps) > 0  # gaps should be populated

    def test_single_observation(self):
        gen = ReportGenerator()
        obs = [_obs("single claim", reliability=0.8)]
        pr = _pipeline_result(accepted=obs)
        report = gen.generate("inv-2", "topic", pr)
        assert len(report.key_findings) == 1
        assert report.key_findings[0]["confidence"] == "high"

    def test_many_observations(self):
        gen = ReportGenerator()
        obs = [_obs(f"claim {i}", reliability=0.6) for i in range(50)]
        pr = _pipeline_result(accepted=obs)
        report = gen.generate("inv-3", "topic", pr)
        # Max 7 key findings
        assert len(report.key_findings) <= 7

    def test_none_claims(self):
        """Observation with empty claim should not crash."""
        gen = ReportGenerator()
        obs = [_obs("", reliability=0.9)]
        pr = _pipeline_result(accepted=obs)
        report = gen.generate("inv-4", "topic", pr)
        assert isinstance(report, IntelligenceReport)

    def test_all_reliability_zero(self):
        gen = ReportGenerator()
        obs = [_obs(f"c{i}", reliability=0.0) for i in range(5)]
        pr = _pipeline_result(accepted=obs)
        report = gen.generate("inv-5", "topic", pr)
        for kf in report.key_findings:
            assert kf["confidence"] == "low"

    def test_all_reliability_one(self):
        gen = ReportGenerator()
        obs = [_obs(f"c{i}", reliability=1.0) for i in range(5)]
        pr = _pipeline_result(accepted=obs)
        report = gen.generate("inv-6", "topic", pr)
        for kf in report.key_findings:
            assert kf["confidence"] == "high"

    def test_disputed_claims_create_gaps(self):
        gen = ReportGenerator()
        pr = _pipeline_result(disputed=["claim A is disputed"])
        report = gen.generate("inv-7", "topic", pr)
        assert any("disputed" in g.lower() for g in report.intelligence_gaps)

    def test_to_dict_roundtrip(self):
        gen = ReportGenerator()
        obs = [_obs("claim", reliability=0.7)]
        pr = _pipeline_result(accepted=obs)
        report = gen.generate("inv-8", "topic", pr)
        d = report.to_dict()
        assert d["investigation_id"] == "inv-8"
        assert isinstance(d["generated_at"], str)

    def test_to_markdown(self):
        gen = ReportGenerator()
        obs = [_obs("important claim", reliability=0.9)]
        pr = _pipeline_result(accepted=obs)
        report = gen.generate("inv-9", "topic X", pr)
        md = report.to_markdown()
        assert "# Intelligence Brief: topic X" in md
        assert "important claim" in md


# ── LLM report path ──────────────────────────────────────────────────

class TestLLMReportPath:
    def _valid_json(self) -> str:
        return json.dumps({
            "executive_summary": "Summary here",
            "key_findings": [{"finding": "f1", "confidence": "high", "sources": "s1"}],
            "entity_analysis": "entities",
            "geographic_overview": "geo",
            "source_assessment": "sources",
            "intelligence_gaps": ["gap1"],
            "recommended_actions": ["action1"],
        })

    def test_valid_json_response(self):
        llm = _mock_llm(self._valid_json())
        gen = ReportGenerator(llm_client=llm)
        pr = _pipeline_result(accepted=[_obs()])
        report = gen.generate("inv-llm-1", "topic", pr)
        assert report.generation_method == "llm"
        assert report.executive_summary == "Summary here"

    def test_invalid_json_falls_back(self):
        llm = _mock_llm("this is not json at all")
        gen = ReportGenerator(llm_client=llm)
        pr = _pipeline_result(accepted=[_obs()])
        report = gen.generate("inv-llm-2", "topic", pr)
        assert report.generation_method == "deterministic"

    def test_empty_string_falls_back(self):
        llm = _mock_llm("")
        gen = ReportGenerator(llm_client=llm)
        pr = _pipeline_result(accepted=[_obs()])
        report = gen.generate("inv-llm-3", "topic", pr)
        assert report.generation_method == "deterministic"

    def test_none_response_falls_back(self):
        llm = _mock_llm(None)
        gen = ReportGenerator(llm_client=llm)
        pr = _pipeline_result(accepted=[_obs()])
        report = gen.generate("inv-llm-4", "topic", pr)
        # BUG: if LLM returns None, .strip() will crash with
        # AttributeError: 'NoneType' object has no attribute 'strip'
        # This falls back to deterministic, which is acceptable.
        assert report.generation_method == "deterministic"

    def test_code_fence_json(self):
        """```json\\n{...}\\n```"""
        llm = _mock_llm("```json\n" + self._valid_json() + "\n```")
        gen = ReportGenerator(llm_client=llm)
        pr = _pipeline_result(accepted=[_obs()])
        report = gen.generate("inv-llm-5", "topic", pr)
        assert report.generation_method == "llm"

    def test_code_fence_plain(self):
        """```\\n{...}\\n```"""
        llm = _mock_llm("```\n" + self._valid_json() + "\n```")
        gen = ReportGenerator(llm_client=llm)
        pr = _pipeline_result(accepted=[_obs()])
        report = gen.generate("inv-llm-6", "topic", pr)
        assert report.generation_method == "llm"

    def test_no_fences(self):
        llm = _mock_llm(self._valid_json())
        gen = ReportGenerator(llm_client=llm)
        pr = _pipeline_result(accepted=[_obs()])
        report = gen.generate("inv-llm-7", "topic", pr)
        assert report.generation_method == "llm"

    def test_partial_fence_opening_only(self):
        """Opening fence but no closing -- should fail JSON parse, fallback."""
        llm = _mock_llm("```json\n" + self._valid_json())
        gen = ReportGenerator(llm_client=llm)
        pr = _pipeline_result(accepted=[_obs()])
        report = gen.generate("inv-llm-8", "topic", pr)
        # The stripping logic only removes leading ```, so the JSON should still parse
        assert report.generation_method == "llm"

    def test_llm_raises_exception(self):
        llm = MagicMock()
        llm.summarize.side_effect = RuntimeError("LLM down")
        gen = ReportGenerator(llm_client=llm)
        pr = _pipeline_result(accepted=[_obs()])
        report = gen.generate("inv-llm-9", "topic", pr)
        assert report.generation_method == "deterministic"


# =====================================================================
#  embeddings.py
# =====================================================================

class TestOllamaEmbeddingClient:
    @patch("sts_monitor.embeddings.httpx.post")
    def test_embed_basic(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"embedding": [0.1, 0.2, 0.3]}),
            raise_for_status=MagicMock(),
        )
        client = OllamaEmbeddingClient()
        result = client.embed("hello")
        assert result.vector == [0.1, 0.2, 0.3]
        assert result.text == "hello"

    @patch("sts_monitor.embeddings.httpx.post")
    def test_embed_empty_text(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"embedding": [0.0]}),
            raise_for_status=MagicMock(),
        )
        client = OllamaEmbeddingClient()
        result = client.embed("")
        assert result.text == ""

    @patch("sts_monitor.embeddings.httpx.post")
    def test_embed_unicode(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"embedding": [0.5]}),
            raise_for_status=MagicMock(),
        )
        client = OllamaEmbeddingClient()
        result = client.embed("Привет мир 🌍")
        assert result.text == "Привет мир 🌍"

    @patch("sts_monitor.embeddings.httpx.post")
    def test_embed_very_long_text(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"embedding": [0.1]}),
            raise_for_status=MagicMock(),
        )
        client = OllamaEmbeddingClient()
        long_text = "x" * 100_000
        result = client.embed(long_text)
        assert len(result.text) == 100_000

    @patch("sts_monitor.embeddings.httpx.post")
    def test_embed_batch_preserves_index_on_failure(self, mock_post):
        """Failed embeddings should produce None at the same index."""
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise ConnectionError("boom")
            resp = MagicMock()
            resp.json.return_value = {"embedding": [0.1 * call_count]}
            resp.raise_for_status = MagicMock()
            return resp

        mock_post.side_effect = side_effect
        client = OllamaEmbeddingClient()
        results = client.embed_batch(["a", "b", "c"])
        assert len(results) == 3
        assert results[0] is not None
        assert results[1] is None  # failed
        assert results[2] is not None


class TestQdrantStore:
    @patch("sts_monitor.embeddings.httpx.put")
    def test_upsert_empty(self, mock_put):
        store = QdrantStore()
        count = store.upsert([])
        assert count == 0
        mock_put.assert_not_called()

    @patch("sts_monitor.embeddings.httpx.put")
    def test_upsert_batch(self, mock_put):
        mock_put.return_value = MagicMock(raise_for_status=MagicMock())
        store = QdrantStore()
        points = [{"id": str(i), "vector": [0.1], "payload": {}} for i in range(5)]
        count = store.upsert(points)
        assert count == 5


class TestSemanticSearchEngine:
    def _mock_engine(self):
        embedder = MagicMock(spec=OllamaEmbeddingClient)
        store = MagicMock(spec=QdrantStore)
        return SemanticSearchEngine(embedder, store), embedder, store

    def test_index_observations_empty(self):
        engine, embedder, store = self._mock_engine()
        result = engine.index_observations([])
        assert result["indexed"] == 0
        assert result["failed"] == 0

    def test_index_observations_skips_none_embeddings(self):
        engine, embedder, store = self._mock_engine()
        # embed_batch returns [valid, None, valid]
        embedder.embed_batch.return_value = [
            EmbeddingResult(text="a", vector=[0.1], model="m", latency_ms=1),
            None,
            EmbeddingResult(text="c", vector=[0.3], model="m", latency_ms=1),
        ]
        store.upsert.return_value = 2
        store.count.return_value = 2
        observations = [
            {"id": 1, "claim": "c1", "investigation_id": "i1", "source": "s", "url": "u", "captured_at": "t", "reliability_hint": 0.5},
            {"id": 2, "claim": "c2", "investigation_id": "i1", "source": "s", "url": "u", "captured_at": "t", "reliability_hint": 0.5},
            {"id": 3, "claim": "c3", "investigation_id": "i1", "source": "s", "url": "u", "captured_at": "t", "reliability_hint": 0.5},
        ]
        result = engine.index_observations(observations)
        # upsert called with 2 points (skipping None)
        args, _ = store.upsert.call_args
        assert len(args[0]) == 2

    def test_index_observations_skips_empty_vector(self):
        engine, embedder, store = self._mock_engine()
        embedder.embed_batch.return_value = [
            EmbeddingResult(text="a", vector=[], model="m", latency_ms=1),
        ]
        store.count.return_value = 0
        observations = [
            {"id": 1, "claim": "c1", "investigation_id": "i1", "source": "s", "url": "u", "captured_at": "t", "reliability_hint": 0.5},
        ]
        result = engine.index_observations(observations)
        store.upsert.assert_not_called()
        assert result["indexed"] == 0

    def test_search_with_empty_query(self):
        engine, embedder, store = self._mock_engine()
        embedder.embed.return_value = EmbeddingResult(text="", vector=[0.0], model="m", latency_ms=1)
        store.search.return_value = []
        store.count.return_value = 0
        result = engine.search("")
        assert result.query == ""
        assert result.matches == []

    def test_observation_point_id_deterministic(self):
        id1 = _observation_point_id(42)
        id2 = _observation_point_id(42)
        assert id1 == id2
        # different input -> different id
        id3 = _observation_point_id(43)
        assert id1 != id3


# =====================================================================
#  research_agent.py
# =====================================================================

class TestResearchAgentSessions:
    def _agent(self, llm=None):
        return ResearchAgent(
            llm_client=llm or _mock_llm(),
            max_iterations=2,
            inter_iteration_delay_s=0,
        )

    def test_get_session_nonexistent(self):
        agent = self._agent()
        assert agent.get_session("nope") is None

    def test_list_sessions_empty(self):
        agent = self._agent()
        assert agent.list_sessions() == []

    def test_stop_nonexistent_returns_false(self):
        agent = self._agent()
        assert agent.stop_session("nope") is False

    def test_concurrent_get_session(self):
        agent = self._agent()
        # Pre-populate a session
        session = ResearchSession(
            session_id="s1", topic="t", status="running", started_at=datetime.now(UTC),
        )
        agent._sessions["s1"] = session

        results = []
        errors = []

        def reader():
            try:
                for _ in range(100):
                    s = agent.get_session("s1")
                    results.append(s)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert all(r is session for r in results)

    @patch("sts_monitor.research_agent.SearchConnector")
    @patch("sts_monitor.research_agent.NitterConnector")
    @patch("sts_monitor.research_agent.get_accounts_for_categories")
    @patch("sts_monitor.research_agent.RSSConnector")
    @patch("sts_monitor.research_agent.get_curated_feeds")
    def test_run_with_empty_connectors(
        self, mock_feeds, mock_rss_cls, mock_accounts, mock_nitter_cls, mock_search_cls
    ):
        """All connectors return empty results; should complete without error."""
        from sts_monitor.connectors.base import ConnectorResult

        empty = ConnectorResult(connector="test", observations=[])
        mock_search_cls.return_value.collect.return_value = empty
        mock_nitter_cls.return_value.collect.return_value = empty
        mock_rss_cls.return_value.collect.return_value = empty
        mock_feeds.return_value = []
        mock_accounts.return_value = []

        llm = _mock_llm(json.dumps({
            "key_findings": [],
            "confidence": 0.5,
            "new_search_queries": [],
            "urls_to_scrape": [],
            "twitter_accounts_to_follow": [],
            "twitter_search_queries": [],
            "assessment": "Nothing found",
            "should_continue": False,
            "reasoning": "No data",
        }))
        agent = self._agent(llm)

        # Patch telegram import to avoid ImportError
        with patch.dict("sys.modules", {"sts_monitor.connectors.telegram": MagicMock()}):
            with patch("sts_monitor.research_agent.TelegramConnector", create=True, side_effect=ImportError):
                session = agent.run("s1", "test topic")

        assert session.status in ("completed", "stopped")

    def test_ask_llm_returns_none(self):
        llm = _mock_llm(None)
        agent = self._agent(llm)
        result = agent._ask_llm("topic", 1, [], [], 0)
        # Should not crash; returns fallback dict
        assert isinstance(result, dict)
        assert result["should_continue"] is False

    def test_ask_llm_returns_empty_string(self):
        llm = _mock_llm("")
        agent = self._agent(llm)
        result = agent._ask_llm("topic", 1, [], [], 0)
        assert isinstance(result, dict)

    def test_ask_llm_returns_malformed_json(self):
        llm = _mock_llm("{not valid json")
        agent = self._agent(llm)
        result = agent._ask_llm("topic", 1, [], [], 0)
        assert isinstance(result, dict)
        assert result["should_continue"] is False

    def test_ask_llm_valid_json(self):
        valid = json.dumps({
            "key_findings": ["finding1"],
            "confidence": 0.8,
            "new_search_queries": ["q1"],
            "urls_to_scrape": [],
            "should_continue": True,
            "assessment": "Good",
            "reasoning": "More data needed",
        })
        llm = _mock_llm(valid)
        agent = self._agent(llm)
        result = agent._ask_llm("topic", 1, [_obs()], [], 1)
        assert result["key_findings"] == ["finding1"]
        assert result["should_continue"] is True


# =====================================================================
#  simulation.py
# =====================================================================

class TestSimulation:
    def test_batch_size_zero(self):
        obs = generate_simulated_observations("topic", batch_size=0, include_noise=False)
        assert obs == []

    def test_batch_size_zero_with_noise(self):
        obs = generate_simulated_observations("topic", batch_size=0, include_noise=True)
        # Only noise observations
        assert len(obs) == 2

    def test_batch_size_one(self):
        obs = generate_simulated_observations("topic", batch_size=1, include_noise=False)
        assert len(obs) == 1

    def test_batch_size_100(self):
        obs = generate_simulated_observations("topic", batch_size=100, include_noise=False)
        assert len(obs) == 100

    def test_include_noise_true(self):
        obs = generate_simulated_observations("topic", batch_size=5, include_noise=True)
        assert len(obs) == 7  # 5 + 2 noise
        # Noise observations have reliability 0.0 and 1.0
        noise_reliabilities = {o.reliability_hint for o in obs if "noise" in o.source}
        assert 0.0 in noise_reliabilities
        assert 1.0 in noise_reliabilities

    def test_include_noise_false(self):
        obs = generate_simulated_observations("topic", batch_size=5, include_noise=False)
        assert len(obs) == 5

    def test_all_reliability_in_range(self):
        obs = generate_simulated_observations("topic", batch_size=100, include_noise=True)
        for o in obs:
            assert 0.0 <= o.reliability_hint <= 1.0, (
                f"reliability_hint {o.reliability_hint} out of range for {o.source}"
            )

    def test_sources_are_strings(self):
        obs = generate_simulated_observations("topic", batch_size=10)
        for o in obs:
            assert isinstance(o.source, str)
            assert len(o.source) > 0


# =====================================================================
#  privacy.py
# =====================================================================

class TestUserAgentRotator:
    def test_returns_string(self):
        ua = get_random_ua()
        assert isinstance(ua, str)
        assert len(ua) > 10

    def test_returns_from_pool(self):
        for _ in range(50):
            ua = get_random_ua()
            assert ua in _USER_AGENTS

    def test_privacy_headers_with_rotation(self):
        config = PrivacyConfig(rotate_ua=True)
        headers = get_privacy_headers(config)
        assert "User-Agent" in headers
        assert headers["User-Agent"] in _USER_AGENTS

    def test_privacy_headers_without_rotation(self):
        config = PrivacyConfig(rotate_ua=False)
        headers = get_privacy_headers(config)
        assert headers["User-Agent"] == _USER_AGENTS[0]

    def test_referrer_stripped(self):
        config = PrivacyConfig(strip_referrer=True)
        headers = get_privacy_headers(config)
        assert headers["Referer"] == ""

    def test_referrer_not_stripped(self):
        config = PrivacyConfig(strip_referrer=False)
        headers = get_privacy_headers(config)
        assert "Referer" not in headers


class TestProxyUrl:
    def test_no_proxy(self):
        config = PrivacyConfig(use_tor=False, proxy_url=None)
        assert get_proxy_url(config) is None

    def test_tor_proxy(self):
        config = PrivacyConfig(use_tor=True)
        assert get_proxy_url(config) == "socks5://127.0.0.1:9050"

    def test_custom_proxy_overrides_tor(self):
        config = PrivacyConfig(use_tor=True, proxy_url="http://proxy:8080")
        assert get_proxy_url(config) == "http://proxy:8080"


class TestDoHResolver:
    @patch("sts_monitor.privacy.httpx.get")
    def test_resolve_doh_success(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={
                "Answer": [
                    {"type": 1, "data": "1.2.3.4"},
                    {"type": 1, "data": "5.6.7.8"},
                    {"type": 28, "data": "::1"},  # AAAA, should be skipped
                ]
            }),
            raise_for_status=MagicMock(),
        )
        ips = resolve_doh("example.com")
        assert ips == ["1.2.3.4", "5.6.7.8"]

    @patch("sts_monitor.privacy.httpx.get")
    def test_resolve_doh_failure(self, mock_get):
        mock_get.side_effect = ConnectionError("network down")
        ips = resolve_doh("example.com")
        assert ips == []

    @patch("sts_monitor.privacy.httpx.get")
    def test_resolve_doh_empty_answer(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"Answer": []}),
            raise_for_status=MagicMock(),
        )
        ips = resolve_doh("example.com")
        assert ips == []


class TestCheckTorStatus:
    @patch("sts_monitor.privacy.httpx.Client")
    def test_tor_available(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"IsTor": True, "IP": "198.51.100.1"}),
            raise_for_status=MagicMock(),
        )
        mock_client_cls.return_value = mock_client
        status = check_tor_status()
        assert status["tor_available"] is True
        assert status["is_tor"] is True

    @patch("sts_monitor.privacy.httpx.Client")
    def test_tor_unavailable(self, mock_client_cls):
        mock_client_cls.side_effect = ConnectionError("no tor")
        status = check_tor_status()
        assert status["tor_available"] is False
        assert status["is_tor"] is False


class TestPrivacyDelay:
    @patch("sts_monitor.privacy.time.sleep")
    def test_no_jitter(self, mock_sleep):
        config = PrivacyConfig(add_jitter=False, min_request_delay_s=0.5)
        privacy_delay(config)
        mock_sleep.assert_called_once_with(0.5)

    @patch("sts_monitor.privacy.time.sleep")
    def test_with_jitter(self, mock_sleep):
        config = PrivacyConfig(add_jitter=True, min_request_delay_s=0.5, max_request_delay_s=2.0)
        privacy_delay(config)
        mock_sleep.assert_called_once()
        delay = mock_sleep.call_args[0][0]
        assert 0.5 <= delay <= 2.0
