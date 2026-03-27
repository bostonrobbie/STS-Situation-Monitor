"""Active region profile helpers.

Provides the mapping from the canonical RegionProfileORM fields to the
connector-specific parameter names so each ingest endpoint can auto-fill
region defaults with a one-liner.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from sts_monitor.models import RegionProfileORM

# ISO-3 → FIPS country code mapping (GDELT uses FIPS)
_ISO_TO_FIPS = {"USA": "US", "GBR": "UK", "CAN": "CA", "AUS": "AS", "DEU": "GM", "FRA": "FR"}


def get_active_region(session: Session) -> RegionProfileORM | None:
    """Return the single active region profile, or None."""
    return session.execute(
        select(RegionProfileORM).where(RegionProfileORM.is_active.is_(True)).limit(1)
    ).scalar_one_or_none()


def region_defaults_for_connector(region: RegionProfileORM, connector_name: str) -> dict:
    """Map region profile fields to connector-specific kwargs.

    Returns a dict of param_name → value suitable for checking against
    the ingest request payload.  Only includes keys relevant to the
    given connector.
    """
    bbox_tuple = (region.bbox_lat_min, region.bbox_lon_min, region.bbox_lat_max, region.bbox_lon_max)
    fips_code = _ISO_TO_FIPS.get(region.country_code, region.country_code[:2] if region.country_code else None)

    mapping: dict[str, dict] = {
        "nws": {"area": region.state_code},
        "fema": {"state": region.state_code},
        "opensky": {
            "bbox_lamin": region.bbox_lat_min,
            "bbox_lomin": region.bbox_lon_min,
            "bbox_lamax": region.bbox_lat_max,
            "bbox_lomax": region.bbox_lon_max,
        },
        "webcams": {
            "nearby_lat": region.center_lat,
            "nearby_lon": region.center_lon,
            "nearby_radius_km": int(region.radius_km),
        },
        "nasa_firms": {"country_code": region.country_code},
        "gdelt": {"source_country": fips_code},
        "acled": {"country": region.country_name},
        "reliefweb": {"country": region.country_name},
    }
    return mapping.get(connector_name, {})


# Massachusetts seed data used by migration and startup seeding.
MASSACHUSETTS_DEFAULTS = {
    "name": "massachusetts",
    "display_name": "Massachusetts",
    "state_code": "MA",
    "country_code": "USA",
    "country_name": "United States",
    "bbox_lat_min": 41.0,
    "bbox_lon_min": -73.5,
    "bbox_lat_max": 42.9,
    "bbox_lon_max": -69.9,
    "center_lat": 42.36,
    "center_lon": -71.06,
    "radius_km": 120.0,
    "map_zoom": 8.0,
    "is_active": True,
}
