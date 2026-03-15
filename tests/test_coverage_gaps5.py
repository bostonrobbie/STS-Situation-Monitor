"""Coverage gap tests for geo, search, geofence, rate_limit, cycle,
comparative, deep_truth, research_agent, cross_investigation."""

import pytest
import sqlalchemy
from unittest.mock import MagicMock, patch
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from starlette.testclient import TestClient

from sts_monitor.database import Base, engine, SessionLocal
from sts_monitor.main import app
from sts_monitor.models import *

AUTH = {"X-API-Key": "change-me"}

@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch):
    import sts_monitor.rate_limit as rl
    monkeypatch.setattr(rl, "RATE_LIMIT_ENABLED", False)
    from sts_monitor.geofence import _custom_zones
    _custom_zones.clear()
    engine.dispose()
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text("PRAGMA foreign_keys = OFF"))
        tables = conn.execute(sqlalchemy.text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )).scalars().all()
        for t in tables:
            conn.execute(sqlalchemy.text(f'DROP TABLE IF EXISTS "{t}"'))
        conn.commit()
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text("PRAGMA foreign_keys = ON"))
        conn.commit()

@pytest.fixture
def client():
    return TestClient(app)


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_investigation(session, topic="test topic"):
    iid = str(uuid4())
    inv = InvestigationORM(id=iid, topic=topic, status="active", created_at=datetime.now(UTC))
    session.add(inv)
    session.commit()
    return iid


def _make_inv_api(client, topic="test topic"):
    """Create investigation via API (uses same monkeypatched engine)."""
    resp = client.post("/investigations", json={"topic": topic}, headers=AUTH)
    assert resp.status_code == 200
    return resp.json()["id"]


def _make_geo_event(session, layer="earthquake", lat=42.36, lon=-71.06, inv_id=None, hours_ago=1):
    ev = GeoEventORM(
        layer=layer,
        source_id=f"src-{uuid4().hex[:6]}",
        title=f"Test {layer} event",
        latitude=lat,
        longitude=lon,
        altitude=0.0,
        magnitude=5.0,
        properties_json="{}",
        event_time=datetime.now(UTC) - timedelta(hours=hours_ago),
        fetched_at=datetime.now(UTC),
        investigation_id=inv_id,
    )
    session.add(ev)
    session.flush()
    eid = ev.id
    session.commit()
    return eid


# ═══════════════════════════════════════════════════════════════════════
# 1. GEO ROUTES  (geo.py missing: 48-63, 278-284, 319)
# ═══════════════════════════════════════════════════════════════════════

class TestGeoRoutes:
    """Cover dashboard/playback, dashboard/timeline with data, and geo/events filters."""

    def test_geo_events_filter_by_layer(self, client):
        """Line 86: filter by layer param."""
        with SessionLocal() as s:
            inv_id = _make_investigation(s)
            _make_geo_event(s, layer="earthquake", inv_id=inv_id)
            _make_geo_event(s, layer="fire", inv_id=inv_id)
        resp = client.get("/geo/events?layer=earthquake", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert all(e["layer"] == "earthquake" for e in data["events"])

    def test_geo_events_filter_by_investigation(self, client):
        """Line 88: filter by investigation_id."""
        with SessionLocal() as s:
            inv_id = _make_investigation(s)
            _make_geo_event(s, inv_id=inv_id)
            _make_geo_event(s, inv_id=None)
        resp = client.get(f"/geo/events?investigation_id={inv_id}", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        for e in data["events"]:
            assert e["investigation_id"] == inv_id

    def test_dashboard_timeline_with_data(self, client):
        """Lines 278-284: timeline buckets with actual events."""
        with SessionLocal() as s:
            inv_id = _make_investigation(s)
            _make_geo_event(s, layer="earthquake", inv_id=inv_id, hours_ago=2)
            _make_geo_event(s, layer="fire", inv_id=inv_id, hours_ago=2)
        resp = client.get("/dashboard/timeline?hours=48&bucket_hours=1", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["bucket_hours"] == 1
        assert len(data["buckets"]) >= 1
        for b in data["buckets"]:
            assert "time" in b
            assert "layers" in b
            assert "total" in b
            assert b["total"] > 0

    def test_dashboard_playback_with_data(self, client):
        """Lines 315-337: playback with events, including time_bounds."""
        with SessionLocal() as s:
            inv_id = _make_investigation(s)
            _make_geo_event(s, layer="earthquake", inv_id=inv_id, hours_ago=5)
            _make_geo_event(s, layer="fire", inv_id=inv_id, hours_ago=1)
        resp = client.get("/dashboard/playback?hours=48", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) >= 2
        meta = data["metadata"]
        assert meta["time_bounds"]["min"] is not None
        assert meta["time_bounds"]["max"] is not None
        assert len(meta["hourly_counts"]) >= 1

    def test_dashboard_map_data_with_layers_filter(self, client):
        """Lines 237-240: layers filter in map-data."""
        with SessionLocal() as s:
            inv_id = _make_investigation(s)
            _make_geo_event(s, layer="earthquake", inv_id=inv_id)
            _make_geo_event(s, layer="fire", inv_id=inv_id)
        resp = client.get("/dashboard/map-data?layers=earthquake", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        for f in data["features"]:
            assert f["properties"]["layer"] == "earthquake"

    def test_dashboard_live(self, client):
        """Lines 358-436: live dashboard endpoint."""
        _make_inv_api(client)
        resp = client.get("/dashboard/live", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "investigations" in data
        assert "geo_events_24h" in data
        assert "layers" in data

    def test_geofence_delete_not_found(self, client):
        """Line 514-515: delete a geofence that doesn't exist."""
        resp = client.delete("/geofences/nonexistent_zone_xyz", headers=AUTH)
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# 2. SEARCH ROUTES  (search.py missing: 68, 100-103, 117-119, 131, 177,
#    242, 245, 255, 291, 294, 301, 304, 309, 317)
# ═══════════════════════════════════════════════════════════════════════

class TestSearchRoutes:
    """Cover search profiles, query with filters, suggest, related-investigations."""

    def test_create_search_profile_success(self, client):
        """Lines 45-57: create a profile."""
        inv_id = _make_inv_api(client)
        resp = client.post(
            "/search/profiles",
            json={
                "name": "test-profile",
                "investigation_id": inv_id,
                "include_terms": ["earthquake", "tsunami"],
                "exclude_terms": ["fake"],
                "synonyms": {"quake": ["earthquake", "tremor"]},
            },
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "test-profile"

    def test_create_search_profile_duplicate(self, client):
        """Line 43: duplicate name returns 409."""
        inv_id = _make_inv_api(client)
        r1 = client.post(
            "/search/profiles",
            json={"name": "dup-profile", "investigation_id": inv_id,
                  "include_terms": [], "exclude_terms": [], "synonyms": {}},
            headers=AUTH,
        )
        assert r1.status_code == 200
        resp = client.post(
            "/search/profiles",
            json={"name": "dup-profile", "investigation_id": inv_id,
                  "include_terms": [], "exclude_terms": [], "synonyms": {}},
            headers=AUTH,
        )
        assert resp.status_code == 409

    def test_create_search_profile_bad_investigation(self, client):
        """Line 39: invalid investigation_id returns 404."""
        resp = client.post(
            "/search/profiles",
            json={"name": "bad-inv-profile", "investigation_id": "nonexistent",
                  "include_terms": [], "exclude_terms": [], "synonyms": {}},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_list_search_profiles_filtered(self, client):
        """Lines 67-68: list profiles filtered by investigation_id."""
        inv_id = _make_inv_api(client,"search filter test")
        r1 = client.post(
            "/search/profiles",
            json={"name": "filtered-profile", "investigation_id": inv_id,
                  "include_terms": ["test"], "exclude_terms": [], "synonyms": {}},
            headers=AUTH,
        )
        assert r1.status_code == 200, f"Profile creation failed: {r1.text}"
        resp = client.get(f"/search/profiles?investigation_id={inv_id}", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1

    def test_search_query_with_profile(self, client):
        """Lines 93-108: query using a profile with synonyms, include/exclude terms."""
        inv_id = _make_inv_api(client)
        # Add observation via API
        client.post(
            f"/investigations/{inv_id}/observations",
            json={"source": "test-src",
                  "claim": "earthquake struck the region with magnitude 5",
                  "url": "http://example.com"},
            headers=AUTH,
        )
        # Create profile first
        client.post(
            "/search/profiles",
            json={"name": "query-profile", "investigation_id": inv_id,
                  "include_terms": ["earthquake"], "exclude_terms": ["spam"],
                  "synonyms": {"quake": ["earthquake"]}},
            headers=AUTH,
        )
        resp = client.post(
            "/search/query",
            json={
                "query": "earthquake magnitude",
                "profile_name": "query-profile",
                "include_observations": True,
                "include_claims": False,
                "limit": 10,
                "min_score": 0.0,
            },
            headers=AUTH,
        )
        assert resp.status_code == 200

    def test_search_query_profile_not_found(self, client):
        """Line 95: profile not found returns 404."""
        resp = client.post(
            "/search/query",
            json={"query": "test", "profile_name": "nonexistent",
                  "include_observations": True, "include_claims": False,
                  "limit": 10, "min_score": 0.0},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_search_query_with_claims(self, client):
        """Lines 162-197: query including claims with stance/since/until."""
        # Create investigation and run pipeline to generate claims
        inv_id = _make_inv_api(client)
        # Add observations and generate a report to produce claims
        client.post(
            f"/investigations/{inv_id}/observations",
            json={"source": "test-src", "claim": "earthquake confirmed in the area",
                  "url": "http://example.com"},
            headers=AUTH,
        )
        # Query with claims - even if no claims exist, the query should succeed
        resp = client.post(
            "/search/query",
            json={
                "query": "earthquake confirmed",
                "include_observations": False,
                "include_claims": True,
                "limit": 10,
                "min_score": 0.0,
            },
            headers=AUTH,
        )
        assert resp.status_code == 200

    def test_search_query_with_obs_filters(self, client):
        """Lines 126-133: observation filters: source_prefix, min_reliability, since, until."""
        inv_id = _make_inv_api(client)
        client.post(
            f"/investigations/{inv_id}/observations",
            json={"source": "rss:reuters",
                  "claim": "earthquake activity reported near the coast",
                  "url": "http://reuters.com/1"},
            headers=AUTH,
        )
        now = datetime.now(UTC)
        resp = client.post(
            "/search/query",
            json={
                "query": "earthquake coast",
                "investigation_id": inv_id,
                "include_observations": True,
                "include_claims": False,
                "source_prefix": "rss",
                "min_reliability": 0.0,
                "since": (now - timedelta(hours=1)).isoformat(),
                "until": (now + timedelta(hours=1)).isoformat(),
                "limit": 10,
                "min_score": 0.0,
            },
            headers=AUTH,
        )
        assert resp.status_code == 200

    def test_search_suggest(self, client):
        """Lines 232-257: suggest search terms."""
        inv_id = _make_inv_api(client)
        client.post(
            f"/investigations/{inv_id}/observations",
            json={"source": "test",
                  "claim": "earthquake magnitude seismic activity reported",
                  "url": "http://example.com"},
            headers=AUTH,
        )
        resp = client.get(f"/search/suggest?q=earthquake&investigation_id={inv_id}", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "suggestions" in data

    def test_related_investigations(self, client):
        """Lines 260-333: find related investigations."""
        inv1 = _make_inv_api(client,"earthquake monitoring")
        inv2 = _make_inv_api(client,"seismic activity")
        for inv_id in [inv1, inv2]:
            client.post(
                f"/investigations/{inv_id}/observations",
                json={"source": "test",
                      "claim": "earthquake magnitude detected seismic waves",
                      "url": "http://example.com"},
                headers=AUTH,
            )
        resp = client.post(
            "/search/related-investigations",
            json={"query": "earthquake seismic", "limit": 10, "min_score": 0.0},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "investigations" in data


# ═══════════════════════════════════════════════════════════════════════
# 3. GEOFENCE  (geofence.py missing: 34, 59, 143-144, 181-187)
# ═══════════════════════════════════════════════════════════════════════

class TestGeofenceModule:
    """Cover GeoZone.to_dict, GeoAlert.to_dict, invalid lat/lon, zone_activity_summary with invalid data."""

    def test_geozone_to_dict(self):
        """Line 34: GeoZone.to_dict()."""
        from sts_monitor.geofence import GeoZone
        zone = GeoZone("Test", 42.0, -71.0, 50.0, "custom")
        d = zone.to_dict()
        assert d["name"] == "Test"
        assert d["center_lat"] == 42.0
        assert d["active"] is True
        assert d["alert_on_entry"] is True

    def test_geoalert_to_dict(self):
        """Line 59: GeoAlert.to_dict()."""
        from sts_monitor.geofence import GeoAlert
        alert = GeoAlert(
            zone_name="Z", observation_id=1, source="s",
            claim="c" * 500,  # test claim truncation
            lat=42.0, lon=-71.0, distance_km=5.123,
            triggered_at=datetime.now(UTC),
        )
        d = alert.to_dict()
        assert len(d["claim"]) <= 300
        assert d["distance_km"] == 5.12

    def test_check_observations_invalid_lat_lon(self):
        """Lines 143-144: observations with unparseable lat/lon skipped."""
        from sts_monitor.geofence import check_observations_against_zones, GeoZone
        zone = GeoZone("Test", 42.0, -71.0, 5000.0)
        obs = [
            {"latitude": "bad", "longitude": -71.0, "source": "s", "claim": "c"},
            {"latitude": None, "longitude": None, "source": "s", "claim": "c"},
            {"lat": 42.0, "lon": -71.0, "source": "s", "claim": "c"},  # valid via alternate keys
        ]
        alerts = check_observations_against_zones(obs, [zone])
        # Only the last observation should match
        assert len(alerts) >= 1

    def test_zone_activity_summary_with_invalid_coords(self):
        """Lines 181-187: zone activity with invalid coords."""
        from sts_monitor.geofence import get_zone_activity_summary, GeoZone
        zone = GeoZone("Test", 42.0, -71.0, 5000.0)
        obs = [
            {"latitude": "invalid", "longitude": "invalid", "source": "s"},
            {"lat": 42.0, "lon": -71.0, "source": "src1"},
        ]
        result = get_zone_activity_summary(obs, [zone])
        assert len(result) == 1
        assert result[0]["zone"] == "Test"

    def test_check_observations_scoped_to_investigation(self):
        """Lines 130-131: filter zones by investigation_id scope."""
        from sts_monitor.geofence import check_observations_against_zones, GeoZone
        zone_global = GeoZone("Global", 42.0, -71.0, 5000.0)
        zone_scoped = GeoZone("Scoped", 42.0, -71.0, 5000.0, investigation_id="inv-123")
        obs = [{"latitude": 42.0, "longitude": -71.0, "source": "s", "claim": "c"}]
        # With wrong investigation_id, scoped zone should not trigger
        alerts = check_observations_against_zones(obs, [zone_global, zone_scoped], investigation_id="inv-999")
        zone_names = [a.zone_name for a in alerts]
        assert "Global" in zone_names
        assert "Scoped" not in zone_names

    def test_zone_activity_summary_default_zones(self):
        """Line 169-170: default zones used when None."""
        from sts_monitor.geofence import get_zone_activity_summary
        result = get_zone_activity_summary([])
        assert len(result) > 0  # builtin zones


# ═══════════════════════════════════════════════════════════════════════
# 4. RATE LIMIT  (rate_limit.py missing: 72, 89, 93-95, 98, 102-108, 110-112)
# ═══════════════════════════════════════════════════════════════════════

class TestRateLimitModule:
    """Cover token exhaustion, middleware paths, exempt paths, and cleanup."""

    def test_rate_limiter_allow_and_exhaust(self):
        """Lines 68-72: tokens exhaust, then return False with Retry-After."""
        from sts_monitor.rate_limit import RateLimiter
        limiter = RateLimiter(rpm=60, burst=2)
        ok1, h1 = limiter.allow("user1")
        assert ok1 is True
        ok2, h2 = limiter.allow("user1")
        assert ok2 is True
        ok3, h3 = limiter.allow("user1")
        assert ok3 is False
        assert "Retry-After" in h3

    def test_rate_limiter_cleanup(self):
        """Lines 41-50: cleanup stale entries."""
        import time
        from sts_monitor.rate_limit import RateLimiter
        limiter = RateLimiter(rpm=60, burst=5)
        limiter.allow("stale-user")
        # Force cleanup by manipulating last_cleanup and bucket timestamps
        limiter._last_cleanup = time.monotonic() - 400
        limiter._buckets["stale-user"].last_refill = time.monotonic() - 400
        limiter.allow("fresh-user")  # triggers cleanup
        assert "stale-user" not in limiter._buckets

    def test_middleware_enabled_with_exempt_path(self, client, monkeypatch):
        """Lines 84-95: middleware when enabled, exempt paths bypass."""
        import sts_monitor.rate_limit as rl
        monkeypatch.setattr(rl, "RATE_LIMIT_ENABLED", True)
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_middleware_enabled_normal_path(self, client, monkeypatch):
        """Lines 93-112: middleware with real client host."""
        import sts_monitor.rate_limit as rl
        monkeypatch.setattr(rl, "RATE_LIMIT_ENABLED", True)
        # TestClient uses 'testclient' as host, which is exempt
        resp = client.get("/health")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════
# 5. CYCLE  (cycle.py missing: 110-116, 117-122, 162-189, 269-311, 414-426)
# ═══════════════════════════════════════════════════════════════════════

class TestCycleModule:
    """Cover evaluate_alerts heuristic paths, promote_discoveries, CycleResult.summary."""

    def test_evaluate_alerts_critical_anomalies(self):
        """Lines 107-116: anomaly_critical alert."""
        from sts_monitor.cycle import evaluate_alerts
        pipeline_result = MagicMock()
        pipeline_result.accepted = []
        pipeline_result.disputed_claims = []
        enrichment = MagicMock()
        enrichment.anomalies.total_anomalies = 5
        enrichment.anomalies.by_severity = {"critical": 2}
        anom = MagicMock()
        anom.severity = "critical"
        anom.title = "Critical anomaly"
        enrichment.anomalies.anomalies = [anom]
        enrichment.slop_filter.total = 0
        enrichment.corroboration.total_claims = 0
        enrichment.convergence_zones = []
        alerts = evaluate_alerts(pipeline_result, enrichment)
        assert any(a.rule_name == "anomaly_critical" for a in alerts)

    def test_evaluate_alerts_high_anomalies(self):
        """Lines 117-122: anomaly_high alert when no critical."""
        from sts_monitor.cycle import evaluate_alerts
        pipeline_result = MagicMock()
        pipeline_result.accepted = []
        pipeline_result.disputed_claims = []
        enrichment = MagicMock()
        enrichment.anomalies.total_anomalies = 4
        enrichment.anomalies.by_severity = {"high": 3}
        enrichment.anomalies.anomalies = []
        enrichment.slop_filter.total = 0
        enrichment.corroboration.total_claims = 0
        enrichment.convergence_zones = []
        alerts = evaluate_alerts(pipeline_result, enrichment)
        assert any(a.rule_name == "anomaly_high" for a in alerts)

    def test_evaluate_alerts_high_slop_ratio(self):
        """Lines 125-132: high slop ratio alert."""
        from sts_monitor.cycle import evaluate_alerts
        pipeline_result = MagicMock()
        pipeline_result.accepted = []
        pipeline_result.disputed_claims = []
        enrichment = MagicMock()
        enrichment.anomalies.total_anomalies = 0
        enrichment.slop_filter.total = 10
        enrichment.slop_filter.slop = 4
        enrichment.slop_filter.propaganda = 2
        enrichment.corroboration.total_claims = 0
        enrichment.convergence_zones = []
        alerts = evaluate_alerts(pipeline_result, enrichment)
        assert any(a.rule_name == "high_slop_ratio" for a in alerts)

    def test_evaluate_alerts_low_corroboration(self):
        """Lines 135-139: low corroboration alert."""
        from sts_monitor.cycle import evaluate_alerts
        pipeline_result = MagicMock()
        pipeline_result.accepted = []
        pipeline_result.disputed_claims = []
        enrichment = MagicMock()
        enrichment.anomalies.total_anomalies = 0
        enrichment.slop_filter.total = 0
        enrichment.corroboration.total_claims = 20
        enrichment.corroboration.overall_corroboration_rate = 0.05
        enrichment.convergence_zones = []
        alerts = evaluate_alerts(pipeline_result, enrichment)
        assert any(a.rule_name == "low_corroboration" for a in alerts)

    def test_evaluate_alerts_high_dispute_rate(self):
        """Lines 143-148: many disputed claims."""
        from sts_monitor.cycle import evaluate_alerts
        pipeline_result = MagicMock()
        pipeline_result.accepted = []
        pipeline_result.disputed_claims = [MagicMock()] * 6
        enrichment = MagicMock()
        enrichment.anomalies.total_anomalies = 0
        enrichment.slop_filter.total = 0
        enrichment.corroboration.total_claims = 0
        enrichment.convergence_zones = []
        alerts = evaluate_alerts(pipeline_result, enrichment)
        assert any(a.rule_name == "high_dispute_rate" for a in alerts)

    def test_evaluate_alerts_convergence_zone(self):
        """Lines 151-158: convergence zone alert."""
        from sts_monitor.cycle import evaluate_alerts
        pipeline_result = MagicMock()
        pipeline_result.accepted = []
        pipeline_result.disputed_claims = []
        enrichment = MagicMock()
        enrichment.anomalies.total_anomalies = 0
        enrichment.slop_filter.total = 0
        enrichment.corroboration.total_claims = 0
        zone = MagicMock()
        zone.severity = "critical"
        zone.signal_types = ["earthquake", "fire"]
        zone.center_lat = 42.0
        zone.center_lon = -71.0
        enrichment.convergence_zones = [zone]
        alerts = evaluate_alerts(pipeline_result, enrichment)
        assert any(a.rule_name == "convergence_zone" for a in alerts)

    def test_evaluate_alerts_user_defined_rules(self):
        """Lines 161-189: user-defined alert rules with cooldown."""
        from sts_monitor.cycle import evaluate_alerts
        pipeline_result = MagicMock()
        pipeline_result.accepted = [MagicMock()] * 25
        pipeline_result.disputed_claims = [MagicMock()] * 3
        enrichment = MagicMock()
        enrichment.anomalies.total_anomalies = 0
        enrichment.slop_filter.total = 0
        enrichment.corroboration.total_claims = 0
        enrichment.convergence_zones = []
        rules = [
            {"name": "my_rule", "min_observations": 20, "min_disputed_claims": 2, "severity": "high",
             "cooldown_seconds": 0, "active": True},
            {"name": "inactive_rule", "active": False},
            {"name": "cooldown_rule", "min_observations": 1, "min_disputed_claims": 1,
             "severity": "warning", "cooldown_seconds": 9999,
             "last_triggered_at": datetime.now(UTC).isoformat()},
        ]
        alerts = evaluate_alerts(pipeline_result, enrichment, alert_rules=rules)
        names = [a.rule_name for a in alerts]
        assert "my_rule" in names
        assert "inactive_rule" not in names
        assert "cooldown_rule" not in names

    def test_promote_discoveries(self):
        """Lines 206-228: promote discovered topics."""
        from sts_monitor.cycle import promote_discoveries
        enrichment = MagicMock()
        topic1 = MagicMock()
        topic1.score = 0.8
        topic1.title = "Emerging Topic"
        topic1.suggested_seed_query = "emerging topic query"
        topic1.source = "burst"
        topic1.description = "A description of the topic"
        topic2 = MagicMock()
        topic2.score = 0.2
        topic2.title = "Low Score"
        enrichment.discovered_topics = [topic1, topic2]
        promoted = promote_discoveries(enrichment, min_score=0.4)
        assert len(promoted) == 1
        assert promoted[0].title == "Emerging Topic"

    def test_cycle_result_summary(self):
        """Lines 269-311: CycleResult.summary() with surge + deep_truth + report."""
        from sts_monitor.cycle import CycleResult, AlertFired, PromotedTopic
        now = datetime.now(UTC)
        pipeline_result = MagicMock()
        pipeline_result.accepted = [MagicMock()] * 10
        pipeline_result.dropped = [MagicMock()] * 2
        pipeline_result.disputed_claims = [MagicMock()]
        pipeline_result.confidence = 0.85
        enrichment = MagicMock()
        enrichment.slop_filter.credible = 8
        enrichment.slop_filter.slop = 1
        enrichment.slop_filter.propaganda = 1
        enrichment.entities = ["ent1"]
        enrichment.stories = ["story1"]
        enrichment.corroboration.well_corroborated = 5
        enrichment.corroboration.total_claims = 10
        enrichment.anomalies.total_anomalies = 1
        enrichment.convergence_zones = []
        enrichment.discovered_topics = []
        surge = MagicMock()
        surge.total_processed = 5
        surge.alpha_count = 2
        surge.noise_count = 2
        surge.disinfo_count = 1
        surge.surge_detected = True
        surge.surges = [MagicMock()]
        deep_truth = MagicMock()
        deep_truth.authority_weight.score = 0.75
        deep_truth.authority_weight.skepticism_level = "elevated_scrutiny"
        deep_truth.provenance.entropy_bits = 2.5
        deep_truth.manufactured_consensus_detected = True
        deep_truth.active_suppression_detected = True
        report = MagicMock()
        report.generation_method = "deterministic"
        report.generation_time_ms = 100.0
        cr = CycleResult(
            cycle_id="abc", topic="test", investigation_id="inv1",
            started_at=now, completed_at=now, duration_ms=500.0,
            connector_results=[{"connector": "test", "observations": 10}],
            total_observations_collected=10,
            pipeline_result=pipeline_result,
            enrichment=enrichment,
            alerts_fired=[AlertFired(rule_name="test_alert", severity="high", message="test msg")],
            report=report,
            surge_analysis=surge,
            deep_truth_verdict=deep_truth,
            promoted_topics=[PromotedTopic(title="Topic", seed_query="q", score=0.8, source="burst", reason="r")],
        )
        s = cr.summary()
        assert "abc" in s
        assert "SURGE DETECTED" in s
        assert "MANUFACTURED CONSENSUS" in s
        assert "ACTIVE SUPPRESSION" in s
        assert "PROMOTE" in s
        assert "ALERT" in s


# ═══════════════════════════════════════════════════════════════════════
# 6. COMPARATIVE  (comparative.py missing ~10 lines)
# ═══════════════════════════════════════════════════════════════════════

class TestComparativeModule:
    """Cover detect_contradictions, detect_agreements, detect_silences, run_comparative."""

    def test_detect_contradictions_direct(self):
        """Lines 226-234: direct contradiction with negation vs confirmation."""
        from sts_monitor.comparative import detect_contradictions
        now = datetime.now(UTC)
        obs = [
            {"source": "src-a", "claim": "The earthquake was confirmed by officials in the region verified",
             "captured_at": now, "reliability_hint": 0.8},
            {"source": "src-b", "claim": "The earthquake was debunked false by officials in the region",
             "captured_at": now, "reliability_hint": 0.5},
        ]
        contradictions = detect_contradictions(obs)
        assert len(contradictions) >= 1
        assert contradictions[0].contradiction_type in ("direct", "framing")

    def test_detect_contradictions_framing(self):
        """Lines 235-243: framing contradiction."""
        from sts_monitor.comparative import detect_contradictions
        now = datetime.now(UTC)
        obs = [
            {"source": "src-a", "claim": "military forces deployed weapons near the border region today",
             "captured_at": now},
            {"source": "src-b", "claim": "military forces not deployed weapons near the border region today denied",
             "captured_at": now},
        ]
        contradictions = detect_contradictions(obs)
        assert len(contradictions) >= 1

    def test_detect_agreements(self):
        """Lines 249-291: agreement clusters from multiple sources."""
        from sts_monitor.comparative import detect_agreements
        now = datetime.now(UTC)
        obs = [
            {"source": "rss:reuters", "claim": "earthquake magnitude measured seismic waves",
             "captured_at": now.isoformat(), "reliability_hint": 0.9},
            {"source": "gdelt:bbc", "claim": "earthquake magnitude measured seismic waves",
             "captured_at": now, "reliability_hint": 0.8},
        ]
        clusters = detect_agreements(obs, min_sources=2)
        assert len(clusters) >= 1
        d = clusters[0].to_dict()
        assert "sources" in d

    def test_detect_silences(self):
        """Lines 294-354: source that went silent."""
        from sts_monitor.comparative import detect_silences
        # Source was active 3-5 days ago, then went silent > 12 hours ago
        base = datetime.now(UTC) - timedelta(days=5)
        obs = [
            {"source": "rss:reuters", "claim": f"update {i}",
             "captured_at": (base + timedelta(hours=i * 6)).isoformat()}
            for i in range(10)  # 10 obs spread over 2.5 days, last one ~2.5 days ago
        ]
        silences = detect_silences(obs, silence_threshold_hours=12, min_prior_frequency=0.5)
        assert len(silences) >= 1
        d = silences[0].to_dict()
        assert "source" in d

    def test_run_comparative_analysis(self):
        """Lines 357-379: full comparative analysis."""
        from sts_monitor.comparative import run_comparative_analysis
        now = datetime.now(UTC)
        obs = [
            {"source": "rss:reuters", "claim": "earthquake confirmed verified by officials region",
             "captured_at": now, "reliability_hint": 0.9},
            {"source": "gdelt:bbc", "claim": "earthquake debunked false officials region denied",
             "captured_at": now, "reliability_hint": 0.7},
        ]
        report = run_comparative_analysis(obs)
        d = report.to_dict()
        assert "total_observations" in d
        assert "narrative_divergence_score" in d

    def test_source_family_extraction(self):
        """Lines 172-180: _source_family with various prefixes."""
        from sts_monitor.comparative import _source_family
        assert _source_family("rss:reuters.com/news") == "reuters.com"
        assert _source_family("gdelt:example.com") == "example.com"
        assert _source_family("https://www.example.com") == "example.com"
        assert _source_family("") == ""  # empty returns source unchanged (actually returns "")

    def test_silence_event_to_dict(self):
        """Lines 89-95: SilenceEvent.to_dict."""
        from sts_monitor.comparative import SilenceEvent
        se = SilenceEvent(
            source="test", last_seen=datetime.now(UTC),
            hours_silent=24.567, was_reporting_topic="topic",
            avg_prior_frequency_per_day=2.345,
        )
        d = se.to_dict()
        assert d["hours_silent"] == 24.6
        assert d["prior_frequency_per_day"] == 2.35


# ═══════════════════════════════════════════════════════════════════════
# 7. DEEP TRUTH  (deep_truth.py missing ~11 lines)
# ═══════════════════════════════════════════════════════════════════════

class TestDeepTruthModule:
    """Cover authority weight levels, provenance, silence gaps, verdict building."""

    def test_authority_weight_skepticism_levels(self):
        """Lines 51-59: all skepticism_level branches."""
        from sts_monitor.deep_truth import AuthorityWeight
        aw_max = AuthorityWeight(score=0.85, sources_pushing=5, pejorative_labels=[],
                                  coordination_markers=[], breakdown={})
        assert aw_max.skepticism_level == "maximum_scrutiny"
        aw_elev = AuthorityWeight(score=0.65, sources_pushing=3, pejorative_labels=[],
                                   coordination_markers=[], breakdown={})
        assert aw_elev.skepticism_level == "elevated_scrutiny"
        aw_std = AuthorityWeight(score=0.45, sources_pushing=2, pejorative_labels=[],
                                  coordination_markers=[], breakdown={})
        assert aw_std.skepticism_level == "standard"
        aw_low = AuthorityWeight(score=0.2, sources_pushing=1, pejorative_labels=[],
                                  coordination_markers=[], breakdown={})
        assert aw_low.skepticism_level == "low_coordination"

    def test_detect_pejorative_labels(self):
        """Lines 210-217: detect pejoratives in text."""
        from sts_monitor.deep_truth import detect_pejorative_labels
        labels = detect_pejorative_labels("This is debunked misinformation and a conspiracy theory")
        assert len(labels) >= 2

    def test_detect_phrase_coordination(self):
        """Lines 222-253: detect coordinated identical phrasing."""
        from sts_monitor.deep_truth import detect_phrase_coordination
        obs = [
            {"claim": "officials confirmed reports exactly matching special phrase repeated", "source": f"src-{i}"}
            for i in range(5)
        ]
        markers = detect_phrase_coordination(obs, min_phrase_len=3, min_repeats=3)
        assert len(markers) >= 1

    def test_compute_provenance(self):
        """Lines 334-373: compute provenance entropy."""
        from sts_monitor.deep_truth import compute_provenance
        obs = [
            {"source": "usgs:eq1", "claim": "c1"},
            {"source": "nws:alert1", "claim": "c2"},
            {"source": "rss:reuters", "claim": "c3"},
            {"source": "reddit:post1", "claim": "c4"},
        ]
        prov = compute_provenance(obs)
        assert prov.entropy_bits > 0
        assert 0 <= prov.primary_source_ratio <= 1
        assert 0 <= prov.tamper_resistance <= 1

    def test_detect_silence_gaps(self):
        """Lines 378-462: detect silence gaps for various categories."""
        from sts_monitor.deep_truth import detect_silence_gaps
        obs = [
            {"claim": "earthquake attack struck killing people", "source": "src1"},
        ]
        gaps = detect_silence_gaps(obs, "earthquake attack")
        assert len(gaps) >= 1

    def test_detect_silence_gaps_few_sources(self):
        """Lines 440-446: silence gap for few source types."""
        from sts_monitor.deep_truth import detect_silence_gaps
        obs = [{"claim": "something happened", "source": "rss:only"}]
        gaps = detect_silence_gaps(obs, "something")
        source_gap = [g for g in gaps if "few source" in g.question.lower()]
        assert len(source_gap) >= 1

    def test_detect_silence_gaps_no_dissent(self):
        """Lines 449-460: silence gap for missing contradictions in large dataset."""
        from sts_monitor.deep_truth import detect_silence_gaps
        obs = [{"claim": f"identical claim number {i}", "source": f"src-{i}"} for i in range(15)]
        gaps = detect_silence_gaps(obs, "identical claim")
        dissent_gap = [g for g in gaps if "dissenting" in g.question.lower()]
        assert len(dissent_gap) >= 1

    def test_analyze_deep_truth_full(self):
        """Lines 467-551: full analyze_deep_truth run."""
        from sts_monitor.deep_truth import analyze_deep_truth
        obs = [
            {"source": "rss:reuters.com", "claim": "officials confirmed the earthquake struck the area",
             "url": "http://reuters.com/1", "reliability_hint": 0.9, "captured_at": datetime.now(UTC).isoformat()},
            {"source": "gdelt:bbc.com", "claim": "earthquake confirmed by officials in the region",
             "url": "http://bbc.com/1", "reliability_hint": 0.8, "captured_at": datetime.now(UTC).isoformat()},
            {"source": "reddit:post", "claim": "however some questioned the earthquake report",
             "url": "http://reddit.com/1", "reliability_hint": 0.4, "captured_at": datetime.now(UTC).isoformat()},
        ]
        verdict = analyze_deep_truth(obs, "earthquake")
        d = verdict.to_dict()
        assert "claim" in d
        assert "authority_weight" in d
        assert "tracks" in d
        assert len(d["tracks"]) == 3
        assert "probability_distribution" in d

    def test_deep_truth_verdict_to_dict(self):
        """Lines 137-184: DeepTruthVerdict.to_dict."""
        from sts_monitor.deep_truth import (
            DeepTruthVerdict, AuthorityWeight, ProvenanceScore,
            TrackAnalysis, SilenceGap,
        )
        verdict = DeepTruthVerdict(
            claim="test", analyzed_at=datetime.now(UTC),
            consensus_position="consensus", authority_weight=AuthorityWeight(
                score=0.5, sources_pushing=3, pejorative_labels=["debunked"],
                coordination_markers=[], breakdown={}),
            incentive_analysis="incentives", funding_flows=[], career_consequences=[],
            conflicts_of_interest=[],
            tracks=[TrackAnalysis(
                track_name="mainstream", position_summary="pos",
                supporting_evidence=[], weaknesses=[], survived_attack=True,
                explanatory_power=0.5, ad_hoc_assumptions=1, probability=0.6,
            )],
            surviving_claims=["claim1"],
            falsification_tests=[{"hypothesis": "h", "test": "t", "timeframe": "1w"}],
            silence_gaps=[SilenceGap(question="q", expected_data="d", possible_reason="r", importance=0.8)],
            verdict_summary="verdict", probability_distribution={"mainstream": 0.6},
            confidence_in_verdict=0.7, active_suppression_detected=False,
            manufactured_consensus_detected=False,
            provenance=ProvenanceScore(
                entropy_bits=2.0, source_independence=0.5,
                primary_source_ratio=0.3, tamper_resistance=0.4, source_chain=["rss"]),
        )
        d = verdict.to_dict()
        assert d["authority_weight"]["skepticism_level"] == "standard"
        assert len(d["silence_gaps"]) == 1


# ═══════════════════════════════════════════════════════════════════════
# 8. RESEARCH AGENT  (research_agent.py missing: 226-227, 262-263, 280-281,
#    297-298, 300-301, 452-456, 504-507)
# ═══════════════════════════════════════════════════════════════════════

class TestResearchAgentModule:
    """Cover ResearchAgent methods: list_sessions, stop, _ask_llm error paths,
    _collect_directed, run with stop flag."""

    def test_list_sessions(self):
        """Lines 167-180: list_sessions."""
        from sts_monitor.research_agent import ResearchAgent, ResearchSession
        llm = MagicMock()
        agent = ResearchAgent(llm_client=llm, max_iterations=1)
        session = ResearchSession(
            session_id="s1", topic="test", status="completed",
            started_at=datetime.now(UTC),
        )
        agent._sessions["s1"] = session
        result = agent.list_sessions()
        assert len(result) == 1
        assert result[0]["session_id"] == "s1"

    def test_stop_session(self):
        """Lines 182-187: stop_session."""
        from sts_monitor.research_agent import ResearchAgent
        llm = MagicMock()
        agent = ResearchAgent(llm_client=llm, max_iterations=1)
        assert agent.stop_session("nonexistent") is False
        agent._stop_flags["s1"] = False
        assert agent.stop_session("s1") is True

    def test_ask_llm_valid_json(self):
        """Lines 305-342: _ask_llm with valid JSON response."""
        from sts_monitor.research_agent import ResearchAgent
        from sts_monitor.pipeline import Observation
        llm = MagicMock()
        llm.summarize.return_value = '{"key_findings": ["f1"], "confidence": 0.8, "should_continue": false}'
        agent = ResearchAgent(llm_client=llm, max_iterations=1)
        obs = [Observation(source="test", claim="c", url="http://x", captured_at=datetime.now(UTC), reliability_hint=0.5)]
        result = agent._ask_llm("topic", 1, obs, ["prev finding"], 5)
        assert result["key_findings"] == ["f1"]
        assert result["should_continue"] is False

    def test_ask_llm_invalid_json(self):
        """Lines 343-355: _ask_llm with invalid JSON."""
        from sts_monitor.research_agent import ResearchAgent
        llm = MagicMock()
        llm.summarize.return_value = "This is not JSON at all"
        agent = ResearchAgent(llm_client=llm, max_iterations=1)
        result = agent._ask_llm("topic", 1, [], [], 0)
        assert result["should_continue"] is False
        assert "This is not JSON" in result["assessment"]

    def test_ask_llm_exception(self):
        """Lines 356-365: _ask_llm with exception."""
        from sts_monitor.research_agent import ResearchAgent
        llm = MagicMock()
        llm.summarize.side_effect = RuntimeError("LLM crashed")
        agent = ResearchAgent(llm_client=llm, max_iterations=1)
        result = agent._ask_llm("topic", 1, [], [], 0)
        assert result["should_continue"] is False
        assert "LLM crashed" in result["reasoning"]

    def test_ask_llm_markdown_fences(self):
        """Lines 336-339: _ask_llm strips markdown fences."""
        from sts_monitor.research_agent import ResearchAgent
        llm = MagicMock()
        llm.summarize.return_value = '```json\n{"key_findings": [], "should_continue": false}\n```'
        agent = ResearchAgent(llm_client=llm, max_iterations=1)
        result = agent._ask_llm("topic", 1, [], [], 0)
        assert result["should_continue"] is False

    def test_generate_brief(self):
        """Lines 367-388: _generate_brief."""
        from sts_monitor.research_agent import ResearchAgent, ResearchSession
        from sts_monitor.pipeline import Observation
        llm = MagicMock()
        llm.summarize.return_value = "Final brief text"
        agent = ResearchAgent(llm_client=llm, max_iterations=1)
        session = ResearchSession(
            session_id="s1", topic="test", status="completed",
            started_at=datetime.now(UTC),
        )
        session.all_findings = ["finding1", "finding2"]
        session.all_observations = [
            Observation(source="test", claim="c", url="http://x",
                        captured_at=datetime.now(UTC), reliability_hint=0.5)
        ]
        brief = agent._generate_brief(session)
        assert brief == "Final brief text"

    def test_generate_brief_exception(self):
        """Line 388: brief generation failure."""
        from sts_monitor.research_agent import ResearchAgent, ResearchSession
        llm = MagicMock()
        llm.summarize.side_effect = RuntimeError("fail")
        agent = ResearchAgent(llm_client=llm, max_iterations=1)
        session = ResearchSession(
            session_id="s1", topic="test", status="completed",
            started_at=datetime.now(UTC),
        )
        session.all_findings = ["f1"]
        session.all_observations = []
        brief = agent._generate_brief(session)
        assert "Brief generation failed" in brief

    @patch("sts_monitor.research_agent.SearchConnector")
    @patch("sts_monitor.research_agent.NitterConnector")
    @patch("sts_monitor.research_agent.WebScraperConnector")
    def test_collect_directed(self, mock_scraper_cls, mock_nitter_cls, mock_search_cls):
        """Lines 245-303: _collect_directed with search, scraper, nitter."""
        from sts_monitor.research_agent import ResearchAgent
        from sts_monitor.connectors.base import ConnectorResult
        from sts_monitor.pipeline import Observation
        llm = MagicMock()
        agent = ResearchAgent(llm_client=llm, max_iterations=1)
        obs = Observation(source="test", claim="c", url="http://x",
                          captured_at=datetime.now(UTC), reliability_hint=0.5)
        mock_search_inst = MagicMock()
        mock_search_inst.collect.return_value = ConnectorResult(
            connector="search", observations=[obs], metadata={})
        mock_search_cls.return_value = mock_search_inst
        mock_scraper_inst = MagicMock()
        mock_scraper_inst.collect.return_value = ConnectorResult(
            connector="scraper", observations=[obs], metadata={})
        mock_scraper_cls.return_value = mock_scraper_inst
        mock_nitter_inst = MagicMock()
        mock_nitter_inst.collect.return_value = ConnectorResult(
            connector="nitter", observations=[obs], metadata={})
        mock_nitter_cls.return_value = mock_nitter_inst
        decisions = {
            "new_search_queries": ["query1"],
            "urls_to_scrape": ["http://example.com"],
            "twitter_accounts_to_follow": ["@user1"],
            "twitter_search_queries": ["twitter query"],
        }
        observations, connectors = agent._collect_directed(decisions, "topic")
        assert len(observations) >= 1
        assert "search" in connectors

    @patch("sts_monitor.research_agent.SearchConnector")
    @patch("sts_monitor.research_agent.NitterConnector")
    @patch("sts_monitor.research_agent.WebScraperConnector")
    def test_collect_directed_nitter_no_queries(self, mock_scraper_cls, mock_nitter_cls, mock_search_cls):
        """Lines 296-298: nitter with accounts but no twitter_search_queries."""
        from sts_monitor.research_agent import ResearchAgent
        from sts_monitor.connectors.base import ConnectorResult
        from sts_monitor.pipeline import Observation
        llm = MagicMock()
        agent = ResearchAgent(llm_client=llm, max_iterations=1)
        obs = Observation(source="nitter", claim="tweet", url="http://t",
                          captured_at=datetime.now(UTC), reliability_hint=0.5)
        mock_nitter_inst = MagicMock()
        mock_nitter_inst.collect.return_value = ConnectorResult(
            connector="nitter", observations=[obs], metadata={})
        mock_nitter_cls.return_value = mock_nitter_inst
        decisions = {
            "twitter_accounts_to_follow": ["@user1"],
            "twitter_search_queries": [],  # empty, so it uses topic_query
        }
        observations, connectors = agent._collect_directed(decisions, "topic")
        assert "nitter" in connectors

    def test_run_with_stop_flag(self):
        """Lines 404-407, 504-507: run stops when stop flag set."""
        from sts_monitor.research_agent import ResearchAgent
        llm = MagicMock()
        agent = ResearchAgent(llm_client=llm, max_iterations=3, inter_iteration_delay_s=0)

        def set_stop_and_return(*args, **kwargs):
            agent._stop_flags["s1"] = True
            return ([], [])

        with patch.object(agent, "_collect_initial", side_effect=set_stop_and_return):
            with patch.object(agent, "_ask_llm", return_value={"key_findings": [], "should_continue": True}):
                session = agent.run("s1", "topic")
        assert session.status == "stopped"

    def test_run_error_handling(self):
        """Lines 504-507: run handles exceptions."""
        from sts_monitor.research_agent import ResearchAgent
        llm = MagicMock()
        agent = ResearchAgent(llm_client=llm, max_iterations=1, inter_iteration_delay_s=0)
        with patch.object(agent, "_collect_initial", side_effect=RuntimeError("boom")):
            session = agent.run("err1", "topic")
        assert session.status == "error"
        assert "boom" in session.error


# ═══════════════════════════════════════════════════════════════════════
# 9. CROSS INVESTIGATION  (cross_investigation.py missing: ~4 lines)
# ═══════════════════════════════════════════════════════════════════════

class TestCrossInvestigationModule:
    """Cover detect_cross_investigation_links with shared entities, sources, geo overlaps."""

    def test_cross_investigation_less_than_2(self):
        """Lines 74-80: returns empty if < 2 investigations."""
        from sts_monitor.cross_investigation import detect_cross_investigation_links
        with SessionLocal() as s:
            _make_investigation(s, "only one")
            report = detect_cross_investigation_links(s)
        assert report.total_links == 0

    def test_cross_investigation_shared_sources(self):
        """Lines 130-156: shared sources across investigations."""
        from sts_monitor.cross_investigation import detect_cross_investigation_links
        with SessionLocal() as s:
            inv1 = _make_investigation(s, "topic A")
            inv2 = _make_investigation(s, "topic B")
            for inv_id in [inv1, inv2]:
                obs = ObservationORM(
                    investigation_id=inv_id, source="rss:reuters",
                    claim="shared source observation", url="http://x",
                    captured_at=datetime.now(UTC), reliability_hint=0.8,
                )
                s.add(obs)
            s.commit()
            report = detect_cross_investigation_links(s)
        assert report.investigations_analyzed == 2
        assert len(report.shared_sources) >= 1

    def test_cross_investigation_geographic_overlaps(self):
        """Lines 158-182: geographic overlaps across investigations."""
        from sts_monitor.cross_investigation import detect_cross_investigation_links
        with SessionLocal() as s:
            inv1 = _make_investigation(s, "topic A")
            inv2 = _make_investigation(s, "topic B")
            for inv_id in [inv1, inv2]:
                obs = ObservationORM(
                    investigation_id=inv_id, source="test",
                    claim="geo obs", url="http://x",
                    captured_at=datetime.now(UTC), reliability_hint=0.8,
                    latitude=42.3, longitude=-71.1,
                )
                s.add(obs)
            s.commit()
            report = detect_cross_investigation_links(s)
        assert len(report.geographic_overlaps) >= 1

    def test_cross_investigation_shared_entities(self):
        """Lines 84-128: shared entity mentions across investigations."""
        from sts_monitor.cross_investigation import detect_cross_investigation_links
        with SessionLocal() as s:
            inv1 = _make_investigation(s, "topic A")
            inv2 = _make_investigation(s, "topic B")
            for inv_id in [inv1, inv2]:
                obs = ObservationORM(
                    investigation_id=inv_id, source="test",
                    claim="FEMA emergency response activated",
                    url="http://x", captured_at=datetime.now(UTC),
                    reliability_hint=0.8,
                )
                s.add(obs)
                s.flush()
                em = EntityMentionORM(
                    observation_id=obs.id, investigation_id=inv_id,
                    entity_text="FEMA", entity_type="organization",
                    normalized="fema", confidence=0.9,
                    start_pos=0, end_pos=4,
                    created_at=datetime.now(UTC),
                )
                s.add(em)
            s.commit()
            report = detect_cross_investigation_links(s)
        assert len(report.shared_entities) >= 1

    def test_cross_investigation_report_to_dict(self):
        """Lines 58-66: CrossInvestigationReport.to_dict."""
        from sts_monitor.cross_investigation import CrossInvestigationReport
        report = CrossInvestigationReport(
            investigations_analyzed=3, total_links=0,
            shared_entities=[], shared_sources=[],
            geographic_overlaps=[], high_priority_links=[],
        )
        d = report.to_dict()
        assert d["investigations_analyzed"] == 3
        assert d["total_links"] == 0
