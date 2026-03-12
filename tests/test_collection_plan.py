"""Tests for the collection plan module."""
from __future__ import annotations

import pytest

from sts_monitor.collection_plan import (
    CURATED_FEEDS,
    CollectionRequirement,
    build_collection_plan,
    get_curated_feeds,
    list_feed_categories,
)

pytestmark = pytest.mark.unit


# ── build_collection_plan ──────────────────────────────────────────────


def test_build_plan_always_includes_gdelt() -> None:
    reqs = build_collection_plan("International trade negotiations")
    connector_types = [c for r in reqs for c in r.connectors]
    assert "gdelt" in connector_types


def test_build_plan_earthquake_includes_usgs() -> None:
    reqs = build_collection_plan("Earthquake in Turkey causes damage")
    connector_types = [c for r in reqs for c in r.connectors]
    assert "usgs" in connector_types


def test_build_plan_fire_includes_nasa_firms() -> None:
    reqs = build_collection_plan("Wildfire spreading across California")
    connector_types = [c for r in reqs for c in r.connectors]
    assert "nasa_firms" in connector_types


def test_build_plan_conflict_includes_acled() -> None:
    reqs = build_collection_plan("Military conflict escalation in border region")
    connector_types = [c for r in reqs for c in r.connectors]
    assert "acled" in connector_types


def test_build_plan_weather_includes_nws() -> None:
    reqs = build_collection_plan("Hurricane approaching Gulf Coast")
    connector_types = [c for r in reqs for c in r.connectors]
    assert "nws" in connector_types


def test_build_plan_disaster_includes_fema() -> None:
    reqs = build_collection_plan("FEMA disaster declaration for flooding emergency")
    connector_types = [c for r in reqs for c in r.connectors]
    assert "fema" in connector_types


def test_build_plan_always_includes_rss() -> None:
    reqs = build_collection_plan("General monitoring of events")
    connector_types = [c for r in reqs for c in r.connectors]
    assert "rss" in connector_types


def test_build_plan_returns_collection_requirements() -> None:
    reqs = build_collection_plan("Earthquake in Japan")
    assert len(reqs) >= 2
    for r in reqs:
        assert isinstance(r, CollectionRequirement)
        assert r.name
        assert r.description
        assert len(r.connectors) >= 1
        assert r.query
        assert r.interval_seconds > 0


def test_build_plan_uses_seed_query() -> None:
    reqs = build_collection_plan("General topic", seed_query="custom-query")
    for r in reqs:
        assert "custom-query" in r.query


def test_build_plan_respects_priority() -> None:
    reqs = build_collection_plan("Storm warning", priority=90)
    # GDELT should have the base priority
    gdelt_req = next(r for r in reqs if "gdelt" in r.connectors)
    assert gdelt_req.priority == 90


# ── list_feed_categories ───────────────────────────────────────────────


def test_list_feed_categories_returns_all() -> None:
    categories = list_feed_categories()
    category_names = {c["category"] for c in categories}
    expected = {"world_news", "conflict_security", "humanitarian", "natural_disasters",
                "cyber_threat", "osint_analysis", "government_press"}
    assert expected == category_names


def test_list_feed_categories_has_counts() -> None:
    categories = list_feed_categories()
    for cat in categories:
        assert "feed_count" in cat
        assert cat["feed_count"] > 0
        assert "feeds" in cat
        assert len(cat["feeds"]) == cat["feed_count"]


# ── get_curated_feeds ──────────────────────────────────────────────────


def test_get_curated_feeds_all() -> None:
    all_feeds = get_curated_feeds()
    total_expected = sum(len(feeds) for feeds in CURATED_FEEDS.values())
    assert len(all_feeds) == total_expected


def test_get_curated_feeds_filter_by_category() -> None:
    feeds = get_curated_feeds(categories=["world_news"])
    assert len(feeds) == len(CURATED_FEEDS["world_news"])
    for f in feeds:
        assert "name" in f
        assert "url" in f


def test_get_curated_feeds_multiple_categories() -> None:
    feeds = get_curated_feeds(categories=["world_news", "cyber_threat"])
    expected_count = len(CURATED_FEEDS["world_news"]) + len(CURATED_FEEDS["cyber_threat"])
    assert len(feeds) == expected_count


def test_get_curated_feeds_unknown_category_returns_empty() -> None:
    feeds = get_curated_feeds(categories=["nonexistent_category"])
    assert feeds == []
