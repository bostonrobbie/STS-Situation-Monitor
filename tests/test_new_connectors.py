"""Tests for ADS-B, marine, Telegram, Internet Archive connectors and privacy module."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from sts_monitor.connectors.adsb import ADSBExchangeConnector
from sts_monitor.connectors.marine import MarineTrafficConnector
from sts_monitor.connectors.telegram import TelegramConnector
from sts_monitor.connectors.archive import InternetArchiveConnector, _extract_domain
from sts_monitor.privacy import (
    PrivacyConfig,
    build_private_client,
    get_privacy_headers,
    get_proxy_url,
    get_random_ua,
    _USER_AGENTS,
)

pytestmark = pytest.mark.unit


# ── Shared fake HTTP helpers ──────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload=None, text: str = "", status_code: int = 200) -> None:
        self._payload = payload
        self._text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._payload

    @property
    def text(self) -> str:
        return self._text


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url: str, **kwargs):
        return self._response


# ── Privacy module tests ──────────────────────────────────────────────────


class TestPrivacyConfig:
    def test_defaults(self):
        cfg = PrivacyConfig()
        assert cfg.rotate_ua is True
        assert cfg.use_tor is False
        assert cfg.strip_referrer is True
        assert cfg.add_jitter is True
        assert cfg.proxy_url is None

    def test_tor_url(self):
        cfg = PrivacyConfig(use_tor=True)
        assert get_proxy_url(cfg) == "socks5://127.0.0.1:9050"

    def test_custom_proxy_overrides_tor(self):
        cfg = PrivacyConfig(use_tor=True, proxy_url="http://myproxy:8080")
        assert get_proxy_url(cfg) == "http://myproxy:8080"

    def test_no_proxy(self):
        cfg = PrivacyConfig(use_tor=False)
        assert get_proxy_url(cfg) is None


class TestPrivacyHeaders:
    def test_user_agent_rotation(self):
        cfg = PrivacyConfig(rotate_ua=True)
        headers = get_privacy_headers(cfg)
        assert headers["User-Agent"] in _USER_AGENTS

    def test_user_agent_fixed(self):
        cfg = PrivacyConfig(rotate_ua=False)
        headers = get_privacy_headers(cfg)
        assert headers["User-Agent"] == _USER_AGENTS[0]

    def test_referrer_stripped(self):
        cfg = PrivacyConfig(strip_referrer=True)
        headers = get_privacy_headers(cfg)
        assert headers.get("Referer") == ""

    def test_dnt_present(self):
        cfg = PrivacyConfig()
        headers = get_privacy_headers(cfg)
        assert headers.get("DNT") == "1"


class TestBuildPrivateClient:
    def test_returns_httpx_client(self):
        import httpx
        cfg = PrivacyConfig()
        client = build_private_client(cfg)
        assert isinstance(client, httpx.Client)
        client.close()

    def test_none_config_uses_defaults(self):
        import httpx
        client = build_private_client(None)
        assert isinstance(client, httpx.Client)
        client.close()


class TestGetRandomUA:
    def test_returns_string(self):
        ua = get_random_ua()
        assert isinstance(ua, str)
        assert len(ua) > 10

    def test_returns_known_ua(self):
        ua = get_random_ua()
        assert ua in _USER_AGENTS


# ── ADS-B connector tests ─────────────────────────────────────────────────


class TestADSBConnector:
    # ADS-B fi API uses "ac" key for aircraft array
    def _make_ac(self, **kwargs) -> dict:
        return kwargs

    def test_empty_response(self, monkeypatch):
        connector = ADSBExchangeConnector(center_lat=40.0, center_lon=-74.0, radius_nm=100)
        fake_resp = _FakeResponse(payload={"ac": []})
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        result = connector.collect()
        assert result.connector == "adsb"
        assert result.observations == []

    def test_civilian_aircraft_collected(self, monkeypatch):
        connector = ADSBExchangeConnector(center_lat=40.0, center_lon=-74.0, radius_nm=100)
        aircraft = [
            {
                "hex": "a1b2c3",
                "flight": "UAL123",
                "lat": 40.5,
                "lon": -74.5,
                "alt_baro": 35000,
                "gs": 450,
                "t": "A320",
                "r": "N12345",
            }
        ]
        fake_resp = _FakeResponse(payload={"ac": aircraft})
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        result = connector.collect()
        assert len(result.observations) == 1
        assert "UAL123" in result.observations[0].claim

    def test_military_aircraft_detected(self, monkeypatch):
        connector = ADSBExchangeConnector(center_lat=40.0, center_lon=-74.0, radius_nm=100)
        aircraft = [
            {
                "hex": "ae1234",  # US military hex prefix
                "flight": "RCH001",
                "lat": 38.0,
                "lon": -77.0,
                "alt_baro": 30000,
                "gs": 500,
            }
        ]
        fake_resp = _FakeResponse(payload={"ac": aircraft})
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        result = connector.collect()
        assert len(result.observations) == 1
        geo = result.metadata.get("geo_events", [])
        assert len(geo) == 1
        assert geo[0]["layer"] == "aircraft"

    def test_military_only_filter(self, monkeypatch):
        connector = ADSBExchangeConnector(
            center_lat=40.0, center_lon=-74.0, radius_nm=100, military_only=True
        )
        aircraft = [
            {"hex": "a1b2c3", "flight": "UAL123", "lat": 40.5, "lon": -74.5},  # civilian
            {"hex": "ae1234", "flight": "RCH001", "lat": 38.0, "lon": -77.0},  # military
        ]
        fake_resp = _FakeResponse(payload={"ac": aircraft})
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        result = connector.collect()
        # Only military aircraft should be in results
        for obs in result.observations:
            assert "RCH001" in obs.claim or "ae1234" in obs.claim.lower()

    def test_http_error_returns_empty(self, monkeypatch):
        connector = ADSBExchangeConnector(center_lat=40.0, center_lon=-74.0, radius_nm=100)
        fake_resp = _FakeResponse(status_code=500)
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        result = connector.collect()
        assert result.observations == []
        assert "error" in result.metadata

    def test_geo_events_have_required_fields(self, monkeypatch):
        connector = ADSBExchangeConnector(center_lat=40.0, center_lon=-74.0, radius_nm=100)
        aircraft = [
            {
                "hex": "ae5678",
                "flight": "HOMER01",
                "lat": 35.0,
                "lon": -80.0,
                "alt_baro": 25000,
                "gs": 400,
            }
        ]
        fake_resp = _FakeResponse(payload={"ac": aircraft})
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        result = connector.collect()
        geo = result.metadata.get("geo_events", [])
        for g in geo:
            assert "latitude" in g
            assert "longitude" in g
            assert "layer" in g
            assert g["layer"] == "aircraft"


# ── Marine connector tests ────────────────────────────────────────────────


class TestMarineConnector:
    def test_empty_response(self, monkeypatch):
        connector = MarineTrafficConnector()
        fake_resp = _FakeResponse(payload=[])
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        result = connector.collect()
        assert result.connector == "marine"
        assert result.observations == []

    def test_vessel_collected(self, monkeypatch):
        connector = MarineTrafficConnector()
        vessels = [
            {
                "mmsi": "123456789",
                "name": "EVER GIVEN",
                "lat": "30.5",
                "lon": "32.5",
                "speed": "5",
                "course": "180",
                "ship_type": "70",
                "country": "PA",
            }
        ]
        fake_resp = _FakeResponse(payload=vessels)
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        result = connector.collect()
        assert len(result.observations) >= 1
        assert "EVER GIVEN" in result.observations[0].claim

    def test_naval_vessel_flagged(self, monkeypatch):
        connector = MarineTrafficConnector()
        vessels = [
            {
                "mmsi": "338123456",
                "name": "USS NIMITZ",
                "lat": "25.0",
                "lon": "-70.0",
                "speed": "15",
                "course": "90",
                "ship_type": "35",  # military
            }
        ]
        fake_resp = _FakeResponse(payload=vessels)
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        result = connector.collect()
        assert len(result.observations) == 1
        geo = result.metadata.get("geo_events", [])
        assert len(geo) == 1
        assert geo[0]["layer"] == "maritime"

    def test_geo_events_have_required_fields(self, monkeypatch):
        connector = MarineTrafficConnector()
        vessels = [
            {
                "mmsi": "111222333",
                "name": "HMS QUEEN ELIZABETH",
                "lat": "51.0",
                "lon": "-1.0",
                "speed": "10",
                "ship_type": "35",
            }
        ]
        fake_resp = _FakeResponse(payload=vessels)
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        result = connector.collect()
        geo = result.metadata.get("geo_events", [])
        for g in geo:
            assert "latitude" in g
            assert "longitude" in g
            assert "layer" in g

    def test_http_error_returns_empty(self, monkeypatch):
        connector = MarineTrafficConnector()
        fake_resp = _FakeResponse(status_code=503)
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        result = connector.collect()
        assert result.observations == []


# ── Telegram connector tests ──────────────────────────────────────────────


FAKE_TELEGRAM_HTML = """
<html><body>
<div class="tgme_widget_message_wrap">
  <div class="tgme_widget_message_text">Breaking: Major earthquake in Turkey. 7.8 magnitude.</div>
  <time datetime="2024-02-06T04:17:00+00:00" class="time">04:17</time>
</div>
<div class="tgme_widget_message_wrap">
  <div class="tgme_widget_message_text">Satellite images confirm troop movements near border.</div>
  <time datetime="2024-02-06T05:00:00+00:00" class="time">05:00</time>
</div>
</body></html>
"""


_FAKE_CHANNELS = [
    {"handle": "testchannel", "name": "Test Channel", "category": "osint"},
]
_FAKE_GEO_CHANNEL = [
    {"handle": "geoconfirmed", "name": "GeoConfirmed", "category": "conflict"},
]


class TestTelegramConnector:
    def test_parses_posts(self, monkeypatch):
        connector = TelegramConnector(channels=_FAKE_CHANNELS, per_channel_limit=10)
        fake_resp = _FakeResponse(text=FAKE_TELEGRAM_HTML)
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        result = connector.collect()
        assert result.connector == "telegram"
        assert len(result.observations) >= 1

    def test_query_filter(self, monkeypatch):
        connector = TelegramConnector(channels=_FAKE_CHANNELS, per_channel_limit=10)
        fake_resp = _FakeResponse(text=FAKE_TELEGRAM_HTML)
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        result = connector.collect(query="earthquake")
        # Should include earthquake post
        assert any("earthquake" in o.claim.lower() or "Turkey" in o.claim for o in result.observations)

    def test_empty_html_returns_empty(self, monkeypatch):
        connector = TelegramConnector(
            channels=[{"handle": "empty", "name": "Empty", "category": "osint"}],
            per_channel_limit=10,
        )
        fake_resp = _FakeResponse(text="<html><body></body></html>")
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        result = connector.collect()
        assert result.observations == []

    def test_http_error_handled(self, monkeypatch):
        connector = TelegramConnector(channels=_FAKE_CHANNELS)
        fake_resp = _FakeResponse(status_code=404)
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        result = connector.collect()
        # Should not raise, return empty
        assert isinstance(result.observations, list)

    def test_source_name(self, monkeypatch):
        connector = TelegramConnector(channels=_FAKE_GEO_CHANNEL)
        fake_resp = _FakeResponse(text=FAKE_TELEGRAM_HTML)
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        result = connector.collect()
        if result.observations:
            assert "telegram" in result.observations[0].source


# ── Internet Archive connector tests ──────────────────────────────────────


FAKE_CDX_RESPONSE = [
    ["timestamp", "original", "mimetype", "statuscode", "length"],
    ["20240101120000", "http://example.com/page", "text/html", "200", "5000"],
]

FAKE_PAGE_HTML = """
<html><body>
<script>var x = 1;</script>
<p>This is example page content about a major news event.</p>
<style>.foo{color:red}</style>
<p>Additional information about the situation.</p>
</body></html>
"""


class _MultiResponseClient:
    """Fake client returning different responses for CDX vs page fetches."""

    def __init__(self, cdx_resp, page_resp):
        self._cdx_resp = cdx_resp
        self._page_resp = page_resp

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url: str, **kwargs):
        if "cdx" in url:
            return self._cdx_resp
        return self._page_resp


class TestInternetArchiveConnector:
    def test_basic_collection(self, monkeypatch):
        connector = InternetArchiveConnector(urls_to_check=["http://example.com/page"])
        cdx_resp = _FakeResponse(payload=FAKE_CDX_RESPONSE)
        page_resp = _FakeResponse(text=FAKE_PAGE_HTML)
        client = _MultiResponseClient(cdx_resp, page_resp)
        monkeypatch.setattr(connector, "_build_client", lambda: client)
        result = connector.collect()
        assert result.connector == "archive"
        assert len(result.observations) >= 1

    def test_extracts_text_strips_scripts(self, monkeypatch):
        connector = InternetArchiveConnector(urls_to_check=["http://example.com/page"])
        cdx_resp = _FakeResponse(payload=FAKE_CDX_RESPONSE)
        page_resp = _FakeResponse(text=FAKE_PAGE_HTML)
        client = _MultiResponseClient(cdx_resp, page_resp)
        monkeypatch.setattr(connector, "_build_client", lambda: client)
        result = connector.collect()
        if result.observations:
            text = result.observations[0].claim
            assert "var x" not in text  # script stripped
            assert ".foo" not in text   # style stripped
            assert "content" in text.lower()

    def test_query_filter(self, monkeypatch):
        connector = InternetArchiveConnector(
            urls_to_check=["http://example.com/page"],
            search_query="news event",
        )
        cdx_resp = _FakeResponse(payload=FAKE_CDX_RESPONSE)
        page_resp = _FakeResponse(text=FAKE_PAGE_HTML)
        client = _MultiResponseClient(cdx_resp, page_resp)
        monkeypatch.setattr(connector, "_build_client", lambda: client)
        result = connector.collect()
        assert len(result.observations) >= 1  # "news event" is in the page

    def test_query_filter_drops_irrelevant(self, monkeypatch):
        connector = InternetArchiveConnector(
            urls_to_check=["http://example.com/page"],
            search_query="xyzzy_not_in_page",
        )
        cdx_resp = _FakeResponse(payload=FAKE_CDX_RESPONSE)
        page_resp = _FakeResponse(text=FAKE_PAGE_HTML)
        client = _MultiResponseClient(cdx_resp, page_resp)
        monkeypatch.setattr(connector, "_build_client", lambda: client)
        result = connector.collect()
        assert result.observations == []

    def test_empty_cdx_returns_empty(self, monkeypatch):
        connector = InternetArchiveConnector(urls_to_check=["http://example.com/page"])
        cdx_resp = _FakeResponse(payload=[])
        client = _MultiResponseClient(cdx_resp, _FakeResponse(text=""))
        monkeypatch.setattr(connector, "_build_client", lambda: client)
        result = connector.collect()
        assert result.observations == []

    def test_reliability_hint(self, monkeypatch):
        connector = InternetArchiveConnector(urls_to_check=["http://example.com/page"])
        cdx_resp = _FakeResponse(payload=FAKE_CDX_RESPONSE)
        page_resp = _FakeResponse(text=FAKE_PAGE_HTML)
        client = _MultiResponseClient(cdx_resp, page_resp)
        monkeypatch.setattr(connector, "_build_client", lambda: client)
        result = connector.collect()
        for obs in result.observations:
            assert obs.reliability_hint == pytest.approx(0.65)


class TestExtractDomain:
    def test_basic_domain(self):
        assert _extract_domain("https://www.example.com/page") == "example.com"

    def test_strips_www(self):
        assert _extract_domain("http://www.bbc.co.uk/news") == "bbc.co.uk"

    def test_no_scheme(self):
        assert _extract_domain("example.com/path") == "example.com"

    def test_with_query(self):
        assert _extract_domain("https://example.com?foo=bar") == "example.com"

    def test_uppercase_normalized(self):
        assert _extract_domain("HTTPS://Example.COM/page") == "example.com"
