"""Tests for geo, camera, and dashboard API endpoints."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sts_monitor.connectors.webcams import CURATED_CAMERAS
from sts_monitor.database import Base, engine
from sts_monitor.main import app

client = TestClient(app)
AUTH = {"X-API-Key": "change-me"}

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


# ── Camera endpoints ───────────────────────────────────────────────────


def test_cameras_regions() -> None:
    response = client.get("/cameras/regions", headers=AUTH)
    assert response.status_code == 200
    regions = response.json()
    assert isinstance(regions, list)
    assert len(regions) == len(CURATED_CAMERAS)
    region_names = {r["region"] for r in regions}
    assert "ukraine" in region_names
    assert "middle_east" in region_names
    for r in regions:
        assert "camera_count" in r
        assert r["camera_count"] > 0


def test_cameras_nearby() -> None:
    # Kyiv coordinates
    response = client.get("/cameras/nearby?lat=50.45&lon=30.52&radius_km=200", headers=AUTH)
    assert response.status_code == 200
    cameras = response.json()
    assert isinstance(cameras, list)
    assert len(cameras) >= 1
    assert any("Kyiv" in c["name"] for c in cameras)


def test_cameras_nearby_remote_location_empty() -> None:
    # Middle of Pacific Ocean — should find nothing within 10km
    response = client.get("/cameras/nearby?lat=0&lon=-160&radius_km=10", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == []


def test_cameras_all_unfiltered() -> None:
    response = client.get("/cameras/all", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert "regions" in body
    assert "cameras" in body
    assert "total" in body
    total_expected = sum(len(c) for c in CURATED_CAMERAS.values())
    assert body["total"] == total_expected


def test_cameras_all_by_region() -> None:
    response = client.get("/cameras/all?region=ukraine", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["region"] == "ukraine"
    assert body["count"] == len(CURATED_CAMERAS["ukraine"])


def test_cameras_all_unknown_region() -> None:
    response = client.get("/cameras/all?region=nonexistent", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 0


# ── Geo layers endpoint ───────────────────────────────────────────────


def test_geo_layers_returns_list() -> None:
    response = client.get("/geo/layers", headers=AUTH)
    assert response.status_code == 200
    layers = response.json()
    assert isinstance(layers, list)
    # Empty DB should return empty list
    # but the structure is correct


# ── Dashboard live endpoint ────────────────────────────────────────────


def test_dashboard_live_returns_expected_structure() -> None:
    response = client.get("/dashboard/live", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert "timestamp" in body
    assert "investigations" in body
    assert "observations_total" in body
    assert "geo_events_24h" in body
    assert "sse_subscribers" in body
    assert "layers" in body
    assert "recent_geo_events" in body
    assert "convergence_zones" in body
    assert "recent_alerts" in body
    assert isinstance(body["recent_geo_events"], list)
    assert isinstance(body["convergence_zones"], list)


# ── Dashboard summary endpoint ─────────────────────────────────────────


def test_dashboard_summary_includes_new_fields() -> None:
    response = client.get("/dashboard/summary", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    # Core fields
    assert "investigations" in body
    assert "observations" in body
    assert "reports" in body
    # New fields from geo/entity/story expansion
    assert "geo_events" in body
    assert "convergence_zones" in body
    assert "entities" in body
    assert "stories" in body
    assert "discovered_topics" in body
    assert "collection_plans" in body
    # All counts should be integers >= 0
    for key in ("geo_events", "convergence_zones", "entities", "stories", "discovered_topics", "collection_plans"):
        assert isinstance(body[key], int)
        assert body[key] >= 0


def test_dashboard_summary_on_empty_db() -> None:
    response = client.get("/dashboard/summary", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["investigations"] == 0
    assert body["observations"] == 0
    assert body["geo_events"] == 0


# ── Auth required ──────────────────────────────────────────────────────


def test_cameras_regions_requires_auth() -> None:
    response = client.get("/cameras/regions")
    assert response.status_code == 401


def test_cameras_nearby_requires_auth() -> None:
    response = client.get("/cameras/nearby?lat=50&lon=30")
    assert response.status_code == 401


def test_geo_layers_requires_auth() -> None:
    response = client.get("/geo/layers")
    assert response.status_code == 401


def test_dashboard_live_requires_auth() -> None:
    response = client.get("/dashboard/live")
    assert response.status_code == 401
