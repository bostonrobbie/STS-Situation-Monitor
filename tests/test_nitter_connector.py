"""Tests for the Nitter/Twitter connector with mocked feedparser."""
from __future__ import annotations

import types

import pytest

from sts_monitor.connectors.nitter import (
    DEFAULT_NITTER_INSTANCES,
    NitterConnector,
    OSINT_ACCOUNTS,
    get_accounts_for_categories,
    list_osint_categories,
)

pytestmark = pytest.mark.unit


# ── Fake feedparser results ──────────────────────────────────────────────


def _make_parsed(entries: list[dict], bozo: bool = False) -> types.SimpleNamespace:
    """Build an object that looks like feedparser.parse() output."""
    return types.SimpleNamespace(entries=entries, bozo=bozo, bozo_exception=None)


_SAMPLE_ENTRIES = [
    {
        "title": "Breaking: earthquake in Turkey",
        "summary": "A 5.2 magnitude earthquake hit central Turkey.",
        "link": "https://nitter.example.com/status/111",
        "author": "sentdefender",
        "published_parsed": (2025, 1, 15, 12, 0, 0, 2, 15, 0),
    },
    {
        "title": "Aid shipment arrives in Gaza",
        "summary": "UN confirms aid delivery.",
        "link": "https://nitter.example.com/status/222",
        "author": "BNONews",
        "published_parsed": (2025, 1, 15, 13, 0, 0, 2, 15, 0),
    },
]


# ── Helper category / account tests ─────────────────────────────────────


def test_list_osint_categories() -> None:
    cats = list_osint_categories()
    assert isinstance(cats, list)
    assert "conflict" in cats
    assert "geopolitics" in cats


def test_get_accounts_for_categories_all() -> None:
    all_accounts = get_accounts_for_categories(None)
    assert isinstance(all_accounts, list)
    assert len(all_accounts) > 0
    # Should be de-duplicated
    assert len(all_accounts) == len(set(all_accounts))


def test_get_accounts_for_categories_specific() -> None:
    accts = get_accounts_for_categories(["conflict"])
    assert "sentdefender" in accts


def test_get_accounts_for_unknown_category() -> None:
    accts = get_accounts_for_categories(["nonexistent_category_xyz"])
    assert accts == []


# ── NitterConnector: search ──────────────────────────────────────────────


def test_nitter_search_returns_observations(monkeypatch) -> None:
    def fake_parse(url, **kwargs):
        return _make_parsed(_SAMPLE_ENTRIES)

    monkeypatch.setattr("sts_monitor.connectors.nitter.feedparser.parse", fake_parse)

    connector = NitterConnector(instances=["https://nitter.test"])
    result = connector.collect(query="earthquake")

    assert result.connector == "nitter"
    assert len(result.observations) >= 1
    # The search entry with "earthquake" should be present
    assert any("earthquake" in o.claim.lower() for o in result.observations)
    assert result.metadata["search_query"] == "earthquake"


def test_nitter_search_path_encodes_query() -> None:
    connector = NitterConnector()
    assert connector._search_feed_path("war in ukraine") == "search?q=war+in+ukraine"


def test_nitter_account_feed_path_strips_at() -> None:
    connector = NitterConnector()
    assert connector._account_feed_path("@sentdefender") == "sentdefender/rss"
    assert connector._account_feed_path("sentdefender") == "sentdefender/rss"


# ── NitterConnector: account monitoring ──────────────────────────────────


def test_nitter_account_collect(monkeypatch) -> None:
    def fake_parse(url, **kwargs):
        return _make_parsed(_SAMPLE_ENTRIES)

    monkeypatch.setattr("sts_monitor.connectors.nitter.feedparser.parse", fake_parse)

    connector = NitterConnector(
        instances=["https://nitter.test"],
        accounts=["sentdefender", "BNONews"],
    )
    result = connector.collect(query=None)

    assert result.connector == "nitter"
    assert result.metadata["accounts_monitored"] == 2
    # With no query, no search is done, but accounts are fetched
    assert len(result.observations) > 0


def test_nitter_account_with_query_filter(monkeypatch) -> None:
    """When query is set, account entries are filtered by keyword."""
    def fake_parse(url, **kwargs):
        return _make_parsed(_SAMPLE_ENTRIES)

    monkeypatch.setattr("sts_monitor.connectors.nitter.feedparser.parse", fake_parse)

    connector = NitterConnector(
        instances=["https://nitter.test"],
        accounts=["sentdefender"],
    )
    result = connector.collect(query="Gaza")

    # Only "Aid shipment arrives in Gaza" should survive the query filter on accounts
    account_obs = [o for o in result.observations if "nitter:@" in o.source]
    assert all("gaza" in o.claim.lower() for o in account_obs)


# ── NitterConnector: instance fallback ───────────────────────────────────


def test_nitter_falls_back_across_instances(monkeypatch) -> None:
    call_log = []

    def fake_parse(url, **kwargs):
        call_log.append(url)
        if "bad-instance" in url:
            return _make_parsed([], bozo=True)
        return _make_parsed(_SAMPLE_ENTRIES)

    monkeypatch.setattr("sts_monitor.connectors.nitter.feedparser.parse", fake_parse)
    monkeypatch.setattr("sts_monitor.connectors.nitter.time.sleep", lambda _: None)

    connector = NitterConnector(
        instances=["https://bad-instance.net", "https://good-instance.net"],
        max_retries=0,
    )
    result = connector.collect(query="test")

    assert len(result.observations) > 0
    # Should have tried bad instance first, then good
    assert any("bad-instance" in u for u in call_log)
    assert any("good-instance" in u for u in call_log)


def test_nitter_all_instances_fail(monkeypatch) -> None:
    def fake_parse(url, **kwargs):
        return _make_parsed([], bozo=True)

    monkeypatch.setattr("sts_monitor.connectors.nitter.feedparser.parse", fake_parse)
    monkeypatch.setattr("sts_monitor.connectors.nitter.time.sleep", lambda _: None)

    connector = NitterConnector(
        instances=["https://fail1.net", "https://fail2.net"],
        accounts=["testaccount"],
        max_retries=0,
    )
    result = connector.collect(query="test")

    assert len(result.observations) == 0
    assert len(result.metadata["failed"]) > 0
    assert result.metadata["failed"][0]["error"].startswith("all instances failed")


# ── NitterConnector: error handling ──────────────────────────────────────


def test_nitter_feedparser_exception(monkeypatch) -> None:
    def fake_parse(url, **kwargs):
        raise ConnectionError("network down")

    monkeypatch.setattr("sts_monitor.connectors.nitter.feedparser.parse", fake_parse)
    monkeypatch.setattr("sts_monitor.connectors.nitter.time.sleep", lambda _: None)

    connector = NitterConnector(
        instances=["https://nitter.test"],
        max_retries=1,
    )
    result = connector.collect(query="test")

    assert len(result.observations) == 0
    assert len(result.metadata["failed"]) > 0


# ── NitterConnector: entry parsing edge cases ────────────────────────────


def test_nitter_skips_empty_entries(monkeypatch) -> None:
    entries = [
        {"title": "", "summary": "", "link": "", "author": ""},
        {"title": "Valid tweet", "summary": "", "link": "http://x.com/1", "author": "user1"},
    ]

    monkeypatch.setattr(
        "sts_monitor.connectors.nitter.feedparser.parse",
        lambda url, **kw: _make_parsed(entries),
    )

    connector = NitterConnector(instances=["https://nitter.test"])
    result = connector.collect(query="Valid")

    assert len(result.observations) == 1
    assert "Valid tweet" in result.observations[0].claim


def test_nitter_missing_published_parsed(monkeypatch) -> None:
    entries = [
        {"title": "No date tweet", "summary": "", "link": "http://x.com/1", "author": "user1"},
    ]

    monkeypatch.setattr(
        "sts_monitor.connectors.nitter.feedparser.parse",
        lambda url, **kw: _make_parsed(entries),
    )

    connector = NitterConnector(instances=["https://nitter.test"])
    result = connector.collect(query=None)

    assert len(result.observations) == 1
    # captured_at should still be set (datetime.now fallback)
    assert result.observations[0].captured_at is not None


def test_nitter_per_account_limit_clamped() -> None:
    connector = NitterConnector(per_account_limit=0)
    assert connector.per_account_limit == 1

    connector2 = NitterConnector(per_account_limit=999)
    assert connector2.per_account_limit == 100


def test_nitter_reliability_hint(monkeypatch) -> None:
    monkeypatch.setattr(
        "sts_monitor.connectors.nitter.feedparser.parse",
        lambda url, **kw: _make_parsed(_SAMPLE_ENTRIES),
    )
    connector = NitterConnector(instances=["https://nitter.test"])
    result = connector.collect(query="earthquake")
    for obs in result.observations:
        assert obs.reliability_hint == 0.45


def test_nitter_no_query_no_accounts() -> None:
    connector = NitterConnector(instances=["https://nitter.test"])
    result = connector.collect(query=None)
    assert len(result.observations) == 0
    assert result.metadata["accounts_monitored"] == 0
