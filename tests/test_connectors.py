"""Tests for all new connectors with mocked HTTP."""
from __future__ import annotations

import csv
import io

import pytest

from sts_monitor.connectors.gdelt import GDELTConnector
from sts_monitor.connectors.usgs import USGSEarthquakeConnector
from sts_monitor.connectors.nasa_firms import NASAFIRMSConnector
from sts_monitor.connectors.acled import ACLEDConnector
from sts_monitor.connectors.nws import NWSAlertConnector
from sts_monitor.connectors.fema import FEMADisasterConnector
from sts_monitor.connectors.reliefweb import ReliefWebConnector
from sts_monitor.connectors.opensky import OpenSkyConnector
from sts_monitor.connectors.webcams import (
    CURATED_CAMERAS,
    WebcamConnector,
    get_cameras_near,
    list_camera_regions,
)

pytestmark = pytest.mark.unit


# ── Shared fake HTTP helpers (same pattern as test_reddit_connector.py) ─


class _FakeResponse:
    def __init__(self, payload=None, text: str = "", status_code: int = 200) -> None:
        self._payload = payload
        self._text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self) -> dict:
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

    def post(self, url: str, **kwargs):
        return self._response


class _FakeErrorClient:
    """Client that raises on any request."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url: str, **kwargs):
        raise ConnectionError("network unreachable")

    def post(self, url: str, **kwargs):
        raise ConnectionError("network unreachable")


def _patch_httpx(monkeypatch, module_path: str, response: _FakeResponse) -> None:
    monkeypatch.setattr(module_path, lambda **kwargs: _FakeClient(response))


def _patch_httpx_error(monkeypatch, module_path: str) -> None:
    monkeypatch.setattr(module_path, lambda **kwargs: _FakeErrorClient())


# ═══════════════════════════════════════════════════════════════════════
# GDELT Connector
# ═══════════════════════════════════════════════════════════════════════


def test_gdelt_collect_returns_observations(monkeypatch) -> None:
    payload = {
        "articles": [
            {
                "url": "https://reuters.com/article/123",
                "title": "Earthquake hits Turkey",
                "domain": "reuters.com",
                "seendate": "20250115120000",
                "sourcecountry": "US",
                "language": "English",
            },
            {
                "url": "https://bbc.co.uk/news/456",
                "title": "Aid convoys enter Gaza",
                "domain": "bbc.co.uk",
                "seendate": "20250115130000",
                "sourcecountry": "GB",
                "language": "English",
            },
        ]
    }
    _patch_httpx(monkeypatch, "sts_monitor.connectors.gdelt.httpx.Client", _FakeResponse(payload=payload))

    connector = GDELTConnector(max_records=10)
    result = connector.collect(query="earthquake")

    assert result.connector == "gdelt"
    assert len(result.observations) == 2
    assert result.observations[0].source == "gdelt:reuters.com"
    assert result.metadata["article_count"] == 2


def test_gdelt_collect_no_query_returns_error(monkeypatch) -> None:
    connector = GDELTConnector()
    result = connector.collect(query=None)
    assert len(result.observations) == 0
    assert "error" in result.metadata


def test_gdelt_collect_handles_http_error(monkeypatch) -> None:
    _patch_httpx_error(monkeypatch, "sts_monitor.connectors.gdelt.httpx.Client")
    connector = GDELTConnector()
    result = connector.collect(query="test")
    assert len(result.observations) == 0
    assert "error" in result.metadata


def test_gdelt_skips_articles_without_title(monkeypatch) -> None:
    payload = {
        "articles": [
            {"url": "https://example.com/1", "title": "", "domain": "example.com"},
            {"url": "https://example.com/2", "title": "Valid Article", "domain": "example.com"},
        ]
    }
    _patch_httpx(monkeypatch, "sts_monitor.connectors.gdelt.httpx.Client", _FakeResponse(payload=payload))

    connector = GDELTConnector()
    result = connector.collect(query="test")
    assert len(result.observations) == 1


# ═══════════════════════════════════════════════════════════════════════
# USGS Earthquake Connector
# ═══════════════════════════════════════════════════════════════════════


def test_usgs_collect_returns_observations(monkeypatch) -> None:
    payload = {
        "features": [
            {
                "id": "us2024abc",
                "geometry": {"coordinates": [-118.2, 34.0, 10.5]},
                "properties": {
                    "mag": 5.2,
                    "place": "15km NW of Los Angeles, CA",
                    "time": 1705324800000,
                    "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us2024abc",
                    "detail": "https://earthquake.usgs.gov/fdsnws/event/1/query?eventid=us2024abc",
                    "tsunami": 0,
                    "alert": "yellow",
                    "felt": 120,
                    "sig": 450,
                    "type": "earthquake",
                },
            }
        ]
    }
    _patch_httpx(monkeypatch, "sts_monitor.connectors.usgs.httpx.Client", _FakeResponse(payload=payload))

    connector = USGSEarthquakeConnector(min_magnitude=4.0)
    result = connector.collect()

    assert result.connector == "usgs_earthquake"
    assert len(result.observations) == 1
    assert "M5.2" in result.observations[0].claim
    assert "Los Angeles" in result.observations[0].claim
    assert result.metadata["event_count"] == 1
    assert len(result.metadata["geo_events"]) == 1


def test_usgs_collect_handles_http_error(monkeypatch) -> None:
    _patch_httpx_error(monkeypatch, "sts_monitor.connectors.usgs.httpx.Client")
    connector = USGSEarthquakeConnector()
    result = connector.collect()
    assert len(result.observations) == 0
    assert "error" in result.metadata


def test_usgs_query_filter(monkeypatch) -> None:
    payload = {
        "features": [
            {
                "id": "us2024abc",
                "geometry": {"coordinates": [-118.2, 34.0, 10.5]},
                "properties": {
                    "mag": 5.2,
                    "place": "15km NW of Los Angeles, CA",
                    "time": 1705324800000,
                    "url": "",
                    "tsunami": 0,
                    "type": "earthquake",
                },
            }
        ]
    }
    _patch_httpx(monkeypatch, "sts_monitor.connectors.usgs.httpx.Client", _FakeResponse(payload=payload))

    connector = USGSEarthquakeConnector()
    result = connector.collect(query="Tokyo")  # Should filter out LA earthquake
    assert len(result.observations) == 0


# ═══════════════════════════════════════════════════════════════════════
# NASA FIRMS Connector
# ═══════════════════════════════════════════════════════════════════════


def _firms_csv() -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "latitude", "longitude", "bright_ti4", "frp", "confidence",
        "acq_date", "acq_time", "satellite", "daynight",
    ])
    writer.writeheader()
    writer.writerow({
        "latitude": "34.0522", "longitude": "-118.2437",
        "bright_ti4": "330.5", "frp": "15.2", "confidence": "nominal",
        "acq_date": "2025-01-15", "acq_time": "1200",
        "satellite": "NOAA-20", "daynight": "D",
    })
    writer.writerow({
        "latitude": "35.0", "longitude": "-119.0",
        "bright_ti4": "310.0", "frp": "5.0", "confidence": "low",
        "acq_date": "2025-01-15", "acq_time": "1205",
        "satellite": "NOAA-20", "daynight": "D",
    })
    return output.getvalue()


def test_nasa_firms_collect_returns_observations(monkeypatch) -> None:
    _patch_httpx(
        monkeypatch,
        "sts_monitor.connectors.nasa_firms.httpx.Client",
        _FakeResponse(text=_firms_csv()),
    )
    connector = NASAFIRMSConnector(map_key="test-key", min_confidence="nominal")
    result = connector.collect()

    assert result.connector == "nasa_firms"
    assert len(result.observations) == 1  # "low" confidence filtered
    assert "34.052" in result.observations[0].claim
    assert result.metadata["filtered_low_confidence"] == 1


def test_nasa_firms_no_key_returns_error() -> None:
    connector = NASAFIRMSConnector(map_key="")
    result = connector.collect()
    assert len(result.observations) == 0
    assert "error" in result.metadata


def test_nasa_firms_handles_http_error(monkeypatch) -> None:
    _patch_httpx_error(monkeypatch, "sts_monitor.connectors.nasa_firms.httpx.Client")
    connector = NASAFIRMSConnector(map_key="test-key")
    result = connector.collect()
    assert len(result.observations) == 0
    assert "error" in result.metadata


# ═══════════════════════════════════════════════════════════════════════
# ACLED Connector
# ═══════════════════════════════════════════════════════════════════════


def test_acled_collect_returns_observations(monkeypatch) -> None:
    payload = {
        "data": [
            {
                "data_id": "12345",
                "event_date": "2025-01-10",
                "event_type": "Battles",
                "sub_event_type": "Armed clash",
                "actor1": "Military Forces",
                "actor2": "Rebel Group",
                "country": "Sudan",
                "admin1": "Darfur",
                "location": "El Fasher",
                "latitude": "13.6300",
                "longitude": "25.3500",
                "fatalities": "5",
                "notes": "Clash near city center",
                "source": "Local media",
                "source_scale": "Subnational",
                "inter1": "1",
            }
        ]
    }
    _patch_httpx(monkeypatch, "sts_monitor.connectors.acled.httpx.Client", _FakeResponse(payload=payload))

    connector = ACLEDConnector(api_key="test", email="test@test.com")
    result = connector.collect()

    assert result.connector == "acled"
    assert len(result.observations) == 1
    assert "Battles" in result.observations[0].claim
    assert "El Fasher" in result.observations[0].claim
    assert result.metadata["event_count"] == 1


def test_acled_no_credentials_returns_error() -> None:
    connector = ACLEDConnector(api_key="", email="")
    result = connector.collect()
    assert len(result.observations) == 0
    assert "error" in result.metadata


def test_acled_handles_http_error(monkeypatch) -> None:
    _patch_httpx_error(monkeypatch, "sts_monitor.connectors.acled.httpx.Client")
    connector = ACLEDConnector(api_key="test", email="test@test.com")
    result = connector.collect()
    assert len(result.observations) == 0
    assert "error" in result.metadata


def test_acled_query_filter(monkeypatch) -> None:
    payload = {
        "data": [
            {
                "data_id": "12345",
                "event_date": "2025-01-10",
                "event_type": "Protests",
                "sub_event_type": "Peaceful protest",
                "actor1": "Protesters",
                "actor2": "",
                "country": "France",
                "admin1": "Paris",
                "location": "Paris",
                "latitude": "48.8566",
                "longitude": "2.3522",
                "fatalities": "0",
                "notes": "",
                "source": "",
                "source_scale": "",
                "inter1": "",
            }
        ]
    }
    _patch_httpx(monkeypatch, "sts_monitor.connectors.acled.httpx.Client", _FakeResponse(payload=payload))

    connector = ACLEDConnector(api_key="test", email="test@test.com")
    result = connector.collect(query="Sudan")  # Filter: should not match France
    assert len(result.observations) == 0


# ═══════════════════════════════════════════════════════════════════════
# NWS Alert Connector
# ═══════════════════════════════════════════════════════════════════════


def test_nws_collect_returns_observations(monkeypatch) -> None:
    payload = {
        "features": [
            {
                "properties": {
                    "id": "urn:oid:2.49.0.1.840.0.abc",
                    "event": "Tornado Warning",
                    "headline": "Tornado Warning for Oklahoma County until 6PM",
                    "description": "A tornado has been sighted moving northeast.",
                    "severity": "Extreme",
                    "certainty": "Observed",
                    "urgency": "Immediate",
                    "areaDesc": "Oklahoma County, OK",
                    "senderName": "NWS Norman OK",
                    "effective": "2025-01-15T17:00:00Z",
                    "expires": "2025-01-15T18:00:00Z",
                    "@id": "https://api.weather.gov/alerts/abc123",
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [-97.5, 35.5],
                },
            }
        ]
    }
    _patch_httpx(monkeypatch, "sts_monitor.connectors.nws.httpx.Client", _FakeResponse(payload=payload))

    connector = NWSAlertConnector()
    result = connector.collect()

    assert result.connector == "nws_alerts"
    assert len(result.observations) == 1
    assert "Tornado" in result.observations[0].claim
    assert result.observations[0].reliability_hint == 0.95
    assert len(result.metadata["geo_events"]) == 1


def test_nws_handles_http_error(monkeypatch) -> None:
    _patch_httpx_error(monkeypatch, "sts_monitor.connectors.nws.httpx.Client")
    connector = NWSAlertConnector()
    result = connector.collect()
    assert len(result.observations) == 0
    assert "error" in result.metadata


def test_nws_query_filter(monkeypatch) -> None:
    payload = {
        "features": [
            {
                "properties": {
                    "id": "abc",
                    "event": "Tornado Warning",
                    "headline": "Tornado Warning for Oklahoma",
                    "description": "",
                    "severity": "Extreme",
                    "areaDesc": "Oklahoma",
                    "effective": "",
                    "expires": "",
                    "@id": "",
                },
                "geometry": None,
            }
        ]
    }
    _patch_httpx(monkeypatch, "sts_monitor.connectors.nws.httpx.Client", _FakeResponse(payload=payload))

    connector = NWSAlertConnector()
    result = connector.collect(query="Hurricane")  # Should filter out tornado
    assert len(result.observations) == 0


# ═══════════════════════════════════════════════════════════════════════
# FEMA Disaster Connector
# ═══════════════════════════════════════════════════════════════════════


def test_fema_collect_returns_observations(monkeypatch) -> None:
    payload = {
        "DisasterDeclarationsSummaries": [
            {
                "disasterNumber": "4800",
                "declarationDate": "2025-01-10T00:00:00.000Z",
                "state": "CA",
                "declarationType": "DR",
                "incidentType": "Fire",
                "declarationTitle": "California Wildfires",
                "designatedArea": "Los Angeles County",
                "incidentBeginDate": "2025-01-07T00:00:00.000Z",
                "incidentEndDate": "",
                "placeCode": "06037",
            }
        ]
    }
    _patch_httpx(monkeypatch, "sts_monitor.connectors.fema.httpx.Client", _FakeResponse(payload=payload))

    connector = FEMADisasterConnector()
    result = connector.collect()

    assert result.connector == "fema"
    assert len(result.observations) == 1
    assert "California Wildfires" in result.observations[0].claim
    assert result.observations[0].reliability_hint == 0.95
    assert len(result.metadata["geo_events"]) == 1


def test_fema_handles_http_error(monkeypatch) -> None:
    _patch_httpx_error(monkeypatch, "sts_monitor.connectors.fema.httpx.Client")
    connector = FEMADisasterConnector()
    result = connector.collect()
    assert len(result.observations) == 0
    assert "error" in result.metadata


def test_fema_query_filter(monkeypatch) -> None:
    payload = {
        "DisasterDeclarationsSummaries": [
            {
                "disasterNumber": "4800",
                "declarationDate": "2025-01-10T00:00:00.000Z",
                "state": "CA",
                "declarationType": "DR",
                "incidentType": "Fire",
                "declarationTitle": "California Wildfires",
                "designatedArea": "LA County",
                "incidentBeginDate": "",
                "incidentEndDate": "",
                "placeCode": "",
            }
        ]
    }
    _patch_httpx(monkeypatch, "sts_monitor.connectors.fema.httpx.Client", _FakeResponse(payload=payload))

    connector = FEMADisasterConnector()
    result = connector.collect(query="Hurricane")
    assert len(result.observations) == 0


# ═══════════════════════════════════════════════════════════════════════
# ReliefWeb Connector
# ═══════════════════════════════════════════════════════════════════════


def test_reliefweb_collect_returns_observations(monkeypatch) -> None:
    payload = {
        "data": [
            {
                "id": "12345",
                "fields": {
                    "title": "Syria Humanitarian Update",
                    "url_alias": "/report/syria/humanitarian-update",
                    "date": {"created": "2025-01-14T00:00:00+00:00"},
                    "source": [{"name": "OCHA"}],
                    "country": [{"name": "Syria"}],
                    "primary_country": {
                        "name": "Syria",
                        "location": {"lat": 35.0, "lon": 38.0},
                    },
                    "format": [{"name": "Situation Report"}],
                    "disaster_type": [{"name": "Complex Emergency"}],
                },
            }
        ]
    }
    _patch_httpx(monkeypatch, "sts_monitor.connectors.reliefweb.httpx.Client", _FakeResponse(payload=payload))

    connector = ReliefWebConnector()
    result = connector.collect()

    assert result.connector == "reliefweb"
    assert len(result.observations) == 1
    assert "Syria" in result.observations[0].claim
    assert result.metadata["report_count"] == 1
    assert len(result.metadata["geo_events"]) == 1


def test_reliefweb_handles_http_error(monkeypatch) -> None:
    _patch_httpx_error(monkeypatch, "sts_monitor.connectors.reliefweb.httpx.Client")
    connector = ReliefWebConnector()
    result = connector.collect()
    assert len(result.observations) == 0
    assert "error" in result.metadata


# ═══════════════════════════════════════════════════════════════════════
# OpenSky Connector
# ═══════════════════════════════════════════════════════════════════════


def test_opensky_collect_returns_observations(monkeypatch) -> None:
    payload = {
        "time": 1705324800,
        "states": [
            [
                "abc123", "UAL123  ", "United States", 1705324700, 1705324800,
                -73.9, 40.7, 10000, False, 250.0, 90.0, 0.0,
                None, 10500, "7700", False, 0,
            ],
        ],
    }
    _patch_httpx(monkeypatch, "sts_monitor.connectors.opensky.httpx.Client", _FakeResponse(payload=payload))

    connector = OpenSkyConnector()
    result = connector.collect()

    assert result.connector == "opensky"
    assert len(result.observations) == 1
    assert "UAL123" in result.observations[0].claim
    assert "EMERGENCY" in result.observations[0].claim
    assert result.observations[0].reliability_hint == 0.92


def test_opensky_handles_http_error(monkeypatch) -> None:
    _patch_httpx_error(monkeypatch, "sts_monitor.connectors.opensky.httpx.Client")
    connector = OpenSkyConnector()
    result = connector.collect()
    assert len(result.observations) == 0
    assert "error" in result.metadata


def test_opensky_query_filter(monkeypatch) -> None:
    payload = {
        "time": 1705324800,
        "states": [
            [
                "abc123", "UAL123  ", "United States", 1705324700, 1705324800,
                -73.9, 40.7, 10000, False, 250.0, 90.0, 0.0,
                None, 10500, "", False, 0,
            ],
        ],
    }
    _patch_httpx(monkeypatch, "sts_monitor.connectors.opensky.httpx.Client", _FakeResponse(payload=payload))

    connector = OpenSkyConnector()
    # Filter by callsign that doesn't match
    result = connector.collect(query="Lufthansa")
    assert len(result.observations) == 0


# ═══════════════════════════════════════════════════════════════════════
# Webcam Connector
# ═══════════════════════════════════════════════════════════════════════


def test_webcam_curated_cameras_no_http() -> None:
    """Curated cameras work without any HTTP calls."""
    connector = WebcamConnector(regions=["ukraine"])
    result = connector.collect()

    assert result.connector == "webcams"
    assert len(result.observations) == len(CURATED_CAMERAS["ukraine"])
    assert result.metadata["total_cameras"] == len(CURATED_CAMERAS["ukraine"])


def test_webcam_query_filter() -> None:
    connector = WebcamConnector(regions=["ukraine"])
    result = connector.collect(query="Kyiv")
    assert len(result.observations) >= 1
    assert all("Kyiv" in o.claim for o in result.observations)


def test_webcam_all_regions() -> None:
    connector = WebcamConnector()  # All regions by default
    result = connector.collect()
    total_expected = sum(len(cams) for cams in CURATED_CAMERAS.values())
    assert len(result.observations) == total_expected


# ═══════════════════════════════════════════════════════════════════════
# Webcam utility functions
# ═══════════════════════════════════════════════════════════════════════


def test_list_camera_regions() -> None:
    regions = list_camera_regions()
    assert len(regions) == len(CURATED_CAMERAS)
    region_names = {r["region"] for r in regions}
    assert "ukraine" in region_names
    assert "middle_east" in region_names
    for r in regions:
        assert r["camera_count"] > 0


def test_get_cameras_near_kyiv() -> None:
    # Kyiv coordinates: ~50.45, 30.52
    cameras = get_cameras_near(50.45, 30.52, radius_km=200)
    assert len(cameras) >= 1
    assert any("Kyiv" in c["name"] for c in cameras)
    # Should be sorted by distance
    for i in range(len(cameras) - 1):
        assert cameras[i]["distance_km"] <= cameras[i + 1]["distance_km"]


def test_get_cameras_near_middle_of_ocean_returns_empty() -> None:
    # Middle of the Pacific Ocean
    cameras = get_cameras_near(0.0, -160.0, radius_km=50)
    assert len(cameras) == 0
