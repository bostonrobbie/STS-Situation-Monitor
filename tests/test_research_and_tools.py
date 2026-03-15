"""Tests for research.py, online_tools.py, and llm.py modules."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from sts_monitor.llm import LLMHealth, LocalLLMClient
from sts_monitor.online_tools import parse_csv_env, send_alert_webhook
from sts_monitor.research import TrendingResearchScanner, TrendingTopic

pytestmark = pytest.mark.unit


# ── TrendingResearchScanner ─────────────────────────────────────────────────


class TestTrendingResearchScannerInit:
    def test_defaults(self) -> None:
        scanner = TrendingResearchScanner()
        assert scanner.timeout_s == 10.0
        assert scanner.max_topics == 10
        assert scanner.per_topic_limit == 5

    def test_max_topics_clamped_low(self) -> None:
        scanner = TrendingResearchScanner(max_topics=-5)
        assert scanner.max_topics == 1

    def test_max_topics_clamped_high(self) -> None:
        scanner = TrendingResearchScanner(max_topics=999)
        assert scanner.max_topics == 50

    def test_per_topic_limit_clamped_low(self) -> None:
        scanner = TrendingResearchScanner(per_topic_limit=0)
        assert scanner.per_topic_limit == 1

    def test_per_topic_limit_clamped_high(self) -> None:
        scanner = TrendingResearchScanner(per_topic_limit=100)
        assert scanner.per_topic_limit == 20

    def test_valid_values_pass_through(self) -> None:
        scanner = TrendingResearchScanner(max_topics=25, per_topic_limit=10, timeout_s=5.0)
        assert scanner.max_topics == 25
        assert scanner.per_topic_limit == 10
        assert scanner.timeout_s == 5.0


class TestFetchTopics:
    @patch("sts_monitor.research.feedparser.parse")
    def test_returns_topics_from_entries(self, mock_parse: MagicMock) -> None:
        mock_parse.return_value = SimpleNamespace(
            entries=[
                {"title": "AI Advances", "published": "2025-01-01", "link": "https://example.com/ai", "tags": None},
                {"title": "Climate News", "published": "2025-01-02", "link": "https://example.com/climate", "tags": None},
            ]
        )
        scanner = TrendingResearchScanner(max_topics=5)
        topics = scanner.fetch_topics(geo="US")

        assert len(topics) == 2
        assert topics[0].topic == "AI Advances"
        assert topics[0].published_at == "2025-01-01"
        assert topics[1].topic == "Climate News"
        mock_parse.assert_called_once()

    @patch("sts_monitor.research.feedparser.parse")
    def test_empty_entries(self, mock_parse: MagicMock) -> None:
        mock_parse.return_value = SimpleNamespace(entries=[])
        scanner = TrendingResearchScanner()
        topics = scanner.fetch_topics()
        assert topics == []

    @patch("sts_monitor.research.feedparser.parse")
    def test_skips_blank_titles(self, mock_parse: MagicMock) -> None:
        mock_parse.return_value = SimpleNamespace(
            entries=[
                {"title": "", "published": None, "link": None, "tags": None},
                {"title": "   ", "published": None, "link": None, "tags": None},
                {"title": "Valid", "published": None, "link": "https://x.com", "tags": None},
            ]
        )
        scanner = TrendingResearchScanner()
        topics = scanner.fetch_topics()
        assert len(topics) == 1
        assert topics[0].topic == "Valid"

    @patch("sts_monitor.research.feedparser.parse")
    def test_extracts_traffic_from_tags(self, mock_parse: MagicMock) -> None:
        mock_parse.return_value = SimpleNamespace(
            entries=[
                {"title": "Trending", "published": None, "link": None, "tags": [{"term": "500K+"}]},
            ]
        )
        scanner = TrendingResearchScanner()
        topics = scanner.fetch_topics()
        assert len(topics) == 1
        assert topics[0].traffic == "500K+"

    @patch("sts_monitor.research.feedparser.parse")
    def test_tags_non_dict_ignored(self, mock_parse: MagicMock) -> None:
        mock_parse.return_value = SimpleNamespace(
            entries=[
                {"title": "Trending", "published": None, "link": None, "tags": ["plain-string"]},
            ]
        )
        scanner = TrendingResearchScanner()
        topics = scanner.fetch_topics()
        assert topics[0].traffic is None

    @patch("sts_monitor.research.feedparser.parse")
    def test_max_topics_limits_results(self, mock_parse: MagicMock) -> None:
        mock_parse.return_value = SimpleNamespace(
            entries=[{"title": f"Topic {i}", "published": None, "link": None, "tags": None} for i in range(20)]
        )
        scanner = TrendingResearchScanner(max_topics=3)
        topics = scanner.fetch_topics()
        assert len(topics) == 3

    @patch("sts_monitor.research.feedparser.parse")
    def test_geo_uppercased_in_url(self, mock_parse: MagicMock) -> None:
        mock_parse.return_value = SimpleNamespace(entries=[])
        scanner = TrendingResearchScanner()
        scanner.fetch_topics(geo="gb")
        call_args = mock_parse.call_args
        assert "geo=GB" in call_args[0][0]


class TestCollectObservations:
    @patch("sts_monitor.research.feedparser.parse")
    def test_creates_observations_from_news(self, mock_parse: MagicMock) -> None:
        trends_feed = SimpleNamespace(
            entries=[{"title": "AI", "published": None, "link": "https://t.co/ai", "tags": None}]
        )
        news_feed = SimpleNamespace(
            entries=[
                {"title": "AI Headline", "summary": "Details here", "link": "https://news.com/1"},
            ]
        )
        mock_parse.side_effect = [trends_feed, news_feed]

        scanner = TrendingResearchScanner(max_topics=5, per_topic_limit=5)
        observations, metadata = scanner.collect_observations(geo="US")

        assert len(observations) == 1
        assert "AI" in observations[0].claim
        assert observations[0].reliability_hint == 0.55
        assert observations[0].source == "trending:US:AI"
        assert metadata["topics_scanned"] == 1
        assert metadata["failed_topics"] == []

    @patch("sts_monitor.research.feedparser.parse")
    def test_failed_topics_tracked(self, mock_parse: MagicMock) -> None:
        trends_feed = SimpleNamespace(
            entries=[
                {"title": "TopicA", "published": None, "link": None, "tags": None},
                {"title": "TopicB", "published": None, "link": None, "tags": None},
            ]
        )
        empty_feed = SimpleNamespace(entries=[])
        news_feed = SimpleNamespace(entries=[{"title": "H", "summary": "", "link": "https://x.com"}])

        mock_parse.side_effect = [trends_feed, empty_feed, news_feed]

        scanner = TrendingResearchScanner()
        observations, metadata = scanner.collect_observations()

        assert metadata["failed_topics"] == ["TopicA"]
        assert metadata["topics_scanned"] == 2
        assert len(observations) == 1

    @patch("sts_monitor.research.feedparser.parse")
    def test_no_topics_returns_empty(self, mock_parse: MagicMock) -> None:
        mock_parse.return_value = SimpleNamespace(entries=[])
        scanner = TrendingResearchScanner()
        observations, metadata = scanner.collect_observations()

        assert observations == []
        assert metadata["topics_scanned"] == 0
        assert metadata["failed_topics"] == []


# ── online_tools ─────────────────────────────────────────────────────────────


class TestParseCsvEnv:
    def test_empty_string(self) -> None:
        assert parse_csv_env("") == []

    def test_normal_csv(self) -> None:
        assert parse_csv_env("a, b, c") == ["a", "b", "c"]

    def test_whitespace_only_items(self) -> None:
        assert parse_csv_env("  ,  ,  ") == []

    def test_single_value(self) -> None:
        assert parse_csv_env("hello") == ["hello"]

    def test_trailing_comma(self) -> None:
        assert parse_csv_env("x, y,") == ["x", "y"]


class TestSendAlertWebhook:
    def test_empty_url_returns_disabled(self) -> None:
        result = send_alert_webhook(webhook_url="", timeout_s=5.0, payload={"msg": "hi"})
        assert result == {"sent": False, "status": "disabled"}

    @patch("sts_monitor.online_tools.httpx.post")
    def test_success_200(self, mock_post: MagicMock) -> None:
        mock_post.return_value = MagicMock(status_code=200)
        result = send_alert_webhook(webhook_url="https://hooks.example.com/x", timeout_s=5.0, payload={"k": "v"})
        assert result == {"sent": True, "status": "http-200"}
        mock_post.assert_called_once_with("https://hooks.example.com/x", json={"k": "v"}, timeout=5.0)

    @patch("sts_monitor.online_tools.httpx.post")
    def test_failure_500(self, mock_post: MagicMock) -> None:
        mock_post.return_value = MagicMock(status_code=500)
        result = send_alert_webhook(webhook_url="https://hooks.example.com/x", timeout_s=5.0, payload={})
        assert result == {"sent": False, "status": "http-500"}

    @patch("sts_monitor.online_tools.httpx.post")
    def test_exception_returns_error(self, mock_post: MagicMock) -> None:
        mock_post.side_effect = httpx.ConnectError("connection refused")
        result = send_alert_webhook(webhook_url="https://hooks.example.com/x", timeout_s=5.0, payload={})
        assert result["sent"] is False
        assert "error:" in result["status"]

    @patch("sts_monitor.online_tools.httpx.post")
    def test_status_399_counts_as_sent(self, mock_post: MagicMock) -> None:
        mock_post.return_value = MagicMock(status_code=399)
        result = send_alert_webhook(webhook_url="https://hooks.example.com/x", timeout_s=5.0, payload={})
        assert result["sent"] is True

    @patch("sts_monitor.online_tools.httpx.post")
    def test_status_400_counts_as_not_sent(self, mock_post: MagicMock) -> None:
        mock_post.return_value = MagicMock(status_code=400)
        result = send_alert_webhook(webhook_url="https://hooks.example.com/x", timeout_s=5.0, payload={})
        assert result["sent"] is False


# ── LocalLLMClient ───────────────────────────────────────────────────────────


class TestLocalLLMClientHealth:
    def _make_client(self, **kwargs) -> LocalLLMClient:
        return LocalLLMClient(base_url="http://localhost:11434", model="llama3", **kwargs)

    @patch("sts_monitor.llm.httpx.get")
    def test_health_model_found(self, mock_get: MagicMock) -> None:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"models": [{"name": "llama3:latest"}]}),
            raise_for_status=MagicMock(),
        )
        client = self._make_client()
        result = client.health()

        assert result.reachable is True
        assert result.model_available is True
        assert result.detail == "ok"
        assert result.latency_ms is not None

    @patch("sts_monitor.llm.httpx.get")
    def test_health_model_not_found(self, mock_get: MagicMock) -> None:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"models": [{"name": "mistral:latest"}]}),
            raise_for_status=MagicMock(),
        )
        client = self._make_client()
        result = client.health()

        assert result.reachable is True
        assert result.model_available is False
        assert "not found" in result.detail

    @patch("sts_monitor.llm.httpx.get")
    def test_health_connection_error(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = httpx.ConnectError("refused")
        client = self._make_client()
        result = client.health()

        assert result.reachable is False
        assert result.model_available is False
        assert "refused" in result.detail
        assert result.latency_ms is not None


class TestLocalLLMClientSummarize:
    def _make_client(self, **kwargs) -> LocalLLMClient:
        return LocalLLMClient(base_url="http://localhost:11434", model="llama3", **kwargs)

    @patch("sts_monitor.llm.time.sleep", return_value=None)
    @patch("sts_monitor.llm.httpx.post")
    def test_summarize_success(self, mock_post: MagicMock, _sleep: MagicMock) -> None:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"response": "Summary text."}),
            raise_for_status=MagicMock(),
        )
        client = self._make_client()
        result = client.summarize("Summarize this.")
        assert result == "Summary text."
        mock_post.assert_called_once()

    @patch("sts_monitor.llm.time.sleep", return_value=None)
    @patch("sts_monitor.llm.httpx.post")
    def test_summarize_failure_raises_after_retries(self, mock_post: MagicMock, _sleep: MagicMock) -> None:
        mock_post.side_effect = httpx.ConnectError("refused")
        client = self._make_client(max_retries=1)

        with pytest.raises(RuntimeError, match="refused"):
            client.summarize("Summarize this.")

    @patch("sts_monitor.llm.time.sleep", return_value=None)
    @patch("sts_monitor.llm.httpx.post")
    def test_summarize_retry_count(self, mock_post: MagicMock, _sleep: MagicMock) -> None:
        mock_post.side_effect = httpx.ConnectError("refused")
        client = self._make_client(max_retries=2)

        with pytest.raises(RuntimeError):
            client.summarize("prompt")

        # initial attempt + 2 retries = 3 calls total
        assert mock_post.call_count == 3

    @patch("sts_monitor.llm.time.sleep", return_value=None)
    @patch("sts_monitor.llm.httpx.post")
    def test_summarize_succeeds_on_retry(self, mock_post: MagicMock, _sleep: MagicMock) -> None:
        fail_resp = httpx.ConnectError("refused")
        ok_resp = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"response": "Got it."}),
            raise_for_status=MagicMock(),
        )
        mock_post.side_effect = [fail_resp, ok_resp]
        client = self._make_client(max_retries=2)

        result = client.summarize("prompt")
        assert result == "Got it."
        assert mock_post.call_count == 2

    @patch("sts_monitor.llm.time.sleep", return_value=None)
    @patch("sts_monitor.llm.httpx.post")
    def test_summarize_empty_response(self, mock_post: MagicMock, _sleep: MagicMock) -> None:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={}),
            raise_for_status=MagicMock(),
        )
        client = self._make_client()
        result = client.summarize("prompt")
        assert result == ""
