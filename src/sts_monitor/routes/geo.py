"""Geo-spatial routes: events, layers, convergence, dashboard, cameras, geofences."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sts_monitor.connectors.webcams import (
    CURATED_CAMERAS,
    get_cameras_near,
    list_camera_regions,
)
from sts_monitor.convergence import GeoPoint, detect_convergence
from sts_monitor.database import get_session
from sts_monitor.event_bus import STSEvent, event_bus
from sts_monitor.geofence import (
    GeoZone,
    add_zone,
    check_observations_against_zones,
    get_all_zones,
    get_zone_activity_summary,
    remove_zone,
)
from sts_monitor.models import (
    AlertEventORM,
    ConvergenceZoneORM,
    GeoEventORM,
    InvestigationORM,
    ObservationORM,
)
from sts_monitor.security import AuthContext, require_api_key

router = APIRouter()

# ── SSE Stream ──────────────────────────────────────────────────────────


@router.get("/events/stream")
async def sse_stream(request: Request, _: None = Depends(require_api_key)):
    """Server-Sent Events stream for real-time updates."""
    queue = event_bus.subscribe()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield event.to_sse()
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            event_bus.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Geo Events / Layers / Convergence ──────────────────────────────────


@router.get("/geo/events")
def list_geo_events(
    layer: str | None = None,
    hours: int = 24,
    limit: int = 500,
    investigation_id: str | None = None,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Get recent geo events, optionally filtered by layer."""
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, min(hours, 720)))
    q = select(GeoEventORM).where(GeoEventORM.event_time >= cutoff)
    if layer:
        q = q.where(GeoEventORM.layer == layer)
    if investigation_id:
        q = q.where(GeoEventORM.investigation_id == investigation_id)
    q = q.order_by(GeoEventORM.event_time.desc()).limit(max(1, min(limit, 5000)))

    rows = session.scalars(q).all()
    return {
        "count": len(rows),
        "cutoff": cutoff.isoformat(),
        "events": [
            {
                "id": r.id,
                "layer": r.layer,
                "source_id": r.source_id,
                "title": r.title,
                "latitude": r.latitude,
                "longitude": r.longitude,
                "altitude": r.altitude,
                "magnitude": r.magnitude,
                "properties": json.loads(r.properties_json),
                "event_time": r.event_time.isoformat(),
                "investigation_id": r.investigation_id,
            }
            for r in rows
        ],
    }


@router.get("/geo/layers")
def list_geo_layers(
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    """List available geo layers with event counts."""
    cutoff_24h = datetime.now(UTC) - timedelta(hours=24)
    rows = session.execute(
        select(GeoEventORM.layer, func.count(GeoEventORM.id))
        .where(GeoEventORM.event_time >= cutoff_24h)
        .group_by(GeoEventORM.layer)
    ).all()
    return [{"layer": layer, "event_count_24h": count} for layer, count in rows]


@router.get("/geo/convergence")
def detect_convergence_zones(
    hours: int = 24,
    radius_km: float = 50.0,
    min_signal_types: int = 3,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Detect convergence zones where multiple signal types cluster geographically."""
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, min(hours, 720)))
    rows = session.scalars(
        select(GeoEventORM).where(GeoEventORM.event_time >= cutoff)
    ).all()

    points = [
        GeoPoint(
            latitude=r.latitude,
            longitude=r.longitude,
            layer=r.layer,
            title=r.title,
            event_time=r.event_time,
            source_id=r.source_id or "",
        )
        for r in rows
    ]

    zones = detect_convergence(
        points,
        radius_km=radius_km,
        min_signal_types=min_signal_types,
        time_window_hours=hours,
    )

    # Persist detected zones
    for zone in zones:
        cz = ConvergenceZoneORM(
            center_lat=zone.center_lat,
            center_lon=zone.center_lon,
            radius_km=zone.radius_km,
            signal_count=zone.signal_count,
            signal_types_json=json.dumps(zone.signal_types),
            severity=zone.severity,
            first_detected_at=zone.first_detected_at,
            last_updated_at=zone.last_updated_at,
        )
        session.add(cz)

    if zones:
        session.commit()
        event_bus.publish_sync(STSEvent(
            event_type="convergence",
            payload={"zones_detected": len(zones), "radius_km": radius_km},
        ))

        # Auto-trigger alerts for high-severity convergence zones
        for zone in zones:
            if zone.severity in ("high", "critical"):
                alert_event = AlertEventORM(
                    rule_id=None,
                    investigation_id=None,
                    triggered_at=datetime.now(UTC),
                    severity=zone.severity,
                    message=(
                        f"Convergence zone detected: {zone.signal_count} signals from "
                        f"{len(zone.signal_types)} types at ({zone.center_lat:.3f}, {zone.center_lon:.3f})"
                    ),
                    detail_json=json.dumps({
                        "zone_center": [zone.center_lat, zone.center_lon],
                        "signal_count": zone.signal_count,
                        "signal_types": zone.signal_types,
                        "radius_km": zone.radius_km,
                    }),
                )
                session.add(alert_event)
        session.commit()

    return {
        "geo_events_analyzed": len(points),
        "zones": [
            {
                "center_lat": z.center_lat,
                "center_lon": z.center_lon,
                "radius_km": z.radius_km,
                "signal_count": z.signal_count,
                "signal_types": z.signal_types,
                "severity": z.severity,
                "first_detected_at": z.first_detected_at.isoformat(),
                "last_updated_at": z.last_updated_at.isoformat(),
                "event_count": len(z.events),
            }
            for z in zones
        ],
    }


# ── Dashboard ──────────────────────────────────────────────────────────


@router.get("/dashboard/map-data")
def dashboard_map_data(
    hours: int = 24,
    layers: str | None = None,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """GeoJSON-like endpoint for the map dashboard."""
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, min(hours, 720)))
    q = select(GeoEventORM).where(GeoEventORM.event_time >= cutoff)
    if layers:
        layer_list = [item.strip() for item in layers.split(",") if item.strip()]
        if layer_list:
            q = q.where(GeoEventORM.layer.in_(layer_list))
    q = q.order_by(GeoEventORM.event_time.desc()).limit(2000)
    rows = session.scalars(q).all()

    features = []
    for r in rows:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r.longitude, r.latitude]},
            "properties": {
                "id": r.id,
                "layer": r.layer,
                "title": r.title,
                "magnitude": r.magnitude,
                "event_time": r.event_time.isoformat(),
                "source_id": r.source_id,
                **json.loads(r.properties_json),
            },
        })

    return {"type": "FeatureCollection", "features": features}


@router.get("/dashboard/timeline")
def dashboard_timeline(
    hours: int = 48,
    bucket_hours: int = 1,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Event counts bucketed by time for timeline visualization."""
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, min(hours, 720)))
    rows = session.scalars(
        select(GeoEventORM).where(GeoEventORM.event_time >= cutoff)
    ).all()

    buckets: dict[str, dict[str, int]] = {}
    for r in rows:
        bucket_key = r.event_time.replace(
            minute=0, second=0, microsecond=0,
            hour=(r.event_time.hour // bucket_hours) * bucket_hours,
        ).isoformat()
        if bucket_key not in buckets:
            buckets[bucket_key] = {}
        buckets[bucket_key][r.layer] = buckets[bucket_key].get(r.layer, 0) + 1

    return {
        "bucket_hours": bucket_hours,
        "cutoff": cutoff.isoformat(),
        "buckets": [
            {"time": k, "layers": v, "total": sum(v.values())}
            for k, v in sorted(buckets.items())
        ],
    }


@router.get("/dashboard/playback")
def dashboard_playback(
    hours: int = 48,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Temporal playback data: GeoJSON features with timestamps for time-slider animation.

    Returns events sorted chronologically with their timestamps,
    allowing the frontend to animate events appearing on the map over time.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, min(hours, 720)))
    rows = session.scalars(
        select(GeoEventORM).where(GeoEventORM.event_time >= cutoff)
        .order_by(GeoEventORM.event_time.asc())
    ).all()

    features = []
    time_bounds = {"min": None, "max": None}
    for r in rows:
        ts = r.event_time.isoformat()
        if time_bounds["min"] is None or ts < time_bounds["min"]:
            time_bounds["min"] = ts
        if time_bounds["max"] is None or ts > time_bounds["max"]:
            time_bounds["max"] = ts

        props = json.loads(r.properties_json) if r.properties_json else {}
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [r.longitude, r.latitude],
            },
            "properties": {
                "id": r.id,
                "layer": r.layer,
                "title": r.title,
                "magnitude": r.magnitude,
                "event_time": ts,
                "timestamp_ms": int(r.event_time.timestamp() * 1000),
                **{k: v for k, v in props.items() if isinstance(v, (str, int, float, bool))},
            },
        })

    # Compute hourly summary for the playback scrubber
    hourly: dict[str, int] = {}
    for r in rows:
        hour_key = r.event_time.replace(minute=0, second=0, microsecond=0).isoformat()
        hourly[hour_key] = hourly.get(hour_key, 0) + 1

    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "total_events": len(features),
            "hours": hours,
            "time_bounds": time_bounds,
            "hourly_counts": [{"time": k, "count": v} for k, v in sorted(hourly.items())],
        },
    }


@router.get("/dashboard/live")
def dashboard_live(
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Live dashboard data combining summary stats, recent events, and active zones."""
    now = datetime.now(UTC)
    cutoff_24h = now - timedelta(hours=24)

    # Counts
    investigation_count = session.scalar(select(func.count(InvestigationORM.id))) or 0
    observation_count = session.scalar(select(func.count(ObservationORM.id))) or 0
    geo_event_count = session.scalar(
        select(func.count(GeoEventORM.id)).where(GeoEventORM.event_time >= cutoff_24h)
    ) or 0

    # Recent geo events (return up to 500 for globe rendering)
    recent_geo = session.scalars(
        select(GeoEventORM).where(GeoEventORM.event_time >= cutoff_24h)
        .order_by(GeoEventORM.event_time.desc()).limit(500)
    ).all()

    # Active convergence zones
    active_zones = session.scalars(
        select(ConvergenceZoneORM).where(ConvergenceZoneORM.resolved_at.is_(None))
        .order_by(ConvergenceZoneORM.last_updated_at.desc()).limit(10)
    ).all()

    # Layer breakdown
    layer_counts = session.execute(
        select(GeoEventORM.layer, func.count(GeoEventORM.id))
        .where(GeoEventORM.event_time >= cutoff_24h)
        .group_by(GeoEventORM.layer)
    ).all()

    # Recent alerts
    recent_alerts = session.scalars(
        select(AlertEventORM).order_by(AlertEventORM.triggered_at.desc()).limit(10)
    ).all()

    return {
        "timestamp": now.isoformat(),
        "investigations": investigation_count,
        "observations_total": observation_count,
        "geo_events_24h": geo_event_count,
        "sse_subscribers": event_bus.subscriber_count,
        "layers": {layer: count for layer, count in layer_counts},
        "recent_geo_events": [
            {
                "id": r.id,
                "layer": r.layer,
                "title": r.title,
                "latitude": r.latitude,
                "longitude": r.longitude,
                "altitude": r.altitude,
                "magnitude": r.magnitude,
                "source_id": r.source_id,
                "event_time": r.event_time.isoformat(),
                "properties": json.loads(r.properties_json) if r.properties_json else {},
            }
            for r in recent_geo
        ],
        "convergence_zones": [
            {
                "id": z.id, "center_lat": z.center_lat, "center_lon": z.center_lon,
                "severity": z.severity, "signal_count": z.signal_count,
                "signal_types": json.loads(z.signal_types_json),
                "last_updated_at": z.last_updated_at.isoformat(),
            }
            for z in active_zones
        ],
        "recent_alerts": [
            {
                "id": a.id, "severity": a.severity, "message": a.message,
                "triggered_at": a.triggered_at.isoformat(),
            }
            for a in recent_alerts
        ],
    }


# ── Cameras ────────────────────────────────────────────────────────────


@router.get("/cameras/regions")
def api_list_camera_regions(
    _auth: AuthContext = Depends(require_api_key),
) -> list[dict[str, Any]]:
    """List available curated camera regions with counts."""
    return list_camera_regions()


@router.get("/cameras/nearby")
def api_get_cameras_nearby(
    lat: float,
    lon: float,
    radius_km: float = 100,
    _auth: AuthContext = Depends(require_api_key),
) -> list[dict[str, Any]]:
    """Find curated cameras near a coordinate."""
    return get_cameras_near(lat, lon, radius_km)


@router.get("/cameras/all")
def api_get_all_cameras(
    region: str | None = None,
    _auth: AuthContext = Depends(require_api_key),
) -> dict[str, Any]:
    """Get all curated cameras, optionally filtered by region."""
    if region:
        cameras = CURATED_CAMERAS.get(region, [])
        return {"region": region, "cameras": cameras, "count": len(cameras)}
    total = sum(len(c) for c in CURATED_CAMERAS.values())
    return {"regions": list(CURATED_CAMERAS.keys()), "cameras": CURATED_CAMERAS, "total": total}


# ── Geofences ──────────────────────────────────────────────────────────


@router.get("/geofences")
def list_geofences(
    _: AuthContext = Depends(require_api_key),
) -> dict[str, Any]:
    """List all configured geofence zones."""
    zones = get_all_zones()
    return {
        "total": len(zones),
        "zones": [
            {"name": z.name, "lat": z.center_lat, "lon": z.center_lon, "radius_km": z.radius_km, "category": z.category}
            for z in zones
        ],
    }


@router.post("/geofences")
def create_geofence(
    name: str,
    lat: float,
    lon: float,
    radius_km: float = 50.0,
    category: str = "custom",
    _: AuthContext = Depends(require_api_key),
) -> dict[str, Any]:
    """Add a custom geofence zone."""
    zone = GeoZone(name=name, center_lat=lat, center_lon=lon, radius_km=radius_km, category=category)
    add_zone(zone)
    return {"status": "created", "zone": {"name": zone.name, "lat": zone.center_lat, "lon": zone.center_lon, "radius_km": zone.radius_km, "category": zone.category}}


@router.delete("/geofences/{zone_name}")
def delete_geofence(
    zone_name: str,
    _: AuthContext = Depends(require_api_key),
) -> dict[str, str]:
    """Remove a custom geofence zone."""
    removed = remove_zone(zone_name)
    if not removed:
        raise HTTPException(status_code=404, detail="Zone not found or is a builtin zone")
    return {"status": "deleted", "zone": zone_name}


@router.post("/investigations/{investigation_id}/geofence-check")
def check_geofences(
    investigation_id: str,
    _: AuthContext = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Check investigation observations against all geofence zones."""
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    observations = session.query(ObservationORM).filter_by(
        investigation_id=investigation_id
    ).limit(1000).all()

    obs_dicts = [
        {"claim": o.claim, "source": o.source, "latitude": o.latitude, "longitude": o.longitude, "captured_at": o.captured_at}
        for o in observations
    ]

    zones = get_all_zones()
    geo_alerts = check_observations_against_zones(obs_dicts, zones, investigation_id)
    summary = get_zone_activity_summary(obs_dicts)

    return {
        "investigation_id": investigation_id,
        "alerts": [a.to_dict() for a in geo_alerts],
        "zone_activity": summary,
    }
