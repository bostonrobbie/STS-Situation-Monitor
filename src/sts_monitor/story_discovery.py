"""Automated story discovery engine.

Finds emerging topics worth investigating by:
1. Analyzing trending topics (Google Trends, GDELT trending)
2. Detecting burst patterns in observation inflow
3. Cross-referencing entity mentions across investigations
4. Surfacing convergence zones as investigation candidates

Inspired by GDELT's Daily Trend Report and WorldMonitor's auto-surfacing.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sts_monitor.entities import ExtractedEntity, extract_entities


@dataclass(slots=True)
class DiscoveredTopic:
    """A topic surfaced by the discovery engine."""
    title: str
    description: str
    score: float  # 0.0–1.0 relevance/importance
    source: str  # how it was discovered: trending, burst, convergence, entity_spike
    key_terms: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    sample_urls: list[str] = field(default_factory=list)
    suggested_seed_query: str = ""
    suggested_connectors: list[str] = field(default_factory=list)
    discovered_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True)
class BurstSignal:
    """Detected burst in observation volume for a term or entity."""
    term: str
    current_count: int
    baseline_count: int
    burst_ratio: float
    window_hours: int


_STOP_WORDS: set[str] = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "not", "no", "nor", "as",
    "if", "then", "than", "it", "its", "this", "that", "these", "those",
    "he", "she", "they", "we", "you", "me", "him", "her", "us", "them",
    "said", "says", "according", "reported", "reports", "also", "about",
    "after", "before", "new", "just", "more", "most", "other", "some",
}

_WORD_RE = re.compile(r"[a-zA-Z]{3,}")


def _extract_terms(text: str) -> list[str]:
    words = _WORD_RE.findall(text.lower())
    return [w for w in words if w not in _STOP_WORDS]


# ── Burst Detection ─────────────────────────────────────────────────────


@dataclass(slots=True)
class ObservationSnapshot:
    """Minimal observation data for discovery analysis."""
    claim: str
    source: str
    captured_at: datetime
    url: str = ""
    reliability_hint: float = 0.5


def detect_bursts(
    observations: list[ObservationSnapshot],
    window_hours: int = 6,
    baseline_hours: int = 48,
    min_burst_ratio: float = 3.0,
    min_current_count: int = 3,
) -> list[BurstSignal]:
    """Detect terms/entities with unusual frequency spikes.

    Compares term frequency in the recent window vs. baseline period.
    A burst_ratio of 3.0 means "3x more mentions than normal."
    """
    now = datetime.now(UTC)
    window_cutoff = now - timedelta(hours=window_hours)
    baseline_cutoff = now - timedelta(hours=baseline_hours)

    recent = [o for o in observations if o.captured_at >= window_cutoff]
    baseline = [o for o in observations if baseline_cutoff <= o.captured_at < window_cutoff]

    if not recent:
        return []

    # Count terms in each window
    recent_terms: Counter = Counter()
    for obs in recent:
        recent_terms.update(_extract_terms(obs.claim))
        for ent in extract_entities(obs.claim):
            recent_terms[ent.text.lower()] += 2  # Entities weighted 2x

    baseline_terms: Counter = Counter()
    for obs in baseline:
        baseline_terms.update(_extract_terms(obs.claim))
        for ent in extract_entities(obs.claim):
            baseline_terms[ent.text.lower()] += 2

    # Normalize by time window size
    baseline_scale = window_hours / max(1, baseline_hours - window_hours)

    bursts: list[BurstSignal] = []
    for term, current_count in recent_terms.most_common(200):
        if current_count < min_current_count:
            continue

        baseline_count = baseline_terms.get(term, 0)
        expected = max(1, baseline_count * baseline_scale)
        ratio = current_count / expected

        if ratio >= min_burst_ratio:
            bursts.append(BurstSignal(
                term=term,
                current_count=current_count,
                baseline_count=baseline_count,
                burst_ratio=round(ratio, 2),
                window_hours=window_hours,
            ))

    bursts.sort(key=lambda b: b.burst_ratio * b.current_count, reverse=True)
    return bursts[:20]


# ── Topic Suggestion ────────────────────────────────────────────────────


def _suggest_connectors_for_topic(title: str, entities: list[str]) -> list[str]:
    """Suggest which connectors would be most relevant for a topic."""
    title_lower = title.lower()
    connectors = ["gdelt", "rss"]  # Always useful

    # Geo/disaster keywords
    geo_keywords = {"earthquake", "quake", "seismic", "tsunami", "tremor"}
    fire_keywords = {"fire", "wildfire", "blaze", "burn", "arson"}
    weather_keywords = {"storm", "hurricane", "tornado", "flood", "cyclone", "typhoon", "drought"}
    conflict_keywords = {"war", "conflict", "attack", "military", "troops", "bomb", "strike", "missile", "protest", "riot"}
    disaster_keywords = {"disaster", "emergency", "evacuation", "relief", "fema", "declaration"}

    if any(kw in title_lower for kw in geo_keywords):
        connectors.append("usgs")
    if any(kw in title_lower for kw in fire_keywords):
        connectors.append("nasa_firms")
    if any(kw in title_lower for kw in weather_keywords):
        connectors.append("nws")
    if any(kw in title_lower for kw in conflict_keywords):
        connectors.append("acled")
    if any(kw in title_lower for kw in disaster_keywords):
        connectors.append("fema")

    return list(dict.fromkeys(connectors))  # Dedupe preserving order


def discover_topics_from_bursts(
    bursts: list[BurstSignal],
    observations: list[ObservationSnapshot],
) -> list[DiscoveredTopic]:
    """Convert burst signals into suggested investigation topics."""
    topics: list[DiscoveredTopic] = []

    now = datetime.now(UTC)
    window_cutoff = now - timedelta(hours=12)
    recent = [o for o in observations if o.captured_at >= window_cutoff]

    for burst in bursts:
        # Find observations containing this burst term
        matching = [
            o for o in recent
            if burst.term.lower() in o.claim.lower()
        ]

        if not matching:
            continue

        # Extract entities from matching observations
        all_entities: set[str] = set()
        for obs in matching[:10]:
            for ent in extract_entities(obs.claim):
                all_entities.add(f"{ent.entity_type}:{ent.text}")

        # Build description from highest-reliability matching observation
        best_obs = max(matching, key=lambda o: o.reliability_hint)
        description = best_obs.claim[:300]

        title = f"Emerging: {burst.term.title()} ({burst.current_count} mentions, {burst.burst_ratio}x spike)"

        topic = DiscoveredTopic(
            title=title,
            description=description,
            score=min(1.0, (burst.burst_ratio * burst.current_count) / 100),
            source="burst",
            key_terms=[burst.term],
            entities=sorted(all_entities)[:10],
            sample_urls=[o.url for o in matching[:5] if o.url],
            suggested_seed_query=burst.term,
            suggested_connectors=_suggest_connectors_for_topic(burst.term, sorted(all_entities)),
        )
        topics.append(topic)

    return topics


def discover_topics_from_convergence(
    convergence_zones: list[dict[str, Any]],
) -> list[DiscoveredTopic]:
    """Convert convergence zones into suggested investigation topics."""
    topics: list[DiscoveredTopic] = []

    for zone in convergence_zones:
        signal_types = zone.get("signal_types", [])
        severity = zone.get("severity", "low")
        center_lat = zone.get("center_lat", 0)
        center_lon = zone.get("center_lon", 0)

        if severity in ("low",):
            continue

        title = f"Convergence Zone: {', '.join(signal_types)} near ({center_lat:.2f}, {center_lon:.2f})"
        description = (
            f"Multiple signal types ({', '.join(signal_types)}) detected within "
            f"{zone.get('radius_km', 50)}km. Severity: {severity}."
        )

        score_map = {"medium": 0.6, "high": 0.8, "critical": 0.95}

        topic = DiscoveredTopic(
            title=title,
            description=description,
            score=score_map.get(severity, 0.5),
            source="convergence",
            key_terms=signal_types,
            entities=[],
            suggested_seed_query=f"{center_lat:.2f},{center_lon:.2f}",
            suggested_connectors=_suggest_connectors_for_topic(" ".join(signal_types), []),
        )
        topics.append(topic)

    return topics


def discover_topics_from_entity_spikes(
    observations: list[ObservationSnapshot],
    window_hours: int = 12,
    baseline_hours: int = 72,
    min_spike_ratio: float = 4.0,
) -> list[DiscoveredTopic]:
    """Find entities (people, orgs, locations) with unusual mention spikes."""
    now = datetime.now(UTC)
    window_cutoff = now - timedelta(hours=window_hours)
    baseline_cutoff = now - timedelta(hours=baseline_hours)

    recent = [o for o in observations if o.captured_at >= window_cutoff]
    baseline = [o for o in observations if baseline_cutoff <= o.captured_at < window_cutoff]

    if not recent:
        return []

    recent_entities: Counter = Counter()
    for obs in recent:
        for ent in extract_entities(obs.claim):
            if ent.confidence >= 0.7:
                recent_entities[f"{ent.entity_type}:{ent.text}"] += 1

    baseline_entities: Counter = Counter()
    for obs in baseline:
        for ent in extract_entities(obs.claim):
            if ent.confidence >= 0.7:
                baseline_entities[f"{ent.entity_type}:{ent.text}"] += 1

    baseline_scale = window_hours / max(1, baseline_hours - window_hours)

    topics: list[DiscoveredTopic] = []
    for entity_key, current_count in recent_entities.most_common(50):
        if current_count < 3:
            continue

        baseline_count = baseline_entities.get(entity_key, 0)
        expected = max(1, baseline_count * baseline_scale)
        ratio = current_count / expected

        if ratio < min_spike_ratio:
            continue

        ent_type, ent_text = entity_key.split(":", 1)

        # Find sample observations
        matching = [o for o in recent if ent_text.lower() in o.claim.lower()][:5]
        best_obs = max(matching, key=lambda o: o.reliability_hint) if matching else None

        topic = DiscoveredTopic(
            title=f"Entity spike: {ent_text} ({ent_type}) — {current_count} mentions, {ratio:.1f}x normal",
            description=best_obs.claim[:300] if best_obs else f"Unusual spike in {ent_text} mentions",
            score=min(1.0, (ratio * current_count) / 80),
            source="entity_spike",
            key_terms=[ent_text.lower()],
            entities=[entity_key],
            sample_urls=[o.url for o in matching if o.url],
            suggested_seed_query=ent_text,
            suggested_connectors=_suggest_connectors_for_topic(ent_text, [entity_key]),
        )
        topics.append(topic)

    return topics


def run_discovery(
    observations: list[ObservationSnapshot],
    convergence_zones: list[dict[str, Any]] | None = None,
) -> list[DiscoveredTopic]:
    """Run all discovery methods and return unified, ranked topic list."""
    all_topics: list[DiscoveredTopic] = []

    # 1. Burst detection
    bursts = detect_bursts(observations)
    all_topics.extend(discover_topics_from_bursts(bursts, observations))

    # 2. Convergence zones
    if convergence_zones:
        all_topics.extend(discover_topics_from_convergence(convergence_zones))

    # 3. Entity spikes
    all_topics.extend(discover_topics_from_entity_spikes(observations))

    # Deduplicate by similar titles
    seen_terms: set[str] = set()
    unique_topics: list[DiscoveredTopic] = []
    for topic in all_topics:
        key = topic.suggested_seed_query.lower()
        if key not in seen_terms:
            seen_terms.add(key)
            unique_topics.append(topic)

    # Sort by score
    unique_topics.sort(key=lambda t: t.score, reverse=True)
    return unique_topics[:30]
