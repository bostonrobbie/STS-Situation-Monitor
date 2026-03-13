"""Tests for Tier 1-3 features: semantic search, cross-investigation links,
claim verification, geofencing, source network, pattern matching, intel briefs,
webhook ingestion, multi-LLM routing, and their API endpoints."""
from __future__ import annotations

import pytest
from datetime import UTC, datetime, timedelta
from starlette.testclient import TestClient

from sts_monitor.database import Base, engine
from sts_monitor.main import app

AUTH = {"X-API-Key": "change-me"}


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch):
    import sts_monitor.rate_limit as rl
    monkeypatch.setattr(rl, "RATE_LIMIT_ENABLED", False)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def investigation_id(client):
    resp = client.post(
        "/investigations",
        json={"topic": "Test Investigation", "priority": 50},
        headers=AUTH,
    )
    assert resp.status_code == 200
    return resp.json()["id"]


@pytest.fixture
def investigation_with_data(client, investigation_id):
    """Create investigation with simulated observations."""
    client.post(
        f"/investigations/{investigation_id}/ingest/simulated",
        json={"batch_size": 30, "include_noise": True},
        headers=AUTH,
    )
    return investigation_id


# ═══════════════════════════════════════════════════════════════════════
# Claim Verification (unit tests)
# ═══════════════════════════════════════════════════════════════════════

class TestClaimVerification:
    def test_verify_empty_observations(self):
        from sts_monitor.claim_verification import verify_investigation_claims
        report = verify_investigation_claims("inv1", "test", [])
        assert report.total_claims_analyzed == 0
        assert report.verified_true == 0
        assert report.method == "heuristic"

    def test_verify_with_contradictions(self):
        from sts_monitor.claim_verification import verify_investigation_claims
        obs = [
            {"claim": "The sky is blue", "source": "src1", "captured_at": datetime.now(UTC).isoformat()},
            {"claim": "The sky is blue confirmed", "source": "src2", "captured_at": datetime.now(UTC).isoformat()},
            {"claim": "The sky is NOT blue denied false", "source": "src3", "captured_at": datetime.now(UTC).isoformat()},
        ]
        report = verify_investigation_claims("inv1", "sky color", obs)
        assert report.total_claims_analyzed >= 1
        assert len(report.results) >= 1

    def test_claim_sentiment(self):
        from sts_monitor.claim_verification import _claim_sentiment
        assert _claim_sentiment("confirmed true verified") == "positive"
        assert _claim_sentiment("denied false debunked") == "negative"
        assert _claim_sentiment("something happened") == "neutral"

    def test_verify_claims_endpoint(self, client, investigation_with_data):
        resp = client.post(
            f"/investigations/{investigation_with_data}/verify-claims?max_claims=5",
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "total_claims_analyzed" in data

    def test_verify_claims_404(self, client):
        resp = client.post("/investigations/nonexistent/verify-claims", headers=AUTH)
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# Geofencing
# ═══════════════════════════════════════════════════════════════════════

class TestGeofencing:
    def test_haversine(self):
        from sts_monitor.geofence import _haversine_km
        # Boston to NYC ~305 km
        d = _haversine_km(42.3601, -71.0589, 40.7128, -74.0060)
        assert 300 < d < 315

    def test_builtin_zones(self):
        from sts_monitor.geofence import BUILTIN_ZONES
        names = [z.name for z in BUILTIN_ZONES]
        assert "Boston Metro" in names
        assert any("Ukraine" in n for n in names)

    def test_add_remove_custom_zone(self):
        from sts_monitor.geofence import add_zone, remove_zone, get_all_zones, GeoZone, _custom_zones
        _custom_zones.clear()
        z = GeoZone(name="Test Zone", center_lat=42.0, center_lon=-71.0, radius_km=10)
        add_zone(z)
        all_zones = get_all_zones()
        assert any(zz.name == "Test Zone" for zz in all_zones)
        removed = remove_zone("Test Zone")
        assert removed is True
        removed2 = remove_zone("Test Zone")
        assert removed2 is False

    def test_check_observations_in_zone(self):
        from sts_monitor.geofence import check_observations_against_zones, GeoZone
        zone = GeoZone(name="TestZone", center_lat=42.36, center_lon=-71.06, radius_km=5)
        obs = [
            {"claim": "Test event", "source": "test", "latitude": 42.361, "longitude": -71.059, "captured_at": datetime.now(UTC)},
            {"claim": "Far away event", "source": "test", "latitude": 10.0, "longitude": 10.0, "captured_at": datetime.now(UTC)},
        ]
        alerts = check_observations_against_zones(obs, [zone])
        assert len(alerts) >= 1
        assert alerts[0].zone_name == "TestZone"

    def test_list_geofences_endpoint(self, client):
        resp = client.get("/geofences", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        assert len(data["zones"]) > 0

    def test_create_delete_geofence_endpoint(self, client):
        resp = client.post(
            "/geofences?name=TestAPI&lat=42.0&lon=-71.0&radius_km=10&category=custom",
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "created"

        resp2 = client.delete("/geofences/TestAPI", headers=AUTH)
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "deleted"

    def test_geofence_check_endpoint(self, client, investigation_with_data):
        resp = client.post(
            f"/investigations/{investigation_with_data}/geofence-check",
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "alerts" in data
        assert "zone_activity" in data


# ═══════════════════════════════════════════════════════════════════════
# Source Network
# ═══════════════════════════════════════════════════════════════════════

class TestSourceNetwork:
    def test_analyze_empty(self):
        from sts_monitor.source_network import analyze_source_network
        report = analyze_source_network([])
        assert report.total_sources == 0
        assert report.total_observations == 0

    def test_analyze_with_observations(self):
        from sts_monitor.source_network import analyze_source_network
        now = datetime.now(UTC)
        obs = [
            {"claim": "Event A happened", "source": "Reuters", "captured_at": now},
            {"claim": "Event A happened", "source": "AP", "captured_at": now + timedelta(minutes=5)},
            {"claim": "Event B occurred", "source": "Reuters", "captured_at": now + timedelta(hours=1)},
            {"claim": "Event B occurred", "source": "BBC", "captured_at": now + timedelta(hours=2)},
        ]
        report = analyze_source_network(obs, co_report_window_hours=6)
        assert report.total_sources >= 2
        assert report.total_observations == 4

    def test_source_network_endpoint(self, client, investigation_with_data):
        resp = client.get(
            f"/investigations/{investigation_with_data}/source-network",
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "total_sources" in data


# ═══════════════════════════════════════════════════════════════════════
# Pattern Matching
# ═══════════════════════════════════════════════════════════════════════

class TestPatternMatching:
    def test_compute_signature_empty(self):
        from sts_monitor.pattern_matching import compute_current_signature
        sig = compute_current_signature([])
        assert sig.observation_velocity == [0, 0, 0, 0, 0]

    def test_compute_signature_with_data(self):
        from sts_monitor.pattern_matching import compute_current_signature
        now = datetime.now(UTC)
        obs = [
            {"claim": f"Event {i} escalating conflict attack crisis", "source": f"src{i % 3}", "captured_at": now - timedelta(hours=24 - i)}
            for i in range(20)
        ]
        sig = compute_current_signature(obs)
        assert any(v > 0 for v in sig.observation_velocity)
        assert sig.source_diversity > 0

    def test_known_patterns_exist(self):
        from sts_monitor.pattern_matching import KNOWN_PATTERNS
        assert len(KNOWN_PATTERNS) >= 6
        names = [p.name for p in KNOWN_PATTERNS]
        assert "Rapid Escalation (Pre-Conflict)" in names

    def test_analyze_patterns(self):
        from sts_monitor.pattern_matching import analyze_patterns
        now = datetime.now(UTC)
        obs = [
            {"claim": f"Crisis event {i} conflict escalation", "source": f"src{i % 2}", "captured_at": now - timedelta(hours=24 - i)}
            for i in range(15)
        ]
        result = analyze_patterns(obs)
        assert "current_signature" in result
        assert "matches" in result
        assert "escalation_score" in result

    def test_pattern_match_endpoint(self, client, investigation_with_data):
        resp = client.get(
            f"/investigations/{investigation_with_data}/pattern-match",
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "current_signature" in data
        assert "matches" in data


# ═══════════════════════════════════════════════════════════════════════
# Intel Briefs
# ═══════════════════════════════════════════════════════════════════════

class TestIntelBriefs:
    def test_generate_brief_empty(self):
        from sts_monitor.intel_briefs import generate_intel_brief
        brief = generate_intel_brief("inv1", "test topic", [], [])
        assert brief["investigation_id"] == "inv1"
        assert brief["topic"] == "test topic"
        assert brief["total_observations"] == 0
        assert "executive_summary" in brief

    def test_generate_brief_with_data(self):
        from sts_monitor.intel_briefs import generate_intel_brief
        now = datetime.now(UTC)
        obs = [
            {"claim": "Major event", "source": "Reuters", "captured_at": now},
            {"claim": "Follow-up", "source": "AP", "captured_at": now + timedelta(hours=1)},
        ]
        entities = [
            {"entity_text": "Boston", "entity_type": "LOCATION", "normalized": "boston"},
        ]
        brief = generate_intel_brief("inv1", "test", obs, entities)
        assert brief["total_observations"] == 2
        assert brief["source_count"] == 2
        assert len(brief["executive_summary"]) > 0

    def test_brief_to_markdown(self):
        from sts_monitor.intel_briefs import generate_intel_brief, brief_to_markdown
        brief = generate_intel_brief("inv1", "test", [], [])
        md = brief_to_markdown(brief)
        assert "# " in md
        assert "Executive Summary" in md
        assert "UNCLASSIFIED" in md

    def test_intel_brief_endpoint(self, client, investigation_with_data):
        resp = client.post(
            f"/investigations/{investigation_with_data}/intel-brief",
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "executive_summary" in data

    def test_intel_brief_markdown_endpoint(self, client, investigation_with_data):
        resp = client.post(
            f"/investigations/{investigation_with_data}/intel-brief/markdown",
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "markdown" in data
        assert "Executive Summary" in data["markdown"]


# ═══════════════════════════════════════════════════════════════════════
# Webhook Ingestion
# ═══════════════════════════════════════════════════════════════════════

class TestWebhookIngestion:
    def test_normalize_single(self):
        from sts_monitor.webhook_ingest import normalize_webhook_payload
        payload = {"claim": "Something happened", "source": "my-scraper", "url": "https://example.com"}
        result = normalize_webhook_payload(payload)
        assert len(result) == 1
        assert result[0]["claim"] == "Something happened"
        assert result[0]["source"] == "my-scraper"

    def test_normalize_batch(self):
        from sts_monitor.webhook_ingest import normalize_webhook_payload
        payload = {
            "observations": [
                {"claim": "Event 1", "source": "src1"},
                {"claim": "Event 2", "source": "src2"},
            ]
        }
        result = normalize_webhook_payload(payload)
        assert len(result) == 2

    def test_normalize_telegram(self):
        from sts_monitor.webhook_ingest import normalize_webhook_payload
        payload = {
            "message": {
                "text": "Breaking: something happened",
                "chat": {"title": "OSINT Channel"},
            }
        }
        result = normalize_webhook_payload(payload)
        assert len(result) == 1
        assert "telegram" in result[0]["source"]
        assert result[0]["claim"] == "Breaking: something happened"

    def test_normalize_rss_push(self):
        from sts_monitor.webhook_ingest import normalize_webhook_payload
        payload = {
            "items": [
                {"title": "Article 1", "link": "https://example.com/1"},
                {"title": "Article 2", "link": "https://example.com/2"},
            ]
        }
        result = normalize_webhook_payload(payload)
        assert len(result) == 2
        assert result[0]["url"] == "https://example.com/1"

    def test_validate_signature(self):
        from sts_monitor.webhook_ingest import validate_webhook_signature
        import hashlib
        import hmac as hmac_mod
        secret = "test-secret"
        body = b'{"test": true}'
        sig = hmac_mod.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert validate_webhook_signature(body, sig, secret) is True
        assert validate_webhook_signature(body, "wrong-sig", secret) is False

    def test_webhook_endpoint(self, client, investigation_id):
        resp = client.post(
            f"/investigations/{investigation_id}/webhook",
            json={"claim": "Test webhook observation", "source": "test-webhook"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ingested"
        assert data["observations_created"] == 1

    def test_webhook_batch_endpoint(self, client, investigation_id):
        resp = client.post(
            f"/investigations/{investigation_id}/webhook",
            json={
                "observations": [
                    {"claim": "Batch item 1", "source": "batch"},
                    {"claim": "Batch item 2", "source": "batch"},
                ]
            },
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.json()["observations_created"] == 2


# ═══════════════════════════════════════════════════════════════════════
# Multi-LLM Router
# ═══════════════════════════════════════════════════════════════════════

class TestMultiLLMRouter:
    def test_tier_classification(self):
        from sts_monitor.multi_llm import _classify_model_tier
        assert _classify_model_tier("phi3") == "fast"
        assert _classify_model_tier("llama3.1:8b") == "medium"
        assert _classify_model_tier("llama3.1:70b") == "large"
        assert _classify_model_tier("unknown-model") == "medium"

    def test_task_tiers(self):
        from sts_monitor.multi_llm import TASK_TIERS
        assert TASK_TIERS["entity_extraction"] == "fast"
        assert TASK_TIERS["analysis"] == "medium"
        assert TASK_TIERS["claim_verification"] == "large"

    def test_router_default_model(self):
        from sts_monitor.multi_llm import MultiLLMRouter
        router = MultiLLMRouter(default_model="test-model")
        # No models scanned, should return default
        model = router.get_model_for_task("analysis")
        assert model == "test-model"

    def test_model_info_to_dict(self):
        from sts_monitor.multi_llm import ModelInfo
        info = ModelInfo(name="test", size_gb=4.0, tier="medium", available=True)
        d = info.to_dict()
        assert d["name"] == "test"
        assert d["tier"] == "medium"
        assert d["available"] is True

    def test_llm_status_endpoint(self, client):
        resp = client.get("/llm/status", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "default_model" in data
        assert "task_routing" in data


# ═══════════════════════════════════════════════════════════════════════
# Cross-Investigation Links
# ═══════════════════════════════════════════════════════════════════════

class TestCrossInvestigation:
    def test_cross_links_endpoint(self, client):
        resp = client.get("/cross-investigation/links", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "investigations_analyzed" in data
        assert "total_links" in data

    def test_cross_links_with_data(self, client):
        # Create two investigations with overlapping entities
        resp1 = client.post("/investigations", json={"topic": "Topic A"}, headers=AUTH)
        id1 = resp1.json()["id"]
        resp2 = client.post("/investigations", json={"topic": "Topic B"}, headers=AUTH)
        id2 = resp2.json()["id"]

        # Ingest data into both
        client.post(f"/investigations/{id1}/ingest/simulated", json={"batch_size": 20}, headers=AUTH)
        client.post(f"/investigations/{id2}/ingest/simulated", json={"batch_size": 20}, headers=AUTH)

        resp = client.get("/cross-investigation/links", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["investigations_analyzed"] >= 2


# ═══════════════════════════════════════════════════════════════════════
# Semantic Search
# ═══════════════════════════════════════════════════════════════════════

class TestSemanticSearch:
    def test_health_endpoint(self, client):
        resp = client.get("/semantic-search/health", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_search_endpoint(self, client):
        resp = client.get("/semantic-search?query=test+event", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "test event"
        assert "results" in data
