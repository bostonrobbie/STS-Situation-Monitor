"""Public webcam aggregator connector.

Aggregates publicly available webcam feeds for situation monitoring zones.
Sources: Windy Webcams API, YouTube Live search, traffic DOT cams,
ALERTWildfire cameras, and curated OSINT camera lists.

Legal: Only accesses publicly intended camera feeds. No unauthorized access.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation


# ── Curated public camera feeds by region/category ──────────────────────

CURATED_CAMERAS: dict[str, list[dict[str, Any]]] = {
    "ukraine": [
        {"name": "Kyiv Maidan", "lat": 50.4501, "lon": 30.5234, "type": "webcam", "url": "https://www.youtube.com/results?search_query=kyiv+live+cam&sp=EgJAAQ%253D%253D"},
        {"name": "Odesa Port", "lat": 46.4825, "lon": 30.7233, "type": "webcam", "url": "https://www.youtube.com/results?search_query=odesa+live+cam&sp=EgJAAQ%253D%253D"},
        {"name": "Lviv Center", "lat": 49.8397, "lon": 24.0297, "type": "webcam", "url": "https://www.youtube.com/results?search_query=lviv+live+cam&sp=EgJAAQ%253D%253D"},
        {"name": "Kharkiv", "lat": 49.9935, "lon": 36.2304, "type": "webcam", "url": "https://www.youtube.com/results?search_query=kharkiv+live+cam&sp=EgJAAQ%253D%253D"},
    ],
    "middle_east": [
        {"name": "Jerusalem Old City", "lat": 31.7767, "lon": 35.2345, "type": "webcam", "url": "https://www.youtube.com/results?search_query=jerusalem+live+cam&sp=EgJAAQ%253D%253D"},
        {"name": "Tel Aviv Beach", "lat": 32.0853, "lon": 34.7818, "type": "webcam", "url": "https://www.youtube.com/results?search_query=tel+aviv+live+cam&sp=EgJAAQ%253D%253D"},
        {"name": "Beirut", "lat": 33.8938, "lon": 35.5018, "type": "webcam", "url": "https://www.youtube.com/results?search_query=beirut+live+cam&sp=EgJAAQ%253D%253D"},
        {"name": "Istanbul Bosphorus", "lat": 41.0082, "lon": 28.9784, "type": "webcam", "url": "https://www.youtube.com/results?search_query=istanbul+bosphorus+live&sp=EgJAAQ%253D%253D"},
    ],
    "natural_disaster_cams": [
        {"name": "ALERTWildfire - CA North", "lat": 38.5, "lon": -121.5, "type": "fire_cam", "url": "https://www.alertwildfire.org/region/northcoast/"},
        {"name": "ALERTWildfire - CA South", "lat": 34.0, "lon": -118.2, "type": "fire_cam", "url": "https://www.alertwildfire.org/region/southcoast/"},
        {"name": "ALERTWildfire - Nevada", "lat": 39.5, "lon": -119.8, "type": "fire_cam", "url": "https://www.alertwildfire.org/region/tahoe/"},
        {"name": "USGS Kilauea Volcano", "lat": 19.421, "lon": -155.287, "type": "volcano_cam", "url": "https://www.usgs.gov/volcanoes/kilauea/webcams"},
        {"name": "USGS Mt St Helens", "lat": 46.1914, "lon": -122.1956, "type": "volcano_cam", "url": "https://www.usgs.gov/volcanoes/mount-st.-helens/webcams"},
    ],
    "traffic_us": [
        {"name": "NYC DOT - Times Square", "lat": 40.758, "lon": -73.9855, "type": "traffic", "url": "https://webcams.nyctmc.org/"},
        {"name": "CalTrans - LA", "lat": 34.0522, "lon": -118.2437, "type": "traffic", "url": "https://cwwp2.dot.ca.gov/vm/streamlist.htm"},
        {"name": "FDOT - Miami", "lat": 25.7617, "lon": -80.1918, "type": "traffic", "url": "https://fl511.com/map"},
    ],
    "ports_maritime": [
        {"name": "Port of Rotterdam", "lat": 51.9055, "lon": 4.4660, "type": "port_cam", "url": "https://www.portofrotterdam.com/en/online/webcams"},
        {"name": "Port of Singapore", "lat": 1.2644, "lon": 103.8222, "type": "port_cam", "url": "https://www.webcamtaxi.com/en/singapore/singapore/marina-bay.html"},
        {"name": "Strait of Hormuz view", "lat": 26.5667, "lon": 56.25, "type": "port_cam", "url": "https://www.marinetraffic.com/en/ais/home/centerx:56.3/centery:26.6/zoom:10"},
    ],
    "global_cities": [
        {"name": "London Parliament", "lat": 51.4995, "lon": -0.1248, "type": "webcam", "url": "https://www.earthcam.com/world/england/london/"},
        {"name": "Paris Eiffel Tower", "lat": 48.8584, "lon": 2.2945, "type": "webcam", "url": "https://www.earthcam.com/world/france/paris/"},
        {"name": "Tokyo Shibuya", "lat": 35.6595, "lon": 139.7004, "type": "webcam", "url": "https://www.youtube.com/results?search_query=shibuya+crossing+live&sp=EgJAAQ%253D%253D"},
        {"name": "Moscow Kremlin", "lat": 55.7520, "lon": 37.6175, "type": "webcam", "url": "https://www.youtube.com/results?search_query=moscow+kremlin+live+cam&sp=EgJAAQ%253D%253D"},
        {"name": "Washington DC Capitol", "lat": 38.8899, "lon": -77.0091, "type": "webcam", "url": "https://www.earthcam.com/usa/dc/capitol/"},
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

        # 1. Curated camera feeds
        for region in self.regions:
            cameras = CURATED_CAMERAS.get(region, [])
            for cam in cameras:
                if query and query.lower() not in cam["name"].lower():
                    continue

                observations.append(Observation(
                    source=f"webcam:{cam['type']}",
                    claim=f"Live camera: {cam['name']} ({cam['type']})",
                    url=cam["url"],
                    captured_at=now,
                    reliability_hint=0.60,
                ))

                geo_events.append({
                    "layer": "camera",
                    "source_id": f"webcam_{cam['name'].lower().replace(' ', '_')}",
                    "title": f"Camera: {cam['name']}",
                    "latitude": cam["lat"],
                    "longitude": cam["lon"],
                    "magnitude": None,
                    "event_time": now,
                    "properties": {
                        "camera_type": cam["type"],
                        "region": region,
                        "url": cam["url"],
                    },
                })

        # 2. Windy Webcams API (if key provided)
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
