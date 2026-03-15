"""Alert rules engine — configurable alerting on investigation data patterns.

Supports rule types:
- volume_spike: Alert when observation rate exceeds threshold
- contradiction_threshold: Alert when contradiction count is high
- entity_velocity: Alert when entity mentions spike
- silence_detection: Alert when expected source goes quiet
- narrative_shift: Alert when dominant narrative changes
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class AlertRule:
    """A configurable alert rule."""
    name: str
    rule_type: str  # volume_spike, contradiction_threshold, entity_velocity, silence, narrative_shift
    investigation_id: str | None = None  # None = global
    threshold: float = 5.0
    window_minutes: int = 60
    cooldown_seconds: int = 600
    severity: str = "warning"  # info, warning, critical
    active: bool = True
    last_triggered_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "rule_type": self.rule_type,
            "investigation_id": self.investigation_id,
            "threshold": self.threshold,
            "window_minutes": self.window_minutes,
            "cooldown_seconds": self.cooldown_seconds,
            "severity": self.severity,
            "active": self.active,
            "last_triggered_at": self.last_triggered_at.isoformat() if self.last_triggered_at else None,
            "metadata": self.metadata,
        }


@dataclass
class AlertEvent:
    """A triggered alert."""
    rule_name: str
    rule_type: str
    severity: str
    message: str
    triggered_at: datetime
    investigation_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_name": self.rule_name,
            "rule_type": self.rule_type,
            "severity": self.severity,
            "message": self.message,
            "triggered_at": self.triggered_at.isoformat(),
            "investigation_id": self.investigation_id,
            "details": self.details,
        }


# ── Rule evaluation functions ──────────────────────────────────────────

def _check_volume_spike(
    rule: AlertRule,
    observations: list[dict[str, Any]],
    now: datetime,
) -> AlertEvent | None:
    """Check if observation volume exceeds threshold in the window."""
    window_start = now - timedelta(minutes=rule.window_minutes)
    recent = [
        o for o in observations
        if _parse_ts(o.get("captured_at")) >= window_start
    ]
    count = len(recent)
    if count >= rule.threshold:
        return AlertEvent(
            rule_name=rule.name,
            rule_type="volume_spike",
            severity=rule.severity,
            message=f"Volume spike: {count} observations in {rule.window_minutes}min (threshold: {rule.threshold})",
            triggered_at=now,
            investigation_id=rule.investigation_id,
            details={"count": count, "threshold": rule.threshold, "window_minutes": rule.window_minutes},
        )
    return None


def _check_contradiction_threshold(
    rule: AlertRule,
    observations: list[dict[str, Any]],
    now: datetime,
) -> AlertEvent | None:
    """Check if contradiction signals exceed threshold."""
    window_start = now - timedelta(minutes=rule.window_minutes)
    recent = [
        o for o in observations
        if _parse_ts(o.get("captured_at")) >= window_start
    ]
    negation_words = {"not", "false", "denied", "debunked", "retracted", "wrong", "hoax", "misleading"}
    disputed = 0
    for o in recent:
        claim = (o.get("claim") or "").lower()
        if any(w in claim for w in negation_words):
            disputed += 1

    if disputed >= rule.threshold:
        return AlertEvent(
            rule_name=rule.name,
            rule_type="contradiction_threshold",
            severity=rule.severity,
            message=f"High contradiction rate: {disputed} disputed claims in {rule.window_minutes}min",
            triggered_at=now,
            investigation_id=rule.investigation_id,
            details={"disputed_count": disputed, "total_recent": len(recent)},
        )
    return None


def _check_entity_velocity(
    rule: AlertRule,
    observations: list[dict[str, Any]],
    now: datetime,
) -> AlertEvent | None:
    """Check if a specific entity is being mentioned at high velocity."""
    from collections import Counter
    window_start = now - timedelta(minutes=rule.window_minutes)
    recent = [
        o for o in observations
        if _parse_ts(o.get("captured_at")) >= window_start
    ]
    target_entity = rule.metadata.get("entity", "").lower()
    if not target_entity:
        # Auto-detect: find any entity spiking
        words: list[str] = []
        for o in recent:
            claim = (o.get("claim") or "").lower()
            words.extend(w for w in claim.split() if len(w) > 3 and w.isalpha())
        counter = Counter(words)
        if counter:
            top_word, top_count = counter.most_common(1)[0]
            if top_count >= rule.threshold:
                return AlertEvent(
                    rule_name=rule.name,
                    rule_type="entity_velocity",
                    severity=rule.severity,
                    message=f"Entity velocity spike: '{top_word}' mentioned {top_count} times in {rule.window_minutes}min",
                    triggered_at=now,
                    investigation_id=rule.investigation_id,
                    details={"entity": top_word, "count": top_count},
                )
    else:
        count = sum(1 for o in recent if target_entity in (o.get("claim") or "").lower())
        if count >= rule.threshold:
            return AlertEvent(
                rule_name=rule.name,
                rule_type="entity_velocity",
                severity=rule.severity,
                message=f"Entity '{target_entity}' mentioned {count} times in {rule.window_minutes}min",
                triggered_at=now,
                investigation_id=rule.investigation_id,
                details={"entity": target_entity, "count": count},
            )
    return None


def _check_silence(
    rule: AlertRule,
    observations: list[dict[str, Any]],
    now: datetime,
) -> AlertEvent | None:
    """Check if expected sources have gone silent."""
    expected = rule.metadata.get("expected_sources", [])
    if not expected:
        return None

    window = timedelta(minutes=rule.window_minutes)
    window_start = now - window
    active_sources = {
        (o.get("source") or "").lower()
        for o in observations
        if _parse_ts(o.get("captured_at")) >= window_start
    }

    silent = [s for s in expected if s.lower() not in active_sources]
    if len(silent) >= rule.threshold:
        return AlertEvent(
            rule_name=rule.name,
            rule_type="silence",
            severity=rule.severity,
            message=f"Source silence: {len(silent)} expected sources went quiet for {rule.window_minutes}min",
            triggered_at=now,
            investigation_id=rule.investigation_id,
            details={"silent_sources": silent, "active_sources": list(active_sources)[:20]},
        )
    return None


def _check_narrative_shift(
    rule: AlertRule,
    observations: list[dict[str, Any]],
    now: datetime,
) -> AlertEvent | None:
    """Detect when the dominant narrative changes significantly."""
    from collections import Counter

    half_window = timedelta(minutes=rule.window_minutes // 2)
    window_start = now - timedelta(minutes=rule.window_minutes)
    mid_point = now - half_window

    earlier = [o for o in observations if window_start <= _parse_ts(o.get("captured_at")) < mid_point]
    later = [o for o in observations if _parse_ts(o.get("captured_at")) >= mid_point]

    if len(earlier) < 3 or len(later) < 3:
        return None

    def top_words(obs_list: list[dict[str, Any]]) -> set[str]:
        words: list[str] = []
        for o in obs_list:
            claim = (o.get("claim") or "").lower()
            words.extend(w for w in claim.split() if len(w) > 3 and w.isalpha())
        counter = Counter(words)
        return {w for w, _ in counter.most_common(10)}

    earlier_topics = top_words(earlier)
    later_topics = top_words(later)

    if not earlier_topics or not later_topics:
        return None

    overlap = earlier_topics & later_topics
    shift_score = 1.0 - (len(overlap) / max(len(earlier_topics | later_topics), 1))

    if shift_score >= (rule.threshold / 10.0):  # threshold in 1-10 scale
        return AlertEvent(
            rule_name=rule.name,
            rule_type="narrative_shift",
            severity=rule.severity,
            message=f"Narrative shift detected: {shift_score:.0%} topic divergence in {rule.window_minutes}min",
            triggered_at=now,
            investigation_id=rule.investigation_id,
            details={
                "shift_score": round(shift_score, 3),
                "earlier_topics": list(earlier_topics),
                "later_topics": list(later_topics),
                "new_topics": list(later_topics - earlier_topics),
            },
        )
    return None


def _parse_ts(val: Any) -> datetime:
    """Parse a timestamp value into a datetime."""
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=UTC)
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except Exception:
            pass
    return datetime.min.replace(tzinfo=UTC)


# ── Evaluation engine ──────────────────────────────────────────────────

_EVALUATORS = {
    "volume_spike": _check_volume_spike,
    "contradiction_threshold": _check_contradiction_threshold,
    "entity_velocity": _check_entity_velocity,
    "silence": _check_silence,
    "narrative_shift": _check_narrative_shift,
}


def evaluate_rules(
    rules: list[AlertRule],
    observations: list[dict[str, Any]],
    now: datetime | None = None,
) -> list[AlertEvent]:
    """Evaluate all rules against current observations."""
    if now is None:
        now = datetime.now(UTC)

    events = []
    for rule in rules:
        if not rule.active:
            continue
        # Check cooldown
        if rule.last_triggered_at:
            cooldown_end = rule.last_triggered_at + timedelta(seconds=rule.cooldown_seconds)
            if now < cooldown_end:
                continue

        evaluator = _EVALUATORS.get(rule.rule_type)
        if not evaluator:
            log.warning("Unknown rule type: %s", rule.rule_type)
            continue

        # Filter observations for this investigation if scoped
        obs = observations
        if rule.investigation_id:
            obs = [o for o in observations if o.get("investigation_id") == rule.investigation_id]

        event = evaluator(rule, obs, now)
        if event:
            rule.last_triggered_at = now
            events.append(event)

    return events


# ── Default rules ──────────────────────────────────────────────────────

def get_default_rules(investigation_id: str | None = None) -> list[AlertRule]:
    """Get a default set of alert rules."""
    return [
        AlertRule(
            name="Volume Spike",
            rule_type="volume_spike",
            investigation_id=investigation_id,
            threshold=20,
            window_minutes=60,
            severity="warning",
        ),
        AlertRule(
            name="High Contradictions",
            rule_type="contradiction_threshold",
            investigation_id=investigation_id,
            threshold=5,
            window_minutes=120,
            severity="critical",
        ),
        AlertRule(
            name="Entity Velocity",
            rule_type="entity_velocity",
            investigation_id=investigation_id,
            threshold=10,
            window_minutes=60,
            severity="warning",
        ),
        AlertRule(
            name="Narrative Shift",
            rule_type="narrative_shift",
            investigation_id=investigation_id,
            threshold=5,
            window_minutes=120,
            severity="warning",
        ),
    ]
