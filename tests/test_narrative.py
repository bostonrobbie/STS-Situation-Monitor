"""Tests for the narrative timeline generator."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sts_monitor.narrative import (
    NarrativeTimeline,
    TimelineEvent,
    _detect_phase,
    _extract_location_from_entities,
    _generate_summary,
    _group_by_time_window,
    _make_headline,
    _parse_time,
    _source_family,
    build_narrative_timeline,
)

pytestmark = pytest.mark.unit


# ── Helpers ────────────────────────────────────────────────────────────


def _obs(
    claim: str,
    source: str = "feed:reuters",
    reliability: float = 0.8,
    captured_at: str | datetime = "2024-06-01T12:00:00Z",
    obs_id: int = 1,
    investigation_id: str = "",
) -> dict:
    ts = captured_at
    if isinstance(ts, datetime):
        ts = ts.isoformat()
    return {
        "id": obs_id,
        "claim": claim,
        "source": source,
        "url": "https://example.com",
        "captured_at": ts,
        "reliability_hint": reliability,
        "investigation_id": investigation_id,
    }


def _base_time() -> datetime:
    return datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


# ── _parse_time ───────────────────────────────────────────────────────


class TestParseTime:
    def test_datetime_passthrough(self) -> None:
        dt = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        assert _parse_time(dt) == dt

    def test_naive_datetime_gets_utc(self) -> None:
        dt = datetime(2024, 6, 1, 12, 0, 0)
        result = _parse_time(dt)
        assert result.tzinfo == UTC

    def test_iso_string(self) -> None:
        result = _parse_time("2024-06-01T12:00:00+00:00")
        assert result.year == 2024
        assert result.month == 6
        assert result.tzinfo is not None

    def test_iso_string_naive(self) -> None:
        result = _parse_time("2024-06-01T12:00:00")
        assert result.tzinfo == UTC

    def test_invalid_string_returns_now(self) -> None:
        before = datetime.now(UTC)
        result = _parse_time("not-a-date")
        after = datetime.now(UTC)
        assert before <= result <= after

    def test_none_returns_now(self) -> None:
        before = datetime.now(UTC)
        result = _parse_time(None)
        after = datetime.now(UTC)
        assert before <= result <= after


# ── _source_family ────────────────────────────────────────────────────


class TestSourceFamily:
    def test_with_prefix(self) -> None:
        assert _source_family("feed:reuters/world") == "reuters"

    def test_without_prefix(self) -> None:
        assert _source_family("reuters") == "reuters"

    def test_strips_and_lowercases(self) -> None:
        assert _source_family("feed: Reuters ") == "reuters"


# ── _detect_phase ─────────────────────────────────────────────────────


class TestDetectPhase:
    def test_initial_report(self) -> None:
        assert _detect_phase("anything", is_first=True, source_count=1,
                             prev_source_count=0, intensity_trend="stable") == "initial_report"

    def test_escalation_by_keywords(self) -> None:
        text = "Major offensive launched with surge of casualties and strikes"
        phase = _detect_phase(text, is_first=False, source_count=1,
                              prev_source_count=1, intensity_trend="stable")
        assert phase == "escalation"

    def test_escalation_by_intensity_trend(self) -> None:
        phase = _detect_phase("some update", is_first=False, source_count=1,
                              prev_source_count=1, intensity_trend="increasing")
        assert phase == "escalation"

    def test_response_by_keywords(self) -> None:
        text = "United Nations security council condemned the attacks and urged ceasefire"
        phase = _detect_phase(text, is_first=False, source_count=2,
                              prev_source_count=2, intensity_trend="stable")
        assert phase == "response"

    def test_de_escalation_by_keywords(self) -> None:
        text = "Ceasefire agreement reached, troops withdraw from area"
        phase = _detect_phase(text, is_first=False, source_count=2,
                              prev_source_count=2, intensity_trend="stable")
        assert phase == "de_escalation"

    def test_de_escalation_by_intensity_trend(self) -> None:
        phase = _detect_phase("routine update", is_first=False, source_count=2,
                              prev_source_count=2, intensity_trend="decreasing")
        assert phase == "de_escalation"

    def test_corroboration(self) -> None:
        """More cumulative sources than previous window -> corroboration."""
        phase = _detect_phase("confirmed the earlier report", is_first=False,
                              source_count=3, prev_source_count=1,
                              intensity_trend="stable")
        assert phase == "corroboration"

    def test_update_fallback(self) -> None:
        phase = _detect_phase("routine check on the situation", is_first=False,
                              source_count=1, prev_source_count=1,
                              intensity_trend="stable")
        assert phase == "update"

    def test_first_always_initial_report(self) -> None:
        """Even with escalation keywords, first event is initial_report."""
        text = "Major offensive with surge of casualties"
        phase = _detect_phase(text, is_first=True, source_count=1,
                              prev_source_count=0, intensity_trend="increasing")
        assert phase == "initial_report"

    def test_de_escalation_priority_over_response(self) -> None:
        """De-escalation words checked before response words."""
        text = "Ceasefire agreement reached after UN condemned attacks and peace talks"
        phase = _detect_phase(text, is_first=False, source_count=2,
                              prev_source_count=2, intensity_trend="stable")
        assert phase == "de_escalation"


# ── _group_by_time_window ─────────────────────────────────────────────


class TestGroupByTimeWindow:
    def test_empty_input(self) -> None:
        assert _group_by_time_window([]) == []

    def test_single_observation(self) -> None:
        obs = [_obs("test claim")]
        groups = _group_by_time_window(obs, window_minutes=30)
        assert len(groups) == 1
        assert len(groups[0]) == 1

    def test_observations_within_window_grouped(self) -> None:
        base = _base_time()
        obs = [
            _obs("first", captured_at=base),
            _obs("second", captured_at=base + timedelta(minutes=10)),
            _obs("third", captured_at=base + timedelta(minutes=20)),
        ]
        groups = _group_by_time_window(obs, window_minutes=30)
        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_observations_across_windows_split(self) -> None:
        base = _base_time()
        obs = [
            _obs("first", captured_at=base),
            _obs("second", captured_at=base + timedelta(hours=2)),
        ]
        groups = _group_by_time_window(obs, window_minutes=30)
        assert len(groups) == 2

    def test_chronological_sorting(self) -> None:
        base = _base_time()
        obs = [
            _obs("later", captured_at=base + timedelta(hours=1)),
            _obs("earlier", captured_at=base),
        ]
        groups = _group_by_time_window(obs, window_minutes=30)
        # Should be sorted: earlier first
        assert groups[0][0]["claim"] == "earlier"

    def test_custom_window_size(self) -> None:
        base = _base_time()
        obs = [
            _obs("first", captured_at=base),
            _obs("second", captured_at=base + timedelta(minutes=50)),
        ]
        # 30-min window: 2 groups
        groups_30 = _group_by_time_window(obs, window_minutes=30)
        assert len(groups_30) == 2

        # 60-min window: 1 group
        groups_60 = _group_by_time_window(obs, window_minutes=60)
        assert len(groups_60) == 1

    def test_string_timestamps(self) -> None:
        obs = [
            _obs("first", captured_at="2024-06-01T12:00:00"),
            _obs("second", captured_at="2024-06-01T12:10:00"),
            _obs("third", captured_at="2024-06-01T14:00:00"),
        ]
        groups = _group_by_time_window(obs, window_minutes=30)
        assert len(groups) == 2


# ── _make_headline ────────────────────────────────────────────────────


class TestMakeHeadline:
    def test_initial_report_prefix(self) -> None:
        h = _make_headline("Explosion in Kyiv", "initial_report", 1, 1)
        assert h.startswith("[FIRST REPORT]")

    def test_escalation_prefix(self) -> None:
        h = _make_headline("Major offensive launched", "escalation", 1, 1)
        assert h.startswith("[ESCALATION]")

    def test_response_prefix(self) -> None:
        h = _make_headline("UN condemns attacks", "response", 1, 1)
        assert h.startswith("[RESPONSE]")

    def test_de_escalation_prefix(self) -> None:
        h = _make_headline("Ceasefire agreed", "de_escalation", 1, 1)
        assert h.startswith("[DE-ESCALATION]")

    def test_corroboration_prefix(self) -> None:
        h = _make_headline("Confirmed by sources", "corroboration", 1, 1)
        assert h.startswith("[CONFIRMED]")

    def test_update_prefix(self) -> None:
        h = _make_headline("Latest update", "update", 1, 1)
        assert h.startswith("[UPDATE]")

    def test_unknown_phase_defaults_to_update(self) -> None:
        h = _make_headline("Something happened", "unknown_phase", 1, 1)
        assert h.startswith("[UPDATE]")

    def test_multiple_reports_suffix(self) -> None:
        h = _make_headline("Explosion in Kyiv", "update", 5, 3)
        assert "5 reports" in h
        assert "3 sources" in h

    def test_single_report_no_suffix(self) -> None:
        h = _make_headline("Explosion in Kyiv", "update", 1, 1)
        assert "reports" not in h

    def test_long_claim_truncated(self) -> None:
        long_claim = "A" * 200 + ". Second sentence."
        h = _make_headline(long_claim, "update", 1, 1)
        # First sentence > 120 chars, so truncated to 117 + "..."
        assert len(h) < 200

    def test_first_sentence_extraction(self) -> None:
        h = _make_headline("First sentence. Second sentence.", "update", 1, 1)
        assert "Second sentence" not in h
        assert "First sentence" in h


# ── _extract_location_from_entities ───────────────────────────────────


class TestExtractLocation:
    def test_finds_first_location(self) -> None:
        from sts_monitor.entities import ExtractedEntity
        entities = [
            ExtractedEntity(text="Putin", entity_type="person", confidence=0.95),
            ExtractedEntity(text="Ukraine", entity_type="location", confidence=0.90),
        ]
        assert _extract_location_from_entities(entities) == "Ukraine"

    def test_no_location_returns_empty(self) -> None:
        from sts_monitor.entities import ExtractedEntity
        entities = [
            ExtractedEntity(text="Putin", entity_type="person", confidence=0.95),
        ]
        assert _extract_location_from_entities(entities) == ""

    def test_empty_entities_returns_empty(self) -> None:
        assert _extract_location_from_entities([]) == ""


# ── _generate_summary ─────────────────────────────────────────────────


class TestGenerateSummary:
    def test_empty_events(self) -> None:
        from collections import Counter
        summary = _generate_summary("test topic", [], Counter(), 0.0)
        assert "No timeline events" in summary
        assert "test topic" in summary

    def test_summary_contains_topic(self) -> None:
        from collections import Counter
        event = TimelineEvent(
            timestamp=_base_time(), phase="initial_report",
            headline="test", detail="test", sources=["reuters"],
            source_count=1, reliability=0.8, entities=[], location="",
            is_pivot=False, observation_ids=[1],
        )
        phases = Counter({"initial_report": 1})
        summary = _generate_summary("Ukraine conflict", [event], phases, 5.0)
        assert "Ukraine conflict" in summary

    def test_summary_contains_time_span(self) -> None:
        from collections import Counter
        event = TimelineEvent(
            timestamp=_base_time(), phase="initial_report",
            headline="test", detail="test", sources=["reuters"],
            source_count=1, reliability=0.8, entities=[], location="",
            is_pivot=False, observation_ids=[1],
        )
        phases = Counter({"initial_report": 1})
        summary = _generate_summary("test", [event], phases, 12.5)
        assert "12.5 hours" in summary

    def test_summary_includes_narrative_arc(self) -> None:
        from collections import Counter
        events = [
            TimelineEvent(
                timestamp=_base_time(), phase="initial_report",
                headline="test", detail="test", sources=["reuters"],
                source_count=1, reliability=0.8, entities=[], location="",
                is_pivot=False, observation_ids=[1],
            ),
            TimelineEvent(
                timestamp=_base_time() + timedelta(hours=1), phase="escalation",
                headline="test", detail="test", sources=["bbc"],
                source_count=1, reliability=0.8, entities=[], location="",
                is_pivot=True, observation_ids=[2],
            ),
        ]
        phases = Counter({"initial_report": 1, "escalation": 1})
        summary = _generate_summary("test", events, phases, 1.0)
        assert "Narrative arc" in summary
        assert "Initial Report" in summary
        assert "Escalation" in summary

    def test_summary_includes_last_pivot(self) -> None:
        from collections import Counter
        events = [
            TimelineEvent(
                timestamp=_base_time(), phase="initial_report",
                headline="test", detail="test", sources=["reuters"],
                source_count=1, reliability=0.8, entities=[], location="",
                is_pivot=False, observation_ids=[1],
            ),
            TimelineEvent(
                timestamp=_base_time() + timedelta(hours=1), phase="escalation",
                headline="test", detail="test", sources=["bbc"],
                source_count=1, reliability=0.8, entities=[], location="",
                is_pivot=True, observation_ids=[2],
            ),
        ]
        phases = Counter({"initial_report": 1, "escalation": 1})
        summary = _generate_summary("test", events, phases, 1.0)
        assert "Last significant shift" in summary


# ── Pivot point detection ─────────────────────────────────────────────


class TestPivotDetection:
    def test_phase_change_creates_pivot(self) -> None:
        base = _base_time()
        observations = [
            _obs("First report about Ukraine conflict", captured_at=base, obs_id=1),
            _obs(
                "Major offensive with surge of casualties and strikes reported in escalation",
                captured_at=base + timedelta(hours=2),
                source="feed:bbc",
                obs_id=2,
            ),
        ]
        timeline = build_narrative_timeline(observations, topic="test")
        # Second event should be a pivot (phase changed from initial_report)
        pivots = [e for e in timeline.events if e.is_pivot]
        assert len(pivots) >= 1

    def test_single_event_no_pivot(self) -> None:
        observations = [_obs("First report about Ukraine conflict")]
        timeline = build_narrative_timeline(observations, topic="test")
        assert timeline.pivots == 0

    def test_intensity_spike_creates_pivot(self) -> None:
        base = _base_time()
        # 1 observation in first window, then many in second (>2x)
        observations = [
            _obs("First report", captured_at=base, obs_id=1),
        ]
        # Add many observations in second window to trigger intensity spike
        for i in range(5):
            observations.append(
                _obs(
                    "Follow up report with additional details about the situation",
                    captured_at=base + timedelta(hours=2, minutes=i),
                    source=f"feed:src{i}",
                    obs_id=10 + i,
                )
            )
        timeline = build_narrative_timeline(observations, topic="test")
        pivots = [e for e in timeline.events if e.is_pivot]
        assert len(pivots) >= 1


# ── build_narrative_timeline ──────────────────────────────────────────


class TestBuildNarrativeTimeline:
    def test_empty_input(self) -> None:
        timeline = build_narrative_timeline([], topic="test", investigation_id="INV-1")
        assert timeline.total_events == 0
        assert timeline.events == []
        assert timeline.pivots == 0
        assert timeline.time_span_hours == 0
        assert timeline.phases == {}
        assert "No observations" in timeline.summary
        assert timeline.topic == "test"
        assert timeline.investigation_id == "INV-1"

    def test_single_observation(self) -> None:
        observations = [_obs("NATO deployed forces to Ukraine", obs_id=42)]
        timeline = build_narrative_timeline(observations, topic="conflict")
        assert timeline.total_events == 1
        assert timeline.events[0].phase == "initial_report"
        assert 42 in timeline.events[0].observation_ids

    def test_chronological_ordering(self) -> None:
        base = _base_time()
        observations = [
            _obs("Third event", captured_at=base + timedelta(hours=4), obs_id=3),
            _obs("First event", captured_at=base, obs_id=1),
            _obs("Second event", captured_at=base + timedelta(hours=2), obs_id=2),
        ]
        timeline = build_narrative_timeline(observations, topic="test")
        timestamps = [e.timestamp for e in timeline.events]
        assert timestamps == sorted(timestamps)

    def test_time_span_calculation(self) -> None:
        base = _base_time()
        observations = [
            _obs("First event", captured_at=base, obs_id=1),
            _obs("Last event", captured_at=base + timedelta(hours=6), obs_id=2),
        ]
        timeline = build_narrative_timeline(observations, topic="test")
        assert timeline.time_span_hours == pytest.approx(6.0, abs=0.1)

    def test_phases_counted(self) -> None:
        base = _base_time()
        observations = [
            _obs("First report about Ukraine", captured_at=base, obs_id=1),
            _obs(
                "Major offensive with surge of casualties in escalation",
                captured_at=base + timedelta(hours=2),
                source="feed:bbc",
                obs_id=2,
            ),
        ]
        timeline = build_narrative_timeline(observations, topic="test")
        assert "initial_report" in timeline.phases
        assert timeline.phases["initial_report"] >= 1

    def test_sources_tracked_per_event(self) -> None:
        base = _base_time()
        observations = [
            _obs("Report about Ukraine", captured_at=base, source="feed:reuters", obs_id=1),
            _obs("Report about Ukraine", captured_at=base + timedelta(minutes=5),
                 source="feed:bbc", obs_id=2),
        ]
        timeline = build_narrative_timeline(observations, topic="test", window_minutes=30)
        # Both obs should be grouped into one event
        assert timeline.total_events == 1
        assert timeline.events[0].source_count == 2

    def test_reliability_averaged(self) -> None:
        base = _base_time()
        observations = [
            _obs("Report A", captured_at=base, reliability=0.9, obs_id=1),
            _obs("Report B", captured_at=base + timedelta(minutes=5),
                 reliability=0.7, obs_id=2),
        ]
        timeline = build_narrative_timeline(observations, topic="test", window_minutes=30)
        assert timeline.events[0].reliability == pytest.approx(0.8, abs=0.01)

    def test_observation_ids_collected(self) -> None:
        base = _base_time()
        observations = [
            _obs("Report A", captured_at=base, obs_id=10),
            _obs("Report B", captured_at=base + timedelta(minutes=5), obs_id=20),
        ]
        timeline = build_narrative_timeline(observations, topic="test", window_minutes=30)
        assert set(timeline.events[0].observation_ids) == {10, 20}

    def test_metadata_populated(self) -> None:
        observations = [_obs("NATO deployed to Ukraine", obs_id=1)]
        timeline = build_narrative_timeline(observations, topic="test")
        meta = timeline.events[0].metadata
        assert "observations_in_window" in meta
        assert "intensity_trend" in meta
        assert "cumulative_sources" in meta

    def test_generated_at_is_recent(self) -> None:
        before = datetime.now(UTC)
        timeline = build_narrative_timeline([], topic="test")
        after = datetime.now(UTC)
        assert before <= timeline.generated_at <= after

    def test_string_timestamps_handled(self) -> None:
        observations = [
            _obs("First event", captured_at="2024-06-01T10:00:00", obs_id=1),
            _obs("Second event", captured_at="2024-06-01T14:00:00", obs_id=2),
        ]
        timeline = build_narrative_timeline(observations, topic="test")
        assert timeline.total_events == 2
        assert timeline.events[0].timestamp < timeline.events[1].timestamp

    def test_datetime_timestamps_handled(self) -> None:
        base = _base_time()
        observations = [
            _obs("First event", captured_at=base, obs_id=1),
            _obs("Second event", captured_at=base + timedelta(hours=2), obs_id=2),
        ]
        timeline = build_narrative_timeline(observations, topic="test")
        assert timeline.total_events == 2

    def test_full_narrative_arc(self) -> None:
        """Simulate a full narrative arc: initial -> corroboration -> escalation -> response -> de-escalation."""
        base = _base_time()
        observations = [
            # Initial report
            _obs("Fighting reported near Ukraine border", captured_at=base, obs_id=1,
                 source="feed:reuters"),
            # Corroboration (new sources)
            _obs("Fighting confirmed near Ukraine border area",
                 captured_at=base + timedelta(hours=1), obs_id=2,
                 source="feed:bbc"),
            _obs("Multiple reports of fighting near Ukraine",
                 captured_at=base + timedelta(hours=1, minutes=5), obs_id=3,
                 source="feed:ap"),
            # Escalation
            _obs("Major offensive with surge of casualties and mass strikes reported",
                 captured_at=base + timedelta(hours=3), obs_id=4,
                 source="feed:reuters"),
            # Response
            _obs("United Nations security council condemned attacks and urged investigation",
                 captured_at=base + timedelta(hours=5), obs_id=5,
                 source="feed:bbc"),
            # De-escalation
            _obs("Ceasefire agreement reached, troops begin to withdraw from the area",
                 captured_at=base + timedelta(hours=8), obs_id=6,
                 source="feed:ap"),
        ]
        timeline = build_narrative_timeline(
            observations, topic="Ukraine conflict", window_minutes=30,
        )
        assert timeline.total_events >= 4
        phases = set(timeline.phases.keys())
        assert "initial_report" in phases

    def test_topic_and_investigation_id_stored(self) -> None:
        observations = [_obs("test claim", obs_id=1)]
        timeline = build_narrative_timeline(
            observations, topic="test topic", investigation_id="INV-42",
        )
        assert timeline.topic == "test topic"
        assert timeline.investigation_id == "INV-42"

    def test_window_minutes_parameter(self) -> None:
        base = _base_time()
        observations = [
            _obs("First", captured_at=base, obs_id=1),
            _obs("Second", captured_at=base + timedelta(minutes=20), obs_id=2),
        ]
        # 10-minute window: 2 events
        t1 = build_narrative_timeline(observations, topic="test", window_minutes=10)
        # 30-minute window: 1 event
        t2 = build_narrative_timeline(observations, topic="test", window_minutes=30)
        assert t1.total_events == 2
        assert t2.total_events == 1


# ── NarrativeTimeline.to_dict ─────────────────────────────────────────


class TestNarrativeTimelineToDict:
    def test_empty_timeline_serialization(self) -> None:
        timeline = build_narrative_timeline([], topic="test")
        d = timeline.to_dict()
        assert d["topic"] == "test"
        assert d["total_events"] == 0
        assert d["events"] == []
        assert d["pivots"] == 0

    def test_serialization_keys(self) -> None:
        observations = [_obs("NATO deployed to Ukraine", obs_id=1)]
        timeline = build_narrative_timeline(observations, topic="test")
        d = timeline.to_dict()
        expected_top_keys = {
            "topic", "investigation_id", "generated_at", "time_span_hours",
            "total_events", "phases", "pivots", "summary", "events",
        }
        assert expected_top_keys <= set(d.keys())

    def test_event_serialization_keys(self) -> None:
        observations = [_obs("NATO deployed to Ukraine", obs_id=1)]
        timeline = build_narrative_timeline(observations, topic="test")
        d = timeline.to_dict()
        event = d["events"][0]
        expected_keys = {
            "timestamp", "phase", "headline", "detail", "sources",
            "source_count", "reliability", "entities", "location",
            "is_pivot", "observation_ids",
        }
        assert expected_keys <= set(event.keys())

    def test_generated_at_is_iso_string(self) -> None:
        timeline = build_narrative_timeline([], topic="test")
        d = timeline.to_dict()
        # Should be parseable ISO format
        datetime.fromisoformat(d["generated_at"])

    def test_reliability_rounded(self) -> None:
        observations = [_obs("NATO deployed to Ukraine", obs_id=1, reliability=0.77777)]
        timeline = build_narrative_timeline(observations, topic="test")
        d = timeline.to_dict()
        rel = d["events"][0]["reliability"]
        # Should be rounded to 3 decimal places
        assert rel == round(0.77777, 3)
