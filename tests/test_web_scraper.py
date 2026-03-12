"""Tests for the web scraper connector with mocked httpx."""
from __future__ import annotations

import pytest

from sts_monitor.connectors.web_scraper import (
    WebScraperConnector,
    _LinkExtractor,
    _TextExtractor,
    _is_crawlable,
    _same_domain,
    extract_links,
    extract_text,
)

pytestmark = pytest.mark.unit


# ── Shared fake HTTP helpers ─────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200, content_type: str = "text/html") -> None:
        self._text = text
        self.status_code = status_code
        self.headers = {"content-type": content_type}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    @property
    def text(self) -> str:
        return self._text


class _FakeClient:
    """Client that returns pre-configured responses per URL."""

    def __init__(self, responses: dict[str, _FakeResponse] | None = None, default: _FakeResponse | None = None) -> None:
        self._responses = responses or {}
        self._default = default or _FakeResponse(text="", status_code=404)
        self.requested_urls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url: str, **kwargs) -> _FakeResponse:
        self.requested_urls.append(url)
        return self._responses.get(url, self._default)


# ── extract_text ─────────────────────────────────────────────────────────


_SIMPLE_HTML = """
<html>
<head><title>Test Page</title></head>
<body>
<h1>Heading</h1>
<p>Paragraph one.</p>
<p>Paragraph two.</p>
<script>var x = 1;</script>
<style>.foo { color: red; }</style>
</body>
</html>
"""


def test_extract_text_returns_title_and_body() -> None:
    title, text = extract_text(_SIMPLE_HTML)
    assert title == "Test Page"
    assert "Heading" in text
    assert "Paragraph one" in text
    assert "Paragraph two" in text


def test_extract_text_strips_scripts_and_styles() -> None:
    _, text = extract_text(_SIMPLE_HTML)
    assert "var x" not in text
    assert "color: red" not in text


def test_extract_text_handles_empty_html() -> None:
    title, text = extract_text("")
    assert title == ""
    assert text == ""


def test_extract_text_handles_malformed_html() -> None:
    title, text = extract_text("<p>Unclosed paragraph<div>Another")
    assert "Unclosed paragraph" in text
    assert "Another" in text


# ── extract_links ────────────────────────────────────────────────────────


_LINK_HTML = """
<html><body>
<a href="/page2">Page 2</a>
<a href="https://external.com/article">External</a>
<a href="#section">Anchor</a>
<a href="javascript:void(0)">JS</a>
<a href="mailto:test@test.com">Email</a>
<a href="relative/path">Relative</a>
</body></html>
"""


def test_extract_links_returns_absolute_urls() -> None:
    links = extract_links(_LINK_HTML, "https://example.com/")
    assert "https://example.com/page2" in links
    assert "https://external.com/article" in links
    assert "https://example.com/relative/path" in links


def test_extract_links_skips_anchors_and_javascript() -> None:
    links = extract_links(_LINK_HTML, "https://example.com/")
    assert not any(l.startswith("#") for l in links)
    assert not any("javascript:" in l for l in links)
    assert not any("mailto:" in l for l in links)


def test_extract_links_handles_empty_html() -> None:
    links = extract_links("", "https://example.com/")
    assert links == []


# ── Scope controls ───────────────────────────────────────────────────────


def test_same_domain_same() -> None:
    assert _same_domain("https://example.com/a", "https://example.com/b") is True


def test_same_domain_different() -> None:
    assert _same_domain("https://example.com", "https://other.com") is False


def test_same_domain_case_insensitive() -> None:
    assert _same_domain("https://EXAMPLE.COM", "https://example.com") is True


def test_is_crawlable_html() -> None:
    assert _is_crawlable("https://example.com/article/123") is True
    assert _is_crawlable("https://example.com/") is True


def test_is_crawlable_skip_extensions() -> None:
    assert _is_crawlable("https://example.com/image.jpg") is False
    assert _is_crawlable("https://example.com/style.css") is False
    assert _is_crawlable("https://example.com/app.js") is False
    assert _is_crawlable("https://example.com/doc.pdf") is False
    assert _is_crawlable("https://example.com/video.mp4") is False


# ── WebScraperConnector: _is_in_scope ────────────────────────────────────


def test_is_in_scope_same_domain() -> None:
    connector = WebScraperConnector(same_domain_only=True)
    assert connector._is_in_scope("https://example.com/page2", "https://example.com/") is True
    assert connector._is_in_scope("https://other.com/page", "https://example.com/") is False


def test_is_in_scope_allowed_domains() -> None:
    connector = WebScraperConnector(allowed_domains=["reuters.com", "bbc.co.uk"])
    assert connector._is_in_scope("https://reuters.com/article/1", "https://example.com") is True
    assert connector._is_in_scope("https://cnn.com/article/1", "https://example.com") is False


def test_is_in_scope_no_restriction() -> None:
    connector = WebScraperConnector(same_domain_only=False)
    assert connector._is_in_scope("https://any-domain.com/page", "https://example.com/") is True


# ── WebScraperConnector: crawl ───────────────────────────────────────────


_PAGE_A = """
<html><head><title>Page A</title></head><body>
<p>This is page A with enough text content to pass the minimum text length filter for the scraper connector test.</p>
<a href="https://example.com/page-b">Go to B</a>
</body></html>
"""

_PAGE_B = """
<html><head><title>Page B</title></head><body>
<p>This is page B with enough text content to pass the minimum text length filter for the scraper connector test.</p>
</body></html>
"""


def test_webscraper_crawl_basic(monkeypatch) -> None:
    responses = {
        "https://example.com/": _FakeResponse(text=_PAGE_A),
        "https://example.com/page-b": _FakeResponse(text=_PAGE_B),
    }
    fake_client = _FakeClient(responses=responses)
    monkeypatch.setattr("sts_monitor.connectors.web_scraper.httpx.Client", lambda **kw: fake_client)
    monkeypatch.setattr("sts_monitor.connectors.web_scraper.time.sleep", lambda _: None)

    connector = WebScraperConnector(
        seed_urls=["https://example.com/"],
        max_depth=1,
        min_text_length=10,
    )
    result = connector.collect()

    assert result.connector == "web_scraper"
    assert len(result.observations) == 2
    assert result.metadata["pages_fetched"] == 2


def test_webscraper_respects_max_depth(monkeypatch) -> None:
    responses = {
        "https://example.com/": _FakeResponse(text=_PAGE_A),
        "https://example.com/page-b": _FakeResponse(text=_PAGE_B),
    }
    fake_client = _FakeClient(responses=responses)
    monkeypatch.setattr("sts_monitor.connectors.web_scraper.httpx.Client", lambda **kw: fake_client)
    monkeypatch.setattr("sts_monitor.connectors.web_scraper.time.sleep", lambda _: None)

    connector = WebScraperConnector(
        seed_urls=["https://example.com/"],
        max_depth=0,  # Don't follow any links
        min_text_length=10,
    )
    result = connector.collect()

    assert len(result.observations) == 1  # Only the seed page


def test_webscraper_respects_max_pages(monkeypatch) -> None:
    responses = {
        "https://example.com/": _FakeResponse(text=_PAGE_A),
        "https://example.com/page-b": _FakeResponse(text=_PAGE_B),
    }
    fake_client = _FakeClient(responses=responses)
    monkeypatch.setattr("sts_monitor.connectors.web_scraper.httpx.Client", lambda **kw: fake_client)
    monkeypatch.setattr("sts_monitor.connectors.web_scraper.time.sleep", lambda _: None)

    connector = WebScraperConnector(
        seed_urls=["https://example.com/"],
        max_depth=2,
        max_pages=1,
        min_text_length=10,
    )
    result = connector.collect()

    assert len(result.observations) == 1


def test_webscraper_same_domain_only(monkeypatch) -> None:
    html_with_external = """
    <html><head><title>Page</title></head><body>
    <p>Enough content to pass the minimum text length filter for this test case here.</p>
    <a href="https://external.com/article">External link</a>
    </body></html>
    """
    responses = {
        "https://example.com/": _FakeResponse(text=html_with_external),
    }
    fake_client = _FakeClient(responses=responses)
    monkeypatch.setattr("sts_monitor.connectors.web_scraper.httpx.Client", lambda **kw: fake_client)
    monkeypatch.setattr("sts_monitor.connectors.web_scraper.time.sleep", lambda _: None)

    connector = WebScraperConnector(
        seed_urls=["https://example.com/"],
        max_depth=2,
        same_domain_only=True,
        min_text_length=10,
    )
    result = connector.collect()

    # Should only crawl the seed, not follow external link
    assert all("external.com" not in url for url in fake_client.requested_urls)


def test_webscraper_query_filtering(monkeypatch) -> None:
    html = """
    <html><head><title>Unrelated Article</title></head><body>
    <p>This article is about cooking recipes and has nothing to do with global events at all.</p>
    </body></html>
    """
    responses = {
        "https://example.com/": _FakeResponse(text=html),
    }
    fake_client = _FakeClient(responses=responses)
    monkeypatch.setattr("sts_monitor.connectors.web_scraper.httpx.Client", lambda **kw: fake_client)
    monkeypatch.setattr("sts_monitor.connectors.web_scraper.time.sleep", lambda _: None)

    connector = WebScraperConnector(
        seed_urls=["https://example.com/"],
        max_depth=0,
        min_text_length=10,
    )
    result = connector.collect(query="earthquake")

    assert len(result.observations) == 0


def test_webscraper_skips_non_html(monkeypatch) -> None:
    responses = {
        "https://example.com/data.json": _FakeResponse(
            text='{"key": "value"}', content_type="application/json"
        ),
    }
    fake_client = _FakeClient(responses=responses)
    monkeypatch.setattr("sts_monitor.connectors.web_scraper.httpx.Client", lambda **kw: fake_client)
    monkeypatch.setattr("sts_monitor.connectors.web_scraper.time.sleep", lambda _: None)

    connector = WebScraperConnector(
        seed_urls=["https://example.com/data.json"],
        max_depth=0,
    )
    result = connector.collect()

    assert len(result.observations) == 0


def test_webscraper_handles_http_error(monkeypatch) -> None:
    class _ErrorClient:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def get(self, url, **kw):
            raise ConnectionError("network error")

    monkeypatch.setattr("sts_monitor.connectors.web_scraper.httpx.Client", lambda **kw: _ErrorClient())
    monkeypatch.setattr("sts_monitor.connectors.web_scraper.time.sleep", lambda _: None)

    connector = WebScraperConnector(seed_urls=["https://example.com/"])
    result = connector.collect()

    assert len(result.observations) == 0


def test_webscraper_deduplicates_urls(monkeypatch) -> None:
    html = """
    <html><head><title>Page</title></head><body>
    <p>Enough text content for the minimum length filter to pass this test case easily here.</p>
    <a href="https://example.com/">Self link</a>
    <a href="https://example.com/#fragment">Fragment link</a>
    </body></html>
    """
    responses = {
        "https://example.com/": _FakeResponse(text=html),
    }
    fake_client = _FakeClient(responses=responses)
    monkeypatch.setattr("sts_monitor.connectors.web_scraper.httpx.Client", lambda **kw: fake_client)
    monkeypatch.setattr("sts_monitor.connectors.web_scraper.time.sleep", lambda _: None)

    connector = WebScraperConnector(
        seed_urls=["https://example.com/"],
        max_depth=1,
        min_text_length=10,
    )
    result = connector.collect()

    # Only one page scraped despite self-referencing links
    assert len(result.observations) == 1


def test_webscraper_min_text_length_filter(monkeypatch) -> None:
    html = "<html><head><title>Short</title></head><body><p>Hi</p></body></html>"
    responses = {
        "https://example.com/": _FakeResponse(text=html),
    }
    fake_client = _FakeClient(responses=responses)
    monkeypatch.setattr("sts_monitor.connectors.web_scraper.httpx.Client", lambda **kw: fake_client)
    monkeypatch.setattr("sts_monitor.connectors.web_scraper.time.sleep", lambda _: None)

    connector = WebScraperConnector(
        seed_urls=["https://example.com/"],
        max_depth=0,
        min_text_length=100,
    )
    result = connector.collect()

    assert len(result.observations) == 0


def test_webscraper_constructor_clamps_values() -> None:
    c = WebScraperConnector(max_depth=99, max_pages=9999)
    assert c.max_depth == 5
    assert c.max_pages == 200

    c2 = WebScraperConnector(max_depth=-1, max_pages=-5)
    assert c2.max_depth == 0
    assert c2.max_pages == 1


def test_webscraper_no_seed_urls() -> None:
    connector = WebScraperConnector(seed_urls=[])
    result = connector.collect()
    assert len(result.observations) == 0
    assert result.metadata["seeds"] == 0


def test_webscraper_observation_reliability(monkeypatch) -> None:
    responses = {
        "https://example.com/": _FakeResponse(text=_PAGE_A),
    }
    fake_client = _FakeClient(responses=responses)
    monkeypatch.setattr("sts_monitor.connectors.web_scraper.httpx.Client", lambda **kw: fake_client)
    monkeypatch.setattr("sts_monitor.connectors.web_scraper.time.sleep", lambda _: None)

    connector = WebScraperConnector(
        seed_urls=["https://example.com/"],
        max_depth=0,
        min_text_length=10,
    )
    result = connector.collect()

    for obs in result.observations:
        assert obs.reliability_hint == 0.50
        assert obs.source.startswith("web:")
