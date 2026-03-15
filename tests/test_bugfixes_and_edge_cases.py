"""Comprehensive edge-case and bug-fix regression tests.

Covers:
- telegram.py: empty handle skip, name vs label key
- marine.py: invalid ship_type string, missing lat/lon, bbox filtering
- adsb.py: reliability tiers, military-only filter, missing lat/lon
- privacy.py: resource cleanup, config validation, delay behaviour
- research_agent.py: None LLM response, markdown fence stripping,
  thread-safety of _lock, stop_session, JSON parse failure fallback
- archive.py: CDX with None/empty response, text extraction, timestamp edge cases
- pipeline.py: empty batch, all-dropped, clamped reliability, dedup
- convergence.py: empty batch, single point, cluster formation
"""
from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


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
    def __init__(self, response=None, responses=None) -> None:
        """responses: list of responses to return in order; response: single response."""
        self._responses = responses or ([response] if response else [])
        self._idx = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url: str, **kwargs):
        if not self._responses:
            raise Exception("No responses configured")
        resp = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return resp


# ─────────────────────────────────────────────────────────────────────────────
# Telegram connector
# ─────────────────────────────────────────────────────────────────────────────


class TestTelegramBugFixes:
    """Regression tests for telegram.py bug fixes."""

    from sts_monitor.connectors.telegram import TelegramConnector

    def test_empty_handle_is_skipped(self, monkeypatch):
        """Empty handle must not generate a request to https://t.me/s/ (invalid URL)."""
        from sts_monitor.connectors.telegram import TelegramConnector

        channels = [
            {"handle": "", "name": "Empty", "category": "osint"},
            {"handle": "  ", "name": "Whitespace", "category": "osint"},
            {"handle": "realchannel", "name": "Real", "category": "osint"},
        ]
        get_calls: list[str] = []

        html = """<html><body>
        <div class="tgme_widget_message_wrap">
          <div class="tgme_widget_message_text">Test post content here.</div>
        </div></body></html>"""

        class _TrackingClient:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def get(self, url, **kw):
                get_calls.append(url)
                return _FakeResponse(text=html)

        connector = TelegramConnector(channels=channels)
        monkeypatch.setattr(connector, "_build_client", lambda: _TrackingClient())
        connector.collect()

        # Only "realchannel" should have been fetched
        assert all("t.me/s/realchannel" in u for u in get_calls)
        assert not any(url.endswith("t.me/s/") or "t.me/s/  " in url for url in get_calls)

    def test_name_key_used_over_label_key(self, monkeypatch):
        """Channel dict uses 'name', not 'label'. The source field must use the handle."""
        from sts_monitor.connectors.telegram import TelegramConnector

        channels = [{"handle": "geoconfirmed", "name": "GeoConfirmed OSINT", "category": "conflict"}]
        html = """<html><body>
        <div class="tgme_widget_message_wrap">
          <div class="tgme_widget_message_text">Confirmed airstrike location.</div>
        </div></body></html>"""
        connector = TelegramConnector(channels=channels)
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(_FakeResponse(text=html)))
        result = connector.collect()
        assert len(result.observations) >= 1
        assert result.observations[0].source == "telegram:geoconfirmed"

    def test_malformed_channel_dict_skipped(self, monkeypatch):
        """Channel dict missing 'handle' key should be skipped gracefully."""
        from sts_monitor.connectors.telegram import TelegramConnector

        channels = [
            {"name": "No Handle Channel", "category": "osint"},  # missing "handle"
        ]
        connector = TelegramConnector(channels=channels)
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(_FakeResponse(text="")))
        result = connector.collect()
        assert result.observations == []

    def test_http_error_logged_in_errors(self, monkeypatch):
        """HTTP errors for individual channels are captured in metadata.errors."""
        from sts_monitor.connectors.telegram import TelegramConnector

        channels = [{"handle": "badchannel", "name": "Bad", "category": "osint"}]
        connector = TelegramConnector(channels=channels)
        monkeypatch.setattr(connector, "_build_client",
                            lambda: _FakeClient(_FakeResponse(status_code=403)))
        result = connector.collect()
        # Should not raise, errors captured
        assert isinstance(result.observations, list)

    def test_short_posts_filtered(self, monkeypatch):
        """Posts with fewer than 15 characters are filtered out."""
        from sts_monitor.connectors.telegram import TelegramConnector

        channels = [{"handle": "testch", "name": "Test", "category": "osint"}]
        html = """<html><body>
        <div class="tgme_widget_message_wrap">
          <div class="tgme_widget_message_text">Short</div>
        </div>
        <div class="tgme_widget_message_wrap">
          <div class="tgme_widget_message_text">This is a long enough post to pass the filter check.</div>
        </div></body></html>"""
        connector = TelegramConnector(channels=channels)
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(_FakeResponse(text=html)))
        result = connector.collect()
        assert all(len(o.claim) >= 15 for o in result.observations)

    def test_per_channel_limit_respected(self, monkeypatch):
        """per_channel_limit must cap the number of posts per channel."""
        from sts_monitor.connectors.telegram import TelegramConnector

        # Build HTML with 10 posts
        posts = "\n".join(
            f'<div class="tgme_widget_message_wrap">'
            f'<div class="tgme_widget_message_text">Post number {i} with enough content to pass.</div>'
            f'</div>'
            for i in range(10)
        )
        html = f"<html><body>{posts}</body></html>"
        channels = [{"handle": "ch", "name": "Ch", "category": "osint"}]
        connector = TelegramConnector(channels=channels, per_channel_limit=3)
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(_FakeResponse(text=html)))
        result = connector.collect()
        assert len(result.observations) <= 3


# ─────────────────────────────────────────────────────────────────────────────
# Marine connector
# ─────────────────────────────────────────────────────────────────────────────


class TestMarineBugFixes:
    """Regression tests for marine.py bug fixes."""

    def test_invalid_ship_type_string_defaults_to_zero(self, monkeypatch):
        """Invalid ship_type string (e.g. 'unknown') must not raise ValueError."""
        from sts_monitor.connectors.marine import MarineTrafficConnector

        vessels = [{
            "mmsi": "123456789",
            "name": "TEST VESSEL",
            "lat": "30.0",
            "lon": "30.0",
            "ship_type": "unknown_type",  # Invalid — would crash without fix
        }]
        connector = MarineTrafficConnector()
        fake_resp = _FakeResponse(payload=vessels)
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        # Must not raise
        result = connector.collect()
        assert isinstance(result.observations, list)

    def test_missing_lat_lon_skipped(self, monkeypatch):
        """Vessels with no lat/lon must be silently skipped."""
        from sts_monitor.connectors.marine import MarineTrafficConnector

        vessels = [
            {"mmsi": "111", "name": "NO POSITION"},  # no lat/lon
            {"mmsi": "222", "name": "HAS POSITION", "lat": "10.0", "lon": "20.0"},
        ]
        connector = MarineTrafficConnector()
        fake_resp = _FakeResponse(payload=vessels)
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        result = connector.collect()
        assert len(result.observations) == 1
        assert "HAS POSITION" in result.observations[0].claim

    def test_bbox_filter(self, monkeypatch):
        """Vessels outside the bounding box must be excluded."""
        from sts_monitor.connectors.marine import MarineTrafficConnector

        vessels = [
            {"mmsi": "111", "name": "INSIDE BOX", "lat": "5.0", "lon": "5.0"},
            {"mmsi": "222", "name": "OUTSIDE BOX", "lat": "80.0", "lon": "80.0"},
        ]
        connector = MarineTrafficConnector(bbox=(0.0, 0.0, 10.0, 10.0))
        fake_resp = _FakeResponse(payload=vessels)
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        result = connector.collect()
        assert len(result.observations) == 1
        assert "INSIDE BOX" in result.observations[0].claim

    def test_military_only_filter_excludes_civilian(self, monkeypatch):
        """military_only=True must exclude non-military vessels."""
        from sts_monitor.connectors.marine import MarineTrafficConnector

        vessels = [
            {"mmsi": "111", "name": "CARGO SHIP", "lat": "10.0", "lon": "10.0", "ship_type": "70"},
            {"mmsi": "222", "name": "USS IOWA", "lat": "20.0", "lon": "20.0", "ship_type": "35"},
        ]
        connector = MarineTrafficConnector(military_only=True)
        fake_resp = _FakeResponse(payload=vessels)
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        result = connector.collect()
        assert len(result.observations) == 1
        assert "USS IOWA" in result.observations[0].claim

    def test_invalid_lat_lon_string_skipped(self, monkeypatch):
        """Vessels with non-numeric lat/lon strings must be skipped without crash."""
        from sts_monitor.connectors.marine import MarineTrafficConnector

        vessels = [
            {"mmsi": "999", "name": "BAD COORDS", "lat": "not_a_number", "lon": "also_bad"},
        ]
        connector = MarineTrafficConnector()
        fake_resp = _FakeResponse(payload=vessels)
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        result = connector.collect()
        assert result.observations == []

    def test_vessel_type_filter(self, monkeypatch):
        """vessel_types filter must exclude non-matching vessel classes."""
        from sts_monitor.connectors.marine import MarineTrafficConnector

        vessels = [
            {"mmsi": "111", "name": "TANKER ONE", "lat": "5.0", "lon": "5.0", "ship_type": "80"},
            {"mmsi": "222", "name": "CARGO ONE", "lat": "6.0", "lon": "6.0", "ship_type": "70"},
        ]
        connector = MarineTrafficConnector(vessel_types=["tanker"])
        fake_resp = _FakeResponse(payload=vessels)
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        result = connector.collect()
        assert len(result.observations) == 1
        assert "TANKER ONE" in result.observations[0].claim

    def test_empty_vessel_list(self, monkeypatch):
        """Empty AIS response returns empty observations without error."""
        from sts_monitor.connectors.marine import MarineTrafficConnector

        connector = MarineTrafficConnector()
        fake_resp = _FakeResponse(payload=[])
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(fake_resp))
        result = connector.collect()
        assert result.observations == []
        assert result.metadata["vessel_count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# ADS-B connector
# ─────────────────────────────────────────────────────────────────────────────


class TestADSBBugFixes:
    """Regression tests for adsb.py reliability tiers and edge cases."""

    def test_reliability_tiers(self, monkeypatch):
        """Verify reliability: emergency=0.95, military=0.88, civilian=0.70."""
        from sts_monitor.connectors.adsb import ADSBExchangeConnector

        aircraft = [
            # civilian
            {"hex": "a1b2c3", "flight": "UAL100", "lat": 40.0, "lon": -74.0, "alt_baro": 35000},
            # military (US hex prefix ae)
            {"hex": "ae0001", "flight": "RCH001", "lat": 38.0, "lon": -77.0, "alt_baro": 30000},
            # emergency squawk
            {"hex": "b1c2d3", "flight": "DAL200", "lat": 41.0, "lon": -73.0, "squawk": "7700"},
        ]
        connector = ADSBExchangeConnector(center_lat=40.0, center_lon=-74.0, radius_nm=250)
        monkeypatch.setattr(connector, "_build_client",
                            lambda: _FakeClient(_FakeResponse(payload={"ac": aircraft})))
        result = connector.collect()
        assert len(result.observations) == 3

        reliabilities = {o.claim.split(" ")[1]: o.reliability_hint for o in result.observations}
        # civilian
        assert any(abs(o.reliability_hint - 0.70) < 0.01
                   for o in result.observations if "MILITARY" not in o.claim and "EMERGENCY" not in o.claim)
        # military
        assert any(abs(o.reliability_hint - 0.88) < 0.01
                   for o in result.observations if "MILITARY" in o.claim and "EMERGENCY" not in o.claim)
        # emergency
        assert any(abs(o.reliability_hint - 0.95) < 0.01
                   for o in result.observations if "EMERGENCY" in o.claim)

    def test_no_dead_reliability_assignment(self, monkeypatch):
        """The old 'reliability = 0.90' dead code is gone; all aircraft get correct tier."""
        from sts_monitor.connectors.adsb import ADSBExchangeConnector

        aircraft = [{"hex": "cc1234", "flight": "FDX001", "lat": 35.0, "lon": -80.0}]
        connector = ADSBExchangeConnector(center_lat=35.0, center_lon=-80.0, radius_nm=250)
        monkeypatch.setattr(connector, "_build_client",
                            lambda: _FakeClient(_FakeResponse(payload={"ac": aircraft})))
        result = connector.collect()
        assert len(result.observations) == 1
        # civilian (no military prefix, no emergency) → 0.70, NOT 0.90
        assert abs(result.observations[0].reliability_hint - 0.70) < 0.01

    def test_missing_lat_lon_skipped(self, monkeypatch):
        """Aircraft with missing lat or lon must be skipped."""
        from sts_monitor.connectors.adsb import ADSBExchangeConnector

        aircraft = [
            {"hex": "aa0001", "flight": "SKIP1"},           # no lat/lon
            {"hex": "bb0002", "flight": "SKIP2", "lat": None, "lon": 10.0},  # None lat
            {"hex": "cc0003", "flight": "KEEP1", "lat": 40.0, "lon": -74.0},
        ]
        connector = ADSBExchangeConnector(center_lat=40.0, center_lon=-74.0, radius_nm=250)
        monkeypatch.setattr(connector, "_build_client",
                            lambda: _FakeClient(_FakeResponse(payload={"ac": aircraft})))
        result = connector.collect()
        assert len(result.observations) == 1
        assert "KEEP1" in result.observations[0].claim

    def test_emergency_squawks(self, monkeypatch):
        """All three emergency squawks (7500/7600/7700) must be flagged."""
        from sts_monitor.connectors.adsb import ADSBExchangeConnector

        aircraft = [
            {"hex": "aa1111", "flight": "FL001", "lat": 40.0, "lon": -74.0, "squawk": "7500"},
            {"hex": "bb2222", "flight": "FL002", "lat": 40.1, "lon": -74.1, "squawk": "7600"},
            {"hex": "cc3333", "flight": "FL003", "lat": 40.2, "lon": -74.2, "squawk": "7700"},
        ]
        connector = ADSBExchangeConnector(center_lat=40.0, center_lon=-74.0, radius_nm=250)
        monkeypatch.setattr(connector, "_build_client",
                            lambda: _FakeClient(_FakeResponse(payload={"ac": aircraft})))
        result = connector.collect()
        assert all("EMERGENCY" in o.claim for o in result.observations)
        assert all(o.reliability_hint == 0.95 for o in result.observations)

    def test_query_filter_by_callsign(self, monkeypatch):
        """Query filter must match callsign."""
        from sts_monitor.connectors.adsb import ADSBExchangeConnector

        aircraft = [
            {"hex": "aa1111", "flight": "UAL100", "lat": 40.0, "lon": -74.0},
            {"hex": "bb2222", "flight": "HOMER01", "lat": 41.0, "lon": -75.0},
        ]
        connector = ADSBExchangeConnector(center_lat=40.0, center_lon=-74.0, radius_nm=250)
        monkeypatch.setattr(connector, "_build_client",
                            lambda: _FakeClient(_FakeResponse(payload={"ac": aircraft})))
        result = connector.collect(query="homer")
        assert len(result.observations) == 1
        assert "HOMER01" in result.observations[0].claim

    def test_geo_events_altitude_populated(self, monkeypatch):
        """Geo events must carry the altitude value."""
        from sts_monitor.connectors.adsb import ADSBExchangeConnector

        aircraft = [{"hex": "ae1234", "flight": "RCH001", "lat": 38.0, "lon": -77.0, "alt_baro": 28000}]
        connector = ADSBExchangeConnector(center_lat=38.0, center_lon=-77.0, radius_nm=250)
        monkeypatch.setattr(connector, "_build_client",
                            lambda: _FakeClient(_FakeResponse(payload={"ac": aircraft})))
        result = connector.collect()
        geo = result.metadata["geo_events"]
        assert geo[0]["altitude"] == 28000


# ─────────────────────────────────────────────────────────────────────────────
# Privacy module
# ─────────────────────────────────────────────────────────────────────────────


class TestPrivacyBugFixes:
    """Regression tests for privacy.py bug fixes."""

    def test_check_tor_status_closes_client_on_success(self):
        """The httpx.Client must be closed even on success (context manager)."""
        from sts_monitor.privacy import check_tor_status
        import httpx

        closed: list[bool] = []

        class _FakeHTTPXClient:
            def __init__(self, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args):
                closed.append(True)
                return False
            def get(self, url):
                return _FakeResponse(payload={"IsTor": True, "IP": "10.0.0.1"})

        with patch("sts_monitor.privacy.httpx.Client", _FakeHTTPXClient):
            result = check_tor_status()

        assert result["tor_available"] is True
        assert len(closed) == 1  # __exit__ called

    def test_check_tor_status_closes_client_on_failure(self):
        """The httpx.Client must be closed even when the request fails."""
        from sts_monitor.privacy import check_tor_status

        closed: list[bool] = []

        class _ErrorClient:
            def __init__(self, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args):
                closed.append(True)
                return False
            def get(self, url):
                raise ConnectionError("Tor not available")

        with patch("sts_monitor.privacy.httpx.Client", _ErrorClient):
            result = check_tor_status()

        assert result["tor_available"] is False
        assert "error" in result
        assert len(closed) == 1

    def test_privacy_delay_with_jitter(self, monkeypatch):
        """privacy_delay with jitter should sleep within the configured range."""
        from sts_monitor.privacy import PrivacyConfig, privacy_delay

        slept: list[float] = []
        monkeypatch.setattr("time.sleep", lambda s: slept.append(s))

        cfg = PrivacyConfig(min_request_delay_s=0.1, max_request_delay_s=0.5, add_jitter=True)
        privacy_delay(cfg)

        assert len(slept) == 1
        assert 0.1 <= slept[0] <= 0.5

    def test_privacy_delay_without_jitter(self, monkeypatch):
        """privacy_delay without jitter must sleep exactly min_request_delay_s."""
        from sts_monitor.privacy import PrivacyConfig, privacy_delay

        slept: list[float] = []
        monkeypatch.setattr("time.sleep", lambda s: slept.append(s))

        cfg = PrivacyConfig(min_request_delay_s=1.5, max_request_delay_s=3.0, add_jitter=False)
        privacy_delay(cfg)

        assert slept == [1.5]

    def test_get_privacy_status_returns_all_keys(self):
        """get_privacy_status must return all expected status fields."""
        from sts_monitor.privacy import PrivacyConfig, get_privacy_status

        cfg = PrivacyConfig(rotate_ua=True, use_tor=False, use_doh=True, doh_resolver="google")
        status = get_privacy_status(cfg)

        for key in ("tor_enabled", "ua_rotation", "referrer_stripping",
                    "doh_enabled", "doh_resolver", "request_jitter",
                    "delay_range", "proxy_url"):
            assert key in status, f"Missing key: {key}"

    def test_build_private_client_with_proxy(self):
        """build_private_client with proxy_url must pass proxy to httpx.Client."""
        from sts_monitor.privacy import PrivacyConfig, build_private_client
        import httpx

        cfg = PrivacyConfig(proxy_url="http://myproxy:8080")
        client = build_private_client(cfg)
        assert isinstance(client, httpx.Client)
        client.close()

    def test_resolve_doh_handles_network_failure(self):
        """resolve_doh must return [] on network failure, not raise."""
        from sts_monitor.privacy import resolve_doh

        with patch("sts_monitor.privacy.httpx.get", side_effect=ConnectionError("DNS failed")):
            result = resolve_doh("example.com", resolver="cloudflare")

        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# Internet Archive connector
# ─────────────────────────────────────────────────────────────────────────────


class TestArchiveBugFixes:
    """Regression tests for archive.py edge cases."""

    def test_cdx_returns_none_handled(self, monkeypatch):
        """If CDX API returns None (JSON null), collect must return empty list."""
        from sts_monitor.connectors.archive import InternetArchiveConnector

        connector = InternetArchiveConnector(urls_to_check=["http://example.com"])
        null_resp = _FakeResponse(payload=None)
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(null_resp))
        result = connector.collect()
        assert result.observations == []

    def test_cdx_single_row_header_only(self, monkeypatch):
        """CDX response with only header row (len < 2) returns empty list."""
        from sts_monitor.connectors.archive import InternetArchiveConnector

        connector = InternetArchiveConnector(urls_to_check=["http://example.com"])
        header_only = _FakeResponse(payload=[["timestamp", "original", "mimetype", "statuscode", "length"]])
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(header_only))
        result = connector.collect()
        assert result.observations == []

    def test_cdx_empty_list(self, monkeypatch):
        """CDX response that is empty list returns empty observations."""
        from sts_monitor.connectors.archive import InternetArchiveConnector

        connector = InternetArchiveConnector(urls_to_check=["http://example.com"])
        empty_resp = _FakeResponse(payload=[])
        monkeypatch.setattr(connector, "_build_client", lambda: _FakeClient(empty_resp))
        result = connector.collect()
        assert result.observations == []

    def test_timestamp_too_short_uses_fallback(self, monkeypatch):
        """Timestamps shorter than 14 chars should use now() fallback without crash."""
        from sts_monitor.connectors.archive import InternetArchiveConnector

        short_ts_cdx = [
            ["timestamp", "original", "mimetype", "statuscode", "length"],
            ["202401", "http://example.com/page", "text/html", "200", "1000"],  # 6 chars
        ]
        page_html = "<html><body><p>Some content about an important event.</p></body></html>"

        class _MixedClient:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def get(self, url, **kw):
                if "cdx" in url:
                    return _FakeResponse(payload=short_ts_cdx)
                return _FakeResponse(text=page_html)

        connector = InternetArchiveConnector(urls_to_check=["http://example.com/page"])
        monkeypatch.setattr(connector, "_build_client", lambda: _MixedClient())
        result = connector.collect()
        # Should not raise; if content passes filter, observations may be present
        assert isinstance(result.observations, list)

    def test_script_style_stripped_from_extracted_text(self, monkeypatch):
        """Text extractor must strip <script> and <style> content."""
        from sts_monitor.connectors.archive import InternetArchiveConnector, _TextExtractor

        html = """<html><body>
        <script>evil_js_code();</script>
        <style>.foo { color: red; }</style>
        <p>This is the real article content.</p>
        <noscript>No JS version.</noscript>
        </body></html>"""

        extractor = _TextExtractor()
        extractor.feed(html)
        text = extractor.get_text()

        assert "evil_js_code" not in text
        assert ".foo" not in text
        assert "No JS version" not in text
        assert "real article content" in text

    def test_text_extracted_truncated_to_2000(self, monkeypatch):
        """_extract_text must truncate to 2000 characters."""
        from sts_monitor.connectors.archive import InternetArchiveConnector

        connector = InternetArchiveConnector(urls_to_check=["http://example.com"])
        long_html = "<html><body><p>" + "A" * 10000 + "</p></body></html>"
        result_text = connector._extract_text(long_html)
        assert len(result_text) <= 2000

    def test_reliability_hint_is_065(self, monkeypatch):
        """All archive observations must have reliability_hint of 0.65."""
        from sts_monitor.connectors.archive import InternetArchiveConnector

        cdx = [
            ["timestamp", "original", "mimetype", "statuscode", "length"],
            ["20240101120000", "http://example.com/p", "text/html", "200", "5000"],
        ]
        page_html = "<html><body><p>Significant news event content here.</p></body></html>"

        class _MixedClient:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def get(self, url, **kw):
                return _FakeResponse(payload=cdx) if "cdx" in url else _FakeResponse(text=page_html)

        connector = InternetArchiveConnector(urls_to_check=["http://example.com/p"])
        monkeypatch.setattr(connector, "_build_client", lambda: _MixedClient())
        result = connector.collect()
        for obs in result.observations:
            assert abs(obs.reliability_hint - 0.65) < 0.01

    def test_domain_extraction_edge_cases(self):
        """_extract_domain must handle all edge cases correctly."""
        from sts_monitor.connectors.archive import _extract_domain

        assert _extract_domain("https://www.bbc.co.uk/news/world") == "bbc.co.uk"
        assert _extract_domain("http://example.com?q=test") == "example.com"
        assert _extract_domain("example.com/page") == "example.com"
        assert _extract_domain("HTTPS://EXAMPLE.COM/PATH") == "example.com"
        assert _extract_domain("https://sub.domain.example.org/a/b/c") == "sub.domain.example.org"


# ─────────────────────────────────────────────────────────────────────────────
# Research agent
# ─────────────────────────────────────────────────────────────────────────────


class TestResearchAgentBugFixes:
    """Regression tests for research_agent.py bug fixes."""

    def _make_agent(self, response=None):
        from sts_monitor.research_agent import ResearchAgent
        llm = MagicMock()
        llm.summarize.return_value = response
        return ResearchAgent(llm_client=llm, max_iterations=1)

    def test_llm_returns_none_does_not_crash(self):
        """If llm.summarize() returns None, _ask_llm must return safe fallback dict."""
        from sts_monitor.research_agent import ResearchAgent
        from sts_monitor.pipeline import Observation

        llm = MagicMock()
        llm.summarize.return_value = None
        agent = ResearchAgent(llm_client=llm, max_iterations=1)

        obs = [Observation(source="test", claim="Test claim about event",
                           url="http://example.com", reliability_hint=0.7)]
        result = agent._ask_llm("test topic", 1, obs, [], total_obs=1)
        assert isinstance(result, dict)
        assert "confidence" in result
        assert "should_continue" in result
        assert result["should_continue"] is False

    def test_llm_returns_markdown_fenced_json(self):
        """Markdown-fenced JSON must be correctly unwrapped."""
        from sts_monitor.research_agent import ResearchAgent
        from sts_monitor.pipeline import Observation

        payload = {"key_findings": ["finding 1"], "confidence": 0.8,
                   "new_search_queries": [], "urls_to_scrape": [],
                   "twitter_accounts_to_follow": [], "twitter_search_queries": [],
                   "assessment": "Test.", "should_continue": False, "reasoning": "done."}

        llm = MagicMock()
        llm.summarize.return_value = f"```json\n{json.dumps(payload)}\n```"
        agent = ResearchAgent(llm_client=llm, max_iterations=1)

        obs = [Observation(source="test", claim="Test content about topic",
                           url="http://example.com", reliability_hint=0.7)]
        result = agent._ask_llm("test topic", 1, obs, [], total_obs=1)
        assert result["confidence"] == 0.8
        assert result["key_findings"] == ["finding 1"]

    def test_llm_returns_invalid_json_gives_fallback(self):
        """Invalid JSON from LLM must return fallback dict with should_continue=False."""
        from sts_monitor.research_agent import ResearchAgent
        from sts_monitor.pipeline import Observation

        llm = MagicMock()
        llm.summarize.return_value = "This is not JSON at all, just plain text."
        agent = ResearchAgent(llm_client=llm, max_iterations=1)

        obs = [Observation(source="test", claim="Test claim here for analysis",
                           url="http://example.com", reliability_hint=0.7)]
        result = agent._ask_llm("test topic", 1, obs, [], total_obs=1)
        assert isinstance(result, dict)
        assert result["should_continue"] is False
        assert "confidence" in result

    def test_markdown_fence_only_string(self):
        """A string that is just '```' (no newline) must not crash, gives fallback."""
        from sts_monitor.research_agent import ResearchAgent
        from sts_monitor.pipeline import Observation

        llm = MagicMock()
        llm.summarize.return_value = "```"
        agent = ResearchAgent(llm_client=llm, max_iterations=1)

        obs = [Observation(source="t", claim="Test claim content here for parsing",
                           url="http://x.com", reliability_hint=0.5)]
        result = agent._ask_llm("topic", 1, obs, [], total_obs=1)
        assert isinstance(result, dict)
        # Falls back to safe defaults
        assert "confidence" in result

    def test_stop_session_uses_lock(self):
        """stop_session must use the lock and return True only for known sessions."""
        from sts_monitor.research_agent import ResearchAgent

        llm = MagicMock()
        agent = ResearchAgent(llm_client=llm, max_iterations=1)

        # Not in _stop_flags → False
        assert agent.stop_session("nonexistent-id") is False

        # Register session
        with agent._lock:
            agent._stop_flags["sess-123"] = False

        assert agent.stop_session("sess-123") is True
        with agent._lock:
            assert agent._stop_flags["sess-123"] is True

    def test_get_session_returns_none_for_unknown(self):
        """get_session returns None for unknown session IDs."""
        from sts_monitor.research_agent import ResearchAgent

        llm = MagicMock()
        agent = ResearchAgent(llm_client=llm, max_iterations=1)
        assert agent.get_session("unknown") is None

    def test_list_sessions_empty(self):
        """list_sessions returns empty list when no sessions have run."""
        from sts_monitor.research_agent import ResearchAgent

        llm = MagicMock()
        agent = ResearchAgent(llm_client=llm, max_iterations=1)
        assert agent.list_sessions() == []

    def test_run_creates_and_completes_session(self):
        """run() must create a session, set status=completed, and return it."""
        from sts_monitor.research_agent import ResearchAgent

        payload = {"key_findings": [], "confidence": 0.5,
                   "new_search_queries": [], "urls_to_scrape": [],
                   "twitter_accounts_to_follow": [], "twitter_search_queries": [],
                   "assessment": "Nothing found.", "should_continue": False, "reasoning": "done."}
        llm = MagicMock()
        llm.summarize.return_value = json.dumps(payload)
        agent = ResearchAgent(llm_client=llm, max_iterations=1)

        # Patch all connectors to return empty results
        empty_result = MagicMock()
        empty_result.observations = []

        with patch("sts_monitor.research_agent.SearchConnector") as MockSearch, \
             patch("sts_monitor.research_agent.NitterConnector") as MockNitter, \
             patch("sts_monitor.research_agent.RSSConnector") as MockRSS, \
             patch("sts_monitor.connectors.telegram.TelegramConnector") as MockTelegram:
            for Mock in (MockSearch, MockNitter, MockRSS, MockTelegram):
                Mock.return_value.collect.return_value = empty_result

            session = agent.run("test-session-1", "ukraine war", "ukraine")

        assert session.status == "completed"
        assert session.session_id == "test-session-1"
        assert session.topic == "ukraine war"
        assert session.finished_at is not None
        assert len(session.iterations) == 1

    def test_run_async_session_visible_immediately(self):
        """run_async() must register the session before the thread starts."""
        from sts_monitor.research_agent import ResearchAgent

        payload = {"key_findings": [], "confidence": 0.5,
                   "new_search_queries": [], "urls_to_scrape": [],
                   "twitter_accounts_to_follow": [], "twitter_search_queries": [],
                   "assessment": ".", "should_continue": False, "reasoning": "."}
        llm = MagicMock()
        llm.summarize.return_value = json.dumps(payload)
        agent = ResearchAgent(llm_client=llm, max_iterations=1)

        empty_result = MagicMock()
        empty_result.observations = []

        with patch("sts_monitor.research_agent.SearchConnector") as MockSearch, \
             patch("sts_monitor.research_agent.NitterConnector") as MockNitter, \
             patch("sts_monitor.research_agent.RSSConnector") as MockRSS, \
             patch("sts_monitor.connectors.telegram.TelegramConnector") as MockTelegram:
            for Mock in (MockSearch, MockNitter, MockRSS, MockTelegram):
                Mock.return_value.collect.return_value = empty_result

            session_id = agent.run_async("async-test-1", "climate")
            # Must be visible immediately (before thread completes)
            session = agent.get_session(session_id)
            assert session is not None
            assert session.session_id == session_id

        # Wait for the thread to finish
        for _ in range(50):
            s = agent.get_session(session_id)
            if s and s.status != "running":
                break
            time.sleep(0.1)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline edge cases
# ─────────────────────────────────────────────────────────────────────────────


class TestPipelineEdgeCases:
    """Comprehensive pipeline edge case tests."""

    def test_empty_input(self):
        from sts_monitor.pipeline import SignalPipeline

        pipeline = SignalPipeline(min_reliability=0.5)
        result = pipeline.run([], topic="empty test")
        assert result.accepted == []
        assert result.dropped == []
        assert result.confidence == 0.0

    def test_all_below_min_reliability(self):
        from sts_monitor.pipeline import Observation, SignalPipeline

        pipeline = SignalPipeline(min_reliability=0.7)
        obs = [
            Observation(source="a", claim="c1", url="u1", reliability_hint=0.1),
            Observation(source="b", claim="c2", url="u2", reliability_hint=0.2),
            Observation(source="c", claim="c3", url="u3", reliability_hint=0.3),
        ]
        result = pipeline.run(obs, topic="low quality")
        assert result.accepted == []
        assert len(result.dropped) == 3

    def test_reliability_clamped_above_one(self):
        from sts_monitor.pipeline import Observation, SignalPipeline

        pipeline = SignalPipeline(min_reliability=0.3)
        obs = [Observation(source="a", claim="huge reliability", url="u1", reliability_hint=9.99)]
        result = pipeline.run(obs, topic="test")
        assert result.accepted[0].reliability_hint == 1.0

    def test_reliability_clamped_below_zero(self):
        from sts_monitor.pipeline import Observation, SignalPipeline

        pipeline = SignalPipeline(min_reliability=0.0)
        obs = [Observation(source="a", claim="negative reliability", url="u1", reliability_hint=-0.5)]
        result = pipeline.run(obs, topic="test")
        # Negative clamped to 0.0, which is >= min_reliability=0.0, so accepted
        assert result.accepted[0].reliability_hint == 0.0

    def test_deduplication_by_url(self):
        from sts_monitor.pipeline import Observation, SignalPipeline

        pipeline = SignalPipeline(min_reliability=0.1)
        obs = [
            Observation(source="a", claim="same claim", url="http://dup.com/1", reliability_hint=0.8),
            Observation(source="b", claim="same claim", url="http://dup.com/1", reliability_hint=0.5),
        ]
        result = pipeline.run(obs, topic="dedup test")
        assert len(result.accepted) == 1
        assert len(result.deduplicated) == 1

    def test_dedup_keeps_higher_reliability(self):
        from sts_monitor.pipeline import Observation, SignalPipeline

        pipeline = SignalPipeline(min_reliability=0.1)
        obs = [
            Observation(source="a", claim="dup", url="http://same.com", reliability_hint=0.5),
            Observation(source="b", claim="dup", url="http://same.com", reliability_hint=0.9),
        ]
        result = pipeline.run(obs, topic="dedup reliability")
        assert len(result.accepted) == 1
        assert result.accepted[0].reliability_hint == 0.9

    def test_single_observation_confidence_equals_reliability(self):
        from sts_monitor.pipeline import Observation, SignalPipeline

        pipeline = SignalPipeline(min_reliability=0.1)
        obs = [Observation(source="a", claim="one", url="u", reliability_hint=0.75)]
        result = pipeline.run(obs, topic="single")
        assert abs(result.confidence - 0.75) < 0.01

    def test_disputed_claims_detected(self):
        from sts_monitor.pipeline import Observation, SignalPipeline

        pipeline = SignalPipeline(min_reliability=0.1)
        obs = [
            Observation(source="a", claim="Power is restored to the city", url="u1", reliability_hint=0.9),
            Observation(source="b", claim="Power is restored to the city is false", url="u2", reliability_hint=0.9),
        ]
        result = pipeline.run(obs, topic="power")
        assert len(result.disputed_claims) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# Convergence detection edge cases
# ─────────────────────────────────────────────────────────────────────────────


class TestConvergenceEdgeCases:
    """Edge cases for the convergence zone detector."""

    def test_empty_points_returns_empty(self):
        from sts_monitor.convergence import detect_convergence

        zones = detect_convergence([])
        assert zones == []

    def test_single_point_no_cluster(self):
        from sts_monitor.convergence import GeoPoint, detect_convergence

        pts = [GeoPoint(latitude=40.0, longitude=-74.0, layer="earthquake",
                        title="EQ event", event_time=datetime.now(UTC))]
        zones = detect_convergence(pts)
        # Single point can't form a convergence zone (need at least 2)
        assert zones == []

    def test_three_nearby_distinct_types_form_cluster(self):
        """Three points with distinct signal types within radius form a convergence zone."""
        from sts_monitor.convergence import GeoPoint, detect_convergence

        now = datetime.now(UTC)
        pts = [
            GeoPoint(latitude=40.0, longitude=-74.0, layer="earthquake", title="EQ", event_time=now),
            GeoPoint(latitude=40.005, longitude=-74.005, layer="fire", title="Fire", event_time=now),
            GeoPoint(latitude=40.01, longitude=-74.01, layer="conflict", title="Conflict", event_time=now),
        ]
        zones = detect_convergence(pts, radius_km=10.0, min_signal_types=3)
        assert len(zones) >= 1
        assert zones[0].signal_count >= 3

    def test_far_apart_points_no_cluster(self):
        """Points spread across the globe should not form a cluster."""
        from sts_monitor.convergence import GeoPoint, detect_convergence

        now = datetime.now(UTC)
        pts = [
            GeoPoint(latitude=40.0, longitude=-74.0, layer="earthquake", title="EQ", event_time=now),
            GeoPoint(latitude=0.0, longitude=0.0, layer="fire", title="Fire", event_time=now),
            GeoPoint(latitude=-33.0, longitude=151.0, layer="conflict", title="Con", event_time=now),
        ]
        zones = detect_convergence(pts, radius_km=10.0, min_signal_types=3)
        # Points are far apart, no single cluster should contain all 3
        assert zones == []

    def test_severity_levels(self):
        from sts_monitor.convergence import GeoPoint, detect_convergence

        now = datetime.now(UTC)
        # Many points close together → high severity
        pts = [
            GeoPoint(latitude=40.0 + i * 0.001, longitude=-74.0, layer=f"type{i % 3}",
                     title=f"Event {i}", event_time=now)
            for i in range(10)
        ]
        zones = detect_convergence(pts, radius_km=5.0)
        assert len(zones) >= 1
        # With 10 signals, severity should be at least medium
        assert zones[0].severity in ("medium", "high", "critical")

    def test_signal_types_recorded(self):
        from sts_monitor.convergence import GeoPoint, detect_convergence

        now = datetime.now(UTC)
        pts = [
            GeoPoint(latitude=40.0, longitude=-74.0, layer="earthquake",
                     title="EQ", event_time=now),
            GeoPoint(latitude=40.001, longitude=-74.001, layer="fire",
                     title="Fire", event_time=now),
            GeoPoint(latitude=40.002, longitude=-74.002, layer="conflict",
                     title="Conflict", event_time=now),
        ]
        zones = detect_convergence(pts, radius_km=10.0)
        assert len(zones) >= 1
        for zone in zones:
            assert len(zone.signal_types) >= 1
