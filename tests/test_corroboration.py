"""Tests for the cross-source corroboration engine."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sts_monitor.corroboration import (
    CorroborationResult,
    CorroborationScore,
    analyze_corroboration,
    claim_similarity,
    classify_source_tier,
    cluster_claims,
    normalize_claim,
    score_cluster,
    _extract_domain,
    _source_family,
    _extract_connector_type,
)

pytestmark = pytest.mark.unit


# ── Helpers ───────────────────────────────────────────────────────────

_NOW = datetime.now(UTC)


def _obs(
    source: str,
    claim: str,
    url: str = "https://example.com/article",
    hours_ago: float = 0,
    reliability: float = 0.5,
) -> dict:
    return {
        "source": source,
        "claim": claim,
        "url": url,
        "captured_at": _NOW - timedelta(hours=hours_ago),
        "reliability_hint": reliability,
    }


# ── _extract_domain ──────────────────────────────────────────────────

def test_extract_domain_basic_url() -> None:
    assert _extract_domain("https://www.reuters.com/article/foo") == "reuters.com"


def test_extract_domain_strips_www() -> None:
    assert _extract_domain("https://www.bbc.com/news") == "bbc.com"


def test_extract_domain_no_scheme() -> None:
    assert _extract_domain("apnews.com/some-article") == "apnews.com"


def test_extract_domain_with_query_string() -> None:
    assert _extract_domain("https://cnn.com/article?ref=123") == "cnn.com"


def test_extract_domain_mixed_case() -> None:
    assert _extract_domain("https://WWW.Reuters.COM/foo") == "reuters.com"


def test_extract_domain_empty() -> None:
    assert _extract_domain("") == ""


# ── _extract_connector_type ──────────────────────────────────────────

def test_extract_connector_type_with_colon() -> None:
    assert _extract_connector_type("rss:bbc.com") == "rss"


def test_extract_connector_type_without_colon() -> None:
    assert _extract_connector_type("gdelt") == "gdelt"


# ── _source_family ───────────────────────────────────────────────────

def test_source_family_with_domain() -> None:
    assert _source_family("rss:www.bbc.com/feed") == "bbc.com"


def test_source_family_bare_source() -> None:
    assert _source_family("gdelt") == "gdelt"


# ── classify_source_tier ─────────────────────────────────────────────

def test_tier1_by_domain() -> None:
    assert classify_source_tier("Reuters", "https://reuters.com/article") == "tier1"


def test_tier1_by_authoritative_connector() -> None:
    assert classify_source_tier("USGS Earthquake Feed", "https://usgs.gov/data", "usgs") == "tier1"


def test_tier2_by_domain() -> None:
    assert classify_source_tier("CNN", "https://cnn.com/breaking") == "tier2"


def test_tier3_by_domain() -> None:
    assert classify_source_tier("RT", "https://rt.com/news") == "tier3"


def test_social_by_connector() -> None:
    assert classify_source_tier("nitter:some_user", "https://nitter.net/some_user", "nitter") == "social"


def test_unknown_source() -> None:
    assert classify_source_tier("myblog", "https://randomblog.xyz/post") == "unknown"


def test_tier1_by_source_name_fallback() -> None:
    """When domain doesn't match but source name contains a tier-1 outlet name."""
    assert classify_source_tier("reuters wire feed", "https://feeds.example.com/rss") == "tier1"


def test_tier2_by_source_name_fallback() -> None:
    assert classify_source_tier("cnn international", "https://feeds.example.com/rss") == "tier2"


def test_authoritative_connector_overrides_domain() -> None:
    """Authoritative connector wins even with an unrelated URL."""
    assert classify_source_tier("Some NASA data", "https://randomblog.xyz/data", "nasa_firms") == "tier1"


# ── normalize_claim ──────────────────────────────────────────────────

def test_normalize_strips_noise_words() -> None:
    result = normalize_claim("BREAKING: Earthquake hits region")
    assert "breaking" not in result
    assert "earthquake" in result


def test_normalize_removes_punctuation() -> None:
    result = normalize_claim("Explosion reported!!! Near airport?")
    assert "!" not in result
    assert "?" not in result


def test_normalize_lowercases() -> None:
    result = normalize_claim("MAJOR FLOOD Warning")
    assert result == normalize_claim("major flood warning")


def test_normalize_collapses_whitespace() -> None:
    result = normalize_claim("   lots   of   spaces   ")
    assert "  " not in result
    assert result == "lots of spaces"


def test_normalize_strips_multiple_noise_words() -> None:
    result = normalize_claim("EXCLUSIVE: Sources say explosion confirmed near airport")
    assert "exclusive" not in result
    assert "sources say" not in result
    assert "confirmed" not in result
    assert "explosion" in result
    assert "airport" in result


# ── claim_similarity ─────────────────────────────────────────────────

def test_similarity_identical_claims() -> None:
    sim = claim_similarity("Earthquake hits Tokyo", "Earthquake hits Tokyo")
    assert sim == pytest.approx(1.0)


def test_similarity_identical_after_normalization() -> None:
    sim = claim_similarity(
        "BREAKING: Earthquake hits Tokyo",
        "UPDATE: Earthquake hits Tokyo",
    )
    assert sim == pytest.approx(1.0)


def test_similarity_completely_different() -> None:
    sim = claim_similarity("Earthquake hits Tokyo", "New trade deal signed in Brussels")
    assert sim < 0.15


def test_similarity_partially_overlapping() -> None:
    sim = claim_similarity(
        "Large earthquake hits northern Japan near Tokyo",
        "Earthquake reported in Tokyo region",
    )
    assert 0.15 < sim < 0.9


def test_similarity_empty_string() -> None:
    assert claim_similarity("", "Something") == 0.0
    assert claim_similarity("Something", "") == 0.0
    assert claim_similarity("", "") == 0.0


# ── cluster_claims ───────────────────────────────────────────────────

def test_cluster_empty_input() -> None:
    assert cluster_claims([]) == []


def test_cluster_single_observation() -> None:
    obs = [_obs("rss:reuters.com", "Earthquake in Chile")]
    clusters = cluster_claims(obs)
    assert len(clusters) == 1
    assert len(clusters[0]) == 1


def test_cluster_similar_claims_grouped() -> None:
    obs = [
        _obs("rss:reuters.com", "Major earthquake strikes Chile killing 10"),
        _obs("rss:bbc.com", "Earthquake strikes Chile, 10 dead"),
        _obs("gdelt", "New trade deal signed in Brussels"),
    ]
    clusters = cluster_claims(obs)
    assert len(clusters) == 2
    # The earthquake claims should cluster together
    earthquake_cluster = [c for c in clusters if "earthquake" in c[0]["claim"].lower()][0]
    assert len(earthquake_cluster) == 2


def test_cluster_different_claims_separate() -> None:
    obs = [
        _obs("rss:reuters.com", "Flood in Bangladesh"),
        _obs("rss:bbc.com", "Election results in France"),
        _obs("gdelt", "Volcano erupts in Iceland"),
    ]
    clusters = cluster_claims(obs)
    assert len(clusters) == 3


def test_cluster_skips_empty_claims() -> None:
    obs = [
        _obs("rss:reuters.com", ""),
        _obs("rss:bbc.com", "Earthquake in Chile"),
    ]
    clusters = cluster_claims(obs)
    assert len(clusters) == 1


def test_cluster_respects_threshold() -> None:
    """Higher threshold requires closer match to cluster."""
    obs = [
        _obs("src1", "Earthquake strikes central Chile region today"),
        _obs("src2", "Chile earthquake reported in central area"),
    ]
    # Low threshold: should cluster
    low = cluster_claims(obs, similarity_threshold=0.1)
    # Very high threshold: should not cluster
    high = cluster_claims(obs, similarity_threshold=0.95)
    assert len(low) <= len(high)


# ── score_cluster ────────────────────────────────────────────────────

def test_score_single_source() -> None:
    cluster = [_obs("rss:reuters.com", "Earthquake in Chile", "https://reuters.com/art")]
    score = score_cluster(cluster)
    assert score.verdict == "single_source"
    assert score.independent_sources == 1


def test_score_two_independent_sources() -> None:
    cluster = [
        _obs("rss:reuters.com", "Earthquake in Chile", "https://reuters.com/art", hours_ago=0),
        _obs("rss:bbc.com", "Earthquake in Chile", "https://bbc.com/news", hours_ago=1),
    ]
    score = score_cluster(cluster)
    assert score.independent_sources == 2
    assert score.verdict in ("partially_corroborated", "well_corroborated")


def test_score_well_corroborated_four_sources() -> None:
    cluster = [
        _obs("rss:reuters.com", "Earthquake in Chile magnitude 7.2", "https://reuters.com/art", hours_ago=0, reliability=0.9),
        _obs("rss:bbc.com", "Earthquake in Chile magnitude 7.2", "https://bbc.com/news", hours_ago=1, reliability=0.9),
        _obs("gdelt:apnews.com", "Earthquake in Chile", "https://apnews.com/art", hours_ago=2, reliability=0.8),
        _obs("usgs:data", "Seismic event Chile 7.2", "https://usgs.gov/data", hours_ago=0.5, reliability=1.0),
    ]
    score = score_cluster(cluster)
    assert score.independent_sources >= 3
    assert score.verdict == "well_corroborated"
    assert score.score >= 0.5


def test_score_breakdown_has_all_factors() -> None:
    cluster = [
        _obs("rss:reuters.com", "Earthquake", "https://reuters.com/art"),
        _obs("rss:bbc.com", "Earthquake", "https://bbc.com/news", hours_ago=1),
    ]
    score = score_cluster(cluster)
    assert "independent_sources" in score.breakdown
    assert "connector_diversity" in score.breakdown
    assert "source_quality" in score.breakdown
    assert "temporal_convergence" in score.breakdown
    assert "avg_reliability" in score.breakdown


def test_score_independent_sources_factor() -> None:
    """More independent families means higher source score."""
    one = score_cluster([_obs("rss:reuters.com", "event", "https://reuters.com/art")])
    three = score_cluster([
        _obs("rss:reuters.com", "event", "https://reuters.com/art"),
        _obs("rss:bbc.com", "event", "https://bbc.com/art", hours_ago=1),
        _obs("gdelt:apnews.com", "event", "https://apnews.com/art", hours_ago=2),
    ])
    assert three.breakdown["independent_sources"] > one.breakdown["independent_sources"]


def test_score_connector_diversity_factor() -> None:
    """Different connector types should boost diversity score."""
    same_connector = score_cluster([
        _obs("rss:reuters.com", "event", "https://reuters.com/art"),
        _obs("rss:bbc.com", "event", "https://bbc.com/art", hours_ago=1),
    ])
    diff_connector = score_cluster([
        _obs("rss:reuters.com", "event", "https://reuters.com/art"),
        _obs("gdelt:bbc.com", "event", "https://bbc.com/art", hours_ago=1),
    ])
    assert diff_connector.breakdown["connector_diversity"] > same_connector.breakdown["connector_diversity"]


def test_score_source_quality_factor() -> None:
    """Tier-1 sources should have higher quality score than unknown sources."""
    tier1_cluster = score_cluster([
        _obs("rss:reuters.com", "event", "https://reuters.com/art"),
    ])
    unknown_cluster = score_cluster([
        _obs("myblog", "event", "https://randomblog.xyz/art"),
    ])
    assert tier1_cluster.breakdown["source_quality"] >= unknown_cluster.breakdown["source_quality"]


def test_score_temporal_convergence_rapid() -> None:
    """Reports within 6 hours get max temporal score."""
    cluster = [
        _obs("rss:reuters.com", "event", "https://reuters.com/art", hours_ago=1),
        _obs("rss:bbc.com", "event", "https://bbc.com/art", hours_ago=0),
    ]
    score = score_cluster(cluster)
    assert score.breakdown["temporal_convergence"] == 0.15
    assert score.temporal_spread_hours <= 6


def test_score_temporal_convergence_slow() -> None:
    """Reports spread over days get lower temporal score."""
    cluster = [
        _obs("rss:reuters.com", "event", "https://reuters.com/art", hours_ago=100),
        _obs("rss:bbc.com", "event", "https://bbc.com/art", hours_ago=0),
    ]
    score = score_cluster(cluster)
    assert score.breakdown["temporal_convergence"] < 0.15


def test_score_first_reported() -> None:
    cluster = [
        _obs("rss:bbc.com", "event", "https://bbc.com/art", hours_ago=0),
        _obs("rss:reuters.com", "event first", "https://reuters.com/art", hours_ago=2),
    ]
    score = score_cluster(cluster)
    # The earlier observation should be first_reported_by
    assert score.first_reported_by == "rss:reuters.com"


def test_score_claim_summary_from_best_reliability() -> None:
    cluster = [
        _obs("rss:reuters.com", "High reliability claim", "https://reuters.com/art", reliability=0.9),
        _obs("rss:bbc.com", "Low reliability claim", "https://bbc.com/art", reliability=0.1),
    ]
    score = score_cluster(cluster)
    assert score.claim_summary == "High reliability claim"


def test_score_same_source_family_counted_once() -> None:
    """Two observations from the same source family count as one independent source."""
    cluster = [
        _obs("rss:reuters.com", "event", "https://reuters.com/art1"),
        _obs("rss:reuters.com", "event", "https://reuters.com/art2", hours_ago=1),
    ]
    score = score_cluster(cluster)
    assert score.independent_sources == 1
    assert score.verdict == "single_source"


def test_score_evidence_sorted_by_time() -> None:
    cluster = [
        _obs("rss:bbc.com", "event", "https://bbc.com/art", hours_ago=0),
        _obs("rss:reuters.com", "event", "https://reuters.com/art", hours_ago=5),
    ]
    score = score_cluster(cluster)
    assert score.evidence[0].captured_at <= score.evidence[1].captured_at


def test_score_string_timestamp() -> None:
    """String timestamps in ISO format should be parsed correctly."""
    cluster = [
        {
            "source": "rss:reuters.com",
            "claim": "event",
            "url": "https://reuters.com/art",
            "captured_at": "2024-01-15T10:00:00+00:00",
            "reliability_hint": 0.7,
        },
    ]
    score = score_cluster(cluster)
    assert score.independent_sources == 1


def test_score_source_tiers_dict() -> None:
    cluster = [
        _obs("rss:reuters.com", "event", "https://reuters.com/art"),
        _obs("nitter:user", "event", "https://nitter.net/user", hours_ago=1),
    ]
    # nitter connector_type -> social
    cluster[1]["source"] = "nitter:user"
    score = score_cluster(cluster)
    assert "tier1" in score.source_tiers or "social" in score.source_tiers


# ── analyze_corroboration (integration) ──────────────────────────────

def test_analyze_empty_input() -> None:
    result = analyze_corroboration([])
    assert result.total_claims == 0
    assert result.scores == []
    assert result.overall_corroboration_rate == 0.0


def test_analyze_single_observation() -> None:
    result = analyze_corroboration([
        _obs("rss:reuters.com", "Earthquake in Chile", "https://reuters.com/art"),
    ])
    assert result.total_claims == 1
    assert result.single_source == 1
    assert result.well_corroborated == 0


def test_analyze_two_different_claims() -> None:
    result = analyze_corroboration([
        _obs("rss:reuters.com", "Earthquake in Chile", "https://reuters.com/art"),
        _obs("rss:bbc.com", "Election in France", "https://bbc.com/art"),
    ])
    assert result.total_claims == 2
    assert result.single_source == 2


def test_analyze_well_corroborated_scenario() -> None:
    result = analyze_corroboration([
        _obs("rss:reuters.com", "Major earthquake hits central Chile 7.2 magnitude", "https://reuters.com/art", hours_ago=0, reliability=0.9),
        _obs("rss:bbc.com", "Earthquake hits Chile magnitude 7.2", "https://bbc.com/art", hours_ago=1, reliability=0.9),
        _obs("gdelt:apnews.com", "Chile earthquake 7.2 magnitude", "https://apnews.com/art", hours_ago=2, reliability=0.8),
        _obs("usgs:data", "Seismic event Chile 7.2", "https://usgs.gov/data", hours_ago=0.5, reliability=1.0),
    ])
    assert result.well_corroborated >= 1
    assert result.overall_corroboration_rate > 0


def test_analyze_mixed_corroboration() -> None:
    result = analyze_corroboration([
        # Corroborated cluster
        _obs("rss:reuters.com", "Major earthquake hits central Chile magnitude 7", "https://reuters.com/art", hours_ago=0, reliability=0.9),
        _obs("rss:bbc.com", "Earthquake hits Chile magnitude 7", "https://bbc.com/art", hours_ago=1, reliability=0.9),
        _obs("gdelt:apnews.com", "Chile earthquake magnitude 7 hits", "https://apnews.com/art", hours_ago=2, reliability=0.8),
        # Standalone claim
        _obs("rss:cnn.com", "New trade deal signed in Brussels today", "https://cnn.com/art", hours_ago=0),
    ])
    assert result.total_claims == 2
    assert result.single_source >= 1


def test_analyze_scores_sorted_descending() -> None:
    result = analyze_corroboration([
        _obs("rss:reuters.com", "Major earthquake hits Chile magnitude 7", "https://reuters.com/art", hours_ago=0, reliability=0.9),
        _obs("rss:bbc.com", "Earthquake hits Chile magnitude 7", "https://bbc.com/art", hours_ago=1, reliability=0.9),
        _obs("gdelt:apnews.com", "Chile earthquake magnitude 7 today", "https://apnews.com/art", hours_ago=2, reliability=0.8),
        _obs("rss:cnn.com", "Unrelated event in another country entirely", "https://cnn.com/art", hours_ago=0),
    ])
    if len(result.scores) >= 2:
        assert result.scores[0].score >= result.scores[1].score


def test_analyze_corroboration_rate() -> None:
    """Overall corroboration rate is fraction of well_corroborated clusters."""
    result = analyze_corroboration([
        _obs("rss:reuters.com", "Event alpha happens", "https://reuters.com/art"),
    ])
    assert result.overall_corroboration_rate == 0.0


def test_analyze_identical_claims_same_source_single() -> None:
    """Multiple identical claims from the same source family count as single source."""
    result = analyze_corroboration([
        _obs("rss:reuters.com", "Earthquake in Chile", "https://reuters.com/art1"),
        _obs("rss:reuters.com", "Earthquake in Chile", "https://reuters.com/art2", hours_ago=1),
    ])
    assert result.total_claims == 1
    assert result.single_source == 1
