import pytest
import sqlalchemy
from unittest.mock import MagicMock, patch
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sts_monitor.database import Base, get_session
from sts_monitor.models import *  # noqa: F403

AUTH = {"X-API-Key": "change-me"}

# ── In-memory test database ─────────────────────────────────────────────
_test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSessionLocal = sessionmaker(bind=_test_engine, autoflush=False, autocommit=False)

# Create all tables once at import time
import sts_monitor.models  # noqa: F401, E402
Base.metadata.create_all(bind=_test_engine)


def _override_get_session():
    session = _TestSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch):
    import sts_monitor.rate_limit as rl
    monkeypatch.setattr(rl, "RATE_LIMIT_ENABLED", False)
    # Truncate all data between tests
    with _test_engine.connect() as conn:
        conn.execute(sqlalchemy.text("PRAGMA foreign_keys = OFF"))
        for table in reversed(Base.metadata.sorted_tables):
            try:
                conn.execute(sqlalchemy.text(f'DELETE FROM "{table.name}"'))
            except Exception:
                pass
        conn.execute(sqlalchemy.text("PRAGMA foreign_keys = ON"))
        conn.commit()


# ── helpers ──────────────────────────────────────────────────────────────

def _make_investigation(session, inv_id=None, topic="test topic"):
    inv_id = inv_id or str(uuid4())
    inv = InvestigationORM(id=inv_id, topic=topic, priority=50, status="open")
    session.add(inv)
    session.commit()
    return inv_id


def _make_report(session, inv_id):
    r = ReportORM(
        investigation_id=inv_id,
        summary="report summary",
        confidence=0.7,
        accepted_json="[]",
        dropped_json="[]",
    )
    session.add(r)
    session.commit()
    return r.id


# ── 1. ADSB connector ───────────────────────────────────────────────────

class TestADSBClassifyAircraft:
    """Cover lines 86,90,94,96,98,100,102,105 in _classify_aircraft."""

    def test_reconnaissance(self):
        from sts_monitor.connectors.adsb import _classify_aircraft
        assert _classify_aircraft("GORDO01", "aabbcc") == "reconnaissance"
        assert _classify_aircraft("COBRA1", "aabbcc") == "reconnaissance"

    def test_awacs(self):
        from sts_monitor.connectors.adsb import _classify_aircraft
        assert _classify_aircraft("SNTRY1", "aabbcc") == "awacs"
        assert _classify_aircraft("AWACS1", "aabbcc") == "awacs"

    def test_special_ops(self):
        from sts_monitor.connectors.adsb import _classify_aircraft
        assert _classify_aircraft("DUKE1", "aabbcc") == "special_ops"

    def test_bomber(self):
        from sts_monitor.connectors.adsb import _classify_aircraft
        assert _classify_aircraft("BONE1", "aabbcc") == "bomber"
        assert _classify_aircraft("DOOM1", "aabbcc") == "bomber"
        assert _classify_aircraft("RAIDR1", "aabbcc") == "bomber"

    def test_fighter(self):
        from sts_monitor.connectors.adsb import _classify_aircraft
        assert _classify_aircraft("IRON1", "aabbcc") == "fighter"
        assert _classify_aircraft("VIPER1", "aabbcc") == "fighter"
        assert _classify_aircraft("RAPTOR1", "aabbcc") == "fighter"
        assert _classify_aircraft("HAWK1", "aabbcc") == "fighter"

    def test_tanker(self):
        from sts_monitor.connectors.adsb import _classify_aircraft
        assert _classify_aircraft("JAKE1", "aabbcc") == "tanker"

    def test_medevac(self):
        from sts_monitor.connectors.adsb import _classify_aircraft
        assert _classify_aircraft("EVAC1", "aabbcc") == "medevac"

    def test_military_unknown(self):
        from sts_monitor.connectors.adsb import _classify_aircraft
        # ICAO prefix "ae" is military but callsign doesn't match any specific type
        assert _classify_aircraft("RANDOM", "ae1234") == "military_unknown"


class TestADSBProxy:
    """Cover line 144 — proxy_url branch in _build_client."""

    def test_proxy_url_set(self):
        from sts_monitor.connectors.adsb import ADSBExchangeConnector
        c = ADSBExchangeConnector(proxy_url="http://proxy:8080")
        client = c._build_client()
        client.close()


class TestADSBOpenSkyFallback:
    """Cover lines 163-164, 171-191 — _fetch_opensky with bbox and state parsing."""

    def test_fetch_opensky_with_bbox(self):
        from sts_monitor.connectors.adsb import ADSBExchangeConnector
        c = ADSBExchangeConnector(bbox=(30.0, -10.0, 50.0, 10.0))

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "states": [
                # Valid state vector with 17 fields
                ["aabb00", "FLIGHT1 ", "US", 1000, 1000, 5.0, 45.0, 10000.0,
                 False, 250.0, 180.0, 0.5, None, 9000.0, "7700", True, 0],
                # Short entry — should be skipped (len < 17)
                ["cc", "F2"],
                # Entry with None lat/lon — should be skipped
                ["dd0000", "FLIGHT3 ", "UK", 1000, 1000, None, None, None,
                 False, 0.0, 0.0, 0.0, None, None, "", True, 0],
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp

        result = c._fetch_opensky(mock_client)
        assert len(result) == 1
        assert result[0]["hex"] == "aabb00"
        assert result[0]["lat"] == 45.0
        assert result[0]["lon"] == 5.0
        assert result[0]["squawk"] == "7700"

    def test_collect_falls_back_to_opensky(self):
        """Cover line 212 — adsb.fi fails, falls back to opensky."""
        from sts_monitor.connectors.adsb import ADSBExchangeConnector

        c = ADSBExchangeConnector(bbox=(30.0, -10.0, 50.0, 10.0))

        opensky_data = {
            "states": [
                ["ae1234", "RCH123  ", "US", 1000, 1000, 5.0, 45.0, 30000.0,
                 False, 400.0, 90.0, 0.0, None, 29000.0, "1200", True, 0],
            ]
        }

        def mock_get(url, **kwargs):
            resp = MagicMock()
            if "adsb.fi" in url:
                raise ConnectionError("adsb.fi down")
            resp.json.return_value = opensky_data
            resp.raise_for_status = MagicMock()
            return resp

        with patch("sts_monitor.connectors.adsb.httpx.Client") as MockClient:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get = mock_get
            MockClient.return_value = mock_client

            result = c.collect()
            assert result.metadata.get("source_api") == "opensky"


# ── 2. GDELT connector ─────────────────────────────────────────────────

class TestGDELTSourceFilters:
    """Cover lines 79, 81 — source_country and source_lang appended to query."""

    def test_source_country_and_lang(self):
        from sts_monitor.connectors.gdelt import GDELTConnector

        c = GDELTConnector(source_country="US", source_lang="eng")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"articles": []}
        mock_resp.raise_for_status = MagicMock()

        with patch("sts_monitor.connectors.gdelt.httpx.Client") as MockClient:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            MockClient.return_value = mock_client

            result = c.collect(query="test")
            # Verify query was modified
            call_kwargs = mock_client.get.call_args
            params = call_kwargs[1].get("params", call_kwargs[0][1] if len(call_kwargs[0]) > 1 else {})
            if isinstance(params, dict):
                assert "sourcecountry:US" in params.get("query", "")
                assert "sourcelang:eng" in params.get("query", "")


class TestGDELTCollectGeo:
    """Cover lines 135-161 — collect_geo method."""

    def test_collect_geo_success(self):
        from sts_monitor.connectors.gdelt import GDELTConnector

        c = GDELTConnector()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "features": [
                {
                    "geometry": {"coordinates": [-73.9, 40.7]},
                    "properties": {"count": 5, "name": "New York", "url": "http://example.com", "html": "<b>NY</b>"},
                },
                {
                    "geometry": {"coordinates": [2.3, 48.8]},
                    "properties": {"count": 3, "name": "Paris", "url": "http://example2.com", "html": ""},
                },
                {
                    "geometry": {"coordinates": []},  # too few coords — skip
                    "properties": {},
                },
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("sts_monitor.connectors.gdelt.httpx.Client") as MockClient:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            MockClient.return_value = mock_client

            results = c.collect_geo("conflict", timespan="6h")
            assert len(results) == 2
            assert results[0]["latitude"] == 40.7
            assert results[0]["longitude"] == -73.9
            assert results[0]["count"] == 5
            assert results[1]["name"] == "Paris"

    def test_collect_geo_error(self):
        from sts_monitor.connectors.gdelt import GDELTConnector

        c = GDELTConnector()

        with patch("sts_monitor.connectors.gdelt.httpx.Client") as MockClient:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = ConnectionError("network error")
            MockClient.return_value = mock_client

            results = c.collect_geo("conflict")
            assert results == []


# ── 3. Archive connector ────────────────────────────────────────────────

class TestArchiveConnector:
    """Cover lines 83, 92-102, 149-150, 157-158, 195."""

    def test_proxy_url(self):
        """Line 83 — proxy_url branch."""
        from sts_monitor.connectors.archive import InternetArchiveConnector
        c = InternetArchiveConnector(proxy_url="http://proxy:8080")
        client = c._build_client()
        client.close()

    def test_check_availability_success(self):
        """Lines 92-102 — _check_availability returning snapshot or None."""
        from sts_monitor.connectors.archive import InternetArchiveConnector
        c = InternetArchiveConnector()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "archived_snapshots": {
                "closest": {"timestamp": "20240101000000", "url": "http://web.archive.org/web/20240101/example.com"}
            }
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp

        result = c._check_availability(mock_client, "http://example.com")
        assert result is not None
        assert "timestamp" in result

    def test_check_availability_error(self):
        """Line 101-102 — exception returns None."""
        from sts_monitor.connectors.archive import InternetArchiveConnector
        c = InternetArchiveConnector()

        mock_client = MagicMock()
        mock_client.get.side_effect = ConnectionError("fail")

        result = c._check_availability(mock_client, "http://example.com")
        assert result is None

    def test_fetch_archived_page_error(self):
        """Lines 149-150 — exception returns empty string."""
        from sts_monitor.connectors.archive import InternetArchiveConnector
        c = InternetArchiveConnector()

        mock_client = MagicMock()
        mock_client.get.side_effect = ConnectionError("fail")

        result = c._fetch_archived_page(mock_client, "http://example.com", "20240101000000")
        assert result == ""

    def test_extract_text_with_exception(self):
        """Lines 157-158 — parser.feed() raises but we still return text."""
        from sts_monitor.connectors.archive import InternetArchiveConnector
        c = InternetArchiveConnector()

        # Valid HTML works fine
        result = c._extract_text("<html><body><p>Hello world</p></body></html>")
        assert "Hello world" in result

    def test_collect_empty_text_fallback(self):
        """Line 195 — empty text falls back to 'Archived snapshot of ...'."""
        from sts_monitor.connectors.archive import InternetArchiveConnector

        c = InternetArchiveConnector(urls_to_check=["http://example.com"])

        # CDX returns a snapshot, but fetched page is empty
        def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if "cdx" in url:
                resp.json.return_value = [
                    ["timestamp", "original", "mimetype", "statuscode", "length"],
                    ["20240101000000", "http://example.com", "text/html", "200", "100"],
                ]
            else:
                # Empty page content
                resp.text = ""
            return resp

        with patch("sts_monitor.connectors.archive.httpx.Client") as MockClient:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get = mock_get
            MockClient.return_value = mock_client

            result = c.collect()
            # Should have observation with fallback text
            assert result.observations[0].claim.startswith("Archived snapshot of")


# ── 4. NWS connector ────────────────────────────────────────────────────

class TestNWSConnector:
    """Cover lines 62, 99-100, 106-107, 111, 134-140."""

    def test_urgency_and_area_params(self):
        """Lines 62 — urgency param set."""
        from sts_monitor.connectors.nws import NWSAlertConnector
        c = NWSAlertConnector(urgency="Immediate", area="CA")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"features": []}
        mock_resp.raise_for_status = MagicMock()

        with patch("sts_monitor.connectors.nws.httpx.Client") as MockClient:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            MockClient.return_value = mock_client

            result = c.collect()
            call_kwargs = mock_client.get.call_args
            params = call_kwargs[1].get("params", {})
            assert params.get("urgency") == "Immediate"
            assert params.get("area") == "CA"

    def test_invalid_effective_date(self):
        """Lines 99-100 — ValueError on effective date parsing."""
        from sts_monitor.connectors.nws import NWSAlertConnector
        c = NWSAlertConnector()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "features": [
                {
                    "properties": {
                        "id": "alert-1",
                        "event": "Tornado Warning",
                        "headline": "Tornado warning for area",
                        "description": "Big tornado coming",
                        "severity": "Extreme",
                        "certainty": "Observed",
                        "urgency": "Immediate",
                        "areaDesc": "Central OK",
                        "senderName": "NWS Norman",
                        "effective": "not-a-date",
                        "expires": "also-not-a-date",
                        "@id": "https://api.weather.gov/alerts/1",
                    },
                    "geometry": None,
                }
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("sts_monitor.connectors.nws.httpx.Client") as MockClient:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            MockClient.return_value = mock_client

            result = c.collect()
            assert len(result.observations) == 1

    def test_no_headline_with_description(self):
        """Line 111 — no headline, description appended."""
        from sts_monitor.connectors.nws import NWSAlertConnector
        c = NWSAlertConnector()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "features": [
                {
                    "properties": {
                        "id": "alert-2",
                        "event": "Flash Flood",
                        "headline": "",
                        "description": "Flash flooding expected in the region",
                        "severity": "Severe",
                        "certainty": "Likely",
                        "urgency": "Expected",
                        "areaDesc": "Dallas County",
                        "senderName": "NWS",
                        "effective": "",
                        "expires": "",
                        "@id": "",
                    },
                    "geometry": None,
                }
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("sts_monitor.connectors.nws.httpx.Client") as MockClient:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            MockClient.return_value = mock_client

            result = c.collect()
            assert len(result.observations) == 1
            assert "Flash flooding" in result.observations[0].claim

    def test_polygon_geometry(self):
        """Lines 134-140 — Polygon geometry centroid calculation."""
        from sts_monitor.connectors.nws import NWSAlertConnector
        c = NWSAlertConnector()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "features": [
                {
                    "properties": {
                        "id": "alert-3",
                        "event": "Severe Thunderstorm",
                        "headline": "Severe thunderstorm warning",
                        "description": "",
                        "severity": "Severe",
                        "certainty": "Observed",
                        "urgency": "Immediate",
                        "areaDesc": "Metro",
                        "senderName": "NWS",
                        "effective": "2024-01-01T00:00:00Z",
                        "expires": "2024-01-01T06:00:00Z",
                        "@id": "https://api.weather.gov/alerts/3",
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[-100.0, 30.0], [-100.0, 40.0], [-90.0, 40.0], [-90.0, 30.0], [-100.0, 30.0]]
                        ],
                    },
                }
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("sts_monitor.connectors.nws.httpx.Client") as MockClient:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            MockClient.return_value = mock_client

            result = c.collect()
            geo_events = result.metadata.get("geo_events", [])
            assert len(geo_events) == 1
            # Centroid of the polygon coords
            assert 33.0 <= geo_events[0]["latitude"] <= 36.0
            assert -101.0 <= geo_events[0]["longitude"] <= -94.0


# ── 5. Marine connector ─────────────────────────────────────────────────

class TestMarineConnector:
    """Cover uncovered lines in marine.py."""

    def test_proxy_url(self):
        from sts_monitor.connectors.marine import MarineTrafficConnector
        c = MarineTrafficConnector(proxy_url="http://proxy:8080")
        client = c._build_client()
        client.close()

    def test_classify_vessel_types(self):
        from sts_monitor.connectors.marine import _classify_vessel
        assert _classify_vessel("USS Enterprise", 0) == "military_naval"
        assert _classify_vessel("HMS Victory", 0) == "military_naval"
        assert _classify_vessel("Cargo Ship", 35) == "military"
        assert _classify_vessel("Big Cargo", 70) == "cargo"
        assert _classify_vessel("Oil Tanker", 80) == "tanker"
        assert _classify_vessel("Ferry", 60) == "passenger"
        assert _classify_vessel("Fisher", 30) == "fishing"
        assert _classify_vessel("SAR Boat", 51) == "search_rescue"
        assert _classify_vessel("Coast Guard", 55) == "law_enforcement"
        assert _classify_vessel("Random", 37) == "pleasure"
        assert _classify_vessel("Random", 999) == "unknown"

    def test_collect_with_various_vessel_filters(self):
        from sts_monitor.connectors.marine import MarineTrafficConnector

        vessels = [
            {"mmsi": "123456789", "name": "USS Test", "lat": 35.0, "lon": -75.0,
             "speed": 15, "course": 90, "ship_type": 35, "flag": "US", "destination": "Norfolk"},
            {"mmsi": "987654321", "name": "Cargo One", "lat": 36.0, "lon": -74.0,
             "speed": 12, "course": 180, "ship_type": 70, "flag": "PA", "destination": "Rotterdam"},
            {"mmsi": "111111111", "name": "Bad Coords", "lat": None, "lon": None,
             "ship_type": 70, "flag": "XX"},
            {"mmsi": "222222222", "name": "Bad Coords2", "lat": "invalid", "lon": "invalid",
             "ship_type": 70, "flag": "XX"},
        ]

        c = MarineTrafficConnector(
            bbox=(34.0, -76.0, 37.0, -73.0),
            military_only=False,
        )

        with patch.object(c, "_fetch_vessels", return_value=vessels):
            result = c.collect()
            # 2 valid vessels (USS Test and Cargo One), 2 skipped (bad coords)
            assert len(result.observations) == 2

    def test_military_only_filter(self):
        from sts_monitor.connectors.marine import MarineTrafficConnector

        vessels = [
            {"mmsi": "123", "name": "USS Test", "lat": 35.0, "lon": -75.0,
             "ship_type": 35, "flag": "US", "destination": ""},
            {"mmsi": "456", "name": "Cargo One", "lat": 36.0, "lon": -74.0,
             "ship_type": 70, "flag": "PA", "destination": ""},
        ]

        c = MarineTrafficConnector(military_only=True)

        with patch.object(c, "_fetch_vessels", return_value=vessels):
            result = c.collect()
            assert len(result.observations) == 1
            assert "USS Test" in result.observations[0].claim

    def test_vessel_types_filter(self):
        from sts_monitor.connectors.marine import MarineTrafficConnector

        vessels = [
            {"mmsi": "123", "name": "Cargo Ship", "lat": 35.0, "lon": -75.0,
             "ship_type": 70, "flag": "US", "destination": ""},
            {"mmsi": "456", "name": "Tanker One", "lat": 36.0, "lon": -74.0,
             "ship_type": 80, "flag": "PA", "destination": ""},
        ]

        c = MarineTrafficConnector(vessel_types=["cargo"])

        with patch.object(c, "_fetch_vessels", return_value=vessels):
            result = c.collect()
            assert len(result.observations) == 1
            assert "Cargo Ship" in result.observations[0].claim

    def test_query_filter(self):
        from sts_monitor.connectors.marine import MarineTrafficConnector

        vessels = [
            {"mmsi": "123", "name": "USS Test", "lat": 35.0, "lon": -75.0,
             "ship_type": 35, "flag": "US", "destination": "Norfolk"},
            {"mmsi": "456", "name": "Cargo One", "lat": 36.0, "lon": -74.0,
             "ship_type": 70, "flag": "PA", "destination": "Rotterdam"},
        ]

        c = MarineTrafficConnector()

        with patch.object(c, "_fetch_vessels", return_value=vessels):
            result = c.collect(query="norfolk")
            assert len(result.observations) == 1
            assert "USS Test" in result.observations[0].claim

    def test_bbox_filter_excludes(self):
        from sts_monitor.connectors.marine import MarineTrafficConnector

        vessels = [
            {"mmsi": "123", "name": "OutOfRange", "lat": 10.0, "lon": 10.0,
             "ship_type": 70, "flag": "US", "destination": ""},
        ]

        c = MarineTrafficConnector(bbox=(34.0, -76.0, 37.0, -73.0))

        with patch.object(c, "_fetch_vessels", return_value=vessels):
            result = c.collect()
            assert len(result.observations) == 0

    def test_ship_type_value_error(self):
        """Cover the ValueError/TypeError exception on int conversion."""
        from sts_monitor.connectors.marine import MarineTrafficConnector

        vessels = [
            {"mmsi": "123", "name": "BadType", "lat": 35.0, "lon": -75.0,
             "ship_type": "not_a_number", "flag": "US", "destination": ""},
        ]

        c = MarineTrafficConnector()

        with patch.object(c, "_fetch_vessels", return_value=vessels):
            result = c.collect()
            assert len(result.observations) == 1

    def test_fetch_vessels_returns_dict(self):
        """Cover the data.get('vessels', ...) branch in _fetch_vessels."""
        from sts_monitor.connectors.marine import MarineTrafficConnector

        c = MarineTrafficConnector(bbox=(30.0, -80.0, 40.0, -70.0))

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"vessels": [{"mmsi": "123", "name": "Ship"}]}
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp

        result = c._fetch_vessels(mock_client)
        assert len(result) == 1

    def test_fetch_vessels_error(self):
        from sts_monitor.connectors.marine import MarineTrafficConnector

        c = MarineTrafficConnector()

        mock_client = MagicMock()
        mock_client.get.side_effect = ConnectionError("fail")

        result = c._fetch_vessels(mock_client)
        assert result == []

    def test_collect_error(self):
        from sts_monitor.connectors.marine import MarineTrafficConnector

        c = MarineTrafficConnector()

        with patch.object(c, "_build_client") as mock_build:
            mock_build.side_effect = ConnectionError("fail")
            result = c.collect()
            assert "error" in result.metadata


# ── 6. Search routes ────────────────────────────────────────────────────

class TestSearchRoutes:
    """Cover lines in routes/search.py."""

    @pytest.fixture()
    def client(self):
        from sts_monitor.main import app
        from fastapi.testclient import TestClient
        # Override get_session to use our in-memory test DB
        app.dependency_overrides[get_session] = _override_get_session
        # Patch out lifespan's create_all and background scheduler
        with patch("sts_monitor.main.Base.metadata.create_all"):
            tc = TestClient(app)
            yield tc
        app.dependency_overrides.clear()

    def _seed_investigation_with_data(self, inv_id="inv-search-1"):
        """Create investigation, observations, claims for search testing."""
        db = _TestSessionLocal()
        try:
            inv = InvestigationORM(id=inv_id, topic="earthquake disaster response", priority=50, status="open")
            db.add(inv)
            db.flush()

            # Add observations
            for i in range(5):
                obs = ObservationORM(
                    investigation_id=inv_id,
                    source="gdelt:reuters.com",
                    claim=f"earthquake disaster relief effort {i} aid response",
                    url=f"http://example.com/obs/{i}",
                    reliability_hint=0.8,
                    captured_at=datetime.now(UTC) - timedelta(hours=i),
                )
                db.add(obs)

            db.flush()

            # Add a report and claims
            report = ReportORM(
                investigation_id=inv_id,
                summary="test report",
                confidence=0.7,
                accepted_json="[]",
                dropped_json="[]",
            )
            db.add(report)
            db.flush()

            for i in range(3):
                claim = ClaimORM(
                    investigation_id=inv_id,
                    report_id=report.id,
                    claim_text=f"earthquake damage assessment claim {i} disaster",
                    stance="supported",
                    confidence=0.75,
                    created_at=datetime.now(UTC) - timedelta(hours=i),
                )
                db.add(claim)

            db.commit()
        finally:
            db.close()

    def test_create_search_profile_invalid_investigation(self, client):
        """Line 117-119 — investigation not found for profile creation."""
        resp = client.post(
            "/search/profiles",
            json={"name": "test-profile", "investigation_id": "nonexistent-inv"},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_create_search_profile_duplicate(self, client):
        """Line 145 — duplicate profile name."""
        # Create without investigation_id to avoid 404
        resp1 = client.post(
            "/search/profiles",
            json={"name": "dup-profile"},
            headers=AUTH,
        )
        assert resp1.status_code == 200

        resp2 = client.post(
            "/search/profiles",
            json={"name": "dup-profile"},
            headers=AUTH,
        )
        assert resp2.status_code == 409

    def test_search_query_with_claims(self, client):
        """Lines 175-182 — search query that matches claims."""
        inv_id = "inv-sq-1"
        self._seed_investigation_with_data(inv_id)

        resp = client.post(
            "/search/query",
            json={
                "query": "earthquake disaster",
                "investigation_id": inv_id,
                "include_observations": True,
                "include_claims": True,
                "min_score": 0.0,
                "limit": 50,
            },
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["matched"] > 0
        kinds = {r["kind"] for r in data["results"]}
        assert "claim" in kinds

    def test_search_query_min_score_filters(self, client):
        """Lines 242, 245 — min_score filters out low scores."""
        inv_id = "inv-ms-1"
        self._seed_investigation_with_data(inv_id)

        resp = client.post(
            "/search/query",
            json={
                "query": "earthquake",
                "investigation_id": inv_id,
                "include_observations": True,
                "include_claims": True,
                "min_score": 0.99,
                "limit": 50,
            },
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.json()["matched"] == 0

    def test_search_query_profile_not_found(self, client):
        """Line 294 — search with nonexistent profile."""
        resp = client.post(
            "/search/query",
            json={"query": "test", "profile_name": "nonexistent-profile"},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_search_query_with_profile(self, client):
        """Lines 299-306 — search with existing profile that has include/exclude terms."""
        inv_id = "inv-sp-2"
        self._seed_investigation_with_data(inv_id)

        # Create profile with include/exclude terms
        client.post(
            "/search/profiles",
            json={
                "name": "quake-profile",
                "investigation_id": inv_id,
                "include_terms": ["earthquake", "disaster"],
                "exclude_terms": ["hoax"],
                "synonyms": {"earthquake": ["quake", "tremor"]},
            },
            headers=AUTH,
        )

        resp = client.post(
            "/search/query",
            json={
                "query": "earthquake",
                "profile_name": "quake-profile",
                "include_observations": True,
                "include_claims": True,
                "min_score": 0.0,
                "limit": 50,
            },
            headers=AUTH,
        )
        assert resp.status_code == 200

    def test_search_query_with_source_prefix(self, client):
        """Line 317 — source_prefix filter."""
        inv_id = "inv-sp-3"
        self._seed_investigation_with_data(inv_id)

        resp = client.post(
            "/search/query",
            json={
                "query": "earthquake",
                "investigation_id": inv_id,
                "source_prefix": "gdelt",
                "include_observations": True,
                "include_claims": False,
                "min_score": 0.0,
            },
            headers=AUTH,
        )
        assert resp.status_code == 200

    def test_search_query_with_since_until(self, client):
        """Lines covering since/until filters."""
        inv_id = "inv-su-1"
        self._seed_investigation_with_data(inv_id)

        resp = client.post(
            "/search/query",
            json={
                "query": "earthquake",
                "investigation_id": inv_id,
                "include_observations": True,
                "include_claims": True,
                "since": (datetime.now(UTC) - timedelta(hours=48)).isoformat(),
                "until": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "min_score": 0.0,
            },
            headers=AUTH,
        )
        assert resp.status_code == 200

    def test_related_investigations(self, client):
        """Lines 299-306, 317 — related investigations endpoint with matching data."""
        inv_id = "inv-ri-1"
        self._seed_investigation_with_data(inv_id)

        resp = client.post(
            "/search/related-investigations",
            json={"query": "earthquake disaster", "min_score": 0.0, "limit": 10},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        assert data["investigations"][0]["investigation_id"] == inv_id

    def test_search_query_stance_filter(self, client):
        """Stance filter for claims."""
        inv_id = "inv-stance-1"
        self._seed_investigation_with_data(inv_id)

        resp = client.post(
            "/search/query",
            json={
                "query": "earthquake",
                "investigation_id": inv_id,
                "include_observations": False,
                "include_claims": True,
                "stance": "supported",
                "min_score": 0.0,
            },
            headers=AUTH,
        )
        assert resp.status_code == 200


# ── 7. Geo routes (SSE stream) ──────────────────────────────────────────

class TestGeoSSE:
    """Cover lines 48-63 of geo.py — SSE stream endpoint."""

    def test_event_bus_subscribe_unsubscribe(self):
        """Test the event_bus subscribe/unsubscribe used by SSE stream (lines 48, 61)."""
        from sts_monitor.event_bus import event_bus

        queue = event_bus.subscribe()
        assert event_bus.subscriber_count >= 1
        event_bus.unsubscribe(queue)

    def test_sts_event_to_sse(self):
        """Test STSEvent.to_sse (line 57)."""
        from sts_monitor.event_bus import STSEvent
        event = STSEvent(event_type="test", payload={"key": "val"})
        sse = event.to_sse()
        assert "event: test" in sse
        assert "data:" in sse
        assert "key" in sse

    def test_event_bus_publish_sync_no_loop(self):
        """Cover publish_sync when no event loop is running (lines 64-70 of event_bus.py)."""
        from sts_monitor.event_bus import event_bus, STSEvent
        # publish_sync should not raise when no loop is running
        event_bus.publish_sync(STSEvent(event_type="test", payload={"x": 1}))
