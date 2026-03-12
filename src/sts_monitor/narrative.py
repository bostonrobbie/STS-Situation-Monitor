"""Narrative timeline generator.

Transforms raw observations into a chronological story showing how a situation
evolved: first report -> corroboration -> escalation -> response -> resolution.

This is what's missing from dot-on-a-map temporal playback. Instead of just
"here's where things happened", this shows "here's what happened, in order,
and how the story developed."

Phases detected:
  - Initial report: first observation(s) on a topic
  - Corroboration: additional independent sources confirm
  - Escalation: intensity/scale increases (more events, higher severity)
  - International response: diplomatic/military/humanitarian actors engage
  - De-escalation or resolution: activity decreasing

Output: ordered timeline events with phase labels, suitable for rendering
as a vertical timeline or story view.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sts_monitor.entities import extract_entities


@dataclass(slots=True)
class TimelineEvent:
    """A single event in the narrative timeline."""
    timestamp: datetime
    phase: str  # initial_report, corroboration, escalation, response, de_escalation, update
    headline: str
    detail: str
    sources: list[str]
    source_count: int
    reliability: float
    entities: list[str]
    location: str
    is_pivot: bool  # marks a significant change in the narrative
    observation_ids: list[int]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NarrativeTimeline:
    """Complete narrative timeline for a topic/investigation."""
    topic: str
    investigation_id: str
    generated_at: datetime
    time_span_hours: float
    total_events: int
    phases: dict[str, int]  # phase -> count
    pivots: int  # number of significant narrative shifts
    events: list[TimelineEvent]
    summary: str  # 2-3 sentence narrative summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "investigation_id": self.investigation_id,
            "generated_at": self.generated_at.isoformat(),
            "time_span_hours": round(self.time_span_hours, 1),
            "total_events": self.total_events,
            "phases": self.phases,
            "pivots": self.pivots,
            "summary": self.summary,
            "events": [
                {
                    "timestamp": e.timestamp.isoformat(),
                    "phase": e.phase,
                    "headline": e.headline,
                    "detail": e.detail,
                    "sources": e.sources,
                    "source_count": e.source_count,
                    "reliability": round(e.reliability, 3),
                    "entities": e.entities,
                    "location": e.location,
                    "is_pivot": e.is_pivot,
                    "observation_ids": e.observation_ids,
                }
                for e in self.events
            ],
        }


# ── Phase detection heuristics ─────────────────────────────────────────

_ESCALATION_WORDS: set[str] = {
    "intensif", "escalat", "spread", "increas", "surge", "mass", "major",
    "expand", "offensive", "invasion", "assault", "bombardment", "strikes",
    "casualties", "killed", "dead", "wounded", "destroyed", "collapse",
    "crisis", "emergency", "evacuat", "catastroph", "severe", "critical",
    "unprecedented", "worst",
}

_RESPONSE_WORDS: set[str] = {
    "condemn", "sanction", "deploy", "mobiliz", "aid", "humanitarian",
    "relief", "negotiate", "ceasefire", "peace", "talks", "summit",
    "resolution", "united nations", "nato", "security council",
    "statement", "response", "react", "urged", "called for",
    "investigation", "probe", "inquiry",
}

_DEESCALATION_WORDS: set[str] = {
    "ceasefire", "truce", "withdraw", "retreat", "peace",
    "agreement", "deal", "settl", "reduc", "decreas", "calm",
    "stabiliz", "normaliz", "restor", "reopen", "return",
    "de-escalat", "deescalat", "end of", "over",
}


def _detect_phase(
    text: str,
    is_first: bool,
    source_count: int,
    prev_source_count: int,
    intensity_trend: str,
) -> str:
    """Classify the narrative phase of an event."""
    text_lower = text.lower()

    if is_first:
        return "initial_report"

    # Check for de-escalation first (it's more specific)
    deesc_hits = sum(1 for w in _DEESCALATION_WORDS if w in text_lower)
    if deesc_hits >= 2 or intensity_trend == "decreasing":
        return "de_escalation"

    # Response phase
    resp_hits = sum(1 for w in _RESPONSE_WORDS if w in text_lower)
    if resp_hits >= 2:
        return "response"

    # Escalation
    esc_hits = sum(1 for w in _ESCALATION_WORDS if w in text_lower)
    if esc_hits >= 2 or intensity_trend == "increasing":
        return "escalation"

    # Corroboration: same story confirmed by more sources
    if source_count > prev_source_count and source_count >= 2:
        return "corroboration"

    return "update"


def _extract_location_from_entities(entities: list) -> str:
    """Get the most prominent location from extracted entities."""
    for ent in entities:
        if ent.entity_type == "location":
            return ent.text
    return ""


# ── Timeline construction ──────────────────────────────────────────────

def _group_by_time_window(
    observations: list[dict[str, Any]],
    window_minutes: int = 30,
) -> list[list[dict[str, Any]]]:
    """Group observations into time windows for timeline events."""
    if not observations:
        return []

    sorted_obs = sorted(observations, key=lambda o: _parse_time(o.get("captured_at")))
    groups: list[list[dict[str, Any]]] = [[sorted_obs[0]]]

    for obs in sorted_obs[1:]:
        last_time = _parse_time(groups[-1][-1].get("captured_at"))
        curr_time = _parse_time(obs.get("captured_at"))

        if (curr_time - last_time).total_seconds() <= window_minutes * 60:
            groups[-1].append(obs)
        else:
            groups.append([obs])

    return groups


def _parse_time(val: Any) -> datetime:
    """Parse a datetime from various formats."""
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=UTC)
        return val
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except (ValueError, TypeError):
            pass
    return datetime.now(UTC)


def _source_family(source: str) -> str:
    parts = source.split(":", 1)
    if len(parts) > 1:
        return parts[1].split("/")[0].strip().lower()
    return source.strip().lower()


def build_narrative_timeline(
    observations: list[dict[str, Any]],
    topic: str = "",
    investigation_id: str = "",
    window_minutes: int = 30,
) -> NarrativeTimeline:
    """Build a narrative timeline from observations.

    Each observation dict should have: id, claim, source, url, captured_at,
    reliability_hint, and optionally investigation_id.
    """
    if not observations:
        return NarrativeTimeline(
            topic=topic,
            investigation_id=investigation_id,
            generated_at=datetime.now(UTC),
            time_span_hours=0,
            total_events=0,
            phases={},
            pivots=0,
            events=[],
            summary=f"No observations available for '{topic}'.",
        )

    # Group into time windows
    groups = _group_by_time_window(observations, window_minutes)

    events: list[TimelineEvent] = []
    cumulative_sources: set[str] = set()
    prev_source_count = 0
    prev_intensity = 0  # observations per window

    for i, group in enumerate(groups):
        # Representative observation (highest reliability)
        best = max(group, key=lambda o: o.get("reliability_hint", 0.5))
        claim = best.get("claim", "")

        # Collect sources in this window
        window_sources = list({_source_family(o.get("source", "")) for o in group})
        cumulative_sources.update(window_sources)

        # Timestamp
        timestamps = [_parse_time(o.get("captured_at")) for o in group]
        event_time = min(timestamps)

        # Entities
        entities = extract_entities(claim)
        entity_labels = [f"{e.entity_type}:{e.text}" for e in entities[:10]]
        location = _extract_location_from_entities(entities)

        # Intensity trend
        current_intensity = len(group)
        if current_intensity > prev_intensity * 1.5 and prev_intensity > 0:
            intensity_trend = "increasing"
        elif current_intensity < prev_intensity * 0.5 and prev_intensity > 0:
            intensity_trend = "decreasing"
        else:
            intensity_trend = "stable"

        # Phase detection
        phase = _detect_phase(
            text=claim,
            is_first=(i == 0),
            source_count=len(cumulative_sources),
            prev_source_count=prev_source_count,
            intensity_trend=intensity_trend,
        )

        # Reliability
        avg_reliability = sum(o.get("reliability_hint", 0.5) for o in group) / len(group)

        # Pivot detection: phase change or significant intensity shift
        is_pivot = False
        if events and events[-1].phase != phase:
            is_pivot = True
        if intensity_trend in ("increasing", "decreasing") and prev_intensity > 0:
            ratio = current_intensity / max(1, prev_intensity)
            if ratio >= 2.0 or ratio <= 0.3:
                is_pivot = True

        # Headline: short summary of this timeline event
        headline = _make_headline(claim, phase, len(group), len(window_sources))

        event = TimelineEvent(
            timestamp=event_time,
            phase=phase,
            headline=headline,
            detail=claim[:500],
            sources=window_sources,
            source_count=len(window_sources),
            reliability=avg_reliability,
            entities=entity_labels,
            location=location,
            is_pivot=is_pivot,
            observation_ids=[o.get("id", 0) for o in group],
            metadata={
                "observations_in_window": len(group),
                "intensity_trend": intensity_trend,
                "cumulative_sources": len(cumulative_sources),
            },
        )
        events.append(event)

        prev_source_count = len(cumulative_sources)
        prev_intensity = current_intensity

    # Compute stats
    phase_counts: Counter = Counter(e.phase for e in events)
    pivot_count = sum(1 for e in events if e.is_pivot)

    time_span = 0.0
    if len(events) >= 2:
        time_span = (events[-1].timestamp - events[0].timestamp).total_seconds() / 3600

    summary = _generate_summary(topic, events, phase_counts, time_span)

    return NarrativeTimeline(
        topic=topic,
        investigation_id=investigation_id,
        generated_at=datetime.now(UTC),
        time_span_hours=time_span,
        total_events=len(events),
        phases=dict(phase_counts),
        pivots=pivot_count,
        events=events,
        summary=summary,
    )


def _make_headline(claim: str, phase: str, obs_count: int, source_count: int) -> str:
    """Create a concise headline for a timeline event."""
    # Truncate claim to first sentence or 120 chars
    first_sentence = claim.split(". ")[0]
    if len(first_sentence) > 120:
        first_sentence = first_sentence[:117] + "..."

    phase_prefix = {
        "initial_report": "FIRST REPORT",
        "corroboration": "CONFIRMED",
        "escalation": "ESCALATION",
        "response": "RESPONSE",
        "de_escalation": "DE-ESCALATION",
        "update": "UPDATE",
    }
    prefix = phase_prefix.get(phase, "UPDATE")

    if obs_count > 1:
        return f"[{prefix}] {first_sentence} ({obs_count} reports, {source_count} sources)"
    return f"[{prefix}] {first_sentence}"


def _generate_summary(
    topic: str,
    events: list[TimelineEvent],
    phases: Counter,
    time_span: float,
) -> str:
    """Generate a 2-3 sentence narrative summary."""
    if not events:
        return f"No timeline events for '{topic}'."

    parts: list[str] = []

    # Opening
    first = events[0]
    parts.append(
        f"Timeline of '{topic}' spans {time_span:.1f} hours with {len(events)} events."
    )

    # Phase summary
    phase_order = ["initial_report", "corroboration", "escalation", "response", "de_escalation"]
    active_phases = [p for p in phase_order if phases.get(p, 0) > 0]

    if active_phases:
        phase_desc = " -> ".join(p.replace("_", " ").title() for p in active_phases)
        parts.append(f"Narrative arc: {phase_desc}.")

    # Key pivot
    pivots = [e for e in events if e.is_pivot]
    if pivots:
        last_pivot = pivots[-1]
        parts.append(
            f"Last significant shift: {last_pivot.phase.replace('_', ' ')} "
            f"at {last_pivot.timestamp.strftime('%Y-%m-%d %H:%M UTC')}."
        )

    return " ".join(parts)
