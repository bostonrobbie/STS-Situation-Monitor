"""Tests for the convergence zone detection module."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sts_monitor.convergence import (
    ConvergenceZone,
    GeoPoint,
    detect_convergence,
    haversine_km,
)

pytestmark = pytest.mark.unit


# ── haversine_km ───────────────────────────────────────────────────────


def test_haversine_same_point_is_zero() -> None:
    assert haversine_km(51.5, -0.12, 51.5, -0.12) == pytest.approx(0.0)


def test_haversine_known_distance_london_to_paris() -> None:
    # London (51.5074, -0.1278) -> Paris (48.8566, 2.3522) ~ 343 km
    dist = haversine_km(51.5074, -0.1278, 48.8566, 2.3522)
    assert 340 < dist < 350


def test_haversine_known_distance_nyc_to_la() -> None:
    # NYC (40.7128, -74.0060) -> LA (34.0522, -118.2437) ~ 3944 km
    dist = haversine_km(40.7128, -74.0060, 34.0522, -118.2437)
    assert 3930 < dist < 3960


def test_haversine_symmetry() -> None:
    d1 = haversine_km(0.0, 0.0, 10.0, 10.0)
    d2 = haversine_km(10.0, 10.0, 0.0, 0.0)
    assert d1 == pytest.approx(d2)


def test_haversine_antipodal_points() -> None:
    # Roughly half-earth
    dist = haversine_km(0.0, 0.0, 0.0, 180.0)
    assert 20000 < dist < 20100


# ── detect_convergence ─────────────────────────────────────────────────


def _make_point(lat: float, lon: float, layer: str, hours_ago: float = 0) -> GeoPoint:
    return GeoPoint(
        latitude=lat,
        longitude=lon,
        layer=layer,
        title=f"{layer} event",
        event_time=datetime.now(UTC) - timedelta(hours=hours_ago),
    )


def test_clustered_points_returns_zone() -> None:
    # Three different signal types within ~10km of each other
    points = [
        _make_point(50.45, 30.52, "earthquake"),
        _make_point(50.451, 30.521, "fire"),
        _make_point(50.452, 30.522, "conflict"),
    ]
    zones = detect_convergence(points, radius_km=50, min_signal_types=3, time_window_hours=24)
    assert len(zones) == 1
    assert zones[0].signal_count >= 3
    assert set(zones[0].signal_types) == {"earthquake", "fire", "conflict"}


def test_spread_out_points_returns_nothing() -> None:
    # Points far apart (different continents)
    points = [
        _make_point(51.5, -0.12, "earthquake"),      # London
        _make_point(-33.86, 151.21, "fire"),          # Sydney
        _make_point(35.68, 139.69, "conflict"),       # Tokyo
    ]
    zones = detect_convergence(points, radius_km=50, min_signal_types=3, time_window_hours=24)
    assert len(zones) == 0


def test_same_signal_type_not_counted_twice() -> None:
    # Two earthquake + one fire = only 2 distinct signal types
    points = [
        _make_point(50.45, 30.52, "earthquake"),
        _make_point(50.451, 30.521, "earthquake"),
        _make_point(50.452, 30.522, "fire"),
    ]
    zones = detect_convergence(points, radius_km=50, min_signal_types=3, time_window_hours=24)
    assert len(zones) == 0


def test_min_signal_types_filtering() -> None:
    points = [
        _make_point(50.45, 30.52, "earthquake"),
        _make_point(50.451, 30.521, "fire"),
        _make_point(50.452, 30.522, "conflict"),
        _make_point(50.453, 30.523, "weather_alert"),
    ]
    # min_signal_types=4 should find a zone
    zones_4 = detect_convergence(points, radius_km=50, min_signal_types=4)
    assert len(zones_4) == 1

    # min_signal_types=5 should NOT find a zone
    zones_5 = detect_convergence(points, radius_km=50, min_signal_types=5)
    assert len(zones_5) == 0


def test_old_points_outside_time_window_ignored() -> None:
    points = [
        _make_point(50.45, 30.52, "earthquake", hours_ago=0),
        _make_point(50.451, 30.521, "fire", hours_ago=0),
        _make_point(50.452, 30.522, "conflict", hours_ago=48),  # Too old
    ]
    zones = detect_convergence(points, radius_km=50, min_signal_types=3, time_window_hours=24)
    assert len(zones) == 0


def test_severity_low_for_three_types() -> None:
    points = [
        _make_point(50.45, 30.52, "earthquake"),
        _make_point(50.451, 30.521, "fire"),
        _make_point(50.452, 30.522, "conflict"),
    ]
    zones = detect_convergence(points, radius_km=50, min_signal_types=3)
    assert zones[0].severity == "medium"


def test_severity_high_for_four_types() -> None:
    points = [
        _make_point(50.45, 30.52, "earthquake"),
        _make_point(50.451, 30.521, "fire"),
        _make_point(50.452, 30.522, "conflict"),
        _make_point(50.453, 30.523, "weather_alert"),
    ]
    zones = detect_convergence(points, radius_km=50, min_signal_types=3)
    assert zones[0].severity == "high"


def test_severity_critical_for_five_types() -> None:
    points = [
        _make_point(50.45, 30.52, "earthquake"),
        _make_point(50.451, 30.521, "fire"),
        _make_point(50.452, 30.522, "conflict"),
        _make_point(50.453, 30.523, "weather_alert"),
        _make_point(50.454, 30.524, "humanitarian"),
    ]
    zones = detect_convergence(points, radius_km=50, min_signal_types=3)
    assert zones[0].severity == "critical"


def test_empty_input_returns_empty() -> None:
    assert detect_convergence([], radius_km=50, min_signal_types=3) == []


def test_zone_center_is_average_of_cluster() -> None:
    points = [
        _make_point(50.0, 30.0, "earthquake"),
        _make_point(50.0, 30.0, "fire"),
        _make_point(50.0, 30.0, "conflict"),
    ]
    zones = detect_convergence(points, radius_km=50, min_signal_types=3)
    assert zones[0].center_lat == pytest.approx(50.0, abs=0.01)
    assert zones[0].center_lon == pytest.approx(30.0, abs=0.01)
