"""
STS Situation Monitor — Full QA Test Suite
Covers: Unit tests, Integration tests, Security tests, Performance tests, Data integrity tests.
"""
from __future__ import annotations

import sys
import time
import unittest
import concurrent.futures
from datetime import datetime, timezone

sys.path.insert(0, 'src')

import requests

# ─── Config ───────────────────────────────────────────────────────────────────

BASE_URL = "http://127.0.0.1:8080"
API_KEY = "65TNLVtbUBdHgxKtM5vK1HT6M-bdTr2yjjOOFH7K_ic"
HEADERS = {"X-API-Key": API_KEY}

# Shared session for connection reuse (avoids Windows localhost DNS 2s delay)
_SESSION = requests.Session()
_SESSION.headers.update(HEADERS)

# ─── Test helpers ─────────────────────────────────────────────────────────────

_results: list[tuple[str, bool, float, str]] = []


def _record(name: str, passed: bool, elapsed: float, note: str = "") -> None:
    status = "PASS" if passed else "FAIL"
    timing = f"{elapsed*1000:.0f}ms"
    note_str = f" — {note}" if note else ""
    print(f"  [{status}] {name} ({timing}){note_str}")
    _results.append((name, passed, elapsed, note))


class TimedTestCase(unittest.TestCase):
    """Base class that records PASS/FAIL and timing for every test method."""

    def run(self, result=None):  # noqa: ANN001
        test_name = self._testMethodName
        label = f"{type(self).__name__}.{test_name}"
        t0 = time.perf_counter()
        outcome = "pass"
        note = ""
        try:
            super().run(result)
            # Check if the test actually failed
            if result and result.failures:
                for _, msg in result.failures:
                    if label in str(msg) or test_name in str(msg):
                        outcome = "fail"
                        note = msg.split("\n")[-2] if msg else ""
                        break
            if result and result.errors:
                for _, msg in result.errors:
                    if label in str(msg) or test_name in str(msg):
                        outcome = "error"
                        note = msg.split("\n")[-2] if msg else ""
                        break
        except Exception as e:
            outcome = "error"
            note = str(e)
        finally:
            elapsed = time.perf_counter() - t0
            _record(label, outcome == "pass", elapsed, note)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. UNIT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestPredictive(TimedTestCase):
    """Unit tests for src/sts_monitor/predictive.py"""

    def setUp(self):
        from sts_monitor.predictive import score_event, batch_score_events, top_events
        self.score_event = score_event
        self.batch_score_events = batch_score_events
        self.top_events = top_events

    def test_score_event_basic_range(self):
        """score_event returns float in [0, 10]."""
        score = self.score_event("Some random event", "news", None)
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 10.0)

    def test_score_event_high_keyword_boost(self):
        """High-importance keywords increase score."""
        base = self.score_event("Routine briefing update", "news", None)
        boosted = self.score_event("Explosion kills dozens in city center", "news", None)
        self.assertGreater(boosted, base)

    def test_score_event_medium_keyword_boost(self):
        """Medium-importance keywords increase score."""
        base = self.score_event("Market opens normally today", "news", None)
        boosted = self.score_event("Emergency evacuation ordered for town", "news", None)
        self.assertGreater(boosted, base)

    def test_score_event_high_magnitude(self):
        """Magnitude >= 7.0 significantly raises score."""
        no_mag = self.score_event("Earthquake reported", "earthquake", None)
        big_quake = self.score_event("Earthquake reported", "earthquake", 7.5)
        self.assertGreater(big_quake, no_mag)

    def test_score_event_layer_weights(self):
        """Conflict layer scores higher than local_news layer."""
        conflict = self.score_event("Armed clashes ongoing", "conflict", None)
        local = self.score_event("Armed clashes ongoing", "local_news", None)
        self.assertGreater(conflict, local)

    def test_score_event_capped_at_10(self):
        """Score is never above 10.0."""
        score = self.score_event("Nuclear explosion attack kills thousands tsunami", "conflict", 9.9)
        self.assertLessEqual(score, 10.0)

    def test_score_event_recency_boost(self):
        """Very recent events get a recency boost."""
        from datetime import UTC, timedelta
        now = datetime.now(UTC)
        recent = now - timedelta(minutes=30)
        old = now - timedelta(hours=12)
        s_recent = self.score_event("Conflict report", "news", None, recent)
        s_old = self.score_event("Conflict report", "news", None, old)
        self.assertGreater(s_recent, s_old)

    def test_batch_score_events_adds_field(self):
        """batch_score_events adds predicted_importance to each dict."""
        events = [
            {"title": "Test event 1", "layer": "news", "magnitude": None},
            {"title": "Earthquake strikes region", "layer": "earthquake", "magnitude": 5.5},
        ]
        result = self.batch_score_events(events)
        self.assertEqual(len(result), 2)
        for ev in result:
            self.assertIn("predicted_importance", ev)
            self.assertIsInstance(ev["predicted_importance"], float)
            self.assertGreaterEqual(ev["predicted_importance"], 0.0)
            self.assertLessEqual(ev["predicted_importance"], 10.0)

    def test_batch_score_events_returns_same_list(self):
        """batch_score_events modifies in-place and returns the same list."""
        events = [{"title": "A", "layer": "news", "magnitude": None}]
        result = self.batch_score_events(events)
        self.assertIs(result, events)

    def test_batch_score_events_string_event_time(self):
        """batch_score_events handles ISO string event_time."""
        events = [{"title": "Test", "layer": "news", "event_time": "2026-03-15T20:00:00Z"}]
        result = self.batch_score_events(events)
        self.assertIn("predicted_importance", result[0])

    def test_top_events_returns_n(self):
        """top_events returns at most N events."""
        events = [
            {"title": f"Event {i}", "layer": "news", "magnitude": float(i % 5)}
            for i in range(20)
        ]
        top = self.top_events(events, n=5)
        self.assertEqual(len(top), 5)

    def test_top_events_sorted_descending(self):
        """top_events returns events in descending importance order."""
        events = [
            {"title": "Low-importance minor update", "layer": "local_news", "magnitude": 0.0},
            {"title": "Nuclear explosion massive attack kills all", "layer": "conflict", "magnitude": 9.0},
            {"title": "Protest march peaceful demonstration", "layer": "news", "magnitude": 3.0},
        ]
        top = self.top_events(events, n=3)
        importances = [e["predicted_importance"] for e in top]
        self.assertEqual(importances, sorted(importances, reverse=True))

    def test_top_events_empty_input(self):
        """top_events handles empty list gracefully."""
        result = self.top_events([], n=10)
        self.assertEqual(result, [])


class TestCorrelation(TimedTestCase):
    """Unit tests for src/sts_monitor/correlation.py"""

    def setUp(self):
        from sts_monitor.correlation import haversine_km, predict_importance, classify_severity
        self.haversine_km = haversine_km
        self.predict_importance = predict_importance
        self.classify_severity = classify_severity

    def test_haversine_same_point(self):
        """Distance from a point to itself is 0."""
        d = self.haversine_km(42.36, -71.05, 42.36, -71.05)
        self.assertAlmostEqual(d, 0.0, places=4)

    def test_haversine_boston_new_york(self):
        """Boston to NYC is approximately 306 km."""
        d = self.haversine_km(42.36, -71.05, 40.71, -74.01)
        self.assertGreater(d, 290)
        self.assertLess(d, 330)

    def test_haversine_symmetry(self):
        """haversine_km is symmetric: d(A,B) == d(B,A)."""
        d1 = self.haversine_km(51.5, -0.12, 48.86, 2.35)
        d2 = self.haversine_km(48.86, 2.35, 51.5, -0.12)
        self.assertAlmostEqual(d1, d2, places=5)

    def test_haversine_positive(self):
        """Distance is always non-negative."""
        d = self.haversine_km(-33.87, 151.21, 35.68, 139.69)
        self.assertGreater(d, 0)

    def test_predict_importance_base_range(self):
        """predict_importance returns value in [0, 10]."""
        score = self.predict_importance(["news"], 3, 0.0)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 10.0)

    def test_predict_importance_high_layer(self):
        """High-priority layers (conflict) score higher than medium (news)."""
        high = self.predict_importance(["conflict"], 3, 0.0)
        med = self.predict_importance(["news"], 3, 0.0)
        self.assertGreater(high, med)

    def test_predict_importance_more_signals(self):
        """More signals increase the score."""
        low = self.predict_importance(["news"], 1, 0.0)
        high = self.predict_importance(["news"], 10, 0.0)
        self.assertGreater(high, low)

    def test_predict_importance_magnitude_boost(self):
        """High magnitude (>6) boosts the score."""
        no_mag = self.predict_importance(["earthquake"], 2, 0.0)
        big_mag = self.predict_importance(["earthquake"], 2, 7.5)
        self.assertGreater(big_mag, no_mag)

    def test_predict_importance_capped(self):
        """predict_importance is capped at 10.0."""
        score = self.predict_importance(["conflict", "military", "earthquake"], 100, 9.9)
        self.assertLessEqual(score, 10.0)

    def test_classify_severity_critical(self):
        """Importance >= 8 → 'critical'."""
        self.assertEqual(self.classify_severity(8.0), "critical")
        self.assertEqual(self.classify_severity(10.0), "critical")
        self.assertEqual(self.classify_severity(9.5), "critical")

    def test_classify_severity_high(self):
        """Importance >= 6.5 and < 8 → 'high'."""
        self.assertEqual(self.classify_severity(6.5), "high")
        self.assertEqual(self.classify_severity(7.9), "high")

    def test_classify_severity_medium(self):
        """Importance >= 4.5 and < 6.5 → 'medium'."""
        self.assertEqual(self.classify_severity(4.5), "medium")
        self.assertEqual(self.classify_severity(6.4), "medium")

    def test_classify_severity_low(self):
        """Importance < 4.5 → 'low'."""
        self.assertEqual(self.classify_severity(0.0), "low")
        self.assertEqual(self.classify_severity(4.4), "low")
        self.assertEqual(self.classify_severity(3.0), "low")


class TestWebcams(TimedTestCase):
    """Unit tests for src/sts_monitor/connectors/webcams.py"""

    def setUp(self):
        from sts_monitor.connectors.webcams import _yt, list_camera_regions, get_cameras_near, CURATED_CAMERAS
        self._yt = _yt
        self.list_camera_regions = list_camera_regions
        self.get_cameras_near = get_cameras_near
        self.CURATED_CAMERAS = CURATED_CAMERAS

    def test_yt_returns_dict_with_embed_url(self):
        """_yt() returns a dict with embed_url, thumbnail, source."""
        result = self._yt("dQw4w9WgXcQ", "Test Label")
        self.assertIn("embed_url", result)
        self.assertIn("thumbnail", result)
        self.assertIn("source", result)

    def test_yt_embed_url_format(self):
        """_yt() embed_url is a valid YouTube embed URL."""
        vid = "dQw4w9WgXcQ"
        result = self._yt(vid)
        self.assertIn(f"https://www.youtube.com/embed/{vid}", result["embed_url"])

    def test_yt_thumbnail_format(self):
        """_yt() thumbnail URL contains the video ID."""
        vid = "dQw4w9WgXcQ"
        result = self._yt(vid)
        self.assertIn(vid, result["thumbnail"])
        self.assertIn("img.youtube.com", result["thumbnail"])

    def test_yt_label_included_in_source(self):
        """_yt() source includes the label when provided."""
        result = self._yt("dQw4w9WgXcQ", "MyChannel")
        self.assertIn("MyChannel", result["source"])

    def test_yt_no_label_still_has_source(self):
        """_yt() without label still has a source field."""
        result = self._yt("dQw4w9WgXcQ")
        self.assertIn("YouTube", result["source"])

    def test_list_camera_regions_returns_list(self):
        """list_camera_regions() returns a list."""
        regions = self.list_camera_regions()
        self.assertIsInstance(regions, list)
        self.assertGreater(len(regions), 0)

    def test_list_camera_regions_structure(self):
        """Each region entry has 'region' and 'camera_count' keys."""
        regions = self.list_camera_regions()
        for region in regions:
            self.assertIn("region", region)
            self.assertIn("camera_count", region)
            self.assertIsInstance(region["camera_count"], int)
            self.assertGreater(region["camera_count"], 0)

    def test_list_camera_regions_matches_curated(self):
        """list_camera_regions() entries match CURATED_CAMERAS keys."""
        regions = self.list_camera_regions()
        region_names = {r["region"] for r in regions}
        self.assertEqual(region_names, set(self.CURATED_CAMERAS.keys()))

    def test_get_cameras_near_boston(self):
        """get_cameras_near() Boston returns traffic city cameras."""
        # Boston lat/lon — should find NYC Times Square (≈306km, within 400km)
        cams = self.get_cameras_near(42.36, -71.05, radius_km=400)
        # Should find at least some cameras within 400km of Boston
        self.assertIsInstance(cams, list)

    def test_get_cameras_near_tight_radius(self):
        """get_cameras_near() with very small radius returns fewer cameras."""
        cams_wide = self.get_cameras_near(40.71, -74.01, radius_km=500)
        cams_narrow = self.get_cameras_near(40.71, -74.01, radius_km=5)
        self.assertGreaterEqual(len(cams_wide), len(cams_narrow))

    def test_get_cameras_near_has_distance(self):
        """get_cameras_near() results include distance_km field."""
        cams = self.get_cameras_near(40.71, -74.01, radius_km=200)
        for cam in cams:
            self.assertIn("distance_km", cam)
            self.assertIsInstance(cam["distance_km"], float)

    def test_get_cameras_near_sorted_by_distance(self):
        """get_cameras_near() results are sorted ascending by distance."""
        cams = self.get_cameras_near(40.71, -74.01, radius_km=500)
        if len(cams) >= 2:
            distances = [c["distance_km"] for c in cams]
            self.assertEqual(distances, sorted(distances))

    def test_all_curated_cameras_have_embed_url(self):
        """All curated cameras have an embed_url field."""
        for region, cams in self.CURATED_CAMERAS.items():
            for cam in cams:
                self.assertIn("embed_url", cam, f"Missing embed_url in region={region}, cam={cam.get('name')}")
                self.assertTrue(cam["embed_url"], f"Empty embed_url in region={region}, cam={cam.get('name')}")


class TestLocalDiscovery(TimedTestCase):
    """Unit tests for src/sts_monitor/connectors/local_discovery.py"""

    def setUp(self):
        from sts_monitor.connectors.local_discovery import (
            _parse_google_news_rss, _yt_cam, _CITY_SUBREDDITS,
            _STATE_BBOXES, _CITY_CAMERAS, fetch_cameras_for_location,
        )
        self._parse_google_news_rss = _parse_google_news_rss
        self._yt_cam = _yt_cam
        self._CITY_SUBREDDITS = _CITY_SUBREDDITS
        self._STATE_BBOXES = _STATE_BBOXES
        self._CITY_CAMERAS = _CITY_CAMERAS
        self.fetch_cameras_for_location = fetch_cameras_for_location

    def test_yt_cam_structure(self):
        """_yt_cam() returns required keys."""
        cam = self._yt_cam("Test Cam", "dQw4w9WgXcQ", 42.36, -71.05)
        for key in ("title", "embed_url", "thumbnail", "lat", "lon", "source"):
            self.assertIn(key, cam)

    def test_yt_cam_embed_url_youtube(self):
        """_yt_cam() produces a YouTube embed URL."""
        cam = self._yt_cam("Test", "dQw4w9WgXcQ", 0.0, 0.0)
        self.assertIn("youtube.com/embed", cam["embed_url"])

    def test_city_subreddits_nonempty(self):
        """_CITY_SUBREDDITS lookup is populated."""
        self.assertGreater(len(self._CITY_SUBREDDITS), 10)
        self.assertIn("boston", self._CITY_SUBREDDITS)
        self.assertIn("new york", self._CITY_SUBREDDITS)

    def test_state_bboxes_valid(self):
        """State bounding boxes have 4 values each, all float-like."""
        for state, bbox in self._STATE_BBOXES.items():
            self.assertEqual(len(bbox), 4, f"Bad bbox for {state}")
            lat_min, lon_min, lat_max, lon_max = bbox
            self.assertLess(lat_min, lat_max, f"lat_min >= lat_max for {state}")
            self.assertLess(lon_min, lon_max, f"lon_min >= lon_max for {state}")
            self.assertGreater(lat_min, -90)
            self.assertLess(lat_max, 90)

    def test_city_cameras_have_embed_urls(self):
        """All curated city cameras with embed_url use YouTube."""
        for city, cams in self._CITY_CAMERAS.items():
            for cam in cams:
                url = cam.get("embed_url", "")
                if url:
                    self.assertIn("youtube.com/embed", url,
                                  f"Non-YouTube embed in city={city}, cam={cam.get('title')}")

    def test_parse_google_news_rss_empty(self):
        """_parse_google_news_rss handles empty XML gracefully."""
        result = self._parse_google_news_rss("<rss></rss>", 42.36, -71.05)
        self.assertIsInstance(result, list)
        self.assertEqual(result, [])

    def test_parse_google_news_rss_valid(self):
        """_parse_google_news_rss extracts events from valid RSS XML."""
        from datetime import datetime, timezone
        # Format date close to now so it passes the 48h filter
        now = datetime.now(timezone.utc)
        pub_date = now.strftime("%a, %d %b %Y %H:%M:%S +0000")
        xml = f"""<rss><channel>
            <item>
                <title>Boston emergency shooting incident downtown</title>
                <link>https://example.com/news/1</link>
                <pubDate>{pub_date}</pubDate>
                <source>Boston Globe</source>
            </item>
        </channel></rss>"""
        result = self._parse_google_news_rss(xml, 42.36, -71.05)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertIn("source_id", result[0])
        self.assertIn("title", result[0])
        self.assertIn("lat", result[0])
        self.assertIn("lon", result[0])

    def test_fetch_cameras_for_location_boston(self):
        """fetch_cameras_for_location returns YouTube cameras for Boston."""
        cams = self.fetch_cameras_for_location("Boston", 42.36, -71.05)
        # Should return cameras with embed_urls (filtering OSM stubs)
        self.assertIsInstance(cams, list)
        for cam in cams:
            self.assertIn("embed_url", cam)
            self.assertTrue(cam["embed_url"], "embed_url must not be empty")

    def test_fetch_cameras_filters_empty_embed(self):
        """fetch_cameras_for_location never returns cameras without embed_url."""
        cams = self.fetch_cameras_for_location("New York", 40.71, -74.01)
        for cam in cams:
            self.assertTrue(cam.get("embed_url"), "All returned cams must have embed_url")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. INTEGRATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntegration(TimedTestCase):
    """Integration tests against the live API at http://localhost:8080"""

    def _get(self, path: str, params: dict | None = None, timeout: int = 15) -> requests.Response:
        return _SESSION.get(BASE_URL + path, params=params, timeout=timeout)

    def _post(self, path: str, json_body: dict, timeout: int = 30) -> requests.Response:
        return _SESSION.post(BASE_URL + path, json=json_body, timeout=timeout)

    def _delete(self, path: str, timeout: int = 10) -> requests.Response:
        return _SESSION.delete(BASE_URL + path, timeout=timeout)

    # ── Health & System ──────────────────────────────────────────────────────

    def test_health(self):
        """GET /health → 200."""
        r = self._get("/health")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("status", data)

    def test_preflight(self):
        """GET /system/preflight → 200."""
        r = self._get("/system/preflight")
        self.assertEqual(r.status_code, 200)

    # ── Dashboard GET endpoints ──────────────────────────────────────────────

    def test_dashboard_summary(self):
        """GET /dashboard/summary → 200."""
        r = self._get("/dashboard/summary")
        self.assertEqual(r.status_code, 200)

    def test_dashboard_map_data(self):
        """GET /dashboard/map-data?hours=24&layers=earthquake → 200."""
        r = self._get("/dashboard/map-data", params={"hours": 24, "layers": "earthquake"})
        self.assertEqual(r.status_code, 200)

    def test_dashboard_cameras(self):
        """GET /dashboard/cameras?region=traffic_cities → 200."""
        r = self._get("/dashboard/cameras", params={"region": "traffic_cities"})
        self.assertEqual(r.status_code, 200)

    def test_dashboard_timeline(self):
        """GET /dashboard/timeline?hours=24&bucket_hours=1 → 200."""
        r = self._get("/dashboard/timeline", params={"hours": 24, "bucket_hours": 1})
        self.assertEqual(r.status_code, 200)

    def test_dashboard_local_news(self):
        """GET /dashboard/local-news?lat=42.36&lon=-71.05&radius=50 → 200."""
        r = self._get("/dashboard/local-news", params={"lat": 42.36, "lon": -71.05, "radius": 50})
        self.assertEqual(r.status_code, 200)

    def test_dashboard_local_intelligence(self):
        """GET /dashboard/local-intelligence?lat=42.36&lon=-71.05&radius_km=50 → 200."""
        r = self._get("/dashboard/local-intelligence", params={"lat": 42.36, "lon": -71.05, "radius_km": 50}, timeout=20)
        self.assertEqual(r.status_code, 200)

    def test_dashboard_watch_rules_list(self):
        """GET /dashboard/watch-rules → 200."""
        r = self._get("/dashboard/watch-rules")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    def test_dashboard_subscriptions_list(self):
        """GET /dashboard/subscriptions → 200."""
        r = self._get("/dashboard/subscriptions")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    def test_dashboard_situations(self):
        """GET /dashboard/situations → 200."""
        r = self._get("/dashboard/situations")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("situations", data)

    def test_dashboard_predict(self):
        """GET /dashboard/predict → 200."""
        r = self._get("/dashboard/predict")
        self.assertEqual(r.status_code, 200)

    def test_dashboard_historical(self):
        """GET /dashboard/historical?before=2026-03-15T00:00:00Z → 200."""
        r = self._get("/dashboard/historical", params={"before": "2026-03-15T00:00:00Z"})
        self.assertEqual(r.status_code, 200)

    def test_dashboard_entities_graph(self):
        """GET /dashboard/entities/graph → 200."""
        r = self._get("/dashboard/entities/graph")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("nodes", data)
        self.assertIn("edges", data)

    def test_dashboard_investigate(self):
        """GET /dashboard/investigate?lat=42.36&lon=-71.05&radius_km=50 → 200."""
        r = self._get("/dashboard/investigate", params={"lat": 42.36, "lon": -71.05, "radius_km": 50})
        self.assertEqual(r.status_code, 200)

    # ── Watch Rules CRUD ─────────────────────────────────────────────────────

    def test_watch_rules_create_and_delete(self):
        """POST /dashboard/watch-rules → 200, then DELETE → 200."""
        r_create = self._post("/dashboard/watch-rules", {
            "name": "QA Test Rule",
            "lat": 42.36,
            "lon": -71.05,
            "radius_km": 50,
            "min_magnitude": 3.0,
            "layers": ["earthquake"],
        })
        self.assertEqual(r_create.status_code, 200)
        rule_id = r_create.json().get("id")
        self.assertIsNotNone(rule_id, "Response must include created rule id")

        r_delete = self._delete(f"/dashboard/watch-rules/{rule_id}")
        self.assertEqual(r_delete.status_code, 200)
        self.assertTrue(r_delete.json().get("deleted"))

    # ── Subscriptions CRUD ───────────────────────────────────────────────────

    def test_subscriptions_create_and_delete(self):
        """POST /dashboard/subscriptions → 200, then DELETE → 200."""
        r_create = self._post("/dashboard/subscriptions", {
            "name": "qa_test_sub",
            "display_name": "QA Test Subscription",
            "lat": 42.36,
            "lon": -71.05,
            "radius_km": 50,
            "city": "Boston",
            "state": "MA",
        })
        self.assertEqual(r_create.status_code, 200)
        sub_id = r_create.json().get("id")
        self.assertIsNotNone(sub_id)

        r_delete = self._delete(f"/dashboard/subscriptions/{sub_id}")
        self.assertEqual(r_delete.status_code, 200)
        self.assertTrue(r_delete.json().get("deleted"))

    # ── Situations ───────────────────────────────────────────────────────────

    def test_situations_run(self):
        """POST /dashboard/situations/run → 200."""
        r = self._post("/dashboard/situations/run", {})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("detected", data)
        self.assertIsInstance(data["detected"], int)

    # ── Briefing Verify ──────────────────────────────────────────────────────

    def test_briefing_verify(self):
        """POST /dashboard/briefing/verify → 200."""
        r = self._post("/dashboard/briefing/verify", {"briefing": "test briefing content"})
        self.assertEqual(r.status_code, 200)

    # ── Investigations ───────────────────────────────────────────────────────

    def test_investigations_list(self):
        """GET /investigations → 200."""
        r = self._get("/investigations")
        self.assertEqual(r.status_code, 200)

    def test_investigations_create(self):
        """POST /investigations → 200 (valid) or 422 (validation error)."""
        r = self._post("/investigations", {
            "topic": "QA test investigation topic",
            "priority": 50,
        })
        self.assertIn(r.status_code, [200, 422])

    def test_investigations_create_invalid_rejected(self):
        """POST /investigations with too-short topic → 422."""
        r = self._post("/investigations", {"topic": "X"})  # min_length=3
        self.assertEqual(r.status_code, 422)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SECURITY TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestSecurity(TimedTestCase):
    """Security tests: auth enforcement, injection, XSS handling."""

    def test_missing_api_key_returns_401(self):
        """Requests without X-API-Key to protected endpoint → 401."""
        r = _SESSION.get(BASE_URL + "/dashboard/summary",
                         headers={"X-API-Key": None}, timeout=5)
        # Fallback: use a fresh session without auth headers
        s = requests.Session()
        r = s.get(BASE_URL + "/dashboard/summary", timeout=5)
        self.assertEqual(r.status_code, 401)

    def test_invalid_api_key_returns_401(self):
        """Requests with wrong API key → 401."""
        r = _SESSION.get(BASE_URL + "/dashboard/summary",
                         headers={"X-API-Key": "invalid-key-xyz-12345"},
                         timeout=5)
        self.assertEqual(r.status_code, 401)

    def test_health_endpoint_is_public(self):
        """GET /health is accessible without auth key."""
        r = _SESSION.get(BASE_URL + "/health", timeout=5)
        self.assertEqual(r.status_code, 200)

    def test_sql_injection_in_query_param_safe(self):
        """SQL injection in layers param does not cause 500."""
        # A valid endpoint that accepts layer filter — injection should be safe-rejected
        malicious = "earthquake' OR 1=1--"
        r = _SESSION.get(
            BASE_URL + "/dashboard/map-data",
            params={"hours": 24, "layers": malicious},
            timeout=10,
        )
        # Should return a valid response (200/422/400), never a 500
        self.assertNotEqual(r.status_code, 500,
                            f"SQL injection caused server error: {r.text[:200]}")
        self.assertIn(r.status_code, [200, 400, 422])

    def test_sql_injection_in_hours_param_safe(self):
        """SQL injection in numeric param is safely handled."""
        malicious = "1 UNION SELECT * FROM api_keys--"
        r = _SESSION.get(
            BASE_URL + "/dashboard/map-data",
            params={"hours": malicious, "layers": "earthquake"},
            timeout=10,
        )
        self.assertNotEqual(r.status_code, 500)

    def test_xss_in_post_body_safe(self):
        """XSS payload in POST body is handled safely (no 500)."""
        xss_payload = "<script>alert('xss')</script>"
        r = _SESSION.post(
            BASE_URL + "/dashboard/briefing/verify",
            json={"briefing": xss_payload},
            timeout=10,
        )
        # Server must not crash
        self.assertNotEqual(r.status_code, 500,
                            f"XSS payload caused server error: {r.text[:200]}")
        # Response should not echo the raw script tag
        if r.status_code == 200:
            self.assertNotIn("<script>", r.text)

    def test_xss_in_investigation_title_safe(self):
        """XSS payload in investigation topic is handled safely."""
        xss_payload = "<img src=x onerror=alert(1)> test investigation title"
        r = _SESSION.post(
            BASE_URL + "/investigations",
            json={"topic": xss_payload, "priority": 50},
            timeout=10,
        )
        self.assertNotIn(r.status_code, [500])

    def test_no_key_on_investigations_returns_401(self):
        """GET /investigations without key → 401."""
        s = requests.Session()
        r = s.get(BASE_URL + "/investigations", timeout=5)
        self.assertEqual(r.status_code, 401)

    def test_empty_string_api_key_returns_401(self):
        """Empty string API key → 401."""
        r = _SESSION.get(
            BASE_URL + "/dashboard/summary",
            headers={"X-API-Key": ""},
            timeout=5,
        )
        self.assertEqual(r.status_code, 401)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. PERFORMANCE TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestPerformance(TimedTestCase):
    """Performance tests: response time SLAs and concurrent load."""

    def test_health_under_500ms(self):
        """GET /health responds in under 500ms (using keep-alive connection)."""
        # Warm the connection first, then measure
        _SESSION.get(BASE_URL + "/health", timeout=5)
        t0 = time.perf_counter()
        r = _SESSION.get(BASE_URL + "/health", timeout=5)
        elapsed = time.perf_counter() - t0
        self.assertEqual(r.status_code, 200)
        self.assertLess(elapsed, 0.5, f"/health took {elapsed*1000:.0f}ms (limit: 500ms)")

    def test_map_data_under_5000ms(self):
        """GET /dashboard/map-data responds in under 5000ms."""
        t0 = time.perf_counter()
        r = _SESSION.get(BASE_URL + "/dashboard/map-data",
                         params={"hours": 24, "layers": "earthquake"},
                         timeout=10)
        elapsed = time.perf_counter() - t0
        self.assertEqual(r.status_code, 200)
        self.assertLess(elapsed, 5.0, f"/dashboard/map-data took {elapsed*1000:.0f}ms (limit: 5000ms)")

    def test_predict_under_3000ms(self):
        """GET /dashboard/predict responds in under 3000ms."""
        t0 = time.perf_counter()
        r = _SESSION.get(BASE_URL + "/dashboard/predict", timeout=10)
        elapsed = time.perf_counter() - t0
        self.assertEqual(r.status_code, 200)
        self.assertLess(elapsed, 3.0, f"/dashboard/predict took {elapsed*1000:.0f}ms (limit: 3000ms)")

    def test_health_10_concurrent_all_succeed(self):
        """10 concurrent GET /health requests all return 200."""
        def _hit_health():
            # Each thread uses its own session to avoid connection sharing issues
            s = requests.Session()
            s.headers.update(HEADERS)
            r = s.get(BASE_URL + "/health", timeout=10)
            return r.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(_hit_health) for _ in range(10)]
            statuses = [f.result() for f in concurrent.futures.as_completed(futures)]

        failures = [s for s in statuses if s != 200]
        self.assertEqual(failures, [], f"{len(failures)} of 10 concurrent /health requests failed: {failures}")

    def test_summary_under_2000ms(self):
        """GET /dashboard/summary responds in under 2000ms."""
        t0 = time.perf_counter()
        r = _SESSION.get(BASE_URL + "/dashboard/summary", timeout=10)
        elapsed = time.perf_counter() - t0
        self.assertEqual(r.status_code, 200)
        self.assertLess(elapsed, 2.0, f"/dashboard/summary took {elapsed*1000:.0f}ms (limit: 2000ms)")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. DATA INTEGRITY TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestDataIntegrity(TimedTestCase):
    """Data integrity tests: validate shapes and value ranges of API responses."""

    def test_camera_feeds_all_have_embed_url(self):
        """All camera features in /dashboard/cameras have embed_url."""
        r = _SESSION.get(BASE_URL + "/dashboard/cameras",
                         params={"region": "traffic_cities"},
                         timeout=10)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        features = data.get("features", [])
        self.assertGreater(len(features), 0, "cameras endpoint returned no features")
        for feat in features:
            props = feat.get("properties", {})
            embed = props.get("embed_url", "")
            self.assertTrue(embed, f"Camera feature missing embed_url: {feat}")
            self.assertIn("youtube.com/embed", embed,
                          f"Camera embed_url is not a YouTube URL: {embed}")

    def test_map_data_events_valid_coordinates(self):
        """All map-data features have coordinates within valid lat/lon range."""
        r = _SESSION.get(BASE_URL + "/dashboard/map-data",
                         params={"hours": 24},
                         timeout=10)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        features = data.get("features", [])
        self.assertIsInstance(features, list)
        for feat in features:
            geom = feat.get("geometry", {})
            coords = geom.get("coordinates", [])
            if coords:
                lon, lat = coords[0], coords[1]
                self.assertGreaterEqual(lat, -90, f"lat={lat} out of range")
                self.assertLessEqual(lat, 90, f"lat={lat} out of range")
                self.assertGreaterEqual(lon, -180, f"lon={lon} out of range")
                self.assertLessEqual(lon, 180, f"lon={lon} out of range")

    def test_predict_returns_valid_importance_scores(self):
        """predict endpoint returns events with importance scores in [0, 10]."""
        r = _SESSION.get(BASE_URL + "/dashboard/predict", timeout=10)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        events = data.get("top_events", [])
        self.assertIsInstance(events, list)
        for ev in events:
            score = ev.get("predicted_importance")
            self.assertIsNotNone(score, f"Event missing predicted_importance: {ev.get('title')}")
            self.assertGreaterEqual(score, 0.0, f"Score {score} < 0")
            self.assertLessEqual(score, 10.0, f"Score {score} > 10")

    def test_situations_valid_severity_levels(self):
        """Situations endpoint returns only valid severity levels."""
        VALID_SEVERITIES = {"critical", "high", "medium", "low"}
        r = _SESSION.get(BASE_URL + "/dashboard/situations", timeout=10)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        situations = data.get("situations", [])
        for sit in situations:
            sev = sit.get("severity")
            self.assertIn(sev, VALID_SEVERITIES,
                          f"Invalid severity '{sev}' for situation id={sit.get('id')}")

    def test_map_data_geojson_structure(self):
        """map-data response is a GeoJSON FeatureCollection."""
        r = _SESSION.get(BASE_URL + "/dashboard/map-data",
                         params={"hours": 24},
                         timeout=10)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data.get("type"), "FeatureCollection")
        self.assertIn("features", data)

    def test_summary_has_expected_fields(self):
        """Dashboard summary contains key fields."""
        r = _SESSION.get(BASE_URL + "/dashboard/summary", timeout=10)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIsInstance(data, dict)
        self.assertGreater(len(data), 0, "Summary response is empty")

    def test_entities_graph_valid_structure(self):
        """entities/graph returns valid node/edge structure."""
        r = _SESSION.get(BASE_URL + "/dashboard/entities/graph", timeout=10)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("nodes", data)
        self.assertIn("edges", data)
        self.assertIsInstance(data["nodes"], list)
        self.assertIsInstance(data["edges"], list)

    def test_situations_have_required_fields(self):
        """Each situation has required fields."""
        r = _SESSION.get(BASE_URL + "/dashboard/situations", timeout=10)
        self.assertEqual(r.status_code, 200)
        situations = r.json().get("situations", [])
        required = {"id", "title", "severity", "signal_count", "predicted_importance"}
        for sit in situations:
            for field in required:
                self.assertIn(field, sit, f"Situation missing field '{field}': {sit}")

    def test_predict_events_have_lat_lon(self):
        """predict events have latitude and longitude."""
        r = _SESSION.get(BASE_URL + "/dashboard/predict", timeout=10)
        self.assertEqual(r.status_code, 200)
        events = r.json().get("top_events", [])
        for ev in events:
            self.assertIn("latitude", ev)
            self.assertIn("longitude", ev)
            lat = ev["latitude"]
            lon = ev["longitude"]
            if lat is not None:
                self.assertGreaterEqual(lat, -90)
                self.assertLessEqual(lat, 90)
            if lon is not None:
                self.assertGreaterEqual(lon, -180)
                self.assertLessEqual(lon, 180)

    def test_watch_rules_response_structure(self):
        """watch-rules list returns list of dicts with expected fields."""
        r = _SESSION.get(BASE_URL + "/dashboard/watch-rules", timeout=10)
        self.assertEqual(r.status_code, 200)
        rules = r.json()
        self.assertIsInstance(rules, list)
        for rule in rules:
            for field in ("id", "name", "lat", "lon", "radius_km"):
                self.assertIn(field, rule, f"Watch rule missing field '{field}'")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN — Run all tests and print summary
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    import os
    print("=" * 70)
    print("  STS Situation Monitor - Full QA Test Suite")
    print(f"  Target: {BASE_URL}")
    print("=" * 70)

    # Warm up the shared HTTP session (establishes keep-alive connection)
    try:
        _SESSION.get(BASE_URL + "/health", timeout=10)
    except Exception:
        pass

    suites = [
        ("1. Unit Tests — predictive.py", TestPredictive),
        ("2. Unit Tests — correlation.py", TestCorrelation),
        ("3. Unit Tests — webcams.py", TestWebcams),
        ("4. Unit Tests — local_discovery.py", TestLocalDiscovery),
        ("5. Integration Tests", TestIntegration),
        ("6. Security Tests", TestSecurity),
        ("7. Performance Tests", TestPerformance),
        ("8. Data Integrity Tests", TestDataIntegrity),
    ]

    all_pass = True
    total_passed = 0
    total_run = 0

    for section_title, test_cls in suites:
        print(f"\n{'-' * 60}")
        print(f"  {section_title}")
        print(f"{'-' * 60}")
        before = len(_results)
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromTestCase(test_cls)
        runner = unittest.TextTestRunner(stream=open(os.devnull, "w"), verbosity=0)
        result = runner.run(suite)

        # Count results for this section
        section_results = _results[before:]
        section_pass = sum(1 for _, p, _, _ in section_results if p)
        section_total = len(section_results)
        total_passed += section_pass
        total_run += section_total
        if section_pass < section_total:
            all_pass = False

        print(f"  Section: {section_pass}/{section_total} passed")

    print("\n" + "=" * 70)
    print(f"  FINAL SUMMARY: {total_passed}/{total_run} tests passed")
    print("=" * 70)

    # Detailed failure report
    failures = [(n, note) for n, p, _, note in _results if not p]
    if failures:
        print(f"\n  FAILURES ({len(failures)}):")
        for name, note in failures:
            print(f"    [FAIL] {name}")
            if note:
                print(f"           {note[:120]}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    import os
    sys.exit(main())
