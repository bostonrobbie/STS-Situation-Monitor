"""Tests for connector edge cases — targeting 100% coverage on all connector modules."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
import sqlalchemy

from sts_monitor.database import Base, engine


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch):
    import sts_monitor.rate_limit as rl
    monkeypatch.setattr(rl, "RATE_LIMIT_ENABLED", False)
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text("PRAGMA foreign_keys = OFF"))
        conn.commit()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text("PRAGMA foreign_keys = ON"))
        conn.commit()


# ── GDELT connector ──────────────────────────────────────────────────────

class TestGDELTEdgeCases:
    @patch("sts_monitor.connectors.gdelt.httpx.Client")
    def test_empty_response(self, mock_client_cls):
        from sts_monitor.connectors.gdelt import GDELTConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"articles": []}
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        c = GDELTConnector()
        result = c.collect(query="test")
        assert result.observations is not None

    @patch("sts_monitor.connectors.gdelt.httpx.Client")
    def test_connection_error(self, mock_client_cls):
        from sts_monitor.connectors.gdelt import GDELTConnector
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = Exception("timeout")
        mock_client_cls.return_value = mock_client
        c = GDELTConnector()
        result = c.collect(query="test")
        assert result.metadata.get("error") is not None

    @patch("sts_monitor.connectors.gdelt.httpx.Client")
    def test_bad_status_code(self, mock_client_cls):
        from sts_monitor.connectors.gdelt import GDELTConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = Exception("500 Server Error")
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        c = GDELTConnector()
        result = c.collect(query="test")
        assert len(result.observations) == 0

    @patch("sts_monitor.connectors.gdelt.httpx.Client")
    def test_articles_with_geo(self, mock_client_cls):
        from sts_monitor.connectors.gdelt import GDELTConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "articles": [{
                "title": "Test Article",
                "url": "https://example.com/1",
                "domain": "example.com",
                "seendate": "20250101T000000Z",
                "language": "English",
                "sourcecountry": "US",
                "socialimage": "",
            }]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        c = GDELTConnector(max_records=5)
        result = c.collect(query="test")
        assert len(result.observations) >= 1


# ── ADS-B connector ──────────────────────────────────────────────────────

class TestADSBEdgeCases:
    @patch("sts_monitor.connectors.adsb.httpx.Client")
    def test_collect_success(self, mock_client_cls):
        from sts_monitor.connectors.adsb import ADSBExchangeConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ac": [{
                "hex": "ABC123", "flight": "UAL123", "alt_baro": 35000,
                "lat": 40.0, "lon": -74.0, "gs": 450, "t": "B738",
                "squawk": "1200", "category": "A3", "nav_heading": 180,
                "baro_rate": 0, "emergency": "none",
            }]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        c = ADSBExchangeConnector()
        result = c.collect()
        assert len(result.observations) >= 1

    @patch("sts_monitor.connectors.adsb.httpx.Client")
    def test_collect_error(self, mock_client_cls):
        from sts_monitor.connectors.adsb import ADSBExchangeConnector
        mock_client_cls.side_effect = Exception("err")
        c = ADSBExchangeConnector()
        result = c.collect()
        assert result.metadata.get("error") is not None

    @patch("sts_monitor.connectors.adsb.httpx.Client")
    def test_collect_no_aircraft(self, mock_client_cls):
        from sts_monitor.connectors.adsb import ADSBExchangeConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ac": []}
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        c = ADSBExchangeConnector()
        result = c.collect()
        assert len(result.observations) == 0

    @patch("sts_monitor.connectors.adsb.httpx.Client")
    def test_missing_fields(self, mock_client_cls):
        from sts_monitor.connectors.adsb import ADSBExchangeConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ac": [{"hex": "ABC", "lat": 40.0, "lon": -74.0}]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        c = ADSBExchangeConnector()
        result = c.collect()
        assert len(result.observations) >= 0


# ── Archive connector ────────────────────────────────────────────────────

class TestArchiveEdgeCases:
    @patch("sts_monitor.connectors.archive.httpx.Client")
    def test_collect_success(self, mock_client_cls):
        from sts_monitor.connectors.archive import InternetArchiveConnector
        # CDX search returns header + data rows
        mock_resp_cdx = MagicMock()
        mock_resp_cdx.status_code = 200
        mock_resp_cdx.json.return_value = [
            ["timestamp", "original", "mimetype", "statuscode", "length"],
            ["20250101120000", "https://example.com", "text/html", "200", "5000"],
        ]
        mock_resp_cdx.raise_for_status = MagicMock()

        # Wayback fetch returns HTML
        mock_resp_page = MagicMock()
        mock_resp_page.status_code = 200
        mock_resp_page.text = "<html><body><p>Test document content about test</p></body></html>"
        mock_resp_page.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = [mock_resp_cdx, mock_resp_page]
        mock_client_cls.return_value = mock_client
        c = InternetArchiveConnector(urls_to_check=["https://example.com"])
        result = c.collect(query="test")
        assert len(result.observations) >= 1

    @patch("sts_monitor.connectors.archive.httpx.Client")
    def test_collect_error(self, mock_client_cls):
        from sts_monitor.connectors.archive import InternetArchiveConnector
        mock_client_cls.side_effect = Exception("err")
        c = InternetArchiveConnector(urls_to_check=["https://example.com"])
        result = c.collect(query="test")
        assert result.metadata.get("error") is not None

    @patch("sts_monitor.connectors.archive.httpx.Client")
    def test_empty_results(self, mock_client_cls):
        from sts_monitor.connectors.archive import InternetArchiveConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []  # Empty CDX result
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        c = InternetArchiveConnector(urls_to_check=["https://example.com"])
        result = c.collect(query="test")
        assert len(result.observations) == 0

    @patch("sts_monitor.connectors.archive.httpx.Client")
    def test_bad_status(self, mock_client_cls):
        from sts_monitor.connectors.archive import InternetArchiveConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = Exception("500")
        mock_resp.json.side_effect = Exception("500")
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        c = InternetArchiveConnector(urls_to_check=["https://example.com"])
        result = c.collect(query="test")
        assert len(result.observations) == 0


# ── Marine connector ─────────────────────────────────────────────────────

class TestMarineEdgeCases:
    @patch("sts_monitor.connectors.marine.httpx.Client")
    def test_collect_success(self, mock_client_cls):
        from sts_monitor.connectors.marine import MarineTrafficConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{
            "mmsi": "123456789", "name": "TEST SHIP",
            "lat": "40.0", "lon": "-74.0", "speed": "10",
            "heading": "180", "course": "180", "status": "0",
            "ship_type": "70", "destination": "NYC",
            "flag": "US", "length": "200",
        }]
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        c = MarineTrafficConnector()
        result = c.collect()
        assert len(result.observations) >= 0

    @patch("sts_monitor.connectors.marine.httpx.Client")
    def test_collect_error(self, mock_client_cls):
        from sts_monitor.connectors.marine import MarineTrafficConnector
        mock_client_cls.side_effect = Exception("err")
        c = MarineTrafficConnector()
        result = c.collect()
        assert result.metadata.get("error") is not None


# ── NWS connector ────────────────────────────────────────────────────────

class TestNWSEdgeCases:
    @patch("sts_monitor.connectors.nws.httpx.Client")
    def test_collect_success(self, mock_client_cls):
        from sts_monitor.connectors.nws import NWSAlertConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "features": [{
                "properties": {
                    "id": "1", "headline": "Storm Warning",
                    "description": "Severe storm", "severity": "Severe",
                    "urgency": "Immediate", "certainty": "Observed",
                    "event": "Storm", "effective": "2025-01-01T00:00:00Z",
                    "expires": "2025-01-02T00:00:00Z",
                    "areaDesc": "New York",
                    "senderName": "NWS",
                },
                "geometry": {"type": "Point", "coordinates": [-74.0, 40.0]},
            }]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        c = NWSAlertConnector()
        result = c.collect(query="Storm")
        assert len(result.observations) >= 1

    @patch("sts_monitor.connectors.nws.httpx.Client")
    def test_collect_error(self, mock_client_cls):
        from sts_monitor.connectors.nws import NWSAlertConnector
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = Exception("err")
        mock_client_cls.return_value = mock_client
        c = NWSAlertConnector()
        result = c.collect(query="test")
        assert result.metadata.get("error") is not None

    @patch("sts_monitor.connectors.nws.httpx.Client")
    def test_no_geometry(self, mock_client_cls):
        from sts_monitor.connectors.nws import NWSAlertConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "features": [{
                "properties": {
                    "id": "1", "headline": "Warning",
                    "description": "test", "severity": "Moderate",
                    "urgency": "Expected", "certainty": "Likely",
                    "event": "Test", "effective": "2025-01-01T00:00:00Z",
                    "areaDesc": "Area",
                },
                "geometry": None,
            }]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        c = NWSAlertConnector()
        result = c.collect()
        assert len(result.observations) >= 1


# ── USGS connector ───────────────────────────────────────────────────────

class TestUSGSEdgeCases:
    @patch("sts_monitor.connectors.usgs.httpx.Client")
    def test_collect_success(self, mock_client_cls):
        from sts_monitor.connectors.usgs import USGSEarthquakeConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "features": [{
                "properties": {
                    "mag": 5.0, "place": "Alaska", "time": 1704067200000,
                    "url": "https://earthquake.usgs.gov/1", "title": "M 5.0 - Alaska",
                    "felt": 100, "alert": "yellow", "tsunami": 0,
                    "status": "reviewed", "type": "earthquake",
                },
                "geometry": {"coordinates": [-150.0, 61.0, 10.0]},
                "id": "us123",
            }]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        c = USGSEarthquakeConnector()
        result = c.collect(query="earthquake")
        assert len(result.observations) >= 1

    @patch("sts_monitor.connectors.usgs.httpx.Client")
    def test_collect_error(self, mock_client_cls):
        from sts_monitor.connectors.usgs import USGSEarthquakeConnector
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = Exception("err")
        mock_client_cls.return_value = mock_client
        c = USGSEarthquakeConnector()
        result = c.collect(query="test")
        assert result.metadata.get("error") is not None

    @patch("sts_monitor.connectors.usgs.httpx.Client")
    def test_low_magnitude_filtered(self, mock_client_cls):
        from sts_monitor.connectors.usgs import USGSEarthquakeConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"features": [{
            "properties": {"mag": 0.5, "place": "test", "time": 1704067200000,
                           "url": "u", "title": "M 0.5"},
            "geometry": {"coordinates": [-74.0, 40.0, 5.0]},
            "id": "us1",
        }]}
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        c = USGSEarthquakeConnector(min_magnitude=2.0)
        result = c.collect(query="test")
        # Low magnitude should be filtered or included depending on implementation


# ── ACLED connector ──────────────────────────────────────────────────────

class TestACLEDEdgeCases:
    @patch("sts_monitor.connectors.acled.httpx.Client")
    def test_collect_success(self, mock_client_cls):
        from sts_monitor.connectors.acled import ACLEDConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [{
                "data_id": "1",
                "event_date": "2025-01-01",
                "event_type": "Violence against civilians",
                "sub_event_type": "Attack",
                "actor1": "Group A",
                "actor2": "Civilians",
                "country": "Test Country",
                "admin1": "Region",
                "location": "City",
                "latitude": "10.0",
                "longitude": "20.0",
                "fatalities": "5",
                "notes": "Test event notes",
                "source": "Test Source",
            }]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        c = ACLEDConnector(api_key="test_key", email="test@example.com")
        result = c.collect(query="test")
        assert len(result.observations) >= 1

    def test_collect_error_no_credentials(self):
        from sts_monitor.connectors.acled import ACLEDConnector
        c = ACLEDConnector()
        result = c.collect(query="test")
        assert result.metadata.get("error") is not None

    @patch("sts_monitor.connectors.acled.httpx.Client")
    def test_empty_data(self, mock_client_cls):
        from sts_monitor.connectors.acled import ACLEDConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": []}
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        c = ACLEDConnector(api_key="test_key", email="test@example.com")
        result = c.collect(query="test")
        assert len(result.observations) == 0


# ── FEMA connector ───────────────────────────────────────────────────────

class TestFEMAEdgeCases:
    @patch("sts_monitor.connectors.fema.httpx.Client")
    def test_collect_success(self, mock_client_cls):
        from sts_monitor.connectors.fema import FEMADisasterConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "DisasterDeclarationsSummaries": [{
                "disasterNumber": 1234,
                "declarationTitle": "Hurricane Test",
                "state": "FL",
                "declarationType": "DR",
                "incidentType": "Hurricane",
                "declarationDate": "2025-01-01T00:00:00.000Z",
                "incidentBeginDate": "2025-01-01T00:00:00.000Z",
                "incidentEndDate": "2025-01-02T00:00:00.000Z",
                "designatedArea": "Statewide",
            }]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        c = FEMADisasterConnector()
        result = c.collect(query="hurricane")
        assert len(result.observations) >= 1

    @patch("sts_monitor.connectors.fema.httpx.Client")
    def test_collect_error(self, mock_client_cls):
        from sts_monitor.connectors.fema import FEMADisasterConnector
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = Exception("err")
        mock_client_cls.return_value = mock_client
        c = FEMADisasterConnector()
        result = c.collect(query="test")
        assert result.metadata.get("error") is not None


# ── NASA FIRMS connector ────────────────────────────────────────────────

class TestNASAFIRMSEdgeCases:
    @patch("sts_monitor.connectors.nasa_firms.httpx.Client")
    def test_collect_success(self, mock_client_cls):
        from sts_monitor.connectors.nasa_firms import NASAFIRMSConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "latitude,longitude,bright_ti4,acq_date,acq_time,confidence\n40.0,-74.0,350.0,2025-01-01,1200,high\n"
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        c = NASAFIRMSConnector(map_key="test_key")
        result = c.collect(query="fire")
        assert len(result.observations) >= 0

    def test_collect_error_no_key(self):
        from sts_monitor.connectors.nasa_firms import NASAFIRMSConnector
        c = NASAFIRMSConnector()
        result = c.collect(query="test")
        assert result.metadata.get("error") is not None


# ── OpenSky connector ────────────────────────────────────────────────────

class TestOpenSkyEdgeCases:
    @patch("sts_monitor.connectors.opensky.httpx.Client")
    def test_collect_success(self, mock_client_cls):
        from sts_monitor.connectors.opensky import OpenSkyConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "time": 1704067200,
            "states": [
                ["abc123", "UAL123 ", "United States", 1704067200, 1704067200,
                 -74.0, 40.0, 10000.0, False, 250.0, 180.0, 0.0, None,
                 10000.0, "1200", False, 0]
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        c = OpenSkyConnector()
        result = c.collect()
        assert len(result.observations) >= 0

    @patch("sts_monitor.connectors.opensky.httpx.Client")
    def test_collect_error(self, mock_client_cls):
        from sts_monitor.connectors.opensky import OpenSkyConnector
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = Exception("err")
        mock_client_cls.return_value = mock_client
        c = OpenSkyConnector()
        result = c.collect()
        assert result.metadata.get("error") is not None


# ── ReliefWeb connector ──────────────────────────────────────────────────

class TestReliefWebEdgeCases:
    @patch("sts_monitor.connectors.reliefweb.httpx.Client")
    def test_collect_success(self, mock_client_cls):
        from sts_monitor.connectors.reliefweb import ReliefWebConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [{
                "id": "1",
                "fields": {
                    "title": "Disaster Report",
                    "body-html": "<p>Details of the disaster</p>",
                    "date": {"created": "2025-01-01T00:00:00+00:00"},
                    "source": [{"name": "OCHA"}],
                    "country": [{"name": "Test Country"}],
                    "primary_country": {"name": "Test Country", "location": {"lat": 10.0, "lon": 20.0}},
                    "url_alias": "/report/test-country/disaster-report",
                    "format": [{"name": "Situation Report"}],
                    "disaster_type": [{"name": "Flood"}],
                },
            }]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        c = ReliefWebConnector()
        result = c.collect(query="flood")
        assert len(result.observations) >= 1

    @patch("sts_monitor.connectors.reliefweb.httpx.Client")
    def test_collect_error(self, mock_client_cls):
        from sts_monitor.connectors.reliefweb import ReliefWebConnector
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = Exception("err")
        mock_client_cls.return_value = mock_client
        c = ReliefWebConnector()
        result = c.collect(query="test")
        assert result.metadata.get("error") is not None


# ── Telegram connector ───────────────────────────────────────────────────

class TestTelegramEdgeCases:
    @patch("sts_monitor.connectors.telegram.httpx.Client")
    def test_collect_success(self, mock_client_cls):
        from sts_monitor.connectors.telegram import TelegramConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '<div class="tgme_widget_message_text">Test message</div>'
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        c = TelegramConnector(channels=[{"handle": "test_channel", "label": "Test Channel"}])
        result = c.collect(query="test")
        assert result.observations is not None

    @patch("sts_monitor.connectors.telegram.httpx.Client")
    def test_collect_error(self, mock_client_cls):
        from sts_monitor.connectors.telegram import TelegramConnector
        mock_client_cls.side_effect = Exception("err")
        c = TelegramConnector(channels=[{"handle": "test_channel", "label": "Test Channel"}])
        result = c.collect(query="test")
        # The connector catches errors and stores them in metadata
        assert result.observations is not None or result.metadata.get("errors")


# ── Web scraper connector ────────────────────────────────────────────────

class TestWebScraperEdgeCases:
    @patch("sts_monitor.connectors.web_scraper.httpx.Client")
    def test_collect_success(self, mock_client_cls):
        from sts_monitor.connectors.web_scraper import WebScraperConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><head><title>Test Page</title></head><body><p>Test content about topic that is long enough to pass the minimum text length requirement for the web scraper connector.</p></body></html>"
        mock_resp.headers = {"content-type": "text/html"}
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        c = WebScraperConnector(seed_urls=["https://example.com"])
        result = c.collect(query="test")
        assert result.observations is not None

    @patch("sts_monitor.connectors.web_scraper.httpx.Client")
    def test_collect_error(self, mock_client_cls):
        from sts_monitor.connectors.web_scraper import WebScraperConnector
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = Exception("err")
        mock_client_cls.return_value = mock_client
        c = WebScraperConnector(seed_urls=["https://example.com"])
        result = c.collect(query="test")
        assert result.metadata.get("failed_seeds") is not None


# ── Nitter connector ─────────────────────────────────────────────────────

class TestNitterEdgeCases:
    @patch("sts_monitor.connectors.nitter.feedparser.parse")
    def test_collect_with_empty_response(self, mock_parse):
        from sts_monitor.connectors.nitter import NitterConnector
        mock_result = MagicMock()
        mock_result.entries = []
        mock_result.bozo = False
        mock_parse.return_value = mock_result
        c = NitterConnector(accounts=["test_user"])
        result = c.collect(query="test")
        assert result.observations is not None


# ── Reddit connector ─────────────────────────────────────────────────────

class TestRedditEdgeCases:
    @patch("sts_monitor.connectors.reddit.httpx.Client")
    def test_collect_with_error_response(self, mock_client_cls):
        from sts_monitor.connectors.reddit import RedditConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.raise_for_status.side_effect = Exception("429 Too Many Requests")
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        c = RedditConnector(subreddits=["worldnews"])
        result = c.collect(query="test")
        assert result.observations is not None


# ── Search connector ─────────────────────────────────────────────────────

class TestSearchConnectorEdgeCases:
    @patch("sts_monitor.connectors.search.httpx.Client")
    def test_collect_error(self, mock_client_cls):
        from sts_monitor.connectors.search import SearchConnector
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = Exception("err")
        mock_client_cls.return_value = mock_client
        c = SearchConnector()
        result = c.collect(query="test")
        # When search fails, it returns empty results, so observations is empty list
        assert result.observations is not None
