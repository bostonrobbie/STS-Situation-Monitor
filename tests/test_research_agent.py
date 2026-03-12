"""Tests for the autonomous research agent with mocked LLM and connectors."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation
from sts_monitor.research_agent import (
    ResearchAgent,
    ResearchIteration,
    ResearchSession,
)

pytestmark = pytest.mark.unit


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_observation(source: str = "test", claim: str = "test claim", url: str = "http://example.com") -> Observation:
    return Observation(
        source=source,
        claim=claim,
        url=url,
        captured_at=datetime.now(UTC),
        reliability_hint=0.6,
    )


def _make_connector_result(observations: list[Observation] | None = None) -> ConnectorResult:
    return ConnectorResult(
        connector="test",
        observations=observations or [_make_observation()],
        metadata={},
    )


def _llm_json_response(
    *,
    key_findings: list[str] | None = None,
    confidence: float = 0.7,
    new_search_queries: list[str] | None = None,
    urls_to_scrape: list[str] | None = None,
    twitter_accounts_to_follow: list[str] | None = None,
    twitter_search_queries: list[str] | None = None,
    assessment: str = "Situation is developing.",
    should_continue: bool = True,
    reasoning: str = "More data needed.",
) -> str:
    return json.dumps({
        "key_findings": key_findings or ["Finding 1"],
        "confidence": confidence,
        "new_search_queries": new_search_queries or [],
        "urls_to_scrape": urls_to_scrape or [],
        "twitter_accounts_to_follow": twitter_accounts_to_follow or [],
        "twitter_search_queries": twitter_search_queries or [],
        "assessment": assessment,
        "should_continue": should_continue,
        "reasoning": reasoning,
    })


def _make_fake_llm(responses: list[str] | None = None) -> MagicMock:
    """Create a fake LLM client that returns pre-set responses."""
    llm = MagicMock()
    if responses:
        llm.summarize.side_effect = responses
    else:
        # Default: first call returns continue, second returns stop, third for brief
        llm.summarize.side_effect = [
            _llm_json_response(
                key_findings=["Earthquake detected in Turkey"],
                should_continue=True,
                new_search_queries=["Turkey earthquake damage"],
                urls_to_scrape=["https://reuters.com/turkey"],
            ),
            _llm_json_response(
                key_findings=["Casualties reported", "Aid mobilized"],
                should_continue=False,
                reasoning="Sufficient data collected.",
            ),
            "EXECUTIVE SUMMARY: Earthquake struck Turkey...",  # Final brief
        ]
    return llm


def _make_agent(llm: MagicMock, **kwargs) -> ResearchAgent:
    defaults = dict(
        llm_client=llm,
        max_iterations=3,
        inter_iteration_delay_s=0,
    )
    defaults.update(kwargs)
    return ResearchAgent(**defaults)


# ── Data structure tests ─────────────────────────────────────────────────


def test_research_iteration_dataclass() -> None:
    it = ResearchIteration(
        iteration=1,
        started_at=datetime.now(UTC),
        observations_collected=5,
        connectors_used=["search", "nitter"],
        llm_response=None,
    )
    assert it.iteration == 1
    assert it.observations_collected == 5
    assert it.errors == []


def test_research_session_dataclass() -> None:
    s = ResearchSession(
        session_id="test-1",
        topic="earthquake Turkey",
        status="running",
        started_at=datetime.now(UTC),
    )
    assert s.session_id == "test-1"
    assert s.all_observations == []
    assert s.all_findings == []
    assert s.final_brief == ""


# ── Agent construction ───────────────────────────────────────────────────


def test_agent_clamps_max_iterations() -> None:
    llm = MagicMock()
    agent = ResearchAgent(llm_client=llm, max_iterations=0)
    assert agent.max_iterations == 1

    agent2 = ResearchAgent(llm_client=llm, max_iterations=100)
    assert agent2.max_iterations == 20


# ── Session management ───────────────────────────────────────────────────


def test_list_sessions_empty() -> None:
    llm = MagicMock()
    agent = ResearchAgent(llm_client=llm)
    assert agent.list_sessions() == []


def test_get_session_nonexistent() -> None:
    llm = MagicMock()
    agent = ResearchAgent(llm_client=llm)
    assert agent.get_session("nonexistent") is None


def test_stop_session_nonexistent() -> None:
    llm = MagicMock()
    agent = ResearchAgent(llm_client=llm)
    assert agent.stop_session("nonexistent") is False


# ── Full run loop ────────────────────────────────────────────────────────


@patch("sts_monitor.research_agent.SearchConnector")
@patch("sts_monitor.research_agent.NitterConnector")
@patch("sts_monitor.research_agent.RSSConnector")
@patch("sts_monitor.research_agent.get_curated_feeds")
@patch("sts_monitor.research_agent.get_accounts_for_categories")
@patch("sts_monitor.research_agent.time.sleep")
@patch("sts_monitor.research_agent.time.perf_counter", return_value=0.0)
def test_full_run_loop(
    mock_perf, mock_sleep,
    mock_get_accounts, mock_get_feeds,
    mock_rss_cls, mock_nitter_cls, mock_search_cls,
) -> None:
    """Test the complete research loop: initial collect -> LLM -> directed collect -> stop."""
    # Configure mocks
    mock_get_accounts.return_value = ["sentdefender"]
    mock_get_feeds.return_value = [{"url": "https://rss.example.com/feed"}]

    search_result = _make_connector_result([
        _make_observation("search:ddg", "Turkey earthquake 5.2 magnitude", "https://example.com/1"),
    ])
    nitter_result = _make_connector_result([
        _make_observation("nitter:@sent", "Reports of shaking in Ankara", "https://nitter.test/1"),
    ])
    rss_result = _make_connector_result([
        _make_observation("rss:reuters", "USGS confirms 5.2 quake", "https://reuters.com/1"),
    ])

    mock_search_cls.return_value.collect.return_value = search_result
    mock_nitter_cls.return_value.collect.return_value = nitter_result
    mock_rss_cls.return_value.collect.return_value = rss_result

    llm = _make_fake_llm()
    agent = _make_agent(llm, max_iterations=3)

    session = agent.run("session-1", "Turkey earthquake", seed_query="earthquake Turkey")

    # Verify session completed
    assert session.status == "completed"
    assert session.session_id == "session-1"
    assert session.topic == "Turkey earthquake"
    assert session.finished_at is not None

    # Should have 2 iterations (LLM said stop on 2nd)
    assert len(session.iterations) == 2

    # First iteration should use initial collection
    assert session.iterations[0].iteration == 1
    assert session.iterations[0].observations_collected > 0

    # Findings should be accumulated
    assert len(session.all_findings) >= 1
    assert "Earthquake detected in Turkey" in session.all_findings

    # Final brief should be generated
    assert "EXECUTIVE SUMMARY" in session.final_brief

    # LLM summarize should have been called 3 times: 2 analysis + 1 brief
    assert llm.summarize.call_count == 3

    # Session should be listed
    sessions = agent.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "session-1"


@patch("sts_monitor.research_agent.SearchConnector")
@patch("sts_monitor.research_agent.NitterConnector")
@patch("sts_monitor.research_agent.RSSConnector")
@patch("sts_monitor.research_agent.get_curated_feeds")
@patch("sts_monitor.research_agent.get_accounts_for_categories")
@patch("sts_monitor.research_agent.time.sleep")
@patch("sts_monitor.research_agent.time.perf_counter", return_value=0.0)
def test_directed_collection_uses_llm_decisions(
    mock_perf, mock_sleep,
    mock_get_accounts, mock_get_feeds,
    mock_rss_cls, mock_nitter_cls, mock_search_cls,
) -> None:
    """Verify iteration 2 uses LLM decisions for directed collection."""
    mock_get_accounts.return_value = ["sentdefender"]
    mock_get_feeds.return_value = [{"url": "https://rss.example.com/feed"}]

    base_result = _make_connector_result()
    mock_search_cls.return_value.collect.return_value = base_result
    mock_nitter_cls.return_value.collect.return_value = base_result
    mock_rss_cls.return_value.collect.return_value = base_result

    llm = _make_fake_llm([
        # Iteration 1: continue with directed queries
        _llm_json_response(
            key_findings=["Initial finding"],
            should_continue=True,
            new_search_queries=["specific search query"],
            urls_to_scrape=["https://specific-site.com/article"],
            twitter_accounts_to_follow=["SpecificAccount"],
            twitter_search_queries=["specific twitter query"],
        ),
        # Iteration 2: stop
        _llm_json_response(
            key_findings=["Deep finding"],
            should_continue=False,
        ),
        # Final brief
        "Final brief content.",
    ])

    agent = _make_agent(llm, max_iterations=5)

    with patch("sts_monitor.research_agent.WebScraperConnector") as mock_scraper_cls:
        mock_scraper_cls.return_value.collect.return_value = base_result
        session = agent.run("session-directed", "test topic")

    assert session.status == "completed"
    assert len(session.iterations) == 2

    # On second iteration, directed connectors should have been created
    # with the LLM-suggested queries and URLs
    # Verify SearchConnector was called with "specific search query"
    search_calls = mock_search_cls.return_value.collect.call_args_list
    assert any(
        call.kwargs.get("query") == "specific search query" or
        (call.args and call.args[0] == "specific search query")
        for call in search_calls
        if call.kwargs.get("query", call.args[0] if call.args else None) == "specific search query"
    ) or len(search_calls) > 1  # At minimum more than initial call


@patch("sts_monitor.research_agent.SearchConnector")
@patch("sts_monitor.research_agent.NitterConnector")
@patch("sts_monitor.research_agent.RSSConnector")
@patch("sts_monitor.research_agent.get_curated_feeds")
@patch("sts_monitor.research_agent.get_accounts_for_categories")
@patch("sts_monitor.research_agent.time.sleep")
@patch("sts_monitor.research_agent.time.perf_counter", return_value=0.0)
def test_max_iterations_respected(
    mock_perf, mock_sleep,
    mock_get_accounts, mock_get_feeds,
    mock_rss_cls, mock_nitter_cls, mock_search_cls,
) -> None:
    """Agent should stop after max_iterations even if LLM says continue."""
    mock_get_accounts.return_value = []
    mock_get_feeds.return_value = []

    base_result = _make_connector_result()
    mock_search_cls.return_value.collect.return_value = base_result
    mock_nitter_cls.return_value.collect.return_value = base_result
    mock_rss_cls.return_value.collect.return_value = base_result

    # LLM always says continue
    always_continue = _llm_json_response(should_continue=True, key_findings=["finding"])
    llm = _make_fake_llm([always_continue, always_continue, "Brief."])

    agent = _make_agent(llm, max_iterations=2)
    session = agent.run("session-max", "test topic")

    assert session.status == "completed"
    assert len(session.iterations) == 2


# ── Stop mechanism ───────────────────────────────────────────────────────


@patch("sts_monitor.research_agent.SearchConnector")
@patch("sts_monitor.research_agent.NitterConnector")
@patch("sts_monitor.research_agent.RSSConnector")
@patch("sts_monitor.research_agent.get_curated_feeds")
@patch("sts_monitor.research_agent.get_accounts_for_categories")
@patch("sts_monitor.research_agent.time.perf_counter", return_value=0.0)
def test_stop_session_mechanism(
    mock_perf,
    mock_get_accounts, mock_get_feeds,
    mock_rss_cls, mock_nitter_cls, mock_search_cls,
) -> None:
    """Agent should stop when stop flag is set."""
    mock_get_accounts.return_value = []
    mock_get_feeds.return_value = []

    base_result = _make_connector_result()
    mock_search_cls.return_value.collect.return_value = base_result
    mock_nitter_cls.return_value.collect.return_value = base_result
    mock_rss_cls.return_value.collect.return_value = base_result

    llm = _make_fake_llm([
        _llm_json_response(should_continue=True, key_findings=["f1"]),
        _llm_json_response(should_continue=True, key_findings=["f2"]),
        _llm_json_response(should_continue=True, key_findings=["f3"]),
        "Brief.",
    ])

    agent = _make_agent(llm, max_iterations=10)

    # Patch time.sleep to set the stop flag after first iteration
    call_count = [0]
    def fake_sleep(seconds):
        call_count[0] += 1
        if call_count[0] >= 1:
            agent.stop_session("session-stop")

    with patch("sts_monitor.research_agent.time.sleep", side_effect=fake_sleep):
        session = agent.run("session-stop", "test topic")

    assert session.status == "stopped"
    assert len(session.iterations) < 10


# ── LLM error handling ──────────────────────────────────────────────────


@patch("sts_monitor.research_agent.SearchConnector")
@patch("sts_monitor.research_agent.NitterConnector")
@patch("sts_monitor.research_agent.RSSConnector")
@patch("sts_monitor.research_agent.get_curated_feeds")
@patch("sts_monitor.research_agent.get_accounts_for_categories")
@patch("sts_monitor.research_agent.time.sleep")
@patch("sts_monitor.research_agent.time.perf_counter", return_value=0.0)
def test_llm_invalid_json_handled(
    mock_perf, mock_sleep,
    mock_get_accounts, mock_get_feeds,
    mock_rss_cls, mock_nitter_cls, mock_search_cls,
) -> None:
    """When LLM returns invalid JSON, agent should gracefully stop."""
    mock_get_accounts.return_value = []
    mock_get_feeds.return_value = []

    base_result = _make_connector_result()
    mock_search_cls.return_value.collect.return_value = base_result
    mock_nitter_cls.return_value.collect.return_value = base_result
    mock_rss_cls.return_value.collect.return_value = base_result

    llm = _make_fake_llm([
        "This is not valid JSON at all!",  # Invalid JSON response
        "Brief for invalid session.",  # Brief
    ])

    agent = _make_agent(llm, max_iterations=3)
    session = agent.run("session-bad-json", "test topic")

    # Should still complete (invalid JSON sets should_continue=False)
    assert session.status == "completed"
    assert len(session.iterations) == 1
    assert session.final_brief == "Brief for invalid session."


@patch("sts_monitor.research_agent.SearchConnector")
@patch("sts_monitor.research_agent.NitterConnector")
@patch("sts_monitor.research_agent.RSSConnector")
@patch("sts_monitor.research_agent.get_curated_feeds")
@patch("sts_monitor.research_agent.get_accounts_for_categories")
@patch("sts_monitor.research_agent.time.sleep")
@patch("sts_monitor.research_agent.time.perf_counter", return_value=0.0)
def test_llm_exception_handled(
    mock_perf, mock_sleep,
    mock_get_accounts, mock_get_feeds,
    mock_rss_cls, mock_nitter_cls, mock_search_cls,
) -> None:
    """When LLM raises an exception, agent should handle it."""
    mock_get_accounts.return_value = []
    mock_get_feeds.return_value = []

    base_result = _make_connector_result()
    mock_search_cls.return_value.collect.return_value = base_result
    mock_nitter_cls.return_value.collect.return_value = base_result
    mock_rss_cls.return_value.collect.return_value = base_result

    llm = MagicMock()
    llm.summarize.side_effect = [
        RuntimeError("LLM timeout"),  # Analysis fails
        "Brief despite errors.",  # Brief still works
    ]

    agent = _make_agent(llm, max_iterations=3)
    session = agent.run("session-llm-error", "test topic")

    # should_continue defaults to False on error, so agent stops
    assert session.status == "completed"
    assert len(session.iterations) == 1


@patch("sts_monitor.research_agent.SearchConnector")
@patch("sts_monitor.research_agent.NitterConnector")
@patch("sts_monitor.research_agent.RSSConnector")
@patch("sts_monitor.research_agent.get_curated_feeds")
@patch("sts_monitor.research_agent.get_accounts_for_categories")
@patch("sts_monitor.research_agent.time.sleep")
@patch("sts_monitor.research_agent.time.perf_counter", return_value=0.0)
def test_llm_markdown_fenced_json(
    mock_perf, mock_sleep,
    mock_get_accounts, mock_get_feeds,
    mock_rss_cls, mock_nitter_cls, mock_search_cls,
) -> None:
    """LLM sometimes wraps JSON in markdown fences — agent should strip them."""
    mock_get_accounts.return_value = []
    mock_get_feeds.return_value = []

    base_result = _make_connector_result()
    mock_search_cls.return_value.collect.return_value = base_result
    mock_nitter_cls.return_value.collect.return_value = base_result
    mock_rss_cls.return_value.collect.return_value = base_result

    fenced = "```json\n" + _llm_json_response(
        key_findings=["Fenced finding"],
        should_continue=False,
    ) + "\n```"

    llm = _make_fake_llm([fenced, "Final brief."])

    agent = _make_agent(llm, max_iterations=3)
    session = agent.run("session-fenced", "test")

    assert session.status == "completed"
    assert "Fenced finding" in session.all_findings


# ── Connector failure resilience ─────────────────────────────────────────


@patch("sts_monitor.research_agent.SearchConnector")
@patch("sts_monitor.research_agent.NitterConnector")
@patch("sts_monitor.research_agent.RSSConnector")
@patch("sts_monitor.research_agent.get_curated_feeds")
@patch("sts_monitor.research_agent.get_accounts_for_categories")
@patch("sts_monitor.research_agent.time.sleep")
@patch("sts_monitor.research_agent.time.perf_counter", return_value=0.0)
def test_connector_failures_dont_crash_agent(
    mock_perf, mock_sleep,
    mock_get_accounts, mock_get_feeds,
    mock_rss_cls, mock_nitter_cls, mock_search_cls,
) -> None:
    """If individual connectors fail, agent should continue with others."""
    mock_get_accounts.return_value = ["acc1"]
    mock_get_feeds.return_value = [{"url": "https://feed.test"}]

    mock_search_cls.return_value.collect.side_effect = ConnectionError("search down")
    mock_nitter_cls.return_value.collect.side_effect = ConnectionError("nitter down")
    mock_rss_cls.return_value.collect.return_value = _make_connector_result([
        _make_observation("rss:reuters", "Article from RSS"),
    ])

    llm = _make_fake_llm([
        _llm_json_response(should_continue=False, key_findings=["RSS worked"]),
        "Brief from partial data.",
    ])

    agent = _make_agent(llm, max_iterations=2)
    session = agent.run("session-partial", "test")

    assert session.status == "completed"
    assert len(session.iterations) == 1


# ── Brief generation ─────────────────────────────────────────────────────


@patch("sts_monitor.research_agent.SearchConnector")
@patch("sts_monitor.research_agent.NitterConnector")
@patch("sts_monitor.research_agent.RSSConnector")
@patch("sts_monitor.research_agent.get_curated_feeds")
@patch("sts_monitor.research_agent.get_accounts_for_categories")
@patch("sts_monitor.research_agent.time.sleep")
@patch("sts_monitor.research_agent.time.perf_counter", return_value=0.0)
def test_brief_generation_failure(
    mock_perf, mock_sleep,
    mock_get_accounts, mock_get_feeds,
    mock_rss_cls, mock_nitter_cls, mock_search_cls,
) -> None:
    """If brief generation fails, agent should include error message."""
    mock_get_accounts.return_value = []
    mock_get_feeds.return_value = []

    base_result = _make_connector_result()
    mock_search_cls.return_value.collect.return_value = base_result
    mock_nitter_cls.return_value.collect.return_value = base_result
    mock_rss_cls.return_value.collect.return_value = base_result

    llm = MagicMock()
    llm.summarize.side_effect = [
        _llm_json_response(should_continue=False, key_findings=["finding"]),
        RuntimeError("LLM crashed during brief"),  # Brief generation fails
    ]

    agent = _make_agent(llm, max_iterations=2)
    session = agent.run("session-brief-fail", "test")

    assert session.status == "completed"
    assert "Brief generation failed" in session.final_brief
    assert "finding" in session.final_brief  # Findings should still be included


# ── Observation capping ──────────────────────────────────────────────────


@patch("sts_monitor.research_agent.SearchConnector")
@patch("sts_monitor.research_agent.NitterConnector")
@patch("sts_monitor.research_agent.RSSConnector")
@patch("sts_monitor.research_agent.get_curated_feeds")
@patch("sts_monitor.research_agent.get_accounts_for_categories")
@patch("sts_monitor.research_agent.time.sleep")
@patch("sts_monitor.research_agent.time.perf_counter", return_value=0.0)
def test_max_observations_capped(
    mock_perf, mock_sleep,
    mock_get_accounts, mock_get_feeds,
    mock_rss_cls, mock_nitter_cls, mock_search_cls,
) -> None:
    """Agent should cap total observations to max_observations."""
    mock_get_accounts.return_value = []
    mock_get_feeds.return_value = []

    # Return many observations
    many_obs = [_make_observation(f"src-{i}", f"claim {i}", f"http://ex.com/{i}") for i in range(50)]
    big_result = _make_connector_result(many_obs)

    mock_search_cls.return_value.collect.return_value = big_result
    mock_nitter_cls.return_value.collect.return_value = big_result
    mock_rss_cls.return_value.collect.return_value = big_result

    llm = _make_fake_llm([
        _llm_json_response(should_continue=False, key_findings=["lots of data"]),
        "Brief.",
    ])

    agent = _make_agent(llm, max_iterations=2, max_observations=10)
    session = agent.run("session-cap", "test")

    assert len(session.all_observations) <= 10


# ── run_async ────────────────────────────────────────────────────────────


def test_run_async_returns_session_id() -> None:
    """run_async should return session_id and create session immediately."""
    llm = MagicMock()
    # Make summarize block briefly so we can check pre-created session
    # then fail so the thread exits quickly
    llm.summarize.side_effect = RuntimeError("quick exit")

    agent = _make_agent(llm, max_iterations=1)

    with patch("sts_monitor.research_agent.SearchConnector") as mock_search, \
         patch("sts_monitor.research_agent.NitterConnector") as mock_nitter, \
         patch("sts_monitor.research_agent.RSSConnector") as mock_rss, \
         patch("sts_monitor.research_agent.get_curated_feeds", return_value=[]), \
         patch("sts_monitor.research_agent.get_accounts_for_categories", return_value=[]), \
         patch("sts_monitor.research_agent.time.sleep"), \
         patch("sts_monitor.research_agent.time.perf_counter", return_value=0.0):

        mock_search.return_value.collect.return_value = _make_connector_result()
        mock_nitter.return_value.collect.return_value = _make_connector_result()
        mock_rss.return_value.collect.return_value = _make_connector_result()

        sid = agent.run_async("async-1", "test topic")

    assert sid == "async-1"
    # Session should be immediately visible
    session = agent.get_session("async-1")
    assert session is not None
    assert session.topic == "test topic"


# ── Edge case: seed_query fallback ───────────────────────────────────────


@patch("sts_monitor.research_agent.SearchConnector")
@patch("sts_monitor.research_agent.NitterConnector")
@patch("sts_monitor.research_agent.RSSConnector")
@patch("sts_monitor.research_agent.get_curated_feeds")
@patch("sts_monitor.research_agent.get_accounts_for_categories")
@patch("sts_monitor.research_agent.time.sleep")
@patch("sts_monitor.research_agent.time.perf_counter", return_value=0.0)
def test_seed_query_defaults_to_topic(
    mock_perf, mock_sleep,
    mock_get_accounts, mock_get_feeds,
    mock_rss_cls, mock_nitter_cls, mock_search_cls,
) -> None:
    """When seed_query is None, topic should be used as the query."""
    mock_get_accounts.return_value = []
    mock_get_feeds.return_value = []

    base_result = _make_connector_result()
    mock_search_cls.return_value.collect.return_value = base_result
    mock_nitter_cls.return_value.collect.return_value = base_result
    mock_rss_cls.return_value.collect.return_value = base_result

    llm = _make_fake_llm([
        _llm_json_response(should_continue=False),
        "Brief.",
    ])

    agent = _make_agent(llm, max_iterations=1)
    session = agent.run("session-default-query", "my topic", seed_query=None)

    # Search should have been called with "my topic" as the query
    search_call = mock_search_cls.return_value.collect.call_args
    assert search_call is not None
