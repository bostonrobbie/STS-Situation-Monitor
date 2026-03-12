"""Tests for the DuckDuckGo search connector with mocked httpx."""
from __future__ import annotations

import pytest

from sts_monitor.connectors.search import (
    SearchConnector,
    _DDGResultParser,
    _extract_real_url,
)

pytestmark = pytest.mark.unit


# ── Shared fake HTTP helpers ─────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200) -> None:
        self._text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    @property
    def text(self) -> str:
        return self._text


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url: str, **kwargs) -> _FakeResponse:
        return self._response

    def get(self, url: str, **kwargs) -> _FakeResponse:
        return self._response


class _FakeErrorClient:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url: str, **kwargs):
        raise ConnectionError("network error")


# ── DDG HTML that mimics real search result structure ─────────────────────


_DDG_HTML = """
<html><body>
<div class="result results_links results_links_deep web-result">
  <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Farticle1&amp;rut=abc">
    Earthquake in Turkey - Example News
  </a>
  <a class="result__snippet" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Farticle1&amp;rut=abc">
    A major earthquake struck central Turkey today, measuring 5.2 on the Richter scale.
  </a>
</div>
<div class="result results_links results_links_deep web-result">
  <a class="result__a" href="https://reuters.com/world/earthquake-update">
    Reuters: Earthquake Update
  </a>
  <a class="result__snippet" href="https://reuters.com/world/earthquake-update">
    Breaking news coverage of the Turkey earthquake with latest updates.
  </a>
</div>
</body></html>
"""


# ── _extract_real_url ────────────────────────────────────────────────────


def test_extract_real_url_with_ddg_redirect() -> None:
    url = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Farticle&rut=abc"
    assert _extract_real_url(url) == "https://example.com/article"


def test_extract_real_url_passthrough() -> None:
    url = "https://example.com/direct-link"
    assert _extract_real_url(url) == "https://example.com/direct-link"


def test_extract_real_url_ddg_without_uddg() -> None:
    url = "https://duckduckgo.com/some/other/path"
    assert _extract_real_url(url) == url


# ── _DDGResultParser ─────────────────────────────────────────────────────


def test_ddg_parser_extracts_results() -> None:
    parser = _DDGResultParser()
    parser.feed(_DDG_HTML)
    assert len(parser.results) == 2
    assert parser.results[0]["title"] == "Earthquake in Turkey - Example News"
    assert "earthquake struck" in parser.results[0]["snippet"].lower()
    assert parser.results[1]["title"] == "Reuters: Earthquake Update"


def test_ddg_parser_empty_html() -> None:
    parser = _DDGResultParser()
    parser.feed("<html><body>No results</body></html>")
    assert len(parser.results) == 0


def test_ddg_parser_missing_snippet() -> None:
    html = """
    <a class="result__a" href="https://example.com">Title Only</a>
    """
    parser = _DDGResultParser()
    parser.feed(html)
    # No snippet means result is not appended (snippet endtag triggers append)
    assert len(parser.results) == 0


# ── SearchConnector.search ───────────────────────────────────────────────


def test_search_returns_parsed_results(monkeypatch) -> None:
    monkeypatch.setattr(
        "sts_monitor.connectors.search.httpx.Client",
        lambda **kw: _FakeClient(_FakeResponse(text=_DDG_HTML)),
    )

    connector = SearchConnector(max_results=10)
    results = connector.search("earthquake Turkey")

    assert len(results) == 2
    # First result should have the real URL extracted
    assert results[0]["url"] == "https://example.com/article1"
    assert "Earthquake" in results[0]["title"]
    assert results[1]["url"] == "https://reuters.com/world/earthquake-update"


def test_search_respects_max_results(monkeypatch) -> None:
    monkeypatch.setattr(
        "sts_monitor.connectors.search.httpx.Client",
        lambda **kw: _FakeClient(_FakeResponse(text=_DDG_HTML)),
    )

    connector = SearchConnector(max_results=1)
    results = connector.search("earthquake")

    assert len(results) == 1


def test_search_handles_network_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "sts_monitor.connectors.search.httpx.Client",
        lambda **kw: _FakeErrorClient(),
    )

    connector = SearchConnector()
    results = connector.search("anything")

    assert results == []


def test_search_handles_malformed_html(monkeypatch) -> None:
    monkeypatch.setattr(
        "sts_monitor.connectors.search.httpx.Client",
        lambda **kw: _FakeClient(_FakeResponse(text="<html><body>not a search page</body></html>")),
    )

    connector = SearchConnector()
    results = connector.search("test")

    assert results == []


# ── SearchConnector.collect ──────────────────────────────────────────────


def test_collect_with_query(monkeypatch) -> None:
    monkeypatch.setattr(
        "sts_monitor.connectors.search.httpx.Client",
        lambda **kw: _FakeClient(_FakeResponse(text=_DDG_HTML)),
    )

    connector = SearchConnector()
    result = connector.collect(query="earthquake")

    assert result.connector == "search"
    assert len(result.observations) == 2
    assert result.metadata["query"] == "earthquake"
    assert result.metadata["result_count"] == 2

    # Check observation fields
    obs = result.observations[0]
    assert obs.source.startswith("search:ddg:")
    assert obs.url == "https://example.com/article1"
    assert obs.reliability_hint == 0.55
    assert "Earthquake" in obs.claim


def test_collect_without_query() -> None:
    connector = SearchConnector()
    result = connector.collect(query=None)

    assert result.connector == "search"
    assert len(result.observations) == 0
    assert "error" in result.metadata


def test_collect_empty_query() -> None:
    connector = SearchConnector()
    result = connector.collect(query="")

    assert len(result.observations) == 0
    assert "error" in result.metadata


def test_collect_handles_network_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "sts_monitor.connectors.search.httpx.Client",
        lambda **kw: _FakeErrorClient(),
    )

    connector = SearchConnector()
    result = connector.collect(query="test")

    assert len(result.observations) == 0
    assert result.metadata["result_count"] == 0


def test_search_filters_non_http_urls(monkeypatch) -> None:
    html = """
    <a class="result__a" href="ftp://files.example.com/data">FTP Link</a>
    <a class="result__snippet" href="ftp://files.example.com/data">An FTP resource.</a>
    """
    monkeypatch.setattr(
        "sts_monitor.connectors.search.httpx.Client",
        lambda **kw: _FakeClient(_FakeResponse(text=html)),
    )

    connector = SearchConnector()
    results = connector.search("test")

    assert len(results) == 0


def test_search_max_results_clamped() -> None:
    c = SearchConnector(max_results=0)
    assert c.max_results == 1

    c2 = SearchConnector(max_results=999)
    assert c2.max_results == 50
