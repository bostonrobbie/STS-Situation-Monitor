"""Geofencing alerts — define geographic zones and trigger alerts on new observations.

Supports:
- Named zones (Boston metro, conflict zone, custom polygon)
- Auto-alert when observations land in a watched zone
- Distance-based proximity alerts
- Zone activity tracking over time
"""
from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class GeoZone:
    """A named geographic zone to monitor."""
    name: str
    center_lat: float
    center_lon: float
    radius_km: float
    category: str = "custom"  # custom, conflict, city, region
    active: bool = True
    alert_on_entry: bool = True
    investigation_id: str | None = None  # scope to specific investigation
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "center_lat": self.center_lat,
            "center_lon": self.center_lon,
            "radius_km": self.radius_km,
            "category": self.category,
            "active": self.active,
            "alert_on_entry": self.alert_on_entry,
            "investigation_id": self.investigation_id,
        }


@dataclass
class GeoAlert:
    """Alert triggered by observation entering a geofence."""
    zone_name: str
    observation_id: int | None
    source: str
    claim: str
    lat: float
    lon: float
    distance_km: float
    triggered_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "zone": self.zone_name,
            "observation_id": self.observation_id,
            "source": self.source,
            "claim": self.claim[:300],
            "lat": self.lat,
            "lon": self.lon,
            "distance_km": round(self.distance_km, 2),
            "triggered_at": self.triggered_at.isoformat(),
        }


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance between two points in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Pre-built zones ─────────────────────────────────────────────────
BUILTIN_ZONES = [
    GeoZone("Boston Metro", 42.3601, -71.0589, 30.0, "city"),
    GeoZone("Cambridge/Somerville", 42.3736, -71.1097, 8.0, "city"),
    GeoZone("Greater Boston", 42.3601, -71.0589, 80.0, "region"),
    GeoZone("Massachusetts", 42.4072, -71.3824, 120.0, "region"),
    GeoZone("New England", 43.0, -71.5, 300.0, "region"),
    GeoZone("Washington DC", 38.9072, -77.0369, 25.0, "city"),
    GeoZone("New York City", 40.7128, -74.0060, 30.0, "city"),
    GeoZone("Ukraine Conflict Zone", 48.3794, 31.1656, 600.0, "conflict"),
    GeoZone("Gaza Strip", 31.3547, 34.3088, 25.0, "conflict"),
    GeoZone("Taiwan Strait", 24.0, 119.5, 200.0, "conflict"),
    GeoZone("South China Sea", 12.0, 114.0, 800.0, "conflict"),
    GeoZone("Red Sea/Bab al-Mandab", 13.0, 43.0, 300.0, "conflict"),
    GeoZone("Persian Gulf", 27.0, 51.0, 400.0, "conflict"),
    GeoZone("Korean DMZ", 38.0, 127.0, 100.0, "conflict"),
]

# Runtime storage
_custom_zones: list[GeoZone] = []


def get_all_zones() -> list[GeoZone]:
    """Get all zones (builtin + custom)."""
    return BUILTIN_ZONES + _custom_zones


def add_zone(zone: GeoZone) -> None:
    """Add a custom geofence zone."""
    _custom_zones.append(zone)


def remove_zone(name: str) -> bool:
    """Remove a custom zone by name."""
    global _custom_zones
    before = len(_custom_zones)
    _custom_zones = [z for z in _custom_zones if z.name != name]
    return len(_custom_zones) < before


def check_observations_against_zones(
    observations: list[dict[str, Any]],
    zones: list[GeoZone] | None = None,
    investigation_id: str | None = None,
) -> list[GeoAlert]:
    """Check which observations fall within geofence zones."""
    if zones is None:
        zones = get_all_zones()

    active_zones = [z for z in zones if z.active and z.alert_on_entry]
    if investigation_id:
        active_zones = [z for z in active_zones if z.investigation_id is None or z.investigation_id == investigation_id]

    alerts = []
    for obs in observations:
        lat = obs.get("latitude") or obs.get("lat")
        lon = obs.get("longitude") or obs.get("lon")
        if lat is None or lon is None:
            continue

        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            continue

        for zone in active_zones:
            dist = _haversine_km(lat, lon, zone.center_lat, zone.center_lon)
            if dist <= zone.radius_km:
                alerts.append(GeoAlert(
                    zone_name=zone.name,
                    observation_id=obs.get("id"),
                    source=obs.get("source", "unknown"),
                    claim=obs.get("claim", ""),
                    lat=lat,
                    lon=lon,
                    distance_km=dist,
                    triggered_at=datetime.now(UTC),
                ))

    alerts.sort(key=lambda a: a.distance_km)
    return alerts


def get_zone_activity_summary(
    observations: list[dict[str, Any]],
    zones: list[GeoZone] | None = None,
) -> list[dict[str, Any]]:
    """Get activity count per zone."""
    if zones is None:
        zones = get_all_zones()

    results = []
    for zone in zones:
        count = 0
        sources = set()
        for obs in observations:
            lat = obs.get("latitude") or obs.get("lat")
            lon = obs.get("longitude") or obs.get("lon")
            if lat is None or lon is None:
                continue
            try:
                dist = _haversine_km(float(lat), float(lon), zone.center_lat, zone.center_lon)
                if dist <= zone.radius_km:
                    count += 1
                    sources.add(obs.get("source", "unknown"))
            except (TypeError, ValueError):
                continue

        results.append({
            "zone": zone.name,
            "category": zone.category,
            "center": [zone.center_lat, zone.center_lon],
            "radius_km": zone.radius_km,
            "observation_count": count,
            "source_count": len(sources),
            "sources": sorted(sources)[:10],
        })

    results.sort(key=lambda r: r["observation_count"], reverse=True)
    return results
