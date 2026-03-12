"""Tests for the story discovery / burst detection module."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sts_monitor.story_discovery import (
    BurstSignal,
    DiscoveredTopic,
    ObservationSnapshot,
    detect_bursts,
    discover_topics_from_convergence,
    run_discovery,
)

pytestmark = pytest.mark.unit


def _make_snapshot(
    claim: str,
    hours_ago: float = 0,
    source: str = "rss:example.com",
    reliability: float = 0.7,
    url: str = "",
) -> ObservationSnapshot:
    return ObservationSnapshot(
        claim=claim,
        source=source,
        captured_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        url=url or f"https://example.com/{hash(claim) % 9999}",
        reliability_hint=reliability,
    )


# ── detect_bursts ──────────────────────────────────────────────────────


def test_detect_bursts_finds_spike() -> None:
    # Create a term that appears many times in the recent window but rarely in baseline
    recent = [_make_snapshot(f"Earthquake strikes Istanbul report {i}", hours_ago=1) for i in range(10)]
    baseline = [_make_snapshot(f"Weather forecast sunny skies report {i}", hours_ago=30) for i in range(5)]

    bursts = detect_bursts(
        recent + baseline,
        window_hours=6,
        baseline_hours=48,
        min_burst_ratio=2.0,
        min_current_count=3,
    )
    # "earthquake" or "istanbul" should be flagged as a burst
    burst_terms = {b.term for b in bursts}
    assert "earthquake" in burst_terms or "istanbul" in burst_terms


def test_detect_bursts_empty_observations() -> None:
    bursts = detect_bursts([])
    assert bursts == []


def test_detect_bursts_no_spike_when_uniform() -> None:
    # Same term in both windows at same rate -> no burst
    recent = [_make_snapshot("routine weather report update", hours_ago=2) for _ in range(3)]
    baseline = [_make_snapshot("routine weather report update", hours_ago=30) for _ in range(30)]

    bursts = detect_bursts(
        recent + baseline,
        window_hours=6,
        baseline_hours=48,
        min_burst_ratio=3.0,
        min_current_count=3,
    )
    # "routine" should NOT spike since baseline is proportionally similar
    routine_bursts = [b for b in bursts if b.term == "routine"]
    assert len(routine_bursts) == 0


def test_burst_signal_has_expected_fields() -> None:
    recent = [_make_snapshot(f"Massive explosion downtown area {i}", hours_ago=1) for i in range(8)]
    bursts = detect_bursts(
        recent,
        window_hours=6,
        baseline_hours=48,
        min_burst_ratio=2.0,
        min_current_count=3,
    )
    if bursts:
        b = bursts[0]
        assert isinstance(b.term, str)
        assert b.current_count >= 3
        assert b.burst_ratio >= 2.0
        assert b.window_hours == 6


# ── discover_topics_from_convergence ───────────────────────────────────


def test_convergence_topics_skip_low_severity() -> None:
    zones = [
        {
            "signal_types": ["earthquake", "fire"],
            "severity": "low",
            "center_lat": 50.0,
            "center_lon": 30.0,
            "radius_km": 50,
        }
    ]
    topics = discover_topics_from_convergence(zones)
    assert len(topics) == 0


def test_convergence_topics_medium_severity() -> None:
    zones = [
        {
            "signal_types": ["earthquake", "fire", "conflict"],
            "severity": "medium",
            "center_lat": 50.0,
            "center_lon": 30.0,
            "radius_km": 50,
        }
    ]
    topics = discover_topics_from_convergence(zones)
    assert len(topics) == 1
    assert topics[0].source == "convergence"
    assert topics[0].score == 0.6


def test_convergence_topics_critical_severity() -> None:
    zones = [
        {
            "signal_types": ["earthquake", "fire", "conflict", "weather", "humanitarian"],
            "severity": "critical",
            "center_lat": 50.0,
            "center_lon": 30.0,
            "radius_km": 50,
        }
    ]
    topics = discover_topics_from_convergence(zones)
    assert len(topics) == 1
    assert topics[0].score == 0.95


# ── run_discovery ──────────────────────────────────────────────────────


def test_run_discovery_with_convergence_zones() -> None:
    observations = [_make_snapshot(f"Earthquake near Istanbul {i}", hours_ago=1) for i in range(5)]
    zones = [
        {
            "signal_types": ["earthquake", "fire", "conflict"],
            "severity": "high",
            "center_lat": 41.0,
            "center_lon": 29.0,
            "radius_km": 50,
        }
    ]
    topics = run_discovery(observations, convergence_zones=zones)
    convergence_topics = [t for t in topics if t.source == "convergence"]
    assert len(convergence_topics) >= 1


def test_run_discovery_empty_observations() -> None:
    topics = run_discovery([])
    assert topics == []


def test_run_discovery_deduplicates_topics() -> None:
    # Same term in bursts and entity spikes should be deduped
    observations = [_make_snapshot(f"NATO summit in Ukraine report {i}", hours_ago=1) for i in range(10)]
    topics = run_discovery(observations)
    seed_queries = [t.suggested_seed_query.lower() for t in topics]
    # No duplicate seed queries
    assert len(seed_queries) == len(set(seed_queries))


def test_discovered_topic_has_expected_fields() -> None:
    observations = [_make_snapshot(f"Wildfire burns near Los Angeles {i}", hours_ago=1) for i in range(10)]
    zones = [
        {
            "signal_types": ["fire", "weather_alert", "disaster"],
            "severity": "high",
            "center_lat": 34.0,
            "center_lon": -118.2,
            "radius_km": 50,
        }
    ]
    topics = run_discovery(observations, convergence_zones=zones)
    for t in topics:
        assert isinstance(t.title, str) and len(t.title) > 0
        assert 0.0 <= t.score <= 1.0
        assert t.source in ("burst", "convergence", "entity_spike")
        assert isinstance(t.key_terms, list)
        assert isinstance(t.suggested_connectors, list)
