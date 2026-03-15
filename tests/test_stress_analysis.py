"""Stress tests for STS-Situation-Monitor core modules.

Exercises edge cases, boundary conditions, and unusual inputs across:
- pipeline.py (SignalPipeline)
- corroboration.py (claim clustering, scoring)
- anomaly_detector.py (z-score anomaly detection)
- narrative.py (narrative timeline building)
- story_discovery.py (story clustering)
"""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta, timezone

import pytest

from sts_monitor.anomaly_detector import (
    Anomaly,
    _escalation_score,
    _extract_topic_words,
    _mean,
    _std,
    _z_score,
    detect_sentiment_shifts,
    detect_silence_anomalies,
    detect_source_behavior_shifts,
    detect_volume_anomalies,
    run_anomaly_detection,
)
from sts_monitor.corroboration import (
    analyze_corroboration,
    claim_similarity,
    classify_source_tier,
    cluster_claims,
    normalize_claim,
    score_cluster,
    _extract_domain,
)
from sts_monitor.narrative import (
    NarrativeTimeline,
    build_narrative_timeline,
    _group_by_time_window,
    _parse_time as narrative_parse_time,
)
from sts_monitor.pipeline import Observation, PipelineResult, SignalPipeline
from sts_monitor.story_discovery import (
    BurstSignal,
    ObservationSnapshot,
    detect_bursts,
    discover_topics_from_bursts,
    discover_topics_from_convergence,
    discover_topics_from_entity_spikes,
    run_discovery,
    _extract_terms,
)

pytestmark = pytest.mark.unit


# ════════════════════════════════════════════════════════════════════════
# Helper factories
# ════════════════════════════════════════════════════════════════════════

def _obs(claim="test claim", source="rss:test.com", url="https://test.com/1",
         captured_at=None, reliability=0.5):
    if captured_at is None:
        captured_at = datetime.now(UTC)
    return Observation(
        source=source, claim=claim, url=url,
        captured_at=captured_at, reliability_hint=reliability,
    )


def _obs_dict(claim="test claim", source="rss:test.com", url="https://test.com/1",
              captured_at=None, reliability=0.5, obs_id=1):
    if captured_at is None:
        captured_at = datetime.now(UTC)
    return {
        "id": obs_id,
        "claim": claim,
        "source": source,
        "url": url,
        "captured_at": captured_at,
        "reliability_hint": reliability,
    }


def _snapshot(claim="test claim", source="rss:test.com", url="https://test.com/1",
              captured_at=None, reliability=0.5):
    if captured_at is None:
        captured_at = datetime.now(UTC)
    return ObservationSnapshot(
        claim=claim, source=source, captured_at=captured_at,
        url=url, reliability_hint=reliability,
    )


# ════════════════════════════════════════════════════════════════════════
# 1. PIPELINE TESTS
# ════════════════════════════════════════════════════════════════════════

class TestPipelineEmpty:
    """Test pipeline with empty and minimal inputs."""

    def test_empty_observations(self):
        pipe = SignalPipeline()
        result = pipe.run([], "empty topic")
        assert result.accepted == []
        assert result.dropped == []
        assert result.deduplicated == []
        assert result.confidence == 0.0

    def test_empty_claim_string(self):
        pipe = SignalPipeline()
        obs = _obs(claim="")
        result = pipe.run([obs], "test")
        assert len(result.deduplicated) == 1

    def test_empty_source_string(self):
        pipe = SignalPipeline()
        obs = _obs(source="")
        result = pipe.run([obs], "test")
        assert len(result.deduplicated) == 1

    def test_empty_url_string(self):
        pipe = SignalPipeline()
        obs = _obs(url="")
        result = pipe.run([obs], "test")
        assert len(result.deduplicated) == 1

    def test_empty_topic_string(self):
        pipe = SignalPipeline()
        obs = _obs()
        result = pipe.run([obs], "")
        assert "Topic ''" in result.summary


class TestPipelineHugeInputs:
    """Test pipeline with large volumes of data."""

    def test_1000_observations(self):
        pipe = SignalPipeline()
        obs_list = [
            _obs(claim=f"claim number {i}", url=f"https://test.com/{i}",
                 source=f"source{i % 10}:domain{i % 5}.com")
            for i in range(1000)
        ]
        result = pipe.run(obs_list, "large test")
        assert len(result.deduplicated) == 1000
        assert result.confidence > 0.0

    def test_very_long_claim_string(self):
        pipe = SignalPipeline()
        long_claim = "word " * 10000  # 50,000 chars
        obs = _obs(claim=long_claim)
        result = pipe.run([obs], "test")
        assert len(result.deduplicated) == 1

    def test_very_long_url(self):
        pipe = SignalPipeline()
        long_url = "https://test.com/" + "a" * 10000
        obs = _obs(url=long_url)
        result = pipe.run([obs], "test")
        assert len(result.deduplicated) == 1


class TestPipelineDuplicates:
    """Test pipeline deduplication with identical observations."""

    def test_all_identical_observations(self):
        pipe = SignalPipeline()
        obs_list = [_obs() for _ in range(100)]
        result = pipe.run(obs_list, "test")
        # All identical URL + claim should dedup to 1
        assert len(result.deduplicated) == 1

    def test_same_claim_different_urls(self):
        pipe = SignalPipeline()
        obs_list = [
            _obs(claim="same claim", url=f"https://test.com/{i}")
            for i in range(50)
        ]
        result = pipe.run(obs_list, "test")
        assert len(result.deduplicated) == 50

    def test_dedup_keeps_highest_reliability(self):
        pipe = SignalPipeline()
        obs_list = [
            _obs(reliability=0.1),
            _obs(reliability=0.9),
            _obs(reliability=0.5),
        ]
        result = pipe.run(obs_list, "test")
        assert len(result.deduplicated) == 1
        assert result.deduplicated[0].reliability_hint == 0.9


class TestPipelineUnicode:
    """Test pipeline with unicode and special characters."""

    def test_unicode_claim(self):
        pipe = SignalPipeline()
        obs = _obs(claim="地震が発生しました。マグニチュード7.2")
        result = pipe.run([obs], "test")
        assert len(result.deduplicated) == 1

    def test_emoji_claim(self):
        pipe = SignalPipeline()
        obs = _obs(claim="🚨 BREAKING: Major explosion reported 💥🔥")
        result = pipe.run([obs], "test")
        assert len(result.deduplicated) == 1

    def test_rtl_arabic_claim(self):
        pipe = SignalPipeline()
        obs = _obs(claim="تقارير عن انفجار كبير في العاصمة")
        result = pipe.run([obs], "test")
        assert len(result.deduplicated) == 1

    def test_mixed_unicode_normalization(self):
        pipe = SignalPipeline()
        # Claims that differ only by unicode normalization
        obs1 = _obs(claim="café attack", url="https://test.com/1")
        obs2 = _obs(claim="café attack", url="https://test.com/2")  # same but different URL
        result = pipe.run([obs1, obs2], "test")
        assert len(result.deduplicated) == 2


class TestPipelineExtremeDatetimes:
    """Test pipeline with extreme datetime values."""

    def test_epoch_datetime(self):
        pipe = SignalPipeline()
        obs = _obs(captured_at=datetime(1970, 1, 1, tzinfo=UTC))
        result = pipe.run([obs], "test")
        assert len(result.accepted) == 1

    def test_far_future_datetime(self):
        pipe = SignalPipeline()
        obs = _obs(captured_at=datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC))
        result = pipe.run([obs], "test")
        assert len(result.accepted) == 1

    def test_naive_datetime(self):
        """Naive datetimes should be handled by _as_utc."""
        pipe = SignalPipeline()
        obs = _obs(captured_at=datetime(2024, 6, 15, 12, 0, 0))  # no timezone
        result = pipe.run([obs], "test")
        assert len(result.accepted) == 1

    def test_non_utc_timezone(self):
        pipe = SignalPipeline()
        tz_tokyo = timezone(timedelta(hours=9))
        obs = _obs(captured_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz_tokyo))
        result = pipe.run([obs], "test")
        assert len(result.accepted) == 1


class TestPipelineExtremeReliability:
    """Test pipeline with extreme reliability_hint values."""

    def test_zero_reliability(self):
        pipe = SignalPipeline()
        obs = _obs(reliability=0.0)
        result = pipe.run([obs], "test")
        assert len(result.dropped) == 1
        assert len(result.accepted) == 0

    def test_one_reliability(self):
        pipe = SignalPipeline()
        obs = _obs(reliability=1.0)
        result = pipe.run([obs], "test")
        assert len(result.accepted) == 1

    def test_negative_reliability(self):
        """Negative reliability should be clamped to 0.0."""
        pipe = SignalPipeline()
        obs = _obs(reliability=-1.0)
        result = pipe.run([obs], "test")
        assert len(result.dropped) == 1

    def test_over_one_reliability(self):
        """Reliability > 1.0 should be clamped to 1.0."""
        pipe = SignalPipeline()
        obs = _obs(reliability=999.0)
        result = pipe.run([obs], "test")
        assert len(result.accepted) == 1
        assert result.accepted[0].reliability_hint == 1.0

    def test_nan_reliability(self):
        """NaN reliability is now correctly treated as 0.0 (minimum confidence).

        Previously, Python's min(1.0, NaN) returned 1.0, silently promoting
        NaN-reliability observations to maximum confidence. Now fixed to default
        NaN to 0.0.
        """
        pipe = SignalPipeline()
        obs = _obs(reliability=float('nan'))
        result = pipe.run([obs], "test")
        # NaN reliability is treated as 0.0 and dropped (below default min_reliability)
        assert len(result.dropped) == 1
        assert len(result.accepted) == 0

    def test_inf_reliability(self):
        """Infinity reliability should be clamped to 1.0."""
        pipe = SignalPipeline()
        obs = _obs(reliability=float('inf'))
        result = pipe.run([obs], "test")
        assert len(result.accepted) == 1
        assert result.accepted[0].reliability_hint == 1.0


class TestPipelineAdversarial:
    """Test pipeline with adversarial inputs."""

    def test_xss_in_claim(self):
        pipe = SignalPipeline()
        obs = _obs(claim='<script>alert("xss")</script>')
        result = pipe.run([obs], "test")
        assert len(result.deduplicated) == 1

    def test_sql_injection_in_claim(self):
        pipe = SignalPipeline()
        obs = _obs(claim="'; DROP TABLE observations; --")
        result = pipe.run([obs], "test")
        assert len(result.deduplicated) == 1

    def test_null_bytes_in_claim(self):
        pipe = SignalPipeline()
        obs = _obs(claim="claim with \x00 null bytes \x00")
        result = pipe.run([obs], "test")
        assert len(result.deduplicated) == 1

    def test_contradiction_marker_injection(self):
        """Claim that contains all contradiction markers."""
        pipe = SignalPipeline()
        obs = _obs(claim="false hoax debunked not true fabricated fake denied refuted")
        result = pipe.run([obs], "test")
        assert pipe._is_contradiction(obs.claim)

    def test_whitespace_only_claim(self):
        pipe = SignalPipeline()
        obs = _obs(claim="   \t\n   ")
        result = pipe.run([obs], "test")
        assert len(result.deduplicated) == 1


# ════════════════════════════════════════════════════════════════════════
# 2. CORROBORATION TESTS
# ════════════════════════════════════════════════════════════════════════

class TestCorroborationEmpty:
    """Test corroboration with empty inputs."""

    def test_empty_observations(self):
        result = analyze_corroboration([])
        assert result.total_claims == 0
        assert result.scores == []

    def test_empty_claim_in_observation(self):
        obs = _obs_dict(claim="")
        result = analyze_corroboration([obs])
        # Empty claim after normalization should be skipped by cluster_claims
        assert result.total_claims == 0

    def test_cluster_claims_empty_list(self):
        assert cluster_claims([]) == []

    def test_normalize_claim_empty(self):
        assert normalize_claim("") == ""

    def test_claim_similarity_empty_strings(self):
        assert claim_similarity("", "") == 0.0
        assert claim_similarity("hello world", "") == 0.0
        assert claim_similarity("", "hello world") == 0.0


class TestCorroborationHuge:
    """Test corroboration with large inputs."""

    def test_1000_observations_different_claims(self):
        obs_list = [
            _obs_dict(claim=f"unique claim number {i}", source=f"rss:source{i}.com",
                       url=f"https://source{i}.com/article", obs_id=i)
            for i in range(1000)
        ]
        result = analyze_corroboration(obs_list)
        assert result.total_claims > 0

    def test_very_long_claim_text(self):
        long_claim = "earthquake damage " * 5000
        obs = _obs_dict(claim=long_claim)
        result = analyze_corroboration([obs])
        assert result.total_claims == 1

    def test_score_cluster_single_obs(self):
        cluster = [_obs_dict()]
        score = score_cluster(cluster)
        assert score.score >= 0.0
        assert score.verdict == "single_source"


class TestCorroborationDuplicates:
    """Test corroboration with duplicate inputs."""

    def test_all_identical_observations(self):
        obs_list = [_obs_dict() for _ in range(100)]
        result = analyze_corroboration(obs_list)
        # All identical claims should cluster together
        assert result.total_claims == 1

    def test_identical_claims_different_sources(self):
        obs_list = [
            _obs_dict(claim="earthquake in Turkey", source=f"rss:source{i}.com",
                       url=f"https://source{i}.com/a", obs_id=i)
            for i in range(5)
        ]
        result = analyze_corroboration(obs_list)
        assert result.total_claims >= 1
        if result.scores:
            assert result.scores[0].independent_sources >= 2


class TestCorroborationUnicode:
    """Test corroboration with unicode characters."""

    def test_normalize_claim_unicode(self):
        result = normalize_claim("地震が発生しました BREAKING")
        assert "breaking" not in result  # BREAKING is a noise word

    def test_claim_similarity_unicode(self):
        sim = claim_similarity(
            "地震 earthquake in 東京 Tokyo",
            "地震 earthquake in 東京 Tokyo"
        )
        assert sim == 1.0

    def test_extract_domain_unicode_url(self):
        domain = _extract_domain("https://日本語.jp/path")
        assert domain == "日本語.jp"


class TestCorroborationEdgeCases:
    """Edge cases in corroboration scoring."""

    def test_score_cluster_with_string_datetime(self):
        """captured_at as ISO string should be parsed."""
        obs = _obs_dict()
        obs["captured_at"] = "2024-06-15T12:00:00+00:00"
        score = score_cluster([obs])
        assert score.score >= 0.0

    def test_score_cluster_with_invalid_datetime_string(self):
        """Invalid datetime string should fallback to now()."""
        obs = _obs_dict()
        obs["captured_at"] = "not-a-date"
        score = score_cluster([obs])
        assert score.score >= 0.0

    def test_classify_source_tier_empty_inputs(self):
        tier = classify_source_tier("", "", "")
        assert tier == "unknown"

    def test_classify_source_tier_authoritative(self):
        tier = classify_source_tier("usgs", "", "usgs")
        assert tier == "tier1"

    def test_claim_similarity_identical(self):
        sim = claim_similarity("earthquake hits Turkey", "earthquake hits Turkey")
        assert sim == 1.0

    def test_claim_similarity_completely_different(self):
        sim = claim_similarity("earthquake in Japan", "soccer match results London")
        assert sim < 0.3

    def test_score_cluster_all_noise_words_claim(self):
        """Claim with only noise words after normalization."""
        obs = _obs_dict(claim="breaking update just in confirmed reports say")
        result = analyze_corroboration([obs])
        # After removing noise words, normalized text may be empty
        # This should not crash


class TestCorroborationAdversarial:
    """Adversarial inputs for corroboration."""

    def test_regex_bomb_claim(self):
        """Claims designed to cause regex backtracking."""
        evil_claim = "a" * 1000 + "!" * 1000
        result = normalize_claim(evil_claim)
        assert isinstance(result, str)

    def test_xss_in_url(self):
        obs = _obs_dict(url='javascript:alert("xss")')
        score = score_cluster([obs])
        assert score.score >= 0.0


# ════════════════════════════════════════════════════════════════════════
# 3. ANOMALY DETECTOR TESTS
# ════════════════════════════════════════════════════════════════════════

class TestAnomalyEmpty:
    """Test anomaly detection with empty inputs."""

    def test_detect_volume_empty(self):
        result = detect_volume_anomalies([])
        assert result == []

    def test_detect_source_shift_empty(self):
        result = detect_source_behavior_shifts([])
        assert result == []

    def test_detect_sentiment_empty(self):
        result = detect_sentiment_shifts([])
        assert result == []

    def test_detect_silence_empty(self):
        result = detect_silence_anomalies([])
        assert result == []

    def test_run_anomaly_detection_empty(self):
        report = run_anomaly_detection([])
        assert report.total_anomalies == 0


class TestAnomalyStatHelpers:
    """Test statistical helper functions."""

    def test_mean_empty(self):
        assert _mean([]) == 0.0

    def test_mean_single(self):
        assert _mean([5.0]) == 5.0

    def test_std_empty(self):
        assert _std([]) == 0.0

    def test_std_single_value(self):
        assert _std([5.0]) == 0.0

    def test_std_identical_values(self):
        assert _std([3.0, 3.0, 3.0, 3.0]) == 0.0

    def test_z_score_zero_std(self):
        """When std=0 and value equals mean, z-score should be 0."""
        assert _z_score(5.0, 5.0, 0.0) == 0.0

    def test_z_score_zero_std_above(self):
        """When std=0 and value > mean, z-score should be 10.0 (capped)."""
        assert _z_score(10.0, 5.0, 0.0) == 10.0

    def test_z_score_zero_std_below(self):
        """When std=0 and value < mean, z-score should be -10.0 (capped)."""
        assert _z_score(1.0, 5.0, 0.0) == -10.0

    def test_z_score_normal(self):
        z = _z_score(10.0, 5.0, 2.5)
        assert z == 2.0

    def test_escalation_score_empty(self):
        assert _escalation_score("") == 0.0

    def test_escalation_score_neutral(self):
        assert _escalation_score("the weather is nice today") == 0.0

    def test_escalation_score_escalatory(self):
        score = _escalation_score("attack killed casualties destroyed")
        assert score > 0.0

    def test_escalation_score_deescalatory(self):
        score = _escalation_score("peace ceasefire truce negotiate agreement")
        assert score < 0.0

    def test_extract_topic_words_empty(self):
        assert _extract_topic_words("") == []

    def test_extract_topic_words_only_stopwords(self):
        assert _extract_topic_words("the and or but") == []


class TestAnomalyHuge:
    """Test anomaly detection with large inputs."""

    def test_volume_anomaly_1000_obs(self):
        """1000 observations spread over baseline and detection windows."""
        now = datetime.now(UTC)
        obs_list = []
        # 900 in baseline (spread over 72 hours)
        for i in range(900):
            t = now - timedelta(hours=72 - (i * 72 / 900))
            obs_list.append(_obs_dict(
                claim=f"baseline claim {i}", captured_at=t,
                source="rss:test.com", obs_id=i,
            ))
        # 100 in detection window (last 6 hours) - should be a spike
        for i in range(100):
            t = now - timedelta(hours=5 - (i * 5 / 100))
            obs_list.append(_obs_dict(
                claim=f"spike claim {i}", captured_at=t,
                source="rss:test.com", obs_id=900 + i,
            ))
        result = detect_volume_anomalies(obs_list)
        # Should detect volume spike
        assert isinstance(result, list)


class TestAnomalyExtremeDatetimes:
    """Test anomaly detection with extreme datetime values."""

    def test_epoch_datetime_obs(self):
        obs = _obs_dict(captured_at=datetime(1970, 1, 1, tzinfo=UTC))
        result = detect_volume_anomalies([obs])
        assert isinstance(result, list)

    def test_far_future_datetime_obs(self):
        obs = _obs_dict(captured_at=datetime(2099, 12, 31, tzinfo=UTC))
        result = detect_volume_anomalies([obs])
        assert isinstance(result, list)

    def test_string_datetime_parsing(self):
        obs = _obs_dict()
        obs["captured_at"] = "2024-06-15T12:00:00+00:00"
        result = detect_volume_anomalies([obs])
        assert isinstance(result, list)

    def test_invalid_datetime_string(self):
        obs = _obs_dict()
        obs["captured_at"] = "not-a-date"
        # Should fallback to now() via _parse_time
        result = detect_volume_anomalies([obs])
        assert isinstance(result, list)

    def test_none_datetime(self):
        obs = _obs_dict()
        obs["captured_at"] = None
        result = detect_volume_anomalies([obs])
        assert isinstance(result, list)


class TestAnomalyAdversarial:
    """Adversarial inputs for anomaly detection."""

    def test_empty_claim_sentiment(self):
        now = datetime.now(UTC)
        obs_list = [
            _obs_dict(claim="", captured_at=now - timedelta(hours=i))
            for i in range(20)
        ]
        result = detect_sentiment_shifts(obs_list)
        assert isinstance(result, list)

    def test_all_escalation_words(self):
        now = datetime.now(UTC)
        baseline = [
            _obs_dict(claim="peaceful calm day",
                       captured_at=now - timedelta(hours=24 + i))
            for i in range(20)
        ]
        detection = [
            _obs_dict(claim="attack strike bomb missile assault killed dead casualties",
                       captured_at=now - timedelta(hours=i % 6))
            for i in range(20)
        ]
        result = detect_sentiment_shifts(baseline + detection)
        assert isinstance(result, list)


class TestAnomalyRunIntegration:
    """BUG HUNT: Test run_anomaly_detection for integration issues."""

    def test_run_anomaly_detection_with_expected_sources(self):
        """Test that expected_sources parameter is correctly passed through."""
        now = datetime.now(UTC)
        obs_list = [
            _obs_dict(claim=f"claim {i}", source="rss:test.com",
                       captured_at=now - timedelta(hours=i), obs_id=i)
            for i in range(100)
        ]
        # This should not crash even with expected_sources
        report = run_anomaly_detection(
            obs_list, expected_sources=["rss"]
        )
        assert report.total_anomalies >= 0

    def test_anomaly_report_to_dict(self):
        report = run_anomaly_detection([])
        d = report.to_dict()
        assert "detected_at" in d
        assert "total_anomalies" in d
        assert d["total_anomalies"] == 0


# ════════════════════════════════════════════════════════════════════════
# 4. NARRATIVE TIMELINE TESTS
# ════════════════════════════════════════════════════════════════════════

class TestNarrativeEmpty:
    """Test narrative timeline with empty inputs."""

    def test_empty_observations(self):
        tl = build_narrative_timeline([], topic="test")
        assert tl.total_events == 0
        assert tl.events == []
        assert tl.time_span_hours == 0

    def test_empty_topic(self):
        tl = build_narrative_timeline([], topic="")
        assert "No observations available" in tl.summary

    def test_empty_claim_in_observation(self):
        obs = _obs_dict(claim="")
        tl = build_narrative_timeline([obs], topic="test")
        assert tl.total_events == 1


class TestNarrativeHuge:
    """Test narrative timeline with large inputs."""

    def test_1000_observations(self):
        """Fixed: _group_by_time_window now compares against group start time.

        1000 observations 5 minutes apart (83+ hours total) should produce
        many timeline events, not collapse into a single one.
        """
        now = datetime.now(UTC)
        obs_list = [
            _obs_dict(
                claim=f"event {i} occurred in the region",
                source=f"rss:source{i % 10}.com",
                captured_at=now - timedelta(minutes=i * 5),
                obs_id=i,
            )
            for i in range(1000)
        ]
        tl = build_narrative_timeline(obs_list, topic="large event")
        # Should produce many events across 83+ hours (not collapse into 1)
        assert tl.total_events > 10
        assert tl.time_span_hours > 50

    def test_very_long_claim(self):
        long_claim = "major earthquake " * 2000
        obs = _obs_dict(claim=long_claim)
        tl = build_narrative_timeline([obs], topic="test")
        assert tl.total_events == 1
        # Detail should be truncated to 500 chars
        assert len(tl.events[0].detail) <= 500


class TestNarrativeDuplicates:
    """Test narrative with duplicate inputs."""

    def test_all_identical_timestamps(self):
        """All observations at the exact same time should group together."""
        now = datetime.now(UTC)
        obs_list = [
            _obs_dict(claim=f"claim {i}", captured_at=now, obs_id=i)
            for i in range(50)
        ]
        tl = build_narrative_timeline(obs_list, topic="test")
        # All should be in one time window
        assert tl.total_events == 1


class TestNarrativeExtremeDatetimes:
    """Test narrative with extreme datetime values."""

    def test_epoch_and_future_mix(self):
        obs_list = [
            _obs_dict(claim="old event", captured_at=datetime(1970, 1, 1, tzinfo=UTC), obs_id=1),
            _obs_dict(claim="future event", captured_at=datetime(2099, 12, 31, tzinfo=UTC), obs_id=2),
        ]
        tl = build_narrative_timeline(obs_list, topic="time span test")
        assert tl.total_events == 2
        assert tl.time_span_hours > 0

    def test_naive_datetime_handling(self):
        obs = _obs_dict(captured_at=datetime(2024, 6, 15, 12, 0, 0))  # no tz
        tl = build_narrative_timeline([obs], topic="test")
        assert tl.total_events == 1

    def test_string_datetime_handling(self):
        obs = _obs_dict()
        obs["captured_at"] = "2024-06-15T12:00:00"
        tl = build_narrative_timeline([obs], topic="test")
        assert tl.total_events == 1

    def test_invalid_datetime_fallback(self):
        obs = _obs_dict()
        obs["captured_at"] = 12345  # integer, not datetime or string
        tl = build_narrative_timeline([obs], topic="test")
        assert tl.total_events == 1


class TestNarrativeUnicode:
    """Test narrative with unicode characters."""

    def test_unicode_claims(self):
        now = datetime.now(UTC)
        obs_list = [
            _obs_dict(claim="地震が東京で発生", captured_at=now, obs_id=1),
            _obs_dict(claim="Землетрясение в Токио", captured_at=now + timedelta(hours=1), obs_id=2),
            _obs_dict(claim="زلزال في طوكيو", captured_at=now + timedelta(hours=2), obs_id=3),
        ]
        tl = build_narrative_timeline(obs_list, topic="earthquake")
        assert tl.total_events >= 1

    def test_emoji_in_claims(self):
        obs = _obs_dict(claim="🚨 BREAKING: Major event 🌍")
        tl = build_narrative_timeline([obs], topic="test")
        assert tl.total_events == 1


class TestNarrativeAdversarial:
    """Adversarial inputs for narrative."""

    def test_xss_in_claim(self):
        obs = _obs_dict(claim='<script>alert("xss")</script> attack on city')
        tl = build_narrative_timeline([obs], topic="test")
        assert tl.total_events == 1

    def test_to_dict_serialization(self):
        now = datetime.now(UTC)
        obs = _obs_dict(claim="test claim", captured_at=now)
        tl = build_narrative_timeline([obs], topic="test")
        d = tl.to_dict()
        assert "events" in d
        assert len(d["events"]) == 1
        assert "timestamp" in d["events"][0]


class TestNarrativeGroupByWindow:
    """Test the time window grouping function."""

    def test_group_empty(self):
        assert _group_by_time_window([]) == []

    def test_group_single_obs(self):
        obs = _obs_dict(captured_at=datetime.now(UTC))
        groups = _group_by_time_window([obs])
        assert len(groups) == 1
        assert len(groups[0]) == 1

    def test_group_tight_cluster(self):
        now = datetime.now(UTC)
        obs_list = [
            _obs_dict(captured_at=now + timedelta(minutes=i), obs_id=i)
            for i in range(10)
        ]
        groups = _group_by_time_window(obs_list, window_minutes=30)
        assert len(groups) == 1

    def test_group_spread_observations(self):
        now = datetime.now(UTC)
        obs_list = [
            _obs_dict(captured_at=now + timedelta(hours=i), obs_id=i)
            for i in range(5)
        ]
        groups = _group_by_time_window(obs_list, window_minutes=30)
        assert len(groups) == 5


# ════════════════════════════════════════════════════════════════════════
# 5. STORY DISCOVERY TESTS
# ════════════════════════════════════════════════════════════════════════

class TestStoryDiscoveryEmpty:
    """Test story discovery with empty inputs."""

    def test_detect_bursts_empty(self):
        result = detect_bursts([])
        assert result == []

    def test_discover_topics_from_bursts_empty(self):
        result = discover_topics_from_bursts([], [])
        assert result == []

    def test_discover_topics_from_convergence_empty(self):
        result = discover_topics_from_convergence([])
        assert result == []

    def test_discover_topics_from_entity_spikes_empty(self):
        result = discover_topics_from_entity_spikes([])
        assert result == []

    def test_run_discovery_empty(self):
        result = run_discovery([])
        assert result == []

    def test_extract_terms_empty(self):
        assert _extract_terms("") == []


class TestStoryDiscoveryHuge:
    """Test story discovery with large inputs."""

    def test_detect_bursts_1000_snapshots(self):
        now = datetime.now(UTC)
        snapshots = [
            _snapshot(
                claim=f"earthquake damage report number {i}",
                captured_at=now - timedelta(hours=i % 48),
                source=f"rss:source{i % 10}.com",
            )
            for i in range(1000)
        ]
        result = detect_bursts(snapshots)
        assert isinstance(result, list)
        assert len(result) <= 20  # capped at 20

    def test_very_long_claim_burst_detection(self):
        now = datetime.now(UTC)
        long_claim = "earthquake " * 5000
        snapshot = _snapshot(claim=long_claim, captured_at=now)
        result = detect_bursts([snapshot])
        assert isinstance(result, list)


class TestStoryDiscoveryDuplicates:
    """Test story discovery with duplicate inputs."""

    def test_all_identical_snapshots(self):
        now = datetime.now(UTC)
        snapshots = [
            _snapshot(claim="earthquake in Turkey", captured_at=now)
            for _ in range(100)
        ]
        result = detect_bursts(snapshots)
        assert isinstance(result, list)


class TestStoryDiscoveryConvergence:
    """Test convergence zone topic discovery."""

    def test_low_severity_filtered(self):
        zones = [{"signal_types": ["earthquake"], "severity": "low",
                   "center_lat": 35.0, "center_lon": 139.0, "radius_km": 50}]
        result = discover_topics_from_convergence(zones)
        assert len(result) == 0

    def test_high_severity_included(self):
        zones = [{"signal_types": ["earthquake", "fire"], "severity": "high",
                   "center_lat": 35.0, "center_lon": 139.0, "radius_km": 50}]
        result = discover_topics_from_convergence(zones)
        assert len(result) == 1
        assert result[0].score == 0.8

    def test_missing_fields_in_zone(self):
        """Zone dict with missing optional fields."""
        zones = [{"signal_types": [], "severity": "critical"}]
        result = discover_topics_from_convergence(zones)
        assert len(result) == 1

    def test_connector_suggestion_earthquake(self):
        zones = [{"signal_types": ["earthquake"], "severity": "high",
                   "center_lat": 0, "center_lon": 0, "radius_km": 50}]
        result = discover_topics_from_convergence(zones)
        assert "usgs" in result[0].suggested_connectors

    def test_connector_suggestion_conflict(self):
        zones = [{"signal_types": ["military conflict"], "severity": "high",
                   "center_lat": 0, "center_lon": 0, "radius_km": 50}]
        result = discover_topics_from_convergence(zones)
        assert "acled" in result[0].suggested_connectors


class TestStoryDiscoveryUnicode:
    """Test story discovery with unicode inputs."""

    def test_unicode_claims_in_snapshots(self):
        now = datetime.now(UTC)
        snapshots = [
            _snapshot(claim="地震が発生しました", captured_at=now),
        ]
        result = detect_bursts(snapshots)
        assert isinstance(result, list)

    def test_extract_terms_unicode(self):
        """_extract_terms uses [a-zA-Z]{3,} so unicode terms are dropped."""
        result = _extract_terms("地震 earthquake 発生")
        assert "earthquake" in result
        # Japanese chars should not be in result due to regex


class TestStoryDiscoveryAdversarial:
    """Adversarial inputs for story discovery."""

    def test_run_discovery_with_convergence_zones(self):
        result = run_discovery([], convergence_zones=[
            {"signal_types": ["test"], "severity": "high",
             "center_lat": 0, "center_lon": 0, "radius_km": 50}
        ])
        assert isinstance(result, list)

    def test_burst_signal_with_zero_baseline(self):
        """Term with zero baseline count should not cause division by zero."""
        now = datetime.now(UTC)
        snapshots = [
            _snapshot(
                claim="completely novel term xylophone xylophone xylophone",
                captured_at=now - timedelta(hours=1),
            )
        ]
        result = detect_bursts(snapshots, min_current_count=1, min_burst_ratio=1.0)
        assert isinstance(result, list)


# ════════════════════════════════════════════════════════════════════════
# 6. CROSS-MODULE INTERACTION BUGS
# ════════════════════════════════════════════════════════════════════════

class TestCrossModuleBugs:
    """Tests that exercise interactions between modules."""

    def test_pipeline_then_corroboration(self):
        """Pipeline output fed into corroboration analysis."""
        pipe = SignalPipeline()
        obs_list = [
            _obs(claim="explosion in capital", source="rss:bbc.com",
                 url="https://bbc.com/1", reliability=0.8),
            _obs(claim="explosion in capital confirmed", source="rss:reuters.com",
                 url="https://reuters.com/1", reliability=0.9),
        ]
        result = pipe.run(obs_list, "explosion")
        # Convert pipeline output to corroboration input
        corr_input = [
            {"claim": o.claim, "source": o.source, "url": o.url,
             "captured_at": o.captured_at, "reliability_hint": o.reliability_hint}
            for o in result.accepted
        ]
        corr_result = analyze_corroboration(corr_input)
        assert corr_result.total_claims >= 1

    def test_nan_propagation_through_confidence(self):
        """BUG: NaN reliability can propagate into confidence calculation."""
        pipe = SignalPipeline()
        obs_list = [
            _obs(claim=f"claim {i}", url=f"https://t.com/{i}",
                 source=f"s{i}:t.com", reliability=0.7)
            for i in range(5)
        ]
        # Add a NaN reliability that gets dropped
        obs_list.append(_obs(claim="nan claim", url="https://t.com/nan",
                             source="snan:t.com", reliability=float('nan')))
        result = pipe.run(obs_list, "test")
        # NaN obs should be dropped, confidence should be a valid float
        assert not math.isnan(result.confidence)

    def test_confidence_with_only_nan_accepted(self):
        """NaN reliability is now correctly treated as 0.0.

        With min_reliability=0.0, NaN→0.0 is accepted (0.0 >= 0.0).
        """
        pipe = SignalPipeline(min_reliability=0.0)
        obs = _obs(reliability=float('nan'))
        result = pipe.run([obs], "test")
        # NaN becomes 0.0, which passes min_reliability=0.0
        assert len(result.accepted) == 1
        assert result.accepted[0].reliability_hint == 0.0
