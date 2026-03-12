"""Tests for the story clustering module."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sts_monitor.clustering import ObservationRef, Story, cluster_observations

pytestmark = pytest.mark.unit


def _make_obs(
    claim: str,
    source: str = "rss:example.com",
    hours_ago: float = 0,
    reliability: float = 0.8,
) -> ObservationRef:
    return ObservationRef(
        id=hash(claim) % 100000,
        source=source,
        claim=claim,
        url=f"https://example.com/{hash(claim) % 9999}",
        captured_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        reliability_hint=reliability,
    )


# ── Grouping similar observations ──────────────────────────────────────


def test_cluster_groups_similar_observations() -> None:
    observations = [
        _make_obs("Earthquake strikes Istanbul causing widespread damage", source="rss:bbc.com"),
        _make_obs("Major earthquake hits Istanbul with severe destruction", source="rss:reuters.com"),
        _make_obs("Istanbul rocked by powerful earthquake", source="rss:ap.com"),
    ]
    stories = cluster_observations(observations, min_cluster_size=2, min_term_overlap=0.10)
    assert len(stories) >= 1
    # All three should be in the same cluster
    assert stories[0].observation_count >= 2


def test_cluster_keeps_distinct_topics_separate() -> None:
    earthquake_obs = [
        _make_obs("Earthquake strikes Istanbul causing widespread damage", source="rss:bbc.com"),
        _make_obs("Major earthquake hits Istanbul with severe destruction", source="rss:reuters.com"),
        _make_obs("Istanbul rocked by powerful earthquake", source="rss:ap.com"),
    ]
    flood_obs = [
        _make_obs("Flooding in Bangladesh displaces thousands of families", source="rss:bbc.com"),
        _make_obs("Bangladesh flood waters continue rising across delta region", source="rss:reuters.com"),
        _make_obs("Severe flooding reported throughout Bangladesh delta", source="rss:ap.com"),
    ]
    stories = cluster_observations(
        earthquake_obs + flood_obs,
        min_cluster_size=2,
        min_term_overlap=0.10,
    )
    # Should get at least 2 distinct clusters
    assert len(stories) >= 2


def test_min_cluster_size_filters_singletons() -> None:
    observations = [
        _make_obs("Earthquake strikes Istanbul"),
        _make_obs("Completely unrelated topic about space exploration on Mars"),
    ]
    stories = cluster_observations(observations, min_cluster_size=2, min_term_overlap=0.10)
    # Neither should form a cluster of size >= 2
    assert len(stories) == 0


def test_empty_input_returns_empty_list() -> None:
    assert cluster_observations([]) == []


def test_single_observation_returns_empty_with_min_cluster_2() -> None:
    observations = [_make_obs("Only one observation")]
    stories = cluster_observations(observations, min_cluster_size=2)
    assert len(stories) == 0


def test_story_has_expected_fields() -> None:
    observations = [
        _make_obs("NATO forces deploy additional troops to eastern border region", source="rss:bbc.com"),
        _make_obs("NATO deploys troops along eastern border in response to threat", source="rss:reuters.com"),
        _make_obs("Additional NATO troops sent to defend eastern border areas", source="reddit:r/worldnews"),
    ]
    stories = cluster_observations(observations, min_cluster_size=2, min_term_overlap=0.10)
    assert len(stories) >= 1
    story = stories[0]
    assert story.id
    assert story.headline
    assert len(story.key_terms) > 0
    assert story.observation_count >= 2
    assert story.source_count >= 1
    assert 0.0 <= story.avg_reliability <= 1.0
    assert story.trending_score >= 0


def test_old_observations_outside_window_excluded() -> None:
    observations = [
        _make_obs("Old earthquake report from Istanbul", hours_ago=100),
        _make_obs("Old earthquake update from Istanbul", hours_ago=100),
        _make_obs("Old earthquake aftermath in Istanbul", hours_ago=100),
    ]
    stories = cluster_observations(
        observations,
        min_cluster_size=2,
        time_window_hours=48,
        min_term_overlap=0.10,
    )
    assert len(stories) == 0


def test_source_diversity_tracked() -> None:
    observations = [
        _make_obs("Bridge collapse reported in downtown area", source="rss:bbc.com"),
        _make_obs("Bridge collapses in downtown causing road closure", source="reddit:r/news"),
        _make_obs("Downtown bridge has collapsed blocking traffic", source="rss:reuters.com"),
    ]
    stories = cluster_observations(observations, min_cluster_size=2, min_term_overlap=0.10)
    if stories:
        assert stories[0].source_count >= 2
