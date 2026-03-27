"""Massachusetts Environment connector — USGS water gauges and NOAA tides.

Aggregates free, no-auth government environmental APIs for Massachusetts:
- USGS Water Services: real-time stream/river gauge heights
- NOAA Tides & Currents: tide predictions for MA coastal stations

AirNow AQI data requires a free API key and is skipped when no key is provided.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.pipeline import Observation


# Default NOAA stations: Boston Harbor, Woods Hole, Nantucket
_DEFAULT_NOAA_STATIONS = ["8443970", "8447930", "8449130"]

_NOAA_STATION_NAMES = {
    "8443970": "Boston Harbor",
    "8447930": "Woods Hole",
    "8449130": "Nantucket Island",
}

_USGS_WATER_URL = "https://waterservices.usgs.gov/nwis/iv/"
_NOAA_TIDES_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"


class MAEnvironmentConnector:
    """Fetches real-time environmental data for Massachusetts."""

    name = "ma_environment"

    def __init__(
        self,
        *,
        airnow_api_key: str = "",
        usgs_param: str = "00065",
        noaa_stations: list[str] | None = None,
        timeout_s: float = 15.0,
    ) -> None:
        self.airnow_api_key = airnow_api_key
        self.usgs_param = usgs_param
        self.noaa_stations = noaa_stations or list(_DEFAULT_NOAA_STATIONS)
        self.timeout_s = timeout_s

    # ------------------------------------------------------------------
    # USGS Water Services
    # ------------------------------------------------------------------

    def _fetch_usgs_water(self, client: httpx.Client) -> tuple[list[Observation], dict[str, Any]]:
        """Fetch real-time gauge data from USGS for all active MA sites."""
        observations: list[Observation] = []
        meta: dict[str, Any] = {}

        param_label = "gauge height" if self.usgs_param == "00065" else "discharge"

        params = {
            "format": "json",
            "stateCd": "MA",
            "parameterCd": self.usgs_param,
            "siteStatus": "active",
        }

        try:
            resp = client.get(_USGS_WATER_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            meta["usgs_error"] = str(exc)
            return observations, meta

        time_series = (
            data.get("value", {})
            .get("timeSeries", [])
        )
        meta["usgs_site_count"] = len(time_series)

        for series in time_series:
            source_info = series.get("sourceInfo", {})
            site_name = source_info.get("siteName", "Unknown site")
            site_code_info = source_info.get("siteCode", [{}])
            site_code = site_code_info[0].get("value", "") if site_code_info else ""

            geo = source_info.get("geoLocation", {}).get("geogLocation", {})
            lat = geo.get("latitude")
            lon = geo.get("longitude")

            values = series.get("values", [{}])
            recent_values = values[0].get("value", []) if values else []
            if not recent_values:
                continue

            latest = recent_values[-1]
            reading = latest.get("value")
            timestamp_str = latest.get("dateTime", "")

            if reading is None or reading == "":
                continue

            try:
                value_float = float(reading)
            except (ValueError, TypeError):
                continue

            unit = "ft" if self.usgs_param == "00065" else "cfs"
            claim = f"USGS Gauge: {site_name} at {value_float:.2f} {unit} ({param_label})"

            captured_at = datetime.now(UTC)
            if timestamp_str:
                try:
                    captured_at = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass

            observations.append(
                Observation(
                    source=f"usgs_water:{site_code}",
                    claim=claim,
                    url=f"https://waterdata.usgs.gov/nwis/uv?site_no={site_code}",
                    captured_at=captured_at,
                    reliability_hint=0.9,
                )
            )

        return observations, meta

    # ------------------------------------------------------------------
    # NOAA Tides & Currents
    # ------------------------------------------------------------------

    def _fetch_noaa_tides(self, client: httpx.Client) -> tuple[list[Observation], dict[str, Any]]:
        """Fetch tide predictions from NOAA for configured stations."""
        observations: list[Observation] = []
        meta: dict[str, Any] = {"noaa_stations_requested": len(self.noaa_stations)}
        failed_stations: list[dict[str, str]] = []

        today = datetime.now(UTC).strftime("%Y%m%d")

        for station in self.noaa_stations:
            params = {
                "begin_date": today,
                "range": "24",
                "station": station,
                "product": "predictions",
                "datum": "MLLW",
                "units": "english",
                "time_zone": "lst_ldt",
                "format": "json",
            }

            try:
                resp = client.get(_NOAA_TIDES_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                failed_stations.append({"station": station, "error": str(exc)})
                continue

            predictions = data.get("predictions", [])
            if not predictions:
                continue

            station_name = _NOAA_STATION_NAMES.get(station, f"Station {station}")

            # Find high and low tide peaks from the prediction series
            highs, lows = self._find_tide_extremes(predictions)

            for pred_type, extremes in [("high", highs), ("low", lows)]:
                for extreme in extremes:
                    t_str = extreme.get("t", "")
                    v_str = extreme.get("v", "0")

                    try:
                        value = float(v_str)
                    except (ValueError, TypeError):
                        continue

                    claim = (
                        f"NOAA Tide {pred_type.title()}: {station_name} "
                        f"at {value:.2f} ft MLLW ({t_str})"
                    )

                    observations.append(
                        Observation(
                            source=f"noaa_tides:{station}",
                            claim=claim,
                            url=f"https://tidesandcurrents.noaa.gov/noaatidepredictions.html?id={station}",
                            captured_at=datetime.now(UTC),
                            reliability_hint=0.9,
                        )
                    )

        meta["noaa_failed_stations"] = failed_stations
        return observations, meta

    @staticmethod
    def _find_tide_extremes(
        predictions: list[dict[str, str]],
    ) -> tuple[list[dict], list[dict]]:
        """Identify local maxima (highs) and minima (lows) in tide predictions."""
        highs: list[dict] = []
        lows: list[dict] = []

        if len(predictions) < 3:
            return highs, lows

        values = []
        for p in predictions:
            try:
                values.append(float(p.get("v", "0")))
            except (ValueError, TypeError):
                values.append(0.0)

        for i in range(1, len(values) - 1):
            if values[i] > values[i - 1] and values[i] > values[i + 1]:
                highs.append(predictions[i])
            elif values[i] < values[i - 1] and values[i] < values[i + 1]:
                lows.append(predictions[i])

        return highs, lows

    # ------------------------------------------------------------------
    # Main collect
    # ------------------------------------------------------------------

    def collect(self, query: str | None = None) -> ConnectorResult:
        all_observations: list[Observation] = []
        metadata: dict[str, Any] = {}

        with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
            # USGS water gauges
            usgs_obs, usgs_meta = self._fetch_usgs_water(client)
            all_observations.extend(usgs_obs)
            metadata.update(usgs_meta)

            # NOAA tides
            noaa_obs, noaa_meta = self._fetch_noaa_tides(client)
            all_observations.extend(noaa_obs)
            metadata.update(noaa_meta)

        # Apply optional runtime query filter
        if query:
            q_lower = query.lower()
            all_observations = [
                obs for obs in all_observations if q_lower in obs.claim.lower()
            ]

        metadata["total_observations"] = len(all_observations)
        return ConnectorResult(
            connector=self.name,
            observations=all_observations,
            metadata=metadata,
        )
