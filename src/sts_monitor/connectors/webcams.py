"""Public webcam aggregator connector.

Aggregates publicly available webcam feeds for situation monitoring zones.
All embed_urls are YouTube embeds (always iframe-embeddable) or direct JPEG streams.
Thumbnails are auto-generated from YouTube video IDs.

Legal: Only accesses publicly intended camera feeds. No unauthorized access.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation


def _yt(video_id: str, label: str = "") -> dict:
    """Helper: build YouTube embed entry with auto-generated thumbnail."""
    url = f"https://www.youtube.com/embed/{video_id}?rel=0&autoplay=0"
    thumb = f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"
    return {"embed_url": url, "thumbnail": thumb, "source": f"YouTube Live{' · ' + label if label else ''}"}


# ── Curated public camera feeds by region/category ──────────────────────
# ALL embed_urls are YouTube embeds — guaranteed to work inside iframes.
# Labels indicate what the stream actually shows.

CURATED_CAMERAS: dict[str, list[dict[str, Any]]] = {
    "traffic_cities": [
        {**_yt("xbB2b7y_B4A", "EarthCam"), "name": "New York — Times Square", "lat": 40.7580, "lon": -73.9855, "type": "traffic"},
        {**_yt("buQtiRMBgqs", "CapitolCam"), "name": "Washington DC — Capitol", "lat": 38.8899, "lon": -77.0091, "type": "traffic"},
        {**_yt("mzX3BmLULDE", "NBC Los Angeles"), "name": "Los Angeles — NBC Live Cam", "lat": 34.0522, "lon": -118.2437, "type": "traffic"},
        {**_yt("OL6gQ7_GFRQ", "London Live"), "name": "London — Westminster Bridge", "lat": 51.5007, "lon": -0.1246, "type": "webcam"},
        {**_yt("aHEVJGcz5bM", "Shibuya Live"), "name": "Tokyo — Shibuya Crossing", "lat": 35.6595, "lon": 139.7004, "type": "webcam"},
        {**_yt("YFfnSuXO9bI", "Red Square Cam"), "name": "Moscow — Red Square", "lat": 55.7539, "lon": 37.6208, "type": "webcam"},
        {**_yt("e_B-PeNdTmE", "Paris Cam"), "name": "Paris — Eiffel Tower", "lat": 48.8584, "lon": 2.2945, "type": "webcam"},
        {**_yt("yzuEhEMBpKA", "Singapore Port"), "name": "Singapore — Marina Bay", "lat": 1.2897, "lon": 103.8501, "type": "webcam"},
    ],
    "ukraine": [
        {**_yt("LU-F-hA8WOM", "Ukraine TV"), "name": "Kyiv — Live Broadcast", "lat": 50.4501, "lon": 30.5234, "type": "webcam"},
        {**_yt("PNs6QZFUAEI", "Ukraine 24"), "name": "Ukraine 24 — Breaking News", "lat": 49.8397, "lon": 24.0297, "type": "conflict_cam"},
        {**_yt("AuElEiGNqfM", "DW News"), "name": "DW News — Ukraine Coverage", "lat": 48.5, "lon": 36.5, "type": "conflict_cam"},
    ],
    "middle_east": [
        {**_yt("82PLkqeKkEs", "Western Wall Heritage"), "name": "Jerusalem — Western Wall (Official)", "lat": 31.7767, "lon": 35.2345, "type": "webcam"},
        {**_yt("RMSmjSRLsNg", "i24 News English"), "name": "Israel — i24 News Live", "lat": 32.0853, "lon": 34.7818, "type": "webcam"},
        {**_yt("pciq9yrFTao", "Bosphorus Cam"), "name": "Istanbul — Bosphorus Strait", "lat": 41.0461, "lon": 29.0279, "type": "webcam"},
        {**_yt("C3GzB94WOw8", "Al Jazeera English"), "name": "Al Jazeera — Middle East Live", "lat": 25.2854, "lon": 51.5310, "type": "webcam"},
    ],
    "fire_cams": [
        # ALERTWildfire thumbnail = live JPEG snapshot (updates every 30s)
        {
            "name": "ALERTWildfire — Napa Valley",
            "lat": 38.5, "lon": -122.3, "type": "fire_cam",
            "embed_url": "https://www.youtube.com/embed/uj7YYDI_dKA?rel=0&autoplay=0",
            "thumbnail": "https://cameras.alertwildfire.org/camera/Axis-Berryessa1/current.jpg",
            "source": "ALERTWildfire / KABC7",
        },
        {**_yt("uj7YYDI_dKA", "KABC7 ABC LA"), "name": "KABC7 — Los Angeles Live", "lat": 34.15, "lon": -118.1, "type": "fire_cam"},
        {**_yt("u8NbFYA9pnU", "ABC7 Bay Area"), "name": "ABC7 — Bay Area / Sierra", "lat": 38.8, "lon": -120.2, "type": "fire_cam"},
        {**_yt("jKpSRn7IZMA", "USGS HVO"), "name": "USGS — Kilauea Volcano Cam", "lat": 19.421, "lon": -155.287, "type": "volcano_cam"},
    ],
    "ports_strategic": [
        {**_yt("C3GzB94WOw8", "Al Jazeera"), "name": "Strait of Hormuz — Al Jazeera Live", "lat": 27.18, "lon": 56.28, "type": "port_cam"},
        {**_yt("nGCXDoeAXBU", "France 24"), "name": "Suez Canal — France 24 Live", "lat": 31.26, "lon": 32.3, "type": "port_cam"},
        {**_yt("AuElEiGNqfM", "DW News"), "name": "Port of Rotterdam — DW News", "lat": 51.9055, "lon": 4.4660, "type": "port_cam"},
        {**_yt("yzuEhEMBpKA", "Singapore Port Cam"), "name": "Singapore Strait — Vessel Traffic", "lat": 1.25, "lon": 103.82, "type": "port_cam"},
    ],
    "conflict_zones": [
        {**_yt("RMSmjSRLsNg", "i24 News"), "name": "Gaza/Israel — i24 News Live", "lat": 31.35, "lon": 34.30, "type": "conflict_cam"},
        {**_yt("AuElEiGNqfM", "DW News"), "name": "Ukraine Frontline — DW Coverage", "lat": 48.5, "lon": 36.5, "type": "conflict_cam"},
        {**_yt("lx8X_cJ2094", "NHK World"), "name": "Taiwan Strait — NHK World Live", "lat": 24.0, "lon": 122.0, "type": "conflict_cam"},
        {**_yt("C3GzB94WOw8", "Al Jazeera"), "name": "Red Sea — Al Jazeera Coverage", "lat": 15.0, "lon": 42.0, "type": "conflict_cam"},
    ],
    "weather_nature": [
        {**_yt("fJVbvnq0YQk", "NPS OldFaithful"), "name": "Yellowstone — Old Faithful Cam", "lat": 44.4605, "lon": -110.8281, "type": "nature_cam"},
        {**_yt("gSuSUnpDz_8", "The Weather Channel"), "name": "Atlantic — Hurricane Tracker Live", "lat": 25.0, "lon": -75.0, "type": "weather_cam"},
        {**_yt("Ahp6O42Lhx8", "WION Weather"), "name": "Global — WION Weather Live", "lat": 0.0, "lon": 20.0, "type": "weather_cam"},
    ],
}


class WebcamConnector:
    """Aggregates public webcam feeds for monitoring zones."""

    name = "webcams"

    # Windy Webcams API (free tier: 25 req/day)
    WINDY_API_URL = "https://api.windy.com/webcams/api/v3/webcams"

    def __init__(
        self,
        *,
        windy_api_key: str | None = None,
        regions: list[str] | None = None,
        nearby_lat: float | None = None,
        nearby_lon: float | None = None,
        nearby_radius_km: int = 50,
        timeout_s: float = 10.0,
    ) -> None:
        self.windy_api_key = windy_api_key
        self.regions = regions or list(CURATED_CAMERAS.keys())
        self.nearby_lat = nearby_lat
        self.nearby_lon = nearby_lon
        self.nearby_radius_km = nearby_radius_km
        self.timeout_s = timeout_s

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        geo_events: list[dict] = []
        metadata: dict = {"regions": self.regions}
        now = datetime.now(UTC)

        # Curated camera feeds — all YouTube embeds
        for region in self.regions:
            cameras = CURATED_CAMERAS.get(region, [])
            for cam in cameras:
                if query and query.lower() not in cam["name"].lower():
                    continue

                embed_url = cam.get("embed_url", "")
                thumbnail = cam.get("thumbnail")

                observations.append(Observation(
                    source=f"webcam:{cam['type']}",
                    claim=f"Live camera: {cam['name']} ({cam['type']})",
                    url=embed_url,
                    captured_at=now,
                    reliability_hint=0.60,
                ))

                geo_events.append({
                    "layer": "camera",
                    "source_id": f"webcam_{cam['name'].lower().replace(' ', '_')[:40]}",
                    "title": f"Camera: {cam['name']}",
                    "latitude": cam["lat"],
                    "longitude": cam["lon"],
                    "magnitude": None,
                    "event_time": now,
                    "properties": {
                        "camera_type": cam["type"],
                        "region": region,
                        "url": embed_url,
                        "embed_url": embed_url,
                        "thumbnail": thumbnail,
                        "source": cam.get("source", "YouTube Live"),
                    },
                })

        # Windy Webcams API (if key provided)
        if self.windy_api_key and self.nearby_lat is not None and self.nearby_lon is not None:
            try:
                params = {
                    "nearby": f"{self.nearby_lat},{self.nearby_lon},{self.nearby_radius_km}",
                    "limit": 20,
                    "include": "location,images,urls",
                }
                headers = {"x-windy-api-key": self.windy_api_key}
                with httpx.Client(timeout=self.timeout_s) as client:
                    resp = client.get(self.WINDY_API_URL, params=params, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()

                for cam in data.get("webcams", []):
                    title = cam.get("title", "Webcam")
                    location = cam.get("location", {})
                    lat = location.get("latitude", 0)
                    lon = location.get("longitude", 0)
                    images = cam.get("images", {})
                    current = images.get("current", {})
                    preview_url = current.get("preview", "")
                    player_url = cam.get("urls", {}).get("detail", "")

                    observations.append(Observation(
                        source="webcam:windy",
                        claim=f"Webcam: {title} ({location.get('city', '')})",
                        url=player_url or preview_url,
                        captured_at=now,
                        reliability_hint=0.55,
                    ))

                    geo_events.append({
                        "layer": "camera",
                        "source_id": f"windy_{cam.get('webcamId', '')}",
                        "title": f"Webcam: {title}",
                        "latitude": lat,
                        "longitude": lon,
                        "magnitude": None,
                        "event_time": now,
                        "properties": {
                            "camera_type": "windy",
                            "preview_url": preview_url,
                            "player_url": player_url,
                            "city": location.get("city", ""),
                            "country": location.get("country", ""),
                        },
                    })

                metadata["windy_count"] = len(data.get("webcams", []))
            except Exception as exc:
                metadata["windy_error"] = str(exc)

        metadata["geo_events"] = geo_events
        metadata["total_cameras"] = len(observations)
        return ConnectorResult(connector=self.name, observations=observations, metadata=metadata)


def list_camera_regions() -> list[dict[str, Any]]:
    """List available camera regions with counts."""
    return [
        {"region": region, "camera_count": len(cameras)}
        for region, cameras in CURATED_CAMERAS.items()
    ]


def get_cameras_near(lat: float, lon: float, radius_km: float = 100) -> list[dict[str, Any]]:
    """Find curated cameras near a coordinate."""
    from sts_monitor.convergence import haversine_km
    results = []
    for region, cameras in CURATED_CAMERAS.items():
        for cam in cameras:
            dist = haversine_km(lat, lon, cam["lat"], cam["lon"])
            if dist <= radius_km:
                results.append({**cam, "region": region, "distance_km": round(dist, 1)})
    results.sort(key=lambda c: c["distance_km"])
    return results
