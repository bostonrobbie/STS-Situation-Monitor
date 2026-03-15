"""Tests for the anomaly detection engine."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import sqrt

import pytest

from sts_monitor.anomaly_detector import (
    Anomaly,
    AnomalyReport,
    _escalation_score,
    _extract_topic_words,
    _mean,
    _parse_time,
    _severity_from_z,
    _source_family,
    _std,
    _z_score,
    detect_entity_velocity_anomalies,
    detect_sentiment_shifts,
    detect_silence_anomalies,
    detect_source_behavior_shifts,
    detect_volume_anomalies,
    run_anomaly_detection,
)

pytestmark = pytest.mark.unit


# ── Helpers for timestamp generation ──────────────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


def _baseline_time(hours_ago: float = 24, offset_hours: float = 0) -> datetime:
    """Return a timestamp in the baseline window (default 24h ago + offset)."""
    return _now() - timedelta(hours=hours_ago) + timedelta(hours=offset_hours)


def _detection_time(hours_ago: float = 1) -> datetime:
    """Return a timestamp in the detection window (recent, default 1h ago)."""
    return _now() - timedelta(hours=hours_ago)


def _make_obs(
    claim: str = "Some observation",
    source: str = "test_source",
    captured_at: datetime | None = None,
    url: str = "https://example.com",
) -> dict:
    return {
        "claim": claim,
        "source": source,
        "captured_at": captured_at or _now(),
        "url": url,
    }


# ── Statistical helpers ───────────────────────────────────────────────


class TestMean:
    def test_basic(self) -> None:
        assert _mean([1.0, 2.0, 3.0]) == 2.0

    def test_single_value(self) -> None:
        assert _mean([5.0]) == 5.0

    def test_empty_returns_zero(self) -> None:
        assert _mean([]) == 0.0

    def test_negative_values(self) -> None:
        assert _mean([-2.0, 2.0]) == 0.0


class TestStd:
    def test_basic(self) -> None:
        vals = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
        result = _std(vals)
        assert result == pytest.approx(2.138, abs=0.01)

    def test_single_value_returns_zero(self) -> None:
        assert _std([5.0]) == 0.0

    def test_empty_returns_zero(self) -> None:
        assert _std([]) == 0.0

    def test_identical_values(self) -> None:
        assert _std([3.0, 3.0, 3.0]) == 0.0

    def test_two_values(self) -> None:
        # std of [0, 2] with sample std = sqrt(2)
        result = _std([0.0, 2.0])
        assert result == pytest.approx(sqrt(2.0), abs=0.01)


class TestZScore:
    def test_basic(self) -> None:
        assert _z_score(10.0, 5.0, 2.5) == 2.0

    def test_negative_z(self) -> None:
        assert _z_score(0.0, 5.0, 2.5) == -2.0

    def test_zero_std_equal(self) -> None:
        assert _z_score(5.0, 5.0, 0.0) == 0.0

    def test_zero_std_above(self) -> None:
        assert _z_score(10.0, 5.0, 0.0) == 10.0

    def test_zero_std_below(self) -> None:
        assert _z_score(0.0, 5.0, 0.0) == -10.0


class TestSeverityFromZ:
    def test_low(self) -> None:
        assert _severity_from_z(1.5) == "low"

    def test_medium(self) -> None:
        assert _severity_from_z(2.5) == "medium"

    def test_high(self) -> None:
        assert _severity_from_z(3.5) == "high"

    def test_critical(self) -> None:
        assert _severity_from_z(4.5) == "critical"

    def test_negative_z_uses_abs(self) -> None:
        assert _severity_from_z(-3.5) == "high"

    def test_boundary_medium(self) -> None:
        assert _severity_from_z(2.0) == "medium"

    def test_boundary_high(self) -> None:
        assert _severity_from_z(3.0) == "high"

    def test_boundary_critical(self) -> None:
        assert _severity_from_z(4.0) == "critical"


# ── Parse time and source helpers ─────────────────────────────────────


class TestParseTime:
    def test_datetime_with_tz(self) -> None:
        dt = datetime(2025, 1, 15, 12, 0, tzinfo=UTC)
        assert _parse_time(dt) == dt

    def test_datetime_without_tz_gets_utc(self) -> None:
        dt = datetime(2025, 1, 15, 12, 0)
        result = _parse_time(dt)
        assert result.tzinfo == UTC

    def test_iso_string(self) -> None:
        result = _parse_time("2025-01-15T12:00:00+00:00")
        assert result.year == 2025
        assert result.month == 1
        assert result.day == 15

    def test_iso_string_no_tz(self) -> None:
        result = _parse_time("2025-01-15T12:00:00")
        assert result.tzinfo == UTC

    def test_invalid_returns_now(self) -> None:
        result = _parse_time("not-a-date")
        assert (datetime.now(UTC) - result).total_seconds() < 5

    def test_none_returns_now(self) -> None:
        result = _parse_time(None)
        assert (datetime.now(UTC) - result).total_seconds() < 5


class TestSourceFamily:
    def test_basic_split(self) -> None:
        assert _source_family("reuters: world news") == "reuters"

    def test_no_colon(self) -> None:
        assert _source_family("reuters") == "reuters"

    def test_strips_whitespace(self) -> None:
        assert _source_family("  Reuters : politics") == "reuters"


# ── Escalation score ──────────────────────────────────────────────────


class TestEscalationScore:
    def test_empty_text(self) -> None:
        assert _escalation_score("") == 0.0

    def test_neutral_text(self) -> None:
        assert _escalation_score("The weather is sunny today") == 0.0

    def test_escalatory_text(self) -> None:
        score = _escalation_score("missile attack killed many casualties destroyed buildings")
        assert score > 0.5

    def test_deescalatory_text(self) -> None:
        score = _escalation_score("peace ceasefire agreement negotiate calm")
        assert score < -0.5

    def test_mixed_text_near_zero(self) -> None:
        score = _escalation_score("attack and peace talks continue")
        assert -0.5 <= score <= 0.5

    def test_range_bounded(self) -> None:
        score = _escalation_score("attack bomb missile strike assault killed dead casualties")
        assert -1.0 <= score <= 1.0

    def test_partial_match_works(self) -> None:
        # "escalation" should match "escalat" prefix in terms
        score = _escalation_score("escalation intensified")
        assert score > 0.0


# ── Extract topic words ───────────────────────────────────────────────


class TestExtractTopicWords:
    def test_filters_short_words(self) -> None:
        words = _extract_topic_words("I am ok now")
        assert words == []

    def test_extracts_meaningful_words(self) -> None:
        words = _extract_topic_words("military operations near border")
        assert "military" in words
        assert "operations" in words
        assert "border" in words

    def test_filters_stop_words(self) -> None:
        words = _extract_topic_words("this is about the said situation")
        assert "this" not in words
        assert "about" not in words
        assert "said" not in words
        assert "situation" in words

    def test_lowercases(self) -> None:
        words = _extract_topic_words("NATO Deployed TROOPS")
        assert "nato" in words
        assert "deployed" in words
        assert "troops" in words


# ── Volume anomaly detection ──────────────────────────────────────────


class TestVolumeAnomalies:
    def test_empty_observations(self) -> None:
        assert detect_volume_anomalies([]) == []

    def test_no_baseline_returns_empty(self) -> None:
        # All observations in detection window, none in baseline
        obs = [_make_obs(captured_at=_detection_time(1)) for _ in range(20)]
        result = detect_volume_anomalies(obs, detection_hours=6, baseline_hours=72)
        assert result == []

    def test_spike_detected(self) -> None:
        now = _now()
        obs = []
        # Baseline: 1 observation per hour for 48 hours
        for h in range(8, 56):
            obs.append(_make_obs(captured_at=now - timedelta(hours=h)))
        # Detection window: 30 observations in the last hour (huge spike)
        for _ in range(30):
            obs.append(_make_obs(captured_at=now - timedelta(minutes=30)))

        result = detect_volume_anomalies(obs, detection_hours=6, baseline_hours=72, min_z_score=2.0)
        assert len(result) > 0
        anomaly = result[0]
        assert anomaly.anomaly_type == "volume"
        assert "spike" in anomaly.title.lower()
        assert anomaly.z_score > 0

    def test_drop_detected(self) -> None:
        now = _now()
        obs = []
        # Baseline: 20 observations per hour for many hours
        for h in range(7, 60):
            for _ in range(20):
                obs.append(_make_obs(captured_at=now - timedelta(hours=h, minutes=10)))
        # Detection window: 1 observation in one hour (drop)
        obs.append(_make_obs(captured_at=now - timedelta(hours=1)))

        result = detect_volume_anomalies(obs, detection_hours=6, baseline_hours=72, min_z_score=2.0)
        drops = [a for a in result if "drop" in a.title.lower()]
        assert len(drops) > 0

    def test_normal_volume_no_anomaly(self) -> None:
        now = _now()
        obs = []
        # Uniform 2 obs per hour across baseline and detection
        for h in range(0, 50):
            for _ in range(2):
                obs.append(_make_obs(captured_at=now - timedelta(hours=h, minutes=15)))
        result = detect_volume_anomalies(obs, detection_hours=6, baseline_hours=72, min_z_score=2.0)
        assert result == []

    def test_anomaly_fields_populated(self) -> None:
        now = _now()
        obs = []
        for h in range(8, 56):
            obs.append(_make_obs(captured_at=now - timedelta(hours=h)))
        for _ in range(30):
            obs.append(_make_obs(captured_at=now - timedelta(minutes=30)))

        result = detect_volume_anomalies(obs, detection_hours=6, baseline_hours=72)
        if result:
            a = result[0]
            assert a.metric_name == "observations_per_hour"
            assert a.window_hours == 6
            assert a.affected_entity.startswith("time_bucket:")
            assert len(a.evidence) > 0
            assert a.recommended_action


# ── Source behavior shift detection ───────────────────────────────────


class TestSourceBehaviorShifts:
    def test_empty_observations(self) -> None:
        assert detect_source_behavior_shifts([]) == []

    def test_shift_detected(self) -> None:
        now = _now()
        obs = []
        # Baseline: source covers military topics
        baseline_claims = [
            "military operations continue near border",
            "troops deployed to northern region",
            "defense forces mobilized for exercises",
            "soldiers patrolling the checkpoint area",
            "armored vehicles spotted moving south",
            "military base upgraded with radar systems",
            "naval fleet conducting training operations",
            "infantry division reorganized for deployment",
        ]
        for i, claim in enumerate(baseline_claims):
            obs.append(_make_obs(
                claim=claim,
                source="test_source",
                captured_at=now - timedelta(hours=48 + i),
            ))

        # Detection: source pivots to completely different topics
        detection_claims = [
            "cryptocurrency markets experiencing volatility",
            "blockchain technology disrupts banking sector",
            "digital currency regulations proposed globally",
            "bitcoin mining operations expand rapidly",
            "financial technology startups raising capital",
            "virtual reality gaming platform launched",
            "artificial intelligence research breakthrough",
            "quantum computing advances accelerate",
        ]
        for i, claim in enumerate(detection_claims):
            obs.append(_make_obs(
                claim=claim,
                source="test_source",
                captured_at=now - timedelta(hours=3 + i * 0.5),
            ))

        result = detect_source_behavior_shifts(
            obs, detection_hours=12, baseline_hours=168, min_z_score=2.0,
        )
        assert len(result) > 0
        assert result[0].anomaly_type == "source_shift"

    def test_consistent_source_no_anomaly(self) -> None:
        now = _now()
        obs = []
        claims = [
            "military operations continue near border",
            "troops deployed to northern sector",
            "defense forces mobilized for operations",
            "soldiers monitoring the border area",
        ]
        for i, claim in enumerate(claims):
            # Same topics in baseline and detection
            obs.append(_make_obs(claim=claim, source="test_source",
                                 captured_at=now - timedelta(hours=48 + i)))
            obs.append(_make_obs(claim=claim, source="test_source",
                                 captured_at=now - timedelta(hours=3 + i)))

        result = detect_source_behavior_shifts(obs, detection_hours=12, baseline_hours=168)
        assert result == []

    def test_new_source_not_flagged(self) -> None:
        now = _now()
        # Source only in detection window, not in baseline => skip
        obs = [_make_obs(
            claim="brand new topic coverage here",
            source="new_source",
            captured_at=now - timedelta(hours=2),
        ) for _ in range(5)]
        result = detect_source_behavior_shifts(obs, detection_hours=12, baseline_hours=168)
        assert result == []


# ── Entity velocity anomaly detection ─────────────────────────────────


class TestEntityVelocityAnomalies:
    def test_empty_observations(self) -> None:
        assert detect_entity_velocity_anomalies([]) == []

    def test_surge_detected(self) -> None:
        now = _now()
        obs = []
        # Baseline: NATO mentioned once
        obs.append(_make_obs(
            claim="NATO held a meeting last week",
            source="news",
            captured_at=now - timedelta(hours=24),
        ))
        # Detection: NATO mentioned many times
        for i in range(15):
            obs.append(_make_obs(
                claim="NATO announced emergency deployment and NATO forces mobilize",
                source="news",
                captured_at=now - timedelta(hours=2, minutes=i * 5),
            ))

        result = detect_entity_velocity_anomalies(
            obs, detection_hours=6, baseline_hours=72,
            min_z_score=2.0, min_detection_count=3,
        )
        assert len(result) > 0
        assert result[0].anomaly_type == "entity_velocity"

    def test_below_min_detection_count_ignored(self) -> None:
        now = _now()
        obs = [
            _make_obs(claim="NATO met", source="news", captured_at=now - timedelta(hours=24)),
            _make_obs(claim="NATO discussed", source="news", captured_at=now - timedelta(hours=1)),
        ]
        result = detect_entity_velocity_anomalies(
            obs, detection_hours=6, baseline_hours=72, min_detection_count=3,
        )
        assert result == []

    def test_stable_entity_no_anomaly(self) -> None:
        now = _now()
        obs = []
        # Similar mention rate across baseline and detection
        for h in range(0, 48):
            obs.append(_make_obs(
                claim="Ukraine situation continues",
                source="news",
                captured_at=now - timedelta(hours=h),
            ))
        result = detect_entity_velocity_anomalies(
            obs, detection_hours=6, baseline_hours=72, min_z_score=2.5,
        )
        # Stable rate should not trigger (or only low z-scores)
        high_severity = [a for a in result if a.severity in ("high", "critical")]
        assert len(high_severity) == 0


# ── Silence anomaly detection ─────────────────────────────────────────


class TestSilenceAnomalies:
    def test_empty_observations(self) -> None:
        assert detect_silence_anomalies([]) == []

    def test_silence_detected_for_expected_source(self) -> None:
        now = _now()
        obs = []
        # Source was active in baseline (many observations)
        for h in range(10, 60):
            obs.append(_make_obs(source="reuters", captured_at=now - timedelta(hours=h)))

        # No observations in the last 6 hours (silence)
        result = detect_silence_anomalies(
            obs, expected_sources=["reuters"], silence_hours=6, baseline_hours=72,
        )
        assert len(result) > 0
        assert result[0].anomaly_type == "silence"
        assert "reuters" in result[0].title.lower()

    def test_active_source_no_silence(self) -> None:
        now = _now()
        obs = []
        # Source active recently
        for h in range(0, 48):
            obs.append(_make_obs(source="reuters", captured_at=now - timedelta(hours=h)))

        result = detect_silence_anomalies(
            obs, expected_sources=["reuters"], silence_hours=6, baseline_hours=72,
        )
        assert result == []

    def test_auto_detect_expected_sources(self) -> None:
        now = _now()
        obs = []
        # Source with many baseline observations but silent recently
        for h in range(10, 60):
            obs.append(_make_obs(source="bbc", captured_at=now - timedelta(hours=h)))

        # expected_sources=None triggers auto-detection (threshold >= 5)
        result = detect_silence_anomalies(
            obs, expected_sources=None, silence_hours=6, baseline_hours=72,
        )
        assert len(result) > 0

    def test_low_volume_source_not_auto_expected(self) -> None:
        now = _now()
        # Only 3 observations in baseline — below auto-detection threshold of 5
        obs = [
            _make_obs(source="rare_src", captured_at=now - timedelta(hours=h))
            for h in [20, 30, 40]
        ]
        result = detect_silence_anomalies(
            obs, expected_sources=None, silence_hours=6, baseline_hours=72,
        )
        assert result == []

    def test_unknown_expected_source_no_crash(self) -> None:
        now = _now()
        obs = [_make_obs(source="other", captured_at=now - timedelta(hours=10))]
        result = detect_silence_anomalies(
            obs, expected_sources=["nonexistent"], silence_hours=6, baseline_hours=72,
        )
        assert result == []

    def test_severity_escalates_with_duration(self) -> None:
        now = _now()
        obs = []
        # Source last seen 15 hours ago — should be "high"
        for h in range(15, 60):
            obs.append(_make_obs(source="reuters", captured_at=now - timedelta(hours=h)))

        result = detect_silence_anomalies(
            obs, expected_sources=["reuters"], silence_hours=6, baseline_hours=72,
        )
        assert len(result) > 0
        assert result[0].severity == "high"


# ── Sentiment shift detection ─────────────────────────────────────────


class TestSentimentShifts:
    def test_empty_observations(self) -> None:
        assert detect_sentiment_shifts([]) == []

    def test_no_baseline_returns_empty(self) -> None:
        obs = [_make_obs(
            claim="attack missile strike",
            captured_at=_detection_time(1),
        )]
        result = detect_sentiment_shifts(obs, detection_hours=6, baseline_hours=48)
        assert result == []

    def test_no_detection_returns_empty(self) -> None:
        obs = [_make_obs(
            claim="peace ceasefire agreement",
            captured_at=_baseline_time(24),
        )]
        result = detect_sentiment_shifts(obs, detection_hours=6, baseline_hours=48)
        assert result == []

    def test_escalation_shift_detected(self) -> None:
        now = _now()
        obs = []
        # Baseline: calm language
        calm_claims = [
            "peace talks continue between delegations",
            "ceasefire agreement holding steady",
            "humanitarian aid delivered successfully",
            "negotiations resume tomorrow morning",
            "calm restored after brief disturbance",
            "relief workers rebuild damaged areas",
        ]
        for i, claim in enumerate(calm_claims):
            obs.append(_make_obs(claim=claim, captured_at=now - timedelta(hours=12 + i)))

        # Detection: escalatory language
        escalatory_claims = [
            "massive missile attack destroyed infrastructure",
            "assault casualties mounting rapidly killed",
            "invasion forces intensified bombing offensive",
            "casualties surge after devastating strike",
            "emergency catastrophe declared destroyed",
            "attack killed many in devastating assault",
        ]
        for i, claim in enumerate(escalatory_claims):
            obs.append(_make_obs(claim=claim, captured_at=now - timedelta(hours=2, minutes=i * 10)))

        result = detect_sentiment_shifts(obs, detection_hours=6, baseline_hours=48, min_z_score=1.5)
        assert len(result) > 0
        assert result[0].anomaly_type == "sentiment"
        assert result[0].z_score > 0

    def test_stable_sentiment_no_anomaly(self) -> None:
        now = _now()
        obs = []
        # Same neutral language everywhere
        for h in range(0, 30):
            obs.append(_make_obs(
                claim="Regular daily weather report with forecasts",
                captured_at=now - timedelta(hours=h),
            ))
        result = detect_sentiment_shifts(obs, detection_hours=6, baseline_hours=48, min_z_score=2.0)
        assert result == []


# ── AnomalyReport.to_dict() ──────────────────────────────────────────


class TestAnomalyReportToDict:
    def test_basic_serialization(self) -> None:
        now = _now()
        anomaly = Anomaly(
            anomaly_type="volume",
            severity="high",
            title="Volume spike",
            description="Big spike",
            metric_name="obs_per_hour",
            current_value=50.123,
            baseline_mean=5.456,
            baseline_std=1.789,
            z_score=24.9123,
            detected_at=now,
            window_hours=6,
            affected_entity="time_bucket:2025-01-15-12",
            evidence=["ev1", "ev2"],
            recommended_action="Investigate",
        )
        report = AnomalyReport(
            detected_at=now,
            total_anomalies=1,
            by_type={"volume": 1},
            by_severity={"high": 1},
            anomalies=[anomaly],
            baseline_window_hours=72,
            detection_window_hours=6,
        )

        d = report.to_dict()
        assert d["detected_at"] == now.isoformat()
        assert d["total_anomalies"] == 1
        assert d["by_type"] == {"volume": 1}
        assert d["by_severity"] == {"high": 1}
        assert d["baseline_window_hours"] == 72
        assert d["detection_window_hours"] == 6
        assert len(d["anomalies"]) == 1

        ad = d["anomalies"][0]
        assert ad["type"] == "volume"
        assert ad["severity"] == "high"
        assert ad["current_value"] == 50.12
        assert ad["baseline_mean"] == 5.46
        assert ad["baseline_std"] == 1.79
        assert ad["z_score"] == 24.91
        assert ad["affected_entity"] == "time_bucket:2025-01-15-12"
        assert ad["evidence"] == ["ev1", "ev2"]

    def test_evidence_truncated_to_five(self) -> None:
        now = _now()
        anomaly = Anomaly(
            anomaly_type="volume",
            severity="low",
            title="t",
            description="d",
            metric_name="m",
            current_value=0.0,
            baseline_mean=0.0,
            baseline_std=0.0,
            z_score=0.0,
            detected_at=now,
            window_hours=6,
            affected_entity="e",
            evidence=[f"ev{i}" for i in range(10)],
            recommended_action="r",
        )
        report = AnomalyReport(
            detected_at=now,
            total_anomalies=1,
            by_type={},
            by_severity={},
            anomalies=[anomaly],
            baseline_window_hours=72,
            detection_window_hours=6,
        )
        d = report.to_dict()
        assert len(d["anomalies"][0]["evidence"]) == 5

    def test_empty_report(self) -> None:
        now = _now()
        report = AnomalyReport(
            detected_at=now,
            total_anomalies=0,
            by_type={},
            by_severity={},
            anomalies=[],
            baseline_window_hours=72,
            detection_window_hours=6,
        )
        d = report.to_dict()
        assert d["total_anomalies"] == 0
        assert d["anomalies"] == []


# ── Full integration: run_anomaly_detection ───────────────────────────


class TestRunAnomalyDetection:
    def test_empty_observations(self) -> None:
        report = run_anomaly_detection([])
        assert isinstance(report, AnomalyReport)
        assert report.total_anomalies == 0
        assert report.anomalies == []

    def test_returns_anomaly_report(self) -> None:
        now = _now()
        obs = []
        # Create baseline data
        for h in range(8, 60):
            obs.append(_make_obs(
                claim="Normal daily report on weather",
                source="reuters",
                captured_at=now - timedelta(hours=h),
            ))
        # Create a spike
        for _ in range(40):
            obs.append(_make_obs(
                claim="massive attack killed many casualties destroyed",
                source="reuters",
                captured_at=now - timedelta(hours=1),
            ))

        report = run_anomaly_detection(obs, detection_hours=6, baseline_hours=72)
        assert isinstance(report, AnomalyReport)
        assert report.detected_at is not None
        assert report.baseline_window_hours == 72
        assert report.detection_window_hours == 6

    def test_anomalies_sorted_by_severity_then_z(self) -> None:
        now = _now()
        obs = []
        for h in range(8, 60):
            obs.append(_make_obs(
                claim="Normal daily report",
                source="reuters",
                captured_at=now - timedelta(hours=h),
            ))
        for _ in range(50):
            obs.append(_make_obs(
                claim="attack missile strike bomb killed casualties destroyed",
                source="reuters",
                captured_at=now - timedelta(hours=1),
            ))

        report = run_anomaly_detection(obs, detection_hours=6, baseline_hours=72, min_z_score=1.5)
        if len(report.anomalies) >= 2:
            severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            for i in range(len(report.anomalies) - 1):
                a = report.anomalies[i]
                b = report.anomalies[i + 1]
                assert severity_order.get(a.severity, 4) <= severity_order.get(b.severity, 4)

    def test_by_type_and_by_severity_counts(self) -> None:
        now = _now()
        obs = []
        for h in range(8, 60):
            obs.append(_make_obs(claim="daily report", source="reuters",
                                 captured_at=now - timedelta(hours=h)))
        for _ in range(50):
            obs.append(_make_obs(claim="attack", source="reuters",
                                 captured_at=now - timedelta(hours=1)))

        report = run_anomaly_detection(obs, detection_hours=6, baseline_hours=72)
        assert report.total_anomalies == len(report.anomalies)
        type_sum = sum(report.by_type.values())
        assert type_sum == report.total_anomalies
        severity_sum = sum(report.by_severity.values())
        assert severity_sum == report.total_anomalies

    def test_single_observation(self) -> None:
        obs = [_make_obs(captured_at=_now() - timedelta(hours=1))]
        report = run_anomaly_detection(obs)
        assert isinstance(report, AnomalyReport)

    def test_all_observations_in_detection_window(self) -> None:
        now = _now()
        obs = [
            _make_obs(captured_at=now - timedelta(hours=h))
            for h in range(0, 5)
        ]
        report = run_anomaly_detection(obs, detection_hours=6, baseline_hours=72)
        # No baseline => volume detector returns []
        assert isinstance(report, AnomalyReport)
