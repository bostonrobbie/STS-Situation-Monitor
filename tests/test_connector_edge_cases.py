"""Edge-case tests for connectors with lower coverage."""
from __future__ import annotations

from unittest.mock import patch
import types

import pytest

from sts_monitor.connectors.webcams import (
    CURATED_CAMERAS,
    WebcamConnector,
    get_cameras_near,
    list_camera_regions,
)
from sts_monitor.connectors.rss import RSSConnector
from sts_monitor.connectors.gdelt import GDELTConnector
from sts_monitor.connectors.nws import NWSAlertConnector
from sts_monitor.connectors.adsb import ADSBExchangeConnector

pytestmark = pytest.mark.unit


# ── Shared fake HTTP helpers ─────────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload=None, text="", status_code=200):
        self._payload = payload
        self._text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._payload

    @property
    def text(self):
        return self._text


class _FakeClient:
    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, **kwargs):
        return self._response


def _patch_httpx(monkeypatch, module_path, response):
    monkeypatch.setattr(module_path, lambda **kwargs: _FakeClient(response))


# ═══════════════════════════════════════════════════════════════════════
# WebcamConnector
# ═══════════════════════════════════════════════════════════════════════


class TestWebcamCollectNoQuery:
    """collect() without query returns all curated cameras."""

    def test_returns_all_curated_cameras(self):
        connector = WebcamConnector()
        result = connector.collect()
        total = sum(len(cams) for cams in CURATED_CAMERAS.values())
        assert len(result.observations) == total
        assert result.metadata["total_cameras"] == total

    def test_each_observation_has_webcam_source(self):
        connector = WebcamConnector(regions=["ukraine"])
        result = connector.collect()
        for obs in result.observations:
            assert obs.source.startswith("webcam:")


class TestWebcamCollectWithQuery:
    """collect(query=...) filters cameras by name."""

    def test_query_kyiv_returns_matching(self):
        connector = WebcamConnector(regions=["ukraine"])
        result = connector.collect(query="Kyiv")
        assert len(result.observations) >= 1
        for obs in result.observations:
            assert "Kyiv" in obs.claim

    def test_query_nomatch_returns_empty(self):
        connector = WebcamConnector(regions=["ukraine"])
        result = connector.collect(query="xyznonexistent")
        assert len(result.observations) == 0


class TestWebcamWindyAPI:
    """Windy API integration via mocked httpx."""

    def test_windy_api_success(self, monkeypatch):
        windy_payload = {
            "webcams": [
                {
                    "webcamId": "12345",
                    "title": "Test Webcam",
                    "location": {
                        "latitude": 50.0,
                        "longitude": 30.0,
                        "city": "Kyiv",
                        "country": "Ukraine",
                    },
                    "images": {"current": {"preview": "https://img.example.com/preview.jpg"}},
                    "urls": {"detail": "https://windy.com/webcams/12345"},
                }
            ]
        }
        _patch_httpx(
            monkeypatch,
            "sts_monitor.connectors.webcams.httpx.Client",
            _FakeResponse(payload=windy_payload),
        )
        connector = WebcamConnector(
            windy_api_key="test-key",
            nearby_lat=50.0,
            nearby_lon=30.0,
            regions=[],
        )
        result = connector.collect()
        assert result.metadata.get("windy_count") == 1
        assert any("Test Webcam" in o.claim for o in result.observations)
        assert any(o.source == "webcam:windy" for o in result.observations)

    def test_windy_api_failure_sets_error(self, monkeypatch):
        _patch_httpx(
            monkeypatch,
            "sts_monitor.connectors.webcams.httpx.Client",
            _FakeResponse(status_code=500),
        )
        connector = WebcamConnector(
            windy_api_key="test-key",
            nearby_lat=50.0,
            nearby_lon=30.0,
            regions=["__nonexistent__"],
        )
        result = connector.collect()
        assert "windy_error" in result.metadata
        # No observations: nonexistent curated region and Windy API failed
        assert len(result.observations) == 0


class TestWebcamUtilities:
    """list_camera_regions and get_cameras_near."""

    def test_list_camera_regions_returns_all(self):
        regions = list_camera_regions()
        assert len(regions) == len(CURATED_CAMERAS)
        for r in regions:
            assert "region" in r
            assert "camera_count" in r
            assert r["camera_count"] > 0

    def test_get_cameras_near_known_location(self):
        # Near Kyiv
        cameras = get_cameras_near(50.45, 30.52, radius_km=200)
        assert len(cameras) >= 1
        assert any("Kyiv" in c["name"] for c in cameras)
        # Sorted by distance
        for i in range(len(cameras) - 1):
            assert cameras[i]["distance_km"] <= cameras[i + 1]["distance_km"]

    def test_get_cameras_near_remote_location(self):
        # Middle of the Pacific
        cameras = get_cameras_near(0.0, -160.0, radius_km=50)
        assert len(cameras) == 0


# ═══════════════════════════════════════════════════════════════════════
# RSSConnector
# ═══════════════════════════════════════════════════════════════════════


class TestRSSQueryFiltering:
    """collect() with query filtering — matching and non-matching entries."""

    def test_query_match_returns_entries(self, monkeypatch):
        fake_entry = {"title": "Explosion in Kyiv", "summary": "Large blast heard", "link": "https://example.com/1"}
        fake_parsed = types.SimpleNamespace(bozo=False, bozo_exception=None, entries=[fake_entry])
        monkeypatch.setattr("sts_monitor.connectors.rss.feedparser.parse", lambda *a, **kw: fake_parsed)

        connector = RSSConnector(feed_urls=["https://fake.feed/rss"])
        result = connector.collect(query="Kyiv")
        assert len(result.observations) == 1
        assert "Kyiv" in result.observations[0].claim

    def test_query_no_match_returns_empty(self, monkeypatch):
        fake_entry = {"title": "Weather forecast", "summary": "Sunny", "link": "https://example.com/2"}
        fake_parsed = types.SimpleNamespace(bozo=False, bozo_exception=None, entries=[fake_entry])
        monkeypatch.setattr("sts_monitor.connectors.rss.feedparser.parse", lambda *a, **kw: fake_parsed)

        connector = RSSConnector(feed_urls=["https://fake.feed/rss"])
        result = connector.collect(query="Kyiv")
        assert len(result.observations) == 0


class TestRSSParseWithRetry:
    """_parse_with_retry with bozo feed and retry backoff."""

    def test_bozo_feed_raises_after_retries(self, monkeypatch):
        exc = Exception("malformed XML")
        fake_parsed = types.SimpleNamespace(bozo=True, bozo_exception=exc, entries=[])
        monkeypatch.setattr("sts_monitor.connectors.rss.feedparser.parse", lambda *a, **kw: fake_parsed)
        monkeypatch.setattr("sts_monitor.connectors.rss.time.sleep", lambda _: None)

        connector = RSSConnector(feed_urls=["https://bad.feed/rss"], max_retries=2)
        parsed, meta = connector._parse_with_retry("https://bad.feed/rss")
        assert parsed is None
        assert "malformed XML" in str(meta)

    def test_retry_backoff_succeed_on_second(self, monkeypatch):
        exc = Exception("temporary error")
        bozo = types.SimpleNamespace(bozo=True, bozo_exception=exc, entries=[])
        good = types.SimpleNamespace(bozo=False, bozo_exception=None, entries=[])
        call_count = {"n": 0}

        def fake_parse(*a, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return bozo
            return good

        monkeypatch.setattr("sts_monitor.connectors.rss.feedparser.parse", fake_parse)
        monkeypatch.setattr("sts_monitor.connectors.rss.time.sleep", lambda _: None)

        connector = RSSConnector(feed_urls=[], max_retries=2)
        parsed, attempt = connector._parse_with_retry("https://flaky.feed/rss")
        assert parsed is not None
        assert attempt == 1  # succeeded on second attempt (index 1)


class TestRSSMixedFeeds:
    """collect() with one failed feed and one successful feed."""

    def test_mixed_feed_success_and_failure(self, monkeypatch):
        exc = Exception("parse error")
        bozo = types.SimpleNamespace(bozo=True, bozo_exception=exc, entries=[])
        good_entry = {"title": "Breaking news", "summary": "Details here", "link": "https://good.feed/1"}
        good_feed = types.SimpleNamespace(bozo=False, bozo_exception=None, entries=[good_entry])

        call_count = {"n": 0}

        def fake_parse(url, **kw):
            call_count["n"] += 1
            # First 3 calls (feed1 with retries) return bozo, then good
            if "bad" in url:
                return bozo
            return good_feed

        monkeypatch.setattr("sts_monitor.connectors.rss.feedparser.parse", fake_parse)
        monkeypatch.setattr("sts_monitor.connectors.rss.time.sleep", lambda _: None)

        connector = RSSConnector(
            feed_urls=["https://bad.feed/rss", "https://good.feed/rss"],
            max_retries=2,
        )
        result = connector.collect()
        assert len(result.observations) == 1
        assert len(result.metadata["failed_feeds"]) == 1
        assert result.metadata["failed_feeds"][0]["url"] == "https://bad.feed/rss"


# ═══════════════════════════════════════════════════════════════════════
# GDELTConnector
# ═══════════════════════════════════════════════════════════════════════


class TestGDELTEdgeCases:
    """Edge cases for GDELTConnector."""

    def test_http_error_returns_error_metadata(self, monkeypatch):
        _patch_httpx(
            monkeypatch,
            "sts_monitor.connectors.gdelt.httpx.Client",
            _FakeResponse(status_code=503),
        )
        connector = GDELTConnector()
        result = connector.collect(query="test")
        assert len(result.observations) == 0
        assert "error" in result.metadata

    def test_empty_articles_response(self, monkeypatch):
        _patch_httpx(
            monkeypatch,
            "sts_monitor.connectors.gdelt.httpx.Client",
            _FakeResponse(payload={"articles": []}),
        )
        connector = GDELTConnector()
        result = connector.collect(query="test")
        assert len(result.observations) == 0
        assert result.metadata["article_count"] == 0

    def test_no_query_returns_error(self):
        connector = GDELTConnector()
        result = connector.collect(query=None)
        assert len(result.observations) == 0
        assert "error" in result.metadata

    def test_query_filter_matches(self, monkeypatch):
        payload = {
            "articles": [
                {"url": "https://example.com/1", "title": "Earthquake in Turkey", "domain": "example.com"},
                {"url": "https://example.com/2", "title": "Stock market rally", "domain": "example.com"},
            ]
        }
        _patch_httpx(
            monkeypatch,
            "sts_monitor.connectors.gdelt.httpx.Client",
            _FakeResponse(payload=payload),
        )
        connector = GDELTConnector()
        result = connector.collect(query="earthquake")
        # GDELT query is sent server-side, both articles returned
        assert len(result.observations) == 2

    def test_null_articles_key(self, monkeypatch):
        _patch_httpx(
            monkeypatch,
            "sts_monitor.connectors.gdelt.httpx.Client",
            _FakeResponse(payload={}),
        )
        connector = GDELTConnector()
        result = connector.collect(query="test")
        assert len(result.observations) == 0


# ═══════════════════════════════════════════════════════════════════════
# NWSAlertConnector
# ═══════════════════════════════════════════════════════════════════════


def _nws_feature(event, headline, severity, area, geometry=None):
    """Helper to build a NWS GeoJSON feature."""
    return {
        "properties": {
            "id": f"urn:oid:{event.lower().replace(' ', '_')}",
            "event": event,
            "headline": headline,
            "description": "",
            "severity": severity,
            "certainty": "Observed",
            "urgency": "Immediate",
            "areaDesc": area,
            "senderName": "NWS Test",
            "effective": "2025-01-15T17:00:00Z",
            "expires": "2025-01-15T18:00:00Z",
            "@id": f"https://api.weather.gov/alerts/{event.lower()}",
        },
        "geometry": geometry,
    }


class TestNWSEdgeCases:
    """Edge cases for NWSAlertConnector."""

    def test_http_error(self, monkeypatch):
        _patch_httpx(
            monkeypatch,
            "sts_monitor.connectors.nws.httpx.Client",
            _FakeResponse(status_code=500),
        )
        connector = NWSAlertConnector()
        result = connector.collect()
        assert len(result.observations) == 0
        assert "error" in result.metadata

    def test_empty_features(self, monkeypatch):
        _patch_httpx(
            monkeypatch,
            "sts_monitor.connectors.nws.httpx.Client",
            _FakeResponse(payload={"features": []}),
        )
        connector = NWSAlertConnector()
        result = connector.collect()
        assert len(result.observations) == 0
        assert result.metadata["alert_count"] == 0

    def test_query_filtering(self, monkeypatch):
        payload = {
            "features": [
                _nws_feature("Tornado Warning", "Tornado Warning for OK", "Extreme", "Oklahoma"),
                _nws_feature("Flood Watch", "Flood Watch for TX", "Moderate", "Texas"),
            ]
        }
        _patch_httpx(
            monkeypatch,
            "sts_monitor.connectors.nws.httpx.Client",
            _FakeResponse(payload=payload),
        )
        connector = NWSAlertConnector(severity_filter="Extreme,Severe,Moderate")
        result = connector.collect(query="Tornado")
        assert len(result.observations) == 1
        assert "Tornado" in result.observations[0].claim

    def test_severity_extreme_magnitude_5(self, monkeypatch):
        payload = {
            "features": [
                _nws_feature(
                    "Tornado Warning",
                    "Tornado Warning",
                    "Extreme",
                    "OK",
                    geometry={"type": "Point", "coordinates": [-97.5, 35.5]},
                ),
            ]
        }
        _patch_httpx(
            monkeypatch,
            "sts_monitor.connectors.nws.httpx.Client",
            _FakeResponse(payload=payload),
        )
        connector = NWSAlertConnector()
        result = connector.collect()
        geo = result.metadata["geo_events"]
        assert len(geo) == 1
        assert geo[0]["magnitude"] == 5

    def test_severity_severe_magnitude_4(self, monkeypatch):
        payload = {
            "features": [
                _nws_feature(
                    "Severe Thunderstorm",
                    "Severe Thunderstorm Warning",
                    "Severe",
                    "KS",
                    geometry={"type": "Point", "coordinates": [-97.0, 38.0]},
                ),
            ]
        }
        _patch_httpx(
            monkeypatch,
            "sts_monitor.connectors.nws.httpx.Client",
            _FakeResponse(payload=payload),
        )
        connector = NWSAlertConnector()
        result = connector.collect()
        geo = result.metadata["geo_events"]
        assert len(geo) == 1
        assert geo[0]["magnitude"] == 4

    def test_severity_moderate_magnitude_3(self, monkeypatch):
        payload = {
            "features": [
                _nws_feature(
                    "Winter Storm",
                    "Winter Storm Watch",
                    "Moderate",
                    "CO",
                    geometry={"type": "Point", "coordinates": [-105.0, 39.7]},
                ),
            ]
        }
        _patch_httpx(
            monkeypatch,
            "sts_monitor.connectors.nws.httpx.Client",
            _FakeResponse(payload=payload),
        )
        connector = NWSAlertConnector(severity_filter="Moderate")
        result = connector.collect()
        geo = result.metadata["geo_events"]
        assert len(geo) == 1
        assert geo[0]["magnitude"] == 3

    def test_severity_minor_magnitude_2(self, monkeypatch):
        payload = {
            "features": [
                _nws_feature(
                    "Frost Advisory",
                    "Frost Advisory",
                    "Minor",
                    "VA",
                    geometry={"type": "Point", "coordinates": [-77.4, 37.5]},
                ),
            ]
        }
        _patch_httpx(
            monkeypatch,
            "sts_monitor.connectors.nws.httpx.Client",
            _FakeResponse(payload=payload),
        )
        connector = NWSAlertConnector(severity_filter="Minor")
        result = connector.collect()
        geo = result.metadata["geo_events"]
        assert len(geo) == 1
        assert geo[0]["magnitude"] == 2

    def test_severity_reliability_mapping(self, monkeypatch):
        """Verify severity -> reliability_hint mapping."""
        for severity, expected in [("Extreme", 0.95), ("Severe", 0.85), ("Moderate", 0.70), ("Minor", 0.55)]:
            payload = {
                "features": [
                    _nws_feature("Test Event", f"Test {severity}", severity, "TX",
                                 geometry={"type": "Point", "coordinates": [-97.0, 32.0]}),
                ]
            }
            _patch_httpx(
                monkeypatch,
                "sts_monitor.connectors.nws.httpx.Client",
                _FakeResponse(payload=payload),
            )
            connector = NWSAlertConnector(severity_filter=severity)
            result = connector.collect()
            assert len(result.observations) == 1
            assert result.observations[0].reliability_hint == expected


# ═══════════════════════════════════════════════════════════════════════
# ADSBExchangeConnector
# ═══════════════════════════════════════════════════════════════════════


def _adsb_aircraft(hex_code, callsign, lat, lon, alt_baro=35000, gs=450, squawk="1200", origin="United States"):
    """Helper to build an ADS-B aircraft dict."""
    return {
        "hex": hex_code,
        "flight": callsign,
        "lat": lat,
        "lon": lon,
        "alt_baro": alt_baro,
        "gs": gs,
        "track": 90,
        "vert_rate": 0,
        "squawk": squawk,
        "category": "",
        "t": origin,
    }


class TestADSBEdgeCases:
    """Edge cases for ADSBExchangeConnector."""

    def test_military_callsign_matching(self, monkeypatch):
        payload = {
            "ac": [
                _adsb_aircraft("ae1234", "RCH501", 40.0, -30.0),
                _adsb_aircraft("a12345", "UAL123", 41.0, -31.0),
            ]
        }
        _patch_httpx(
            monkeypatch,
            "sts_monitor.connectors.adsb.httpx.Client",
            _FakeResponse(payload=payload),
        )
        connector = ADSBExchangeConnector(military_only=True)
        result = connector.collect()
        # Only military aircraft should appear
        assert len(result.observations) == 1
        assert "RCH501" in result.observations[0].claim
        assert "MILITARY" in result.observations[0].claim
        assert result.metadata["military_count"] == 1

    def test_altitude_zero_ground_level(self, monkeypatch):
        payload = {
            "ac": [
                _adsb_aircraft("a00001", "DAL100", 33.9, -118.4, alt_baro=0, gs=10),
            ]
        }
        _patch_httpx(
            monkeypatch,
            "sts_monitor.connectors.adsb.httpx.Client",
            _FakeResponse(payload=payload),
        )
        connector = ADSBExchangeConnector()
        result = connector.collect()
        assert len(result.observations) == 1
        # alt=0 is falsy so it should show "ground"
        assert "ground" in result.observations[0].claim

    def test_none_altitude_fields(self, monkeypatch):
        payload = {
            "ac": [
                _adsb_aircraft("a00002", "SWA200", 34.0, -118.0, alt_baro=None, gs=None),
            ]
        }
        _patch_httpx(
            monkeypatch,
            "sts_monitor.connectors.adsb.httpx.Client",
            _FakeResponse(payload=payload),
        )
        connector = ADSBExchangeConnector()
        result = connector.collect()
        assert len(result.observations) == 1
        assert "ground" in result.observations[0].claim

    def test_query_filtering_match(self, monkeypatch):
        payload = {
            "ac": [
                _adsb_aircraft("ae5678", "FORTE10", 40.0, -30.0),
                _adsb_aircraft("a11111", "UAL999", 41.0, -31.0),
            ]
        }
        _patch_httpx(
            monkeypatch,
            "sts_monitor.connectors.adsb.httpx.Client",
            _FakeResponse(payload=payload),
        )
        connector = ADSBExchangeConnector()
        result = connector.collect(query="FORTE")
        assert len(result.observations) == 1
        assert "FORTE10" in result.observations[0].claim

    def test_query_filtering_no_match(self, monkeypatch):
        payload = {
            "ac": [
                _adsb_aircraft("a00003", "UAL500", 40.0, -30.0),
            ]
        }
        _patch_httpx(
            monkeypatch,
            "sts_monitor.connectors.adsb.httpx.Client",
            _FakeResponse(payload=payload),
        )
        connector = ADSBExchangeConnector()
        result = connector.collect(query="FORTE")
        assert len(result.observations) == 0

    def test_emergency_squawk_detected(self, monkeypatch):
        payload = {
            "ac": [
                _adsb_aircraft("a00004", "AAL100", 35.0, -80.0, squawk="7700"),
            ]
        }
        _patch_httpx(
            monkeypatch,
            "sts_monitor.connectors.adsb.httpx.Client",
            _FakeResponse(payload=payload),
        )
        connector = ADSBExchangeConnector()
        result = connector.collect()
        assert len(result.observations) == 1
        assert "EMERGENCY" in result.observations[0].claim
        assert result.observations[0].reliability_hint == 0.95

    def test_missing_lat_lon_skipped(self, monkeypatch):
        payload = {
            "ac": [
                {"hex": "abc", "flight": "TST1", "lat": None, "lon": None, "alt_baro": 100, "gs": 100, "squawk": "", "t": "US"},
                _adsb_aircraft("a99999", "TST2", 40.0, -30.0),
            ]
        }
        _patch_httpx(
            monkeypatch,
            "sts_monitor.connectors.adsb.httpx.Client",
            _FakeResponse(payload=payload),
        )
        connector = ADSBExchangeConnector()
        result = connector.collect()
        # Only the one with valid lat/lon
        assert len(result.observations) == 1
        assert "TST2" in result.observations[0].claim

    def test_http_error_returns_error_metadata(self, monkeypatch):
        monkeypatch.setattr(
            "sts_monitor.connectors.adsb.httpx.Client",
            lambda **kwargs: _raise_client(),
        )

        class _ErrorClient:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def get(self, url, **kw):
                raise ConnectionError("network down")

        monkeypatch.setattr(
            "sts_monitor.connectors.adsb.httpx.Client",
            lambda **kwargs: _ErrorClient(),
        )
        connector = ADSBExchangeConnector()
        result = connector.collect()
        assert len(result.observations) == 0
        assert "error" in result.metadata
