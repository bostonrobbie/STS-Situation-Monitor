"""Convergence detection engine.

Detects when multiple signal types (earthquake + conflict + fire, etc.)
cluster in the same geographic area within a time window.

Inspired by WorldMonitor: "One signal is noise. Three or four converging
in the same location is the signal worth surfacing."
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import atan2, cos, radians, sin, sqrt


@dataclass(slots=True)
class GeoPoint:
    latitude: float
    longitude: float
    layer: str
    title: str
    event_time: datetime
    source_id: str = ""


@dataclass(slots=True)
class ConvergenceZone:
    center_lat: float
    center_lon: float
    radius_km: float
    signal_count: int
    signal_types: list[str]
    severity: str  # low, medium, high, critical
    events: list[GeoPoint]
    first_detected_at: datetime
    last_updated_at: datetime


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance between two points on Earth in km."""
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return r * 2 * atan2(sqrt(a), sqrt(1 - a))


def _severity_from_count(signal_count: int) -> str:
    if signal_count >= 5:
        return "critical"
    if signal_count >= 4:
        return "high"
    if signal_count >= 3:
        return "medium"
    return "low"


def detect_convergence(
    geo_points: list[GeoPoint],
    radius_km: float = 50.0,
    min_signal_types: int = 3,
    time_window_hours: int = 24,
) -> list[ConvergenceZone]:
    """Cluster geo_points by proximity and count distinct signal types.

    Returns convergence zones where >= min_signal_types different layer types
    are active within radius_km of each other within the time window.
    """
    if not geo_points:
        return []

    cutoff = datetime.now(UTC) - timedelta(hours=time_window_hours)
    # Handle both timezone-aware and naive datetimes (SQLite strips tzinfo)
    cutoff_naive = cutoff.replace(tzinfo=None)
    recent = [
        p for p in geo_points
        if (p.event_time.replace(tzinfo=None) if p.event_time else cutoff_naive) >= cutoff_naive
    ]

    if len(recent) < min_signal_types:
        return []

    # Simple greedy clustering: for each point, find all points within radius
    used: set[int] = set()
    zones: list[ConvergenceZone] = []

    for i, anchor in enumerate(recent):
        if i in used:
            continue

        cluster = [anchor]
        cluster_indices = {i}

        for j, other in enumerate(recent):
            if j in used or j == i:
                continue
            dist = haversine_km(anchor.latitude, anchor.longitude, other.latitude, other.longitude)
            if dist <= radius_km:
                cluster.append(other)
                cluster_indices.add(j)

        unique_types = list({p.layer for p in cluster})

        if len(unique_types) >= min_signal_types:
            center_lat = sum(p.latitude for p in cluster) / len(cluster)
            center_lon = sum(p.longitude for p in cluster) / len(cluster)
            times = [p.event_time for p in cluster]

            zone = ConvergenceZone(
                center_lat=round(center_lat, 4),
                center_lon=round(center_lon, 4),
                radius_km=radius_km,
                signal_count=len(unique_types),
                signal_types=unique_types,
                severity=_severity_from_count(len(unique_types)),
                events=cluster,
                first_detected_at=min(times),
                last_updated_at=max(times),
            )
            zones.append(zone)
            used.update(cluster_indices)

    return zones
