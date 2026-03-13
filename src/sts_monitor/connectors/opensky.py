"""OpenSky Network connector — live aircraft surveillance data.

Free for non-commercial use, no authentication required for public API.
Authenticated access increases rate limits.
API docs: https://openskynetwork.github.io/opensky-api/
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation


class OpenSkyConnector:
    """Fetches live aircraft state vectors from OpenSky Network."""

    name = "opensky"

    API_URL = "https://opensky-network.org/api/states/all"

    def __init__(
        self,
        *,
        bbox: tuple[float, float, float, float] | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout_s: float = 15.0,
    ) -> None:
        """
        Args:
            bbox: Bounding box (lamin, lomin, lamax, lomax) to restrict area.
            username: OpenSky username for authenticated access (higher rate limits).
            password: OpenSky password.
        """
        self.bbox = bbox
        self.username = username
        self.password = password
        self.timeout_s = timeout_s

    def collect(self, query: str | None = None) -> ConnectorResult:
        observations: list[Observation] = []
        geo_events: list[dict] = []
        metadata: dict = {"bbox": self.bbox}

        params: dict = {}
        if self.bbox:
            lamin, lomin, lamax, lomax = self.bbox
            params.update({
                "lamin": str(lamin), "lomin": str(lomin),
                "lamax": str(lamax), "lomax": str(lomax),
            })

        auth = None
        if self.username and self.password:
            auth = (self.username, self.password)

        try:
            with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
                resp = client.get(self.API_URL, params=params, auth=auth)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            metadata["error"] = str(exc)
            return ConnectorResult(connector=self.name, observations=[], metadata=metadata)

        states = data.get("states", []) or []
        timestamp = data.get("time", 0)
        metadata["aircraft_count"] = len(states)
        metadata["api_time"] = timestamp

        now = datetime.now(UTC)

        for sv in states:
            # OpenSky state vector fields:
            # [0] icao24, [1] callsign, [2] origin_country, [3] time_position,
            # [4] last_contact, [5] longitude, [6] latitude, [7] baro_altitude,
            # [8] on_ground, [9] velocity, [10] true_track, [11] vertical_rate,
            # [12] sensors, [13] geo_altitude, [14] squawk, [15] spi, [16] position_source

            if len(sv) < 17:
                continue

            icao24 = sv[0] or ""
            callsign = (sv[1] or "").strip()
            origin_country = sv[2] or ""
            longitude = sv[5]
            latitude = sv[6]
            baro_altitude = sv[7]
            on_ground = sv[8]
            velocity = sv[9]
            squawk = sv[14] or ""

            if latitude is None or longitude is None:
                continue

            # Filter by query (callsign or country match)
            if query:
                q_lower = query.lower()
                if (q_lower not in callsign.lower()
                        and q_lower not in origin_country.lower()
                        and q_lower not in icao24.lower()):
                    continue

            # Military/interesting squawk codes
            is_military_squawk = squawk in ("7500", "7600", "7700")  # Hijack, radio fail, emergency
            reliability = 0.92 if is_military_squawk else 0.70

            alt_str = f"alt {baro_altitude:.0f}m" if baro_altitude else "ground" if on_ground else "unknown alt"
            vel_str = f"{velocity:.0f}m/s" if velocity else ""

            claim = f"Aircraft {callsign or icao24} ({origin_country}) at ({latitude:.4f}, {longitude:.4f}) {alt_str}"
            if vel_str:
                claim += f" {vel_str}"
            if is_military_squawk:
                claim += f" [SQUAWK {squawk} EMERGENCY]"

            observations.append(Observation(
                source=f"opensky:{origin_country.lower().replace(' ', '_')}",
                claim=claim,
                url=f"https://opensky-network.org/aircraft-profile?icao24={icao24}",
                captured_at=now,
                reliability_hint=reliability,
            ))

            geo_events.append({
                "layer": "aircraft",
                "source_id": f"opensky_{icao24}",
                "title": claim[:500],
                "latitude": latitude,
                "longitude": longitude,
                "altitude": baro_altitude,
                "magnitude": None,
                "event_time": now,
                "expires_at": now,  # Aircraft positions are ephemeral
                "properties": {
                    "icao24": icao24,
                    "callsign": callsign,
                    "origin_country": origin_country,
                    "velocity": velocity,
                    "on_ground": on_ground,
                    "squawk": squawk,
                    "is_emergency": is_military_squawk,
                },
            })

        metadata["geo_events"] = geo_events
        return ConnectorResult(connector=self.name, observations=observations, metadata=metadata)
