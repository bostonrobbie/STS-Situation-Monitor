"""Comprehensive coverage tests for routes and modules."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import sqlalchemy
from fastapi.testclient import TestClient

from sts_monitor.database import Base, engine
from sts_monitor.main import app
from sts_monitor.models import (
    ClaimEvidenceORM,
    ClaimORM,
    CollectionPlanORM,
    ConvergenceZoneORM,
    DiscoveredTopicORM,
    EntityMentionORM,
    GeoEventORM,
    InvestigationORM,
    ObservationORM,
    ReportORM,
    ResearchSourceORM,
    StoryORM,
    AlertEventORM,
)

AUTH = {"X-API-Key": "change-me"}


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch):
    import sts_monitor.rate_limit as rl
    monkeypatch.setattr(rl, "RATE_LIMIT_ENABLED", False)
    from sts_monitor.geofence import _custom_zones
    _custom_zones.clear()
    # Reset semantic index singleton
    import sts_monitor.semantic_index as si
    si._engine = None
    # Ensure all models are imported so Base.metadata knows about all tables
    import sts_monitor.models  # noqa: F401
    # Close all pooled connections to avoid SQLite locking issues
    engine.dispose()
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text("PRAGMA foreign_keys = OFF"))
        conn.commit()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text("PRAGMA foreign_keys = ON"))
        conn.commit()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db_session():
    """Provide a properly managed database session that gets closed after use."""
    from sts_monitor.database import SessionLocal
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def inv_id(client):
    resp = client.post(
        "/investigations",
        json={"topic": "Coverage Test Investigation", "priority": 75, "seed_query": "coverage test"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    return resp.json()["id"]


def _add_observations(client, inv_id, count=5, claim_prefix="earthquake detected in region"):
    resp = client.post(
        f"/investigations/{inv_id}/ingest/simulated",
        json={"batch_size": count, "include_noise": True},
        headers=AUTH,
    )
    assert resp.status_code == 200
    return resp.json()


def _get_db():
    """Get a database session that must be closed by the caller."""
    from sts_monitor.database import SessionLocal
    return SessionLocal()


def _seed_topic(session, **overrides):
    defaults = dict(
        title="Test Topic",
        description="A discovered topic for testing" * 5,
        score=0.8,
        source="burst",
        key_terms_json=json.dumps(["fire", "earthquake"]),
        entities_json=json.dumps(["FEMA"]),
        sample_urls_json=json.dumps(["https://example.com"]),
        suggested_seed_query="fire earthquake",
        suggested_connectors_json=json.dumps(["gdelt", "rss"]),
        status="new",
        discovered_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    t = DiscoveredTopicORM(**defaults)
    session.add(t)
    session.commit()
    return t


def _seed_geo_event(session, inv_id=None, layer="earthquake", lat=35.0, lon=-118.0, hours_ago=1):
    ev = GeoEventORM(
        layer=layer,
        source_id=f"src-{uuid4().hex[:8]}",
        title=f"Test {layer} event",
        latitude=lat,
        longitude=lon,
        magnitude=5.0,
        properties_json=json.dumps({"depth": 10}),
        event_time=datetime.now(UTC) - timedelta(hours=hours_ago),
        investigation_id=inv_id,
    )
    session.add(ev)
    session.commit()
    return ev


# =========================================================================
# DISCOVERY ROUTES
# =========================================================================


class TestDiscoveryTopics:
    def test_list_discovered_topics_empty(self, client):
        resp = client.get("/discovery/topics", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_discovered_topics_with_data(self, client):
        session = _get_db()
        _seed_topic(session)
        _seed_topic(session, title="Second Topic", score=0.5, status="promoted")

        resp = client.get("/discovery/topics", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["score"] >= data[1]["score"]
        assert "id" in data[0]
        assert "key_terms" in data[0]

    def test_list_discovered_topics_filter_by_status(self, client):
        session = _get_db()
        _seed_topic(session, status="new")
        _seed_topic(session, title="Promoted", status="promoted")

        resp = client.get("/discovery/topics?status=new", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert all(t["status"] == "new" for t in data)

    def test_list_discovered_topics_limit(self, client):
        session = _get_db()
        for i in range(5):
            _seed_topic(session, title=f"Topic {i}", score=0.1 * i)
        resp = client.get("/discovery/topics?limit=2", headers=AUTH)
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_promote_topic_not_found(self, client):
        resp = client.post(
            "/discovery/topics/99999/promote",
            json={"priority": 50},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_promote_topic_success(self, client):
        session = _get_db()
        topic = _seed_topic(session)

        resp = client.post(
            f"/discovery/topics/{topic.id}/promote",
            json={"priority": 70, "owner": "analyst1"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "investigation_id" in data
        assert "collection_plan_id" in data
        assert data["topic_id"] == topic.id

    def test_dismiss_topic_not_found(self, client):
        resp = client.post("/discovery/topics/99999/dismiss", headers=AUTH)
        assert resp.status_code == 404

    def test_dismiss_topic_success(self, client):
        session = _get_db()
        topic = _seed_topic(session)
        resp = client.post(f"/discovery/topics/{topic.id}/dismiss", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["status"] == "dismissed"


class TestCollectionPlans:
    def test_create_plan_investigation_not_found(self, client):
        resp = client.post(
            "/collection-plans",
            json={"investigation_id": "nonexistent", "name": "Plan A",
                  "connectors": ["gdelt"], "query": "test"},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_create_plan_manual(self, client, inv_id):
        resp = client.post(
            "/collection-plans",
            json={"investigation_id": inv_id, "name": "Manual Plan",
                  "connectors": ["gdelt", "rss"], "query": "test query",
                  "priority": 60, "interval_seconds": 1800, "filters": {"lang": "en"}},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Manual Plan"
        assert data["connectors"] == ["gdelt", "rss"]

    def test_create_plan_auto_generate(self, client, inv_id):
        resp = client.post(
            "/collection-plans",
            json={"investigation_id": inv_id, "name": "AutoPlan",
                  "connectors": ["gdelt"], "query": "earthquake",
                  "auto_generate": True, "priority": 50},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "plans_created" in data
        assert data["investigation_id"] == inv_id

    def test_list_collection_plans_empty(self, client):
        resp = client.get("/collection-plans", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_collection_plans_with_filter(self, client, inv_id):
        client.post(
            "/collection-plans",
            json={"investigation_id": inv_id, "name": "Plan X",
                  "connectors": ["gdelt"], "query": "test"},
            headers=AUTH,
        )
        resp = client.get(f"/collection-plans?investigation_id={inv_id}", headers=AUTH)
        assert resp.status_code == 200
        plans = resp.json()
        assert len(plans) >= 1
        assert plans[0]["investigation_id"] == inv_id

    def test_list_collection_plans_active_only_false(self, client, inv_id):
        resp = client.get(f"/collection-plans?active_only=false", headers=AUTH)
        assert resp.status_code == 200

    def test_execute_plan_not_found(self, client):
        resp = client.post("/collection-plans/99999/execute", headers=AUTH)
        assert resp.status_code == 404

    def test_execute_plan_inactive(self, client, inv_id):
        session = _get_db()
        plan = CollectionPlanORM(
            investigation_id=inv_id, name="Inactive Plan",
            connectors_json=json.dumps(["gdelt"]), query="test",
            active=False,
        )
        session.add(plan)
        session.commit()
        resp = client.post(f"/collection-plans/{plan.id}/execute", headers=AUTH)
        assert resp.status_code == 400

    def test_execute_plan_with_unknown_connector(self, client, inv_id):
        session = _get_db()
        plan = CollectionPlanORM(
            investigation_id=inv_id, name="Unknown Conn Plan",
            connectors_json=json.dumps(["unknown_connector"]), query="test",
            active=True,
        )
        session.add(plan)
        session.commit()
        resp = client.post(f"/collection-plans/{plan.id}/execute", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "unknown_connector" in data["results"]
        assert "error" in data["results"]["unknown_connector"]

    def test_execute_plan_with_connector_exception(self, client, inv_id):
        session = _get_db()
        plan = CollectionPlanORM(
            investigation_id=inv_id, name="Failing Plan",
            connectors_json=json.dumps(["gdelt"]), query="test",
            active=True,
        )
        session.add(plan)
        session.commit()
        with patch("sts_monitor.routes.discovery.GDELTConnector") as mock_cls:
            mock_cls.side_effect = Exception("Connection failed")
            resp = client.post(f"/collection-plans/{plan.id}/execute", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data["results"]["gdelt"]

    def test_execute_plan_success(self, client, inv_id):
        session = _get_db()
        plan = CollectionPlanORM(
            investigation_id=inv_id, name="OK Plan",
            connectors_json=json.dumps(["gdelt"]), query="test",
            active=True,
        )
        session.add(plan)
        session.commit()
        with patch("sts_monitor.routes.discovery.ingest_with_geo_connector") as mock_ingest:
            mock_ingest.return_value = {"ingested_count": 3}
            resp = client.post(f"/collection-plans/{plan.id}/execute", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_ingested"] == 3


class TestFeedsEndpoints:
    def test_list_feed_categories(self, client):
        resp = client.get("/feeds/categories", headers=AUTH)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_feeds_by_category_all(self, client):
        resp = client.get("/feeds/by-category", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "count" in data
        assert "feeds" in data

    def test_get_feeds_by_category_filtered(self, client):
        resp = client.get("/feeds/by-category?categories=conflict,natural_disasters", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "count" in data


class TestDiscoverySummary:
    def test_discovery_summary_not_found(self, client):
        resp = client.post("/investigations/nonexistent/discovery", headers=AUTH)
        assert resp.status_code == 404

    def test_discovery_summary_no_observations(self, client, inv_id):
        resp = client.post(f"/investigations/{inv_id}/discovery", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["observation_count"] == 0
        assert data["llm_brief"] is None

    def test_discovery_summary_with_observations(self, client, inv_id):
        _add_observations(client, inv_id, count=10)
        resp = client.post(f"/investigations/{inv_id}/discovery", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["observation_count"] > 0

    def test_discovery_summary_with_llm(self, client, inv_id):
        _add_observations(client, inv_id, count=5)
        with patch("sts_monitor.routes.discovery.llm_client") as mock_llm:
            mock_llm.summarize.return_value = "LLM summary text"
            resp = client.post(
                f"/investigations/{inv_id}/discovery",
                json={"use_llm": True},
                headers=AUTH,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["llm_brief"] == "LLM summary text"

    def test_discovery_summary_llm_failure(self, client, inv_id):
        _add_observations(client, inv_id, count=5)
        with patch("sts_monitor.routes.discovery.llm_client") as mock_llm:
            mock_llm.summarize.side_effect = RuntimeError("LLM down")
            resp = client.post(
                f"/investigations/{inv_id}/discovery",
                json={"use_llm": True},
                headers=AUTH,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "LLM unavailable" in data["llm_brief"]


class TestRunStoryDiscovery:
    def test_run_story_discovery_empty(self, client):
        resp = client.post("/discovery/run", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["observations_analyzed"] == 0

    def test_run_story_discovery_with_data(self, client, inv_id):
        _add_observations(client, inv_id, count=20)
        from sts_monitor.story_discovery import DiscoveredTopic
        fake_topic = DiscoveredTopic(
            title="Test Discovered",
            description="A test discovery topic",
            score=0.9,
            source="burst",
            key_terms=["fire", "earthquake"],
            entities=["FEMA"],
            sample_urls=["https://example.com"],
            suggested_seed_query="fire earthquake",
            suggested_connectors=["gdelt", "rss"],
        )
        with patch("sts_monitor.routes.discovery.run_discovery", return_value=[fake_topic]):
            resp = client.post("/discovery/run?hours=48", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["topics_discovered"] == 1
        assert "observations_analyzed" in data

    def test_run_story_discovery_with_convergence_zones(self, client, inv_id):
        _add_observations(client, inv_id, count=10)
        session = _get_db()
        session.add(ConvergenceZoneORM(
            center_lat=35.0, center_lon=-118.0, radius_km=50,
            signal_count=5, signal_types_json=json.dumps(["earthquake", "fire"]),
            severity="high",
            first_detected_at=datetime.now(UTC), last_updated_at=datetime.now(UTC),
        ))
        session.commit()
        with patch("sts_monitor.routes.discovery.run_discovery", return_value=[]):
            resp = client.post("/discovery/run?hours=48", headers=AUTH)
        assert resp.status_code == 200


# =========================================================================
# GEO ROUTES
# =========================================================================


class TestGeoEvents:
    def test_list_geo_events_empty(self, client):
        resp = client.get("/geo/events", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0

    def test_list_geo_events_with_data(self, client, inv_id):
        session = _get_db()
        _seed_geo_event(session, inv_id=inv_id, layer="earthquake")
        _seed_geo_event(session, inv_id=inv_id, layer="fire", lat=36.0, lon=-119.0)
        resp = client.get("/geo/events", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    def test_list_geo_events_filter_by_layer(self, client, inv_id):
        session = _get_db()
        _seed_geo_event(session, inv_id=inv_id, layer="earthquake")
        _seed_geo_event(session, inv_id=inv_id, layer="fire")
        resp = client.get("/geo/events?layer=earthquake", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert all(e["layer"] == "earthquake" for e in data["events"])

    def test_list_geo_events_filter_by_investigation(self, client, inv_id):
        session = _get_db()
        _seed_geo_event(session, inv_id=inv_id)
        resp = client.get(f"/geo/events?investigation_id={inv_id}", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1


class TestGeoConvergence:
    def test_detect_convergence_no_events(self, client):
        resp = client.get("/geo/convergence", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["geo_events_analyzed"] == 0
        assert data["zones"] == []

    def test_detect_convergence_with_events(self, client, inv_id):
        session = _get_db()
        # Create multiple events at the same location with different layers
        for layer in ["earthquake", "fire", "flood", "conflict"]:
            _seed_geo_event(session, inv_id=inv_id, layer=layer, lat=35.0, lon=-118.0)
        resp = client.get("/geo/convergence?min_signal_types=2&radius_km=100", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["geo_events_analyzed"] == 4


class TestDashboard:
    def test_map_data_empty(self, client):
        resp = client.get("/dashboard/map-data", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert data["features"] == []

    def test_map_data_with_events(self, client, inv_id):
        session = _get_db()
        _seed_geo_event(session, inv_id=inv_id)
        resp = client.get("/dashboard/map-data", headers=AUTH)
        assert resp.status_code == 200
        assert len(resp.json()["features"]) == 1

    def test_map_data_layer_filter(self, client, inv_id):
        session = _get_db()
        _seed_geo_event(session, inv_id=inv_id, layer="earthquake")
        _seed_geo_event(session, inv_id=inv_id, layer="fire")
        resp = client.get("/dashboard/map-data?layers=earthquake", headers=AUTH)
        assert resp.status_code == 200
        features = resp.json()["features"]
        assert all(f["properties"]["layer"] == "earthquake" for f in features)

    def test_timeline_empty(self, client):
        resp = client.get("/dashboard/timeline", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["buckets"] == []

    def test_timeline_with_events(self, client, inv_id):
        session = _get_db()
        _seed_geo_event(session, inv_id=inv_id)
        resp = client.get("/dashboard/timeline?hours=48&bucket_hours=1", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["buckets"]) >= 1

    def test_playback_empty(self, client):
        resp = client.get("/dashboard/playback", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert data["features"] == []
        assert data["metadata"]["total_events"] == 0

    def test_playback_with_events(self, client, inv_id):
        session = _get_db()
        _seed_geo_event(session, inv_id=inv_id, hours_ago=2)
        _seed_geo_event(session, inv_id=inv_id, hours_ago=1, layer="fire", lat=36.0, lon=-119.0)
        resp = client.get("/dashboard/playback?hours=48", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["metadata"]["total_events"] == 2
        assert data["metadata"]["time_bounds"]["min"] is not None

    def test_live_dashboard(self, client, inv_id):
        session = _get_db()
        _seed_geo_event(session, inv_id=inv_id)
        session.add(ConvergenceZoneORM(
            center_lat=35.0, center_lon=-118.0, radius_km=50,
            signal_count=5, signal_types_json=json.dumps(["earthquake"]),
            severity="high",
            first_detected_at=datetime.now(UTC), last_updated_at=datetime.now(UTC),
        ))
        session.add(AlertEventORM(
            triggered_at=datetime.now(UTC), severity="warning",
            message="Test alert", detail_json="{}",
        ))
        session.commit()
        resp = client.get("/dashboard/live", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["investigations"] >= 1
        assert len(data["convergence_zones"]) >= 1
        assert len(data["recent_alerts"]) >= 1


class TestGeofences:
    def test_list_geofences(self, client):
        resp = client.get("/geofences", headers=AUTH)
        assert resp.status_code == 200
        assert "total" in resp.json()

    def test_create_and_delete_geofence(self, client):
        resp = client.post(
            "/geofences?name=TestZone&lat=35.0&lon=-118.0&radius_km=25.0&category=custom",
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.json()["zone"]["name"] == "TestZone"

        resp = client.delete("/geofences/TestZone", headers=AUTH)
        assert resp.status_code == 200

    def test_delete_geofence_not_found(self, client):
        resp = client.delete("/geofences/nonexistent_zone", headers=AUTH)
        assert resp.status_code == 404

    def test_check_geofences_not_found(self, client):
        resp = client.post("/investigations/nonexistent/geofence-check", headers=AUTH)
        assert resp.status_code == 404

    def test_check_geofences_with_data(self, client, inv_id):
        _add_observations(client, inv_id, count=5)
        resp = client.post(f"/investigations/{inv_id}/geofence-check", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["investigation_id"] == inv_id
        assert "alerts" in data
        assert "zone_activity" in data


# =========================================================================
# SEARCH ROUTES
# =========================================================================


class TestSearchProfiles:
    def test_create_search_profile(self, client, inv_id):
        resp = client.post(
            "/search/profiles",
            json={"name": "test-profile", "investigation_id": inv_id,
                  "include_terms": ["earthquake", "fire"],
                  "exclude_terms": ["test"],
                  "synonyms": {"quake": ["earthquake", "tremor"]}},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "test-profile"

    def test_create_search_profile_duplicate(self, client, inv_id):
        client.post(
            "/search/profiles",
            json={"name": "dup-profile", "include_terms": []},
            headers=AUTH,
        )
        resp = client.post(
            "/search/profiles",
            json={"name": "dup-profile", "include_terms": []},
            headers=AUTH,
        )
        assert resp.status_code == 409

    def test_create_search_profile_bad_investigation(self, client):
        resp = client.post(
            "/search/profiles",
            json={"name": "bad-inv", "investigation_id": "nonexistent"},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_list_search_profiles(self, client, inv_id):
        client.post(
            "/search/profiles",
            json={"name": "prof1", "investigation_id": inv_id},
            headers=AUTH,
        )
        resp = client.get("/search/profiles", headers=AUTH)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_list_search_profiles_by_investigation(self, client, inv_id):
        client.post(
            "/search/profiles",
            json={"name": "inv-prof", "investigation_id": inv_id},
            headers=AUTH,
        )
        resp = client.get(f"/search/profiles?investigation_id={inv_id}", headers=AUTH)
        assert resp.status_code == 200


class TestSearchQuery:
    def test_search_query_basic(self, client, inv_id):
        _add_observations(client, inv_id, count=10)
        resp = client.post(
            "/search/query",
            json={"query": "earthquake fire", "include_observations": True,
                  "include_claims": False, "min_score": 0.0, "limit": 50},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "matched" in data
        assert "facets" in data

    def test_search_query_with_profile(self, client, inv_id):
        _add_observations(client, inv_id, count=10)
        client.post(
            "/search/profiles",
            json={"name": "search-prof", "include_terms": ["earthquake"],
                  "synonyms": {"quake": ["earthquake"]}},
            headers=AUTH,
        )
        resp = client.post(
            "/search/query",
            json={"query": "earthquake", "profile_name": "search-prof",
                  "include_observations": True, "include_claims": False,
                  "min_score": 0.0},
            headers=AUTH,
        )
        assert resp.status_code == 200

    def test_search_query_profile_not_found(self, client):
        resp = client.post(
            "/search/query",
            json={"query": "test", "profile_name": "nonexistent"},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_search_query_with_claims(self, client, inv_id):
        _add_observations(client, inv_id, count=5)
        # Run pipeline to generate claims
        client.post(f"/investigations/{inv_id}/run", headers=AUTH)
        resp = client.post(
            "/search/query",
            json={"query": "earthquake", "investigation_id": inv_id,
                  "include_observations": True, "include_claims": True,
                  "min_score": 0.0},
            headers=AUTH,
        )
        assert resp.status_code == 200

    def test_search_query_with_filters(self, client, inv_id):
        _add_observations(client, inv_id, count=5)
        resp = client.post(
            "/search/query",
            json={"query": "earthquake", "investigation_id": inv_id,
                  "source_prefix": "simulated",
                  "min_reliability": 0.3,
                  "since": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
                  "until": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                  "include_observations": True, "include_claims": True,
                  "stance": "supported",
                  "min_score": 0.0},
            headers=AUTH,
        )
        assert resp.status_code == 200

    def test_search_suggest(self, client, inv_id):
        _add_observations(client, inv_id, count=10)
        resp = client.get(f"/search/suggest?q=earthquake&investigation_id={inv_id}&limit=5", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "suggestions" in data

    def test_search_suggest_no_investigation(self, client, inv_id):
        _add_observations(client, inv_id, count=5)
        resp = client.get("/search/suggest?q=earthquake", headers=AUTH)
        assert resp.status_code == 200

    def test_related_investigations(self, client, inv_id):
        _add_observations(client, inv_id, count=10)
        resp = client.post(
            "/search/related-investigations",
            json={"query": "earthquake fire", "limit": 10, "min_score": 0.0},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "investigations" in data


# =========================================================================
# RESEARCH ROUTES
# =========================================================================


class TestResearchRoutes:
    def test_trending_topics(self, client):
        with patch("sts_monitor.research.TrendingResearchScanner") as mock_cls:
            mock_scanner = MagicMock()
            mock_scanner.fetch_topics.return_value = []
            mock_cls.return_value = mock_scanner
            resp = client.get("/research/trending-topics?geo=US&max_topics=5", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["geo"] == "US"
        assert "topics" in data

    def test_start_research_agent(self, client):
        with patch("sts_monitor.routes.research._get_research_agent") as mock_get:
            mock_agent = MagicMock()
            mock_get.return_value = mock_agent
            resp = client.post(
                "/research/agent/start",
                json={"topic": "test research topic", "max_iterations": 3,
                      "nitter_categories": ["osint"], "rss_categories": ["conflict"]},
                headers=AUTH,
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"

    def test_list_research_sessions(self, client):
        with patch("sts_monitor.routes.research._get_or_create_agent") as mock_get:
            mock_agent = MagicMock()
            mock_agent.list_sessions.return_value = []
            mock_get.return_value = mock_agent
            resp = client.get("/research/agent/sessions", headers=AUTH)
        assert resp.status_code == 200

    def test_get_research_session_not_found(self, client):
        with patch("sts_monitor.routes.research._get_or_create_agent") as mock_get:
            mock_agent = MagicMock()
            mock_agent.get_session.return_value = None
            mock_get.return_value = mock_agent
            resp = client.get("/research/agent/sessions/nonexistent", headers=AUTH)
        assert resp.status_code == 404

    def test_get_research_session_found(self, client):
        with patch("sts_monitor.routes.research._get_or_create_agent") as mock_get:
            mock_agent = MagicMock()
            mock_session = MagicMock()
            mock_session.session_id = "sess-1"
            mock_session.topic = "test"
            mock_session.status = "running"
            mock_session.started_at = datetime.now(UTC)
            mock_session.finished_at = None
            mock_session.all_observations = []
            mock_session.all_findings = []
            mock_session.iterations = []
            mock_session.final_brief = None
            mock_session.error = None
            mock_agent.get_session.return_value = mock_session
            mock_get.return_value = mock_agent
            resp = client.get("/research/agent/sessions/sess-1", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["session_id"] == "sess-1"

    def test_get_research_session_with_iterations(self, client):
        with patch("sts_monitor.routes.research._get_or_create_agent") as mock_get:
            mock_agent = MagicMock()
            mock_session = MagicMock()
            mock_session.session_id = "sess-2"
            mock_session.topic = "test"
            mock_session.status = "complete"
            mock_session.started_at = datetime.now(UTC)
            mock_session.finished_at = datetime.now(UTC)
            mock_session.all_observations = ["obs1"]
            mock_session.all_findings = ["finding1"]
            mock_session.final_brief = "Brief"
            mock_session.error = None
            iteration = MagicMock()
            iteration.iteration = 1
            iteration.started_at = datetime.now(UTC)
            iteration.observations_collected = 5
            iteration.connectors_used = ["gdelt"]
            iteration.duration_s = 10.0
            iteration.llm_response = {"assessment": "good", "confidence": 0.8, "should_continue": False}
            mock_session.iterations = [iteration]
            mock_agent.get_session.return_value = mock_session
            mock_get.return_value = mock_agent
            resp = client.get("/research/agent/sessions/sess-2", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["finished_at"] is not None
        assert len(data["iterations"]) == 1

    def test_stop_research_session_not_found(self, client):
        with patch("sts_monitor.routes.research._get_or_create_agent") as mock_get:
            mock_agent = MagicMock()
            mock_agent.stop_session.return_value = False
            mock_get.return_value = mock_agent
            resp = client.post("/research/agent/sessions/bad/stop", headers=AUTH)
        assert resp.status_code == 404

    def test_stop_research_session_success(self, client):
        with patch("sts_monitor.routes.research._get_or_create_agent") as mock_get:
            mock_agent = MagicMock()
            mock_agent.stop_session.return_value = True
            mock_get.return_value = mock_agent
            resp = client.post("/research/agent/sessions/sess-1/stop", headers=AUTH)
        assert resp.status_code == 200

    def test_agent_web_search(self, client):
        with patch("sts_monitor.connectors.search.SearchConnector") as mock_cls:
            mock_conn = MagicMock()
            mock_result = MagicMock()
            mock_result.observations = []
            mock_conn.collect.return_value = mock_result
            mock_cls.return_value = mock_conn
            resp = client.post("/research/agent/search?query=test&max_results=5", headers=AUTH)
        assert resp.status_code == 200

    def test_agent_scrape(self, client):
        with patch("sts_monitor.connectors.web_scraper.WebScraperConnector") as mock_cls:
            mock_conn = MagicMock()
            mock_result = MagicMock()
            mock_result.observations = []
            mock_result.metadata = {}
            mock_conn.collect.return_value = mock_result
            mock_cls.return_value = mock_conn
            resp = client.post(
                "/research/agent/scrape",
                json={"urls": ["https://example.com"], "max_depth": 1},
                headers=AUTH,
            )
        assert resp.status_code == 200

    def test_agent_twitter_search(self, client):
        with patch("sts_monitor.connectors.nitter.NitterConnector") as mock_cls,              patch("sts_monitor.connectors.nitter.get_accounts_for_categories") as mock_cats:
            mock_cats.return_value = ["@account1"]
            mock_conn = MagicMock()
            mock_result = MagicMock()
            mock_result.observations = []
            mock_result.metadata = {}
            mock_conn.collect.return_value = mock_result
            mock_cls.return_value = mock_conn
            resp = client.post(
                "/research/agent/twitter",
                json={"query": "test", "accounts": ["@test"], "categories": ["osint"]},
                headers=AUTH,
            )
        assert resp.status_code == 200

    def test_agent_twitter_no_categories(self, client):
        with patch("sts_monitor.connectors.nitter.NitterConnector") as mock_cls:
            mock_conn = MagicMock()
            mock_result = MagicMock()
            mock_result.observations = []
            mock_result.metadata = {}
            mock_conn.collect.return_value = mock_result
            mock_cls.return_value = mock_conn
            resp = client.post(
                "/research/agent/twitter",
                json={"query": "test", "accounts": ["@test"]},
                headers=AUTH,
            )
        assert resp.status_code == 200

    def test_list_twitter_categories(self, client):
        resp = client.get("/research/agent/twitter/categories", headers=AUTH)
        assert resp.status_code == 200
        assert "categories" in resp.json()

    def test_schedule_research_agent(self, client, inv_id):
        resp = client.post(
            "/research/agent/schedule",
            json={"topic": "scheduled test topic", "seed_query": "test",
                  "investigation_id": inv_id, "interval_seconds": 3600},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "schedule_id" in data

    def test_schedule_research_agent_duplicate(self, client):
        payload = {"topic": "duplicate schedule topic", "interval_seconds": 3600}
        client.post("/research/agent/schedule", json=payload, headers=AUTH)
        resp = client.post("/research/agent/schedule", json=payload, headers=AUTH)
        assert resp.status_code == 409

    def test_auto_investigate_convergence_no_zones(self, client):
        resp = client.post("/research/agent/auto-investigate", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["launched"] == 0

    def test_auto_investigate_convergence_with_zones(self, client):
        session = _get_db()
        for sev in ["high", "critical"]:
            session.add(ConvergenceZoneORM(
                center_lat=35.0, center_lon=-118.0, radius_km=50,
                signal_count=5, signal_types_json=json.dumps(["earthquake", "fire"]),
                severity=sev,
                first_detected_at=datetime.now(UTC), last_updated_at=datetime.now(UTC),
            ))
        session.commit()
        resp = client.post("/research/agent/auto-investigate", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["launched"] == 2
        assert len(data["investigations"]) == 2


class TestResearchSources:
    def test_create_research_source(self, client):
        resp = client.post(
            "/research/sources",
            json={"name": "test-source", "source_type": "news",
                  "base_url": "https://example.com/news", "trust_score": 0.7,
                  "tags": ["conflict"]},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "test-source"

    def test_create_research_source_duplicate(self, client):
        payload = {"name": "dup-source", "source_type": "news",
                   "base_url": "https://example.com", "trust_score": 0.5}
        client.post("/research/sources", json=payload, headers=AUTH)
        resp = client.post("/research/sources", json=payload, headers=AUTH)
        assert resp.status_code == 409

    def test_list_research_sources(self, client):
        resp = client.get("/research/sources", headers=AUTH)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# =========================================================================
# REPORTS ROUTES
# =========================================================================


class TestReportsRoutes:
    def test_get_report_investigation_not_found(self, client):
        resp = client.get("/reports/nonexistent", headers=AUTH)
        assert resp.status_code == 404

    def test_get_report_no_report(self, client, inv_id):
        resp = client.get(f"/reports/{inv_id}", headers=AUTH)
        assert resp.status_code == 404

    def test_get_report_success(self, client, inv_id):
        _add_observations(client, inv_id, count=5)
        client.post(f"/investigations/{inv_id}/run", headers=AUTH)
        resp = client.get(f"/reports/{inv_id}", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "summary" in data
        assert "confidence" in data

    def test_generate_report_not_found(self, client):
        resp = client.post(
            "/reports/generate",
            json={"investigation_id": "nonexistent"},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_generate_report_no_observations(self, client, inv_id):
        resp = client.post(
            "/reports/generate",
            json={"investigation_id": inv_id},
            headers=AUTH,
        )
        assert resp.status_code == 400

    def test_generate_report_success_no_llm(self, client, inv_id):
        _add_observations(client, inv_id, count=10)
        resp = client.post(
            "/reports/generate",
            json={"investigation_id": inv_id, "use_llm": False},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "report_id" in data
        assert "executive_summary" in data

    def test_generate_report_with_entities_and_stories(self, client, inv_id):
        _add_observations(client, inv_id, count=10)
        # Add entities
        session = _get_db()
        obs = session.scalars(
            sqlalchemy.select(ObservationORM).where(
                ObservationORM.investigation_id == inv_id
            ).limit(1)
        ).first()
        if obs:
            session.add(EntityMentionORM(
                observation_id=obs.id, investigation_id=inv_id,
                entity_text="FEMA", entity_type="ORG",
                normalized="fema", confidence=0.9,
            ))
        session.add(StoryORM(
            investigation_id=inv_id, headline="Test Story",
            key_terms_json=json.dumps(["fire"]),
            entities_json=json.dumps(["FEMA"]),
            source_count=2, observation_count=5,
            avg_reliability=0.7, trending_score=0.8,
            first_seen=datetime.now(UTC), last_seen=datetime.now(UTC),
        ))
        session.add(ConvergenceZoneORM(
            center_lat=35.0, center_lon=-118.0, radius_km=50,
            signal_count=5, signal_types_json=json.dumps(["earthquake"]),
            severity="high", investigation_id=inv_id,
            first_detected_at=datetime.now(UTC), last_updated_at=datetime.now(UTC),
        ))
        session.commit()
        resp = client.post(
            "/reports/generate",
            json={"investigation_id": inv_id, "use_llm": False},
            headers=AUTH,
        )
        assert resp.status_code == 200

    def test_generate_markdown_not_found(self, client):
        resp = client.post(
            "/reports/generate/markdown",
            json={"investigation_id": "nonexistent"},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_generate_markdown_no_obs(self, client, inv_id):
        resp = client.post(
            "/reports/generate/markdown",
            json={"investigation_id": inv_id},
            headers=AUTH,
        )
        assert resp.status_code == 400

    def test_generate_markdown_success(self, client, inv_id):
        _add_observations(client, inv_id, count=10)
        resp = client.post(
            "/reports/generate/markdown",
            json={"investigation_id": inv_id, "use_llm": False},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/markdown; charset=utf-8"

    def test_validate_report_lineage_no_report(self, client, inv_id):
        resp = client.get(f"/reports/{inv_id}/validation", headers=AUTH)
        assert resp.status_code == 404

    def test_validate_report_lineage_success(self, client, inv_id):
        _add_observations(client, inv_id, count=5)
        client.post(f"/investigations/{inv_id}/run", headers=AUTH)
        resp = client.get(f"/reports/{inv_id}/validation", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "validation" in data


# =========================================================================
# INVESTIGATIONS ROUTES (uncovered branches)
# =========================================================================


class TestInvestigationsRoutes:
    def test_update_investigation_not_found(self, client):
        resp = client.patch(
            "/investigations/nonexistent",
            json={"priority": 50},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_update_investigation_sla(self, client, inv_id):
        future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        resp = client.patch(
            f"/investigations/{inv_id}",
            json={"sla_due_at": future},
            headers=AUTH,
        )
        assert resp.status_code == 200

    def test_ingestion_runs_not_found(self, client):
        resp = client.get("/investigations/nonexistent/ingestion-runs", headers=AUTH)
        assert resp.status_code == 404

    def test_observations_not_found(self, client):
        resp = client.get("/investigations/nonexistent/observations", headers=AUTH)
        assert resp.status_code == 404

    def test_run_pipeline_not_found(self, client):
        resp = client.post("/investigations/nonexistent/run", headers=AUTH)
        assert resp.status_code == 404

    def test_run_pipeline_with_llm(self, client, inv_id):
        _add_observations(client, inv_id, count=5)
        with patch("sts_monitor.routes.investigations.llm_client") as mock_llm:
            mock_llm.summarize.return_value = "LLM summary"
            resp = client.post(
                f"/investigations/{inv_id}/run",
                json={"use_llm": True},
                headers=AUTH,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == "LLM summary"
        assert data["llm_fallback_used"] is False

    def test_run_pipeline_llm_failure(self, client, inv_id):
        _add_observations(client, inv_id, count=5)
        with patch("sts_monitor.routes.investigations.llm_client") as mock_llm:
            mock_llm.summarize.side_effect = Exception("LLM error")
            resp = client.post(
                f"/investigations/{inv_id}/run",
                json={"use_llm": True},
                headers=AUTH,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["llm_fallback_used"] is True

    def test_feedback_not_found(self, client):
        resp = client.post(
            "/investigations/nonexistent/feedback",
            json={"label": "useful", "notes": "good info"},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_memory_not_found(self, client):
        resp = client.get("/investigations/nonexistent/memory", headers=AUTH)
        assert resp.status_code == 404

    def test_rss_feed_not_found(self, client):
        resp = client.get("/investigations/nonexistent/feed.rss", headers=AUTH)
        assert resp.status_code == 404

    def test_rss_feed_success(self, client, inv_id):
        _add_observations(client, inv_id, count=5)
        client.post(f"/investigations/{inv_id}/run", headers=AUTH)
        resp = client.get(f"/investigations/{inv_id}/feed.rss", headers=AUTH)
        assert resp.status_code == 200
        assert "application/rss+xml" in resp.headers["content-type"]
        assert "<rss" in resp.text

    def test_claims_not_found(self, client):
        resp = client.get("/investigations/nonexistent/claims", headers=AUTH)
        assert resp.status_code == 404

    def test_claims_with_filters(self, client, inv_id):
        _add_observations(client, inv_id, count=5)
        client.post(f"/investigations/{inv_id}/run", headers=AUTH)
        resp = client.get(
            f"/investigations/{inv_id}/claims?stance=supported&limit=10",
            headers=AUTH,
        )
        assert resp.status_code == 200

    def test_claim_evidence_not_found(self, client):
        resp = client.get("/claims/99999/evidence", headers=AUTH)
        assert resp.status_code == 404

    def test_claim_evidence_success(self, client, inv_id):
        _add_observations(client, inv_id, count=5)
        client.post(f"/investigations/{inv_id}/run", headers=AUTH)
        session = _get_db()
        claim = session.scalars(
            sqlalchemy.select(ClaimORM).where(ClaimORM.investigation_id == inv_id).limit(1)
        ).first()
        if claim:
            obs = session.scalars(
                sqlalchemy.select(ObservationORM).where(
                    ObservationORM.investigation_id == inv_id
                ).limit(1)
            ).first()
            if obs:
                session.add(ClaimEvidenceORM(
                    claim_id=claim.id, observation_id=obs.id,
                    weight=0.8, rationale="text match",
                ))
                session.commit()
                resp = client.get(f"/claims/{claim.id}/evidence", headers=AUTH)
                assert resp.status_code == 200
                assert len(resp.json()) >= 1

    def test_timeline_not_found(self, client):
        resp = client.get("/investigations/nonexistent/timeline", headers=AUTH)
        assert resp.status_code == 404

    def test_timeline_success(self, client, inv_id):
        _add_observations(client, inv_id, count=10)
        resp = client.get(f"/investigations/{inv_id}/timeline?window_minutes=30", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "events" in data
        assert "phases" in data

    def test_extract_entities_not_found(self, client):
        resp = client.post("/investigations/nonexistent/extract-entities", headers=AUTH)
        assert resp.status_code == 404

    def test_extract_entities_success(self, client, inv_id):
        _add_observations(client, inv_id, count=5)
        resp = client.post(f"/investigations/{inv_id}/extract-entities?limit=10", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "entities_extracted" in data

    def test_extract_entities_dedup(self, client, inv_id):
        _add_observations(client, inv_id, count=5)
        # Extract once
        client.post(f"/investigations/{inv_id}/extract-entities", headers=AUTH)
        # Extract again -- should skip duplicates
        resp = client.post(f"/investigations/{inv_id}/extract-entities", headers=AUTH)
        assert resp.status_code == 200

    def test_list_entities(self, client, inv_id):
        _add_observations(client, inv_id, count=5)
        client.post(f"/investigations/{inv_id}/extract-entities", headers=AUTH)
        resp = client.get(f"/investigations/{inv_id}/entities", headers=AUTH)
        assert resp.status_code == 200
        assert "entities" in resp.json()

    def test_list_entities_with_filters(self, client, inv_id):
        _add_observations(client, inv_id, count=5)
        client.post(f"/investigations/{inv_id}/extract-entities", headers=AUTH)
        resp = client.get(
            f"/investigations/{inv_id}/entities?entity_type=ORG&min_confidence=0.5&limit=10",
            headers=AUTH,
        )
        assert resp.status_code == 200

    def test_cluster_stories_not_found(self, client):
        resp = client.post("/investigations/nonexistent/cluster-stories", headers=AUTH)
        assert resp.status_code == 404

    def test_cluster_stories_success(self, client, inv_id):
        _add_observations(client, inv_id, count=20)
        resp = client.post(
            f"/investigations/{inv_id}/cluster-stories?hours=48&min_cluster_size=2",
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "stories_found" in data

    def test_list_stories(self, client, inv_id):
        resp = client.get(f"/investigations/{inv_id}/stories", headers=AUTH)
        assert resp.status_code == 200


# =========================================================================
# SEMANTIC INDEX MODULE
# =========================================================================


class TestSemanticIndex:
    def test_index_observations_unavailable(self):
        import sts_monitor.semantic_index as si
        si._engine = None
        with patch("sts_monitor.semantic_index._get_engine", return_value=None):
            result = si.index_observations_batch([{"claim": "test"}])
        assert result["status"] == "unavailable"

    def test_index_observations_success(self):
        import sts_monitor.semantic_index as si
        si._engine = None
        mock_engine = MagicMock()
        mock_engine.index_observations.return_value = {"indexed": 3}
        with patch("sts_monitor.semantic_index._get_engine", return_value=mock_engine):
            result = si.index_observations_batch([{"claim": "test"}])
        assert result["status"] == "ok"
        assert result["indexed"] == 3

    def test_index_observations_error(self):
        import sts_monitor.semantic_index as si
        si._engine = None
        mock_engine = MagicMock()
        mock_engine.index_observations.side_effect = RuntimeError("index error")
        with patch("sts_monitor.semantic_index._get_engine", return_value=mock_engine):
            result = si.index_observations_batch([{"claim": "test"}])
        assert result["status"] == "error"

    def test_semantic_search_unavailable(self):
        import sts_monitor.semantic_index as si
        si._engine = None
        with patch("sts_monitor.semantic_index._get_engine", return_value=None):
            result = si.semantic_search("test query")
        assert result["status"] == "unavailable"

    def test_semantic_search_success(self):
        import sts_monitor.semantic_index as si
        si._engine = None
        mock_engine = MagicMock()
        mock_result = MagicMock()
        mock_result.query = "test"
        mock_match = MagicMock()
        mock_match.observation_id = 1
        mock_match.investigation_id = "inv-1"
        mock_match.source = "rss"
        mock_match.claim = "test claim"
        mock_match.score = 0.9
        mock_match.captured_at = datetime.now(UTC).isoformat()
        mock_result.matches = [mock_match]
        mock_result.total_indexed = 100
        mock_result.search_latency_ms = 5.3
        mock_engine.search.return_value = mock_result
        with patch("sts_monitor.semantic_index._get_engine", return_value=mock_engine):
            result = si.semantic_search("test", limit=10, investigation_id="inv-1", score_threshold=0.5)
        assert result["status"] == "ok"
        assert len(result["matches"]) == 1

    def test_semantic_search_error(self):
        import sts_monitor.semantic_index as si
        si._engine = None
        mock_engine = MagicMock()
        mock_engine.search.side_effect = RuntimeError("search error")
        with patch("sts_monitor.semantic_index._get_engine", return_value=mock_engine):
            result = si.semantic_search("test")
        assert result["status"] == "error"

    def test_find_similar_unavailable(self):
        import sts_monitor.semantic_index as si
        si._engine = None
        with patch("sts_monitor.semantic_index._get_engine", return_value=None):
            result = si.find_similar_observations("test claim")
        assert result == []

    def test_find_similar_success(self):
        import sts_monitor.semantic_index as si
        si._engine = None
        mock_engine = MagicMock()
        mock_match = MagicMock()
        mock_match.observation_id = 1
        mock_match.investigation_id = "inv-1"
        mock_match.source = "rss"
        mock_match.claim = "similar claim"
        mock_match.score = 0.85
        mock_engine.find_similar.return_value = [mock_match]
        with patch("sts_monitor.semantic_index._get_engine", return_value=mock_engine):
            result = si.find_similar_observations("test", observation_id=5, limit=5)
        assert len(result) == 1
        assert result[0]["score"] == 0.85

    def test_find_similar_error(self):
        import sts_monitor.semantic_index as si
        si._engine = None
        mock_engine = MagicMock()
        mock_engine.find_similar.side_effect = RuntimeError("err")
        with patch("sts_monitor.semantic_index._get_engine", return_value=mock_engine):
            result = si.find_similar_observations("test")
        assert result == []

    def test_get_semantic_health_unavailable(self):
        import sts_monitor.semantic_index as si
        si._engine = None
        with patch("sts_monitor.semantic_index._get_engine", return_value=None):
            result = si.get_semantic_health()
        assert result["available"] is False

    def test_get_semantic_health_success(self):
        import sts_monitor.semantic_index as si
        si._engine = None
        mock_engine = MagicMock()
        mock_engine.embedding_client.health.return_value = {"ok": True}
        mock_engine.qdrant_store.health.return_value = {"ok": True}
        mock_engine.qdrant_store.count.return_value = 42
        with patch("sts_monitor.semantic_index._get_engine", return_value=mock_engine):
            result = si.get_semantic_health()
        assert result["available"] is True
        assert result["total_indexed"] == 42

    def test_get_semantic_health_error(self):
        import sts_monitor.semantic_index as si
        si._engine = None
        mock_engine = MagicMock()
        mock_engine.embedding_client.health.side_effect = RuntimeError("down")
        with patch("sts_monitor.semantic_index._get_engine", return_value=mock_engine):
            result = si.get_semantic_health()
        assert result["available"] is False

    def test_get_engine_initialization_failure(self):
        import sts_monitor.semantic_index as si
        si._engine = None
        with patch("sts_monitor.semantic_index.settings") as mock_settings:
            mock_settings.local_llm_url = "http://localhost:9999"
            mock_settings.embedding_model = "test"
            mock_settings.embedding_timeout_s = 5
            mock_settings.qdrant_url = "http://localhost:9998"
            mock_settings.qdrant_vector_size = 384
            mock_settings.qdrant_timeout_s = 5
            with patch.dict("sys.modules", {"sts_monitor.embeddings": MagicMock()}) as mods:
                import sys
                emb_mod = sys.modules["sts_monitor.embeddings"]
                emb_mod.SemanticSearchEngine.side_effect = RuntimeError("fail")
                result = si._get_engine()
        assert result is None


# =========================================================================
# CROSS INVESTIGATION MODULE
# =========================================================================


class TestCrossInvestigation:
    def test_detect_links_less_than_2_investigations(self):
        from sts_monitor.cross_investigation import detect_cross_investigation_links
        session = _get_db()
        report = detect_cross_investigation_links(session)
        assert report.total_links == 0

    def test_detect_links_shared_entities(self, client):
        from sts_monitor.cross_investigation import detect_cross_investigation_links
        session = _get_db()

        # Create two investigations
        inv1_id = str(uuid4())
        inv2_id = str(uuid4())
        session.add(InvestigationORM(id=inv1_id, topic="Investigation 1", priority=50, status="open"))
        session.add(InvestigationORM(id=inv2_id, topic="Investigation 2", priority=50, status="open"))
        session.flush()

        # Add observations with geo coords
        obs1 = ObservationORM(
            investigation_id=inv1_id, source="rss:test",
            claim="FEMA responds to earthquake disaster",
            url="https://example.com/1", reliability_hint=0.8,
            captured_at=datetime.now(UTC),
            latitude=35.0, longitude=-118.0,
        )
        obs2 = ObservationORM(
            investigation_id=inv2_id, source="rss:test",
            claim="FEMA disaster relief operations",
            url="https://example.com/2", reliability_hint=0.7,
            captured_at=datetime.now(UTC),
            latitude=35.0, longitude=-118.0,
        )
        session.add_all([obs1, obs2])
        session.flush()

        # Add shared entity mentions
        session.add(EntityMentionORM(
            observation_id=obs1.id, investigation_id=inv1_id,
            entity_text="FEMA", entity_type="ORG",
            normalized="fema", confidence=0.9,
        ))
        session.add(EntityMentionORM(
            observation_id=obs2.id, investigation_id=inv2_id,
            entity_text="FEMA", entity_type="ORG",
            normalized="fema", confidence=0.9,
        ))
        session.commit()

        report = detect_cross_investigation_links(session)
        assert report.investigations_analyzed == 2
        assert report.total_links > 0
        assert len(report.shared_entities) > 0
        assert len(report.shared_sources) > 0
        assert len(report.geographic_overlaps) > 0

    def test_detect_links_high_priority(self, client):
        from sts_monitor.cross_investigation import detect_cross_investigation_links
        session = _get_db()

        inv_ids = []
        for i in range(3):
            inv_id = str(uuid4())
            inv_ids.append(inv_id)
            session.add(InvestigationORM(id=inv_id, topic=f"Inv {i}", priority=50, status="open"))
        session.flush()

        for inv_id in inv_ids:
            obs = ObservationORM(
                investigation_id=inv_id, source="rss:shared",
                claim="Shared entity across investigations",
                url="https://example.com", reliability_hint=0.8,
                captured_at=datetime.now(UTC),
            )
            session.add(obs)
            session.flush()
            session.add(EntityMentionORM(
                observation_id=obs.id, investigation_id=inv_id,
                entity_text="SharedEntity", entity_type="ORG",
                normalized="sharedentity", confidence=0.9,
            ))
        session.commit()

        report = detect_cross_investigation_links(session)
        assert report.investigations_analyzed == 3
        # shared across 3 investigations = high priority
        assert len(report.high_priority_links) > 0

    def test_cross_link_to_dict(self):
        from sts_monitor.cross_investigation import CrossLink
        link = CrossLink(
            entity_text="test", entity_type="ORG",
            investigation_ids=["a", "b"],
            investigation_topics=["Topic A", "Topic B"],
            mention_counts={"a": 3, "b": 2}, total_mentions=5,
            confidence=0.8, link_type="shared_entity",
            first_seen=datetime.now(UTC), last_seen=datetime.now(UTC),
            sample_claims=["claim1", "claim2"],
        )
        d = link.to_dict()
        assert d["entity"] == "test"
        assert d["confidence"] == 0.8
        assert len(d["investigations"]) == 2

    def test_cross_investigation_report_to_dict(self):
        from sts_monitor.cross_investigation import CrossInvestigationReport
        report = CrossInvestigationReport(
            investigations_analyzed=3, total_links=1,
            shared_entities=[], shared_sources=[],
            geographic_overlaps=[], high_priority_links=[],
        )
        d = report.to_dict()
        assert d["investigations_analyzed"] == 3

    def test_entity_with_short_key_ignored(self, client):
        from sts_monitor.cross_investigation import detect_cross_investigation_links
        session = _get_db()

        inv1_id = str(uuid4())
        inv2_id = str(uuid4())
        session.add(InvestigationORM(id=inv1_id, topic="Inv A", priority=50, status="open"))
        session.add(InvestigationORM(id=inv2_id, topic="Inv B", priority=50, status="open"))
        session.flush()

        obs1 = ObservationORM(
            investigation_id=inv1_id, source="src",
            claim="AB test", url="https://example.com",
            reliability_hint=0.5, captured_at=datetime.now(UTC),
        )
        obs2 = ObservationORM(
            investigation_id=inv2_id, source="src",
            claim="AB test", url="https://example.com",
            reliability_hint=0.5, captured_at=datetime.now(UTC),
        )
        session.add_all([obs1, obs2])
        session.flush()

        # Entity with len < 3 should be ignored
        for obs, inv in [(obs1, inv1_id), (obs2, inv2_id)]:
            session.add(EntityMentionORM(
                observation_id=obs.id, investigation_id=inv,
                entity_text="AB", entity_type="ORG",
                normalized="ab", confidence=0.9,
            ))
        session.commit()

        report = detect_cross_investigation_links(session)
        # "ab" has len < 3 so should not be in shared_entities
        short_entities = [e for e in report.shared_entities if e.entity_text == "ab"]
        assert len(short_entities) == 0

    def test_entity_with_no_observation(self, client):
        from sts_monitor.cross_investigation import detect_cross_investigation_links
        session = _get_db()

        inv1_id = str(uuid4())
        inv2_id = str(uuid4())
        session.add(InvestigationORM(id=inv1_id, topic="Inv C", priority=50, status="open"))
        session.add(InvestigationORM(id=inv2_id, topic="Inv D", priority=50, status="open"))
        session.flush()

        obs1 = ObservationORM(
            investigation_id=inv1_id, source="test-src",
            claim="Test claim text", url="https://example.com",
            reliability_hint=0.5, captured_at=datetime.now(UTC),
        )
        session.add(obs1)
        session.flush()

        # One entity has observation, one has observation_id pointing to a valid obs
        session.add(EntityMentionORM(
            observation_id=obs1.id, investigation_id=inv1_id,
            entity_text="TestOrg", entity_type="ORG",
            normalized="testorg", confidence=0.9,
        ))
        # Second entity in different investigation but referencing same obs (edge case)
        obs2 = ObservationORM(
            investigation_id=inv2_id, source="test-src",
            claim="Another test", url="https://example.com",
            reliability_hint=0.5, captured_at=datetime.now(UTC),
        )
        session.add(obs2)
        session.flush()
        session.add(EntityMentionORM(
            observation_id=obs2.id, investigation_id=inv2_id,
            entity_text="TestOrg", entity_type="ORG",
            normalized="testorg", confidence=0.9,
        ))
        session.commit()

        report = detect_cross_investigation_links(session)
        assert report.investigations_analyzed == 2
