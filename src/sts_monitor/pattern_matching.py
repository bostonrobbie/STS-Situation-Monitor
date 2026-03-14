"""Historical pattern matching — compare current observation patterns against baselines.

Detects:
- Escalation patterns that match historical crisis precursors
- Velocity curves similar to past events
- Anomalous deviations from normal activity levels
- "This looks like what happened before X" alerts
"""
from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class PatternSignature:
    """Signature of an observation pattern for comparison."""
    name: str
    description: str
    observation_velocity: list[float]  # observations per hour over time windows
    source_diversity: float  # 0-1, how many different sources
    sentiment_trend: list[float]  # -1 to 1 over time
    entity_concentration: float  # 0-1, how focused on few entities
    contradiction_rate: float  # 0-1
    escalation_score: float  # 0-1

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "velocity": [round(v, 2) for v in self.observation_velocity],
            "source_diversity": round(self.source_diversity, 3),
            "sentiment_trend": [round(s, 2) for s in self.sentiment_trend],
            "entity_concentration": round(self.entity_concentration, 3),
            "contradiction_rate": round(self.contradiction_rate, 3),
            "escalation_score": round(self.escalation_score, 3),
        }


@dataclass
class PatternMatch:
    """A match between current data and a known pattern."""
    pattern_name: str
    pattern_description: str
    similarity_score: float  # 0-1
    matching_features: list[str]
    diverging_features: list[str]
    assessment: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern_name,
            "description": self.pattern_description,
            "similarity": round(self.similarity_score, 3),
            "matching": self.matching_features,
            "diverging": self.diverging_features,
            "assessment": self.assessment,
        }


# ── Known historical patterns ──────────────────────────────────────
KNOWN_PATTERNS = [
    PatternSignature(
        name="Rapid Escalation (Pre-Conflict)",
        description="Pattern seen before military conflicts: rapid observation increase, "
                    "high source diversity, rising contradiction rate, focused entities",
        observation_velocity=[2, 5, 15, 40, 80],
        source_diversity=0.7,
        sentiment_trend=[-0.1, -0.3, -0.5, -0.7, -0.8],
        entity_concentration=0.6,
        contradiction_rate=0.4,
        escalation_score=0.9,
    ),
    PatternSignature(
        name="Natural Disaster Onset",
        description="Pattern typical of natural disaster reporting: sudden spike, "
                    "government sources dominate, low contradiction, geographic focus",
        observation_velocity=[1, 10, 50, 30, 20],
        source_diversity=0.4,
        sentiment_trend=[-0.5, -0.8, -0.6, -0.4, -0.3],
        entity_concentration=0.3,
        contradiction_rate=0.1,
        escalation_score=0.7,
    ),
    PatternSignature(
        name="Information Suppression",
        description="Pattern suggesting active suppression: initial burst of reports "
                    "followed by sudden drop, sources going silent, high contradiction",
        observation_velocity=[20, 30, 5, 2, 1],
        source_diversity=0.3,
        sentiment_trend=[0.0, -0.2, -0.5, -0.3, 0.0],
        entity_concentration=0.7,
        contradiction_rate=0.6,
        escalation_score=0.4,
    ),
    PatternSignature(
        name="Coordinated Disinformation",
        description="Pattern of coordinated disinfo: many sources, similar timing, "
                    "high contradiction, conflicting claims from 'independent' sources",
        observation_velocity=[5, 20, 20, 20, 15],
        source_diversity=0.8,
        sentiment_trend=[0.3, 0.1, -0.1, -0.3, -0.2],
        entity_concentration=0.5,
        contradiction_rate=0.7,
        escalation_score=0.5,
    ),
    PatternSignature(
        name="Slow-Burn Scandal",
        description="Pattern of developing scandal: gradual increase, specific entity focus, "
                    "investigative sources, rising contradiction as cover-up attempts emerge",
        observation_velocity=[2, 3, 5, 8, 12],
        source_diversity=0.5,
        sentiment_trend=[-0.1, -0.2, -0.3, -0.5, -0.6],
        entity_concentration=0.8,
        contradiction_rate=0.5,
        escalation_score=0.6,
    ),
    PatternSignature(
        name="False Flag / Manufactured Event",
        description="Pattern suggesting manufactured event: sudden appearance with no precursors, "
                    "pre-written narratives, rapid source amplification, inconsistent details",
        observation_velocity=[0, 2, 40, 30, 15],
        source_diversity=0.6,
        sentiment_trend=[0.0, -0.8, -0.7, -0.4, -0.2],
        entity_concentration=0.4,
        contradiction_rate=0.5,
        escalation_score=0.7,
    ),
]

# ── Sentiment heuristic ────────────────────────────────────────────
_NEGATIVE_WORDS = {"crisis", "attack", "killed", "died", "explosion", "war", "conflict",
                    "threat", "danger", "emergency", "casualties", "destruction", "collapse",
                    "denied", "false", "failed", "corrupt"}
_POSITIVE_WORDS = {"peace", "agreement", "resolved", "safe", "confirmed", "success",
                    "recovered", "stable", "cooperation", "aid"}


def _claim_sentiment(claim: str) -> float:
    words = set(claim.lower().split())
    neg = len(words & _NEGATIVE_WORDS)
    pos = len(words & _POSITIVE_WORDS)
    total = neg + pos
    if total == 0:
        return 0.0
    return (pos - neg) / total


def _parse_ts(val) -> datetime:
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=UTC)
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except Exception:
            pass
    return datetime.min.replace(tzinfo=UTC)


def compute_current_signature(
    observations: list[dict[str, Any]],
    window_count: int = 5,
) -> PatternSignature:
    """Compute the pattern signature of current observations."""
    if not observations:
        return PatternSignature(
            name="current", description="Current observation pattern",
            observation_velocity=[0] * window_count,
            source_diversity=0, sentiment_trend=[0] * window_count,
            entity_concentration=0, contradiction_rate=0, escalation_score=0,
        )

    timestamps = [_parse_ts(o.get("captured_at")) for o in observations]
    valid_ts = [t for t in timestamps if t != datetime.min.replace(tzinfo=UTC)]

    if not valid_ts:
        return PatternSignature(
            name="current", description="Current observation pattern",
            observation_velocity=[len(observations)] + [0] * (window_count - 1),
            source_diversity=0, sentiment_trend=[0] * window_count,
            entity_concentration=0, contradiction_rate=0, escalation_score=0,
        )

    # Time-windowed velocity
    earliest = min(valid_ts)
    latest = max(valid_ts)
    span = (latest - earliest).total_seconds() or 3600
    window_size = span / window_count

    velocity = []
    sentiments_per_window = []
    for i in range(window_count):
        start = earliest + timedelta(seconds=i * window_size)
        end = start + timedelta(seconds=window_size)
        window_obs = [o for o, t in zip(observations, timestamps) if start <= t < end]
        velocity.append(len(window_obs))
        if window_obs:
            sentiments_per_window.append(
                sum(_claim_sentiment(o.get("claim", "")) for o in window_obs) / len(window_obs)
            )
        else:
            sentiments_per_window.append(0.0)

    # Source diversity
    sources = {(o.get("source") or "").lower() for o in observations}
    source_diversity = min(1.0, len(sources) / max(1, len(observations) * 0.3))

    # Entity concentration
    words = Counter()
    for o in observations:
        claim_words = [w.lower() for w in (o.get("claim") or "").split() if len(w) > 4]
        words.update(claim_words)
    if words:
        top_count = words.most_common(1)[0][1]
        entity_concentration = min(1.0, top_count / max(1, len(observations)))
    else:
        entity_concentration = 0.0

    # Contradiction rate
    neg_words = {"false", "denied", "debunked", "retracted", "wrong", "hoax"}
    contradictions = sum(1 for o in observations if any(w in (o.get("claim") or "").lower() for w in neg_words))
    contradiction_rate = contradictions / max(1, len(observations))

    # Escalation: are later windows bigger than earlier?
    if len(velocity) >= 2 and velocity[0] > 0:
        escalation_score = min(1.0, velocity[-1] / max(1, velocity[0]) / 5.0)
    elif len(velocity) >= 2:
        escalation_score = min(1.0, velocity[-1] / 20.0)
    else:
        escalation_score = 0.0

    return PatternSignature(
        name="current",
        description="Current observation pattern",
        observation_velocity=velocity,
        source_diversity=source_diversity,
        sentiment_trend=sentiments_per_window,
        entity_concentration=entity_concentration,
        contradiction_rate=contradiction_rate,
        escalation_score=escalation_score,
    )


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    if len(a) != len(b):
        min_len = min(len(a), len(b))
        a, b = a[:min_len], b[:min_len]
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a)) or 1e-10
    mag_b = math.sqrt(sum(x * x for x in b)) or 1e-10
    return dot / (mag_a * mag_b)


def match_against_known_patterns(
    current: PatternSignature,
    threshold: float = 0.3,
) -> list[PatternMatch]:
    """Compare current pattern against known historical patterns."""
    matches = []

    for pattern in KNOWN_PATTERNS:
        # Compare multiple dimensions
        velocity_sim = max(0, _cosine_similarity(current.observation_velocity, pattern.observation_velocity))
        sentiment_sim = max(0, _cosine_similarity(current.sentiment_trend, pattern.sentiment_trend))
        diversity_sim = 1.0 - abs(current.source_diversity - pattern.source_diversity)
        concentration_sim = 1.0 - abs(current.entity_concentration - pattern.entity_concentration)
        contradiction_sim = 1.0 - abs(current.contradiction_rate - pattern.contradiction_rate)
        escalation_sim = 1.0 - abs(current.escalation_score - pattern.escalation_score)

        # Weighted average
        similarity = (
            velocity_sim * 0.25 +
            sentiment_sim * 0.15 +
            diversity_sim * 0.15 +
            concentration_sim * 0.1 +
            contradiction_sim * 0.2 +
            escalation_sim * 0.15
        )

        if similarity < threshold:
            continue

        matching = []
        diverging = []
        for name, sim in [("velocity", velocity_sim), ("sentiment", sentiment_sim),
                           ("source_diversity", diversity_sim), ("entity_focus", concentration_sim),
                           ("contradiction_rate", contradiction_sim), ("escalation", escalation_sim)]:
            if sim >= 0.6:
                matching.append(name)
            elif sim < 0.3:
                diverging.append(name)

        # Generate assessment
        if similarity >= 0.7:
            assessment = f"Strong match with '{pattern.name}' pattern. Investigate immediately."
        elif similarity >= 0.5:
            assessment = f"Moderate resemblance to '{pattern.name}'. Monitor closely."
        else:
            assessment = f"Weak similarity to '{pattern.name}'. Keep watching."

        matches.append(PatternMatch(
            pattern_name=pattern.name,
            pattern_description=pattern.description,
            similarity_score=similarity,
            matching_features=matching,
            diverging_features=diverging,
            assessment=assessment,
        ))

    matches.sort(key=lambda m: m.similarity_score, reverse=True)
    return matches


def analyze_patterns(
    observations: list[dict[str, Any]],
    threshold: float = 0.3,
) -> dict[str, Any]:
    """Full pattern analysis: compute signature and match against known patterns."""
    current = compute_current_signature(observations)
    matches = match_against_known_patterns(current, threshold=threshold)

    return {
        "current_signature": current.to_dict(),
        "matches": [m.to_dict() for m in matches],
        "match_count": len(matches),
        "top_match": matches[0].to_dict() if matches else None,
        "escalation_score": round(current.escalation_score, 3),
        "contradiction_rate": round(current.contradiction_rate, 3),
    }
