"""Anomaly and pattern-break detection engine.

The most valuable intelligence is what's *unusual*. This module detects:

1. Volume anomalies: A region/topic that normally has N events/day suddenly has 10N
2. Source behavior shifts: A source that normally covers X suddenly pivots to Y
3. Silence anomalies: Expected regular signals (e.g. daily weather reports) go missing
4. Sentiment shifts: A topic's tone changes dramatically (escalatory language spike)
5. Entity velocity: A previously quiet entity (person/org) suddenly surges
6. Geographic dispersion: Events normally concentrated suddenly spread or vice versa

Uses simple statistical baselines (no ML needed) — Z-score based detection
against rolling windows. Works offline, no external dependencies.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from math import sqrt
from typing import Any


@dataclass(slots=True)
class Anomaly:
    """A detected anomaly / pattern break."""
    anomaly_type: str  # volume, source_shift, silence, sentiment, entity_velocity, dispersion
    severity: str  # low, medium, high, critical
    title: str
    description: str
    metric_name: str
    current_value: float
    baseline_mean: float
    baseline_std: float
    z_score: float
    detected_at: datetime
    window_hours: int
    affected_entity: str  # what's anomalous (region, source, topic, entity)
    evidence: list[str]  # supporting observations/data points
    recommended_action: str


@dataclass(slots=True)
class AnomalyReport:
    """Results of anomaly detection across all dimensions."""
    detected_at: datetime
    total_anomalies: int
    by_type: dict[str, int]
    by_severity: dict[str, int]
    anomalies: list[Anomaly]
    baseline_window_hours: int
    detection_window_hours: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "detected_at": self.detected_at.isoformat(),
            "total_anomalies": self.total_anomalies,
            "by_type": self.by_type,
            "by_severity": self.by_severity,
            "baseline_window_hours": self.baseline_window_hours,
            "detection_window_hours": self.detection_window_hours,
            "anomalies": [
                {
                    "type": a.anomaly_type,
                    "severity": a.severity,
                    "title": a.title,
                    "description": a.description,
                    "metric": a.metric_name,
                    "current_value": round(a.current_value, 2),
                    "baseline_mean": round(a.baseline_mean, 2),
                    "baseline_std": round(a.baseline_std, 2),
                    "z_score": round(a.z_score, 2),
                    "affected_entity": a.affected_entity,
                    "evidence": a.evidence[:5],
                    "recommended_action": a.recommended_action,
                }
                for a in self.anomalies
            ],
        }


# ── Statistical helpers ────────────────────────────────────────────────

def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    variance = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return sqrt(variance)


def _z_score(value: float, mean: float, std: float) -> float:
    if std == 0:
        return 0.0 if value == mean else (3.0 if value > mean else -3.0)
    return (value - mean) / std


def _severity_from_z(z: float) -> str:
    az = abs(z)
    if az >= 4.0:
        return "critical"
    if az >= 3.0:
        return "high"
    if az >= 2.0:
        return "medium"
    return "low"


def _parse_time(val: Any) -> datetime:
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
    return source.split(":", 1)[0].strip().lower()


# ── Anomaly detectors ─────────────────────────────────────────────────

def detect_volume_anomalies(
    observations: list[dict[str, Any]],
    detection_hours: int = 6,
    baseline_hours: int = 72,
    min_z_score: float = 2.0,
    bucket_minutes: int = 60,
) -> list[Anomaly]:
    """Detect unusual volume spikes or drops in observation inflow.

    Buckets observations by hour, computes baseline mean/std from historical
    window, then flags buckets in the detection window with high z-scores.
    """
    now = datetime.now(UTC)
    baseline_start = now - timedelta(hours=baseline_hours)
    detection_start = now - timedelta(hours=detection_hours)

    # Bucket by time
    baseline_buckets: Counter = Counter()
    detection_buckets: Counter = Counter()

    for obs in observations:
        t = _parse_time(obs.get("captured_at"))
        bucket_key = t.strftime("%Y-%m-%d-%H")

        if t >= detection_start:
            detection_buckets[bucket_key] += 1
        elif t >= baseline_start:
            baseline_buckets[bucket_key] += 1

    if not baseline_buckets:
        return []

    baseline_values = list(baseline_buckets.values())
    mean = _mean(baseline_values)
    std = _std(baseline_values)

    anomalies: list[Anomaly] = []
    for bucket, count in detection_buckets.items():
        z = _z_score(count, mean, std)
        if abs(z) >= min_z_score:
            direction = "spike" if z > 0 else "drop"
            sev = _severity_from_z(z)
            anomalies.append(Anomaly(
                anomaly_type="volume",
                severity=sev,
                title=f"Volume {direction}: {count} observations in hour (baseline: {mean:.1f})",
                description=(
                    f"Observation volume in bucket {bucket} is {abs(z):.1f} standard deviations "
                    f"{'above' if z > 0 else 'below'} the {baseline_hours}h baseline."
                ),
                metric_name="observations_per_hour",
                current_value=count,
                baseline_mean=mean,
                baseline_std=std,
                z_score=z,
                detected_at=now,
                window_hours=detection_hours,
                affected_entity=f"time_bucket:{bucket}",
                evidence=[f"{bucket}: {count} observations (mean: {mean:.1f}, std: {std:.1f})"],
                recommended_action="Investigate cause of volume anomaly; check for breaking events or data source issues",
            ))

    return anomalies


def detect_source_behavior_shifts(
    observations: list[dict[str, Any]],
    detection_hours: int = 12,
    baseline_hours: int = 168,  # 1 week
    min_z_score: float = 2.5,
) -> list[Anomaly]:
    """Detect when a source's topic coverage shifts dramatically.

    If a source normally covers topic X but suddenly starts covering Y,
    that's a signal worth surfacing.
    """
    now = datetime.now(UTC)
    baseline_start = now - timedelta(hours=baseline_hours)
    detection_start = now - timedelta(hours=detection_hours)

    # Track topic words per source
    baseline_topics: dict[str, Counter] = defaultdict(Counter)
    detection_topics: dict[str, Counter] = defaultdict(Counter)

    for obs in observations:
        t = _parse_time(obs.get("captured_at"))
        source = _source_family(obs.get("source", ""))
        words = _extract_topic_words(obs.get("claim", ""))

        if t >= detection_start:
            detection_topics[source].update(words)
        elif t >= baseline_start:
            baseline_topics[source].update(words)

    anomalies: list[Anomaly] = []
    for source in detection_topics:
        if source not in baseline_topics:
            continue

        baseline_top = set(w for w, _ in baseline_topics[source].most_common(20))
        detection_top = set(w for w, _ in detection_topics[source].most_common(20))

        if not baseline_top or not detection_top:
            continue

        # Measure topic shift: what fraction of current top words are new?
        new_topics = detection_top - baseline_top
        shift_ratio = len(new_topics) / len(detection_top)

        if shift_ratio >= 0.5:  # 50%+ new topics
            z = shift_ratio * 4  # Scale to z-score-like
            if z >= min_z_score:
                anomalies.append(Anomaly(
                    anomaly_type="source_shift",
                    severity=_severity_from_z(z),
                    title=f"Source '{source}' topic shift: {shift_ratio:.0%} new topics",
                    description=(
                        f"Source '{source}' is covering new topics not seen in its "
                        f"{baseline_hours}h baseline: {', '.join(sorted(new_topics)[:5])}"
                    ),
                    metric_name="topic_shift_ratio",
                    current_value=shift_ratio,
                    baseline_mean=0.2,  # expected ~20% topic turnover
                    baseline_std=0.1,
                    z_score=z,
                    detected_at=now,
                    window_hours=detection_hours,
                    affected_entity=f"source:{source}",
                    evidence=[
                        f"New topics: {', '.join(sorted(new_topics)[:10])}",
                        f"Baseline topics: {', '.join(sorted(baseline_top)[:10])}",
                    ],
                    recommended_action=f"Review why '{source}' shifted coverage; may indicate breaking event",
                ))

    return anomalies


def detect_entity_velocity_anomalies(
    observations: list[dict[str, Any]],
    detection_hours: int = 6,
    baseline_hours: int = 72,
    min_z_score: float = 2.5,
    min_detection_count: int = 3,
) -> list[Anomaly]:
    """Detect entities (people, orgs, locations) with unusual mention velocity.

    A previously quiet entity suddenly surging is often the earliest signal
    of a developing situation.
    """
    from sts_monitor.entities import extract_entities

    now = datetime.now(UTC)
    baseline_start = now - timedelta(hours=baseline_hours)
    detection_start = now - timedelta(hours=detection_hours)

    baseline_counts: Counter = Counter()
    detection_counts: Counter = Counter()

    for obs in observations:
        t = _parse_time(obs.get("captured_at"))
        entities = extract_entities(obs.get("claim", ""))

        for ent in entities:
            if ent.confidence < 0.6:
                continue
            key = f"{ent.entity_type}:{ent.text}"

            if t >= detection_start:
                detection_counts[key] += 1
            elif t >= baseline_start:
                baseline_counts[key] += 1

    # Normalize baseline to detection window size
    scale = detection_hours / max(1, baseline_hours - detection_hours)

    anomalies: list[Anomaly] = []
    for entity_key, det_count in detection_counts.items():
        if det_count < min_detection_count:
            continue

        baseline_count = baseline_counts.get(entity_key, 0)
        expected = max(0.5, baseline_count * scale)
        z = (det_count - expected) / max(0.5, sqrt(expected))

        if z >= min_z_score:
            anomalies.append(Anomaly(
                anomaly_type="entity_velocity",
                severity=_severity_from_z(z),
                title=f"Entity surge: '{entity_key}' — {det_count} mentions ({z:.1f}x normal)",
                description=(
                    f"Entity '{entity_key}' has {det_count} mentions in the last {detection_hours}h, "
                    f"compared to an expected {expected:.1f} based on {baseline_hours}h baseline."
                ),
                metric_name="entity_mentions",
                current_value=det_count,
                baseline_mean=expected,
                baseline_std=sqrt(expected),
                z_score=z,
                detected_at=now,
                window_hours=detection_hours,
                affected_entity=entity_key,
                evidence=[f"{entity_key}: {det_count} recent vs {baseline_count} baseline"],
                recommended_action=f"Investigate sudden interest in '{entity_key}'; may be early indicator",
            ))

    return anomalies


def detect_silence_anomalies(
    observations: list[dict[str, Any]],
    expected_sources: list[str] | None = None,
    silence_hours: int = 6,
    baseline_hours: int = 72,
) -> list[Anomaly]:
    """Detect when expected regular sources go silent.

    Some data sources (USGS, NWS, RSS feeds) should produce regular updates.
    Silence from a normally active source can mean: source is down, censorship,
    or communication disruption in a region.
    """
    now = datetime.now(UTC)
    baseline_start = now - timedelta(hours=baseline_hours)
    silence_start = now - timedelta(hours=silence_hours)

    # Track source activity
    source_last_seen: dict[str, datetime] = {}
    source_baseline_count: Counter = Counter()

    for obs in observations:
        t = _parse_time(obs.get("captured_at"))
        source = _source_family(obs.get("source", ""))

        if t >= baseline_start:
            source_baseline_count[source] += 1
            if source not in source_last_seen or t > source_last_seen[source]:
                source_last_seen[source] = t

    # Find sources that were active in baseline but silent in detection window
    anomalies: list[Anomaly] = []

    # Determine expected sources
    if expected_sources is None:
        # Auto-detect: sources with >= 5 observations in baseline are "expected"
        expected_sources = [s for s, c in source_baseline_count.items() if c >= 5]

    for source in expected_sources:
        last_seen = source_last_seen.get(source)
        if last_seen is None:
            continue

        hours_silent = (now - last_seen).total_seconds() / 3600
        baseline_count = source_baseline_count.get(source, 0)
        expected_per_hour = baseline_count / max(1, baseline_hours)
        expected_in_window = expected_per_hour * silence_hours

        if hours_silent >= silence_hours and expected_in_window >= 1.0:
            z = hours_silent / max(1, silence_hours)
            anomalies.append(Anomaly(
                anomaly_type="silence",
                severity="medium" if hours_silent < 12 else "high",
                title=f"Source silent: '{source}' — {hours_silent:.1f}h since last update",
                description=(
                    f"Source '{source}' normally produces ~{expected_per_hour:.1f} observations/hour "
                    f"but has been silent for {hours_silent:.1f}h."
                ),
                metric_name="hours_since_last_observation",
                current_value=hours_silent,
                baseline_mean=1.0 / max(0.01, expected_per_hour),
                baseline_std=0.0,
                z_score=z,
                detected_at=now,
                window_hours=silence_hours,
                affected_entity=f"source:{source}",
                evidence=[
                    f"Last seen: {last_seen.isoformat()}",
                    f"Baseline: {baseline_count} observations in {baseline_hours}h",
                ],
                recommended_action=f"Check if '{source}' is experiencing issues or if silence is meaningful",
            ))

    return anomalies


def detect_sentiment_shifts(
    observations: list[dict[str, Any]],
    detection_hours: int = 6,
    baseline_hours: int = 48,
    min_z_score: float = 2.0,
) -> list[Anomaly]:
    """Detect dramatic changes in language tone/intensity.

    Measures "escalation score" of text — presence of conflict/crisis words
    vs neutral/de-escalation words. A sudden shift toward escalatory language
    is a leading indicator.
    """
    now = datetime.now(UTC)
    baseline_start = now - timedelta(hours=baseline_hours)
    detection_start = now - timedelta(hours=detection_hours)

    baseline_scores: list[float] = []
    detection_scores: list[float] = []

    for obs in observations:
        t = _parse_time(obs.get("captured_at"))
        score = _escalation_score(obs.get("claim", ""))

        if t >= detection_start:
            detection_scores.append(score)
        elif t >= baseline_start:
            baseline_scores.append(score)

    if not baseline_scores or not detection_scores:
        return []

    baseline_mean = _mean(baseline_scores)
    baseline_std = _std(baseline_scores)
    detection_mean = _mean(detection_scores)

    z = _z_score(detection_mean, baseline_mean, baseline_std)

    anomalies: list[Anomaly] = []
    if abs(z) >= min_z_score:
        direction = "escalatory" if z > 0 else "de-escalatory"
        anomalies.append(Anomaly(
            anomaly_type="sentiment",
            severity=_severity_from_z(z),
            title=f"Language shift: {direction} tone ({z:+.1f} z-score)",
            description=(
                f"Average escalation score shifted from {baseline_mean:.2f} to {detection_mean:.2f} "
                f"in the last {detection_hours}h ({abs(z):.1f} standard deviations from baseline)."
            ),
            metric_name="escalation_score",
            current_value=detection_mean,
            baseline_mean=baseline_mean,
            baseline_std=baseline_std,
            z_score=z,
            detected_at=now,
            window_hours=detection_hours,
            affected_entity="overall_sentiment",
            evidence=[
                f"Baseline ({baseline_hours}h): mean={baseline_mean:.3f}, std={baseline_std:.3f}",
                f"Detection ({detection_hours}h): mean={detection_mean:.3f}",
            ],
            recommended_action="Review recent observations for escalation indicators",
        ))

    return anomalies


# ── Helper: escalation scoring ─────────────────────────────────────────

_ESCALATION_TERMS: set[str] = {
    "attack", "strike", "bomb", "missile", "assault", "offensive",
    "killed", "dead", "casualties", "wounded", "destroyed", "explod",
    "crisis", "emergency", "catastroph", "devastat", "massacre",
    "invasion", "occupation", "siege", "blockade", "ultimatum",
    "nuclear", "chemical", "biological", "genocide", "war crime",
    "escalat", "intensif", "spread", "surge",
}

_DEESCALATION_TERMS: set[str] = {
    "peace", "ceasefire", "truce", "negotiate", "agreement", "deal",
    "withdraw", "retreat", "calm", "stable", "recover", "rebuild",
    "aid", "relief", "humanitarian", "rescue", "restor",
}


def _escalation_score(text: str) -> float:
    """Score text from -1 (de-escalation) to +1 (escalation). 0 = neutral."""
    text_lower = text.lower()
    words = text_lower.split()
    if not words:
        return 0.0

    esc_count = sum(1 for w in words if any(t in w for t in _ESCALATION_TERMS))
    deesc_count = sum(1 for w in words if any(t in w for t in _DEESCALATION_TERMS))

    total = esc_count + deesc_count
    if total == 0:
        return 0.0

    return (esc_count - deesc_count) / total


_TOPIC_STOP_WORDS: set[str] = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "said", "says", "it", "its", "this", "that",
    "not", "no", "as", "if", "will", "would", "could", "about",
}


def _extract_topic_words(text: str) -> list[str]:
    """Extract meaningful topic words from text."""
    words = re.findall(r"[a-zA-Z]{4,}", text.lower())
    return [w for w in words if w not in _TOPIC_STOP_WORDS]


# ── Main entry point ──────────────────────────────────────────────────

def run_anomaly_detection(
    observations: list[dict[str, Any]],
    detection_hours: int = 6,
    baseline_hours: int = 72,
    min_z_score: float = 2.0,
    expected_sources: list[str] | None = None,
) -> AnomalyReport:
    """Run all anomaly detectors and return unified report.

    Checks: volume, source behavior, entity velocity, silence, sentiment.
    """
    now = datetime.now(UTC)
    all_anomalies: list[Anomaly] = []

    # Volume anomalies
    all_anomalies.extend(detect_volume_anomalies(
        observations, detection_hours, baseline_hours, min_z_score,
    ))

    # Source behavior shifts
    all_anomalies.extend(detect_source_behavior_shifts(
        observations, detection_hours * 2, baseline_hours * 2, min_z_score,
    ))

    # Entity velocity
    all_anomalies.extend(detect_entity_velocity_anomalies(
        observations, detection_hours, baseline_hours, min_z_score,
    ))

    # Silence detection
    all_anomalies.extend(detect_silence_anomalies(
        observations, expected_sources, detection_hours, baseline_hours,
    ))

    # Sentiment shifts
    all_anomalies.extend(detect_sentiment_shifts(
        observations, detection_hours, baseline_hours * 2 // 3, min_z_score,
    ))

    # Sort by severity then z-score
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_anomalies.sort(key=lambda a: (severity_order.get(a.severity, 4), -abs(a.z_score)))

    by_type: Counter = Counter(a.anomaly_type for a in all_anomalies)
    by_severity: Counter = Counter(a.severity for a in all_anomalies)

    return AnomalyReport(
        detected_at=now,
        total_anomalies=len(all_anomalies),
        by_type=dict(by_type),
        by_severity=dict(by_severity),
        anomalies=all_anomalies,
        baseline_window_hours=baseline_hours,
        detection_window_hours=detection_hours,
    )
