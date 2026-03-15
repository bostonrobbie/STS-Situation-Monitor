"""Source reliability scoring — track accuracy of sources over time.

Instead of using static reliability hints, this system learns which sources
are consistently accurate vs unreliable based on corroboration outcomes.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class SourceScore:
    """Tracked reliability score for a single source."""
    source: str
    total_observations: int = 0
    corroborated_count: int = 0
    contradicted_count: int = 0
    disputed_count: int = 0
    unique_claims: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    raw_score: float = 0.5  # 0.0 = completely unreliable, 1.0 = gold standard

    @property
    def accuracy_rate(self) -> float:
        evaluated = self.corroborated_count + self.contradicted_count
        if evaluated == 0:
            return 0.5
        return self.corroborated_count / evaluated

    @property
    def reliability_grade(self) -> str:
        s = self.raw_score
        if s >= 0.85:
            return "A"
        if s >= 0.7:
            return "B"
        if s >= 0.5:
            return "C"
        if s >= 0.3:
            return "D"
        return "F"

    @property
    def days_active(self) -> int:
        if not self.first_seen or not self.last_seen:
            return 0
        return max(1, (self.last_seen - self.first_seen).days)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "total_observations": self.total_observations,
            "corroborated": self.corroborated_count,
            "contradicted": self.contradicted_count,
            "disputed": self.disputed_count,
            "unique_claims": self.unique_claims,
            "accuracy_rate": round(self.accuracy_rate, 3),
            "raw_score": round(self.raw_score, 3),
            "grade": self.reliability_grade,
            "days_active": self.days_active,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
        }


def _source_family(source: str) -> str:
    """Normalize source to its family name."""
    s = source.lower().strip()
    for prefix in ("rss:", "reddit:", "gdelt:", "nitter:", "telegram:"):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
    # Remove URLs and common prefixes
    for remove in ("http://", "https://", "www."):
        s = s.replace(remove, "")
    # Take domain or first meaningful word
    if "/" in s:
        s = s.split("/")[0]
    return s or source


def compute_source_scores(
    observations: list[dict[str, Any]],
    *,
    decay_days: int = 90,
    min_observations: int = 3,
) -> list[SourceScore]:
    """Compute reliability scores for all sources in the observation set.

    Scoring factors:
    - Corroboration rate (claims confirmed by other sources)
    - Contradiction rate (claims denied by other sources)
    - Consistency (variance in reliability across time)
    - Volume (more data = more confident score)
    - Recency decay (older track record decays slowly)
    """
    source_data: dict[str, SourceScore] = {}
    claim_sources: dict[str, list[str]] = defaultdict(list)  # claim -> [sources]
    now = datetime.now(UTC)

    # Phase 1: Aggregate per-source stats
    for obs in observations:
        src = _source_family(obs.get("source", "unknown"))
        claim = (obs.get("claim") or "").strip().lower()
        ts_raw = obs.get("captured_at") or obs.get("timestamp")
        try:
            if isinstance(ts_raw, str):
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            elif isinstance(ts_raw, datetime):
                ts = ts_raw if ts_raw.tzinfo else ts_raw.replace(tzinfo=UTC)
            else:
                ts = now
        except Exception:
            ts = now

        if src not in source_data:
            source_data[src] = SourceScore(source=src, first_seen=ts, last_seen=ts)
        score = source_data[src]
        score.total_observations += 1
        if ts < (score.first_seen or now):
            score.first_seen = ts
        if ts > (score.last_seen or datetime.min.replace(tzinfo=UTC)):
            score.last_seen = ts

        if claim:
            claim_sources[claim].append(src)

    # Phase 2: Check corroboration — claims reported by multiple sources
    for claim, sources in claim_sources.items():
        unique = set(sources)
        for src in unique:
            if src in source_data:
                source_data[src].unique_claims += 1
        if len(unique) >= 2:
            for src in unique:
                if src in source_data:
                    source_data[src].corroborated_count += 1
        # Check for "debunked"/"false"/"retracted" keywords as contradiction signal
        is_disputed = any(w in claim for w in ("false", "debunked", "retracted", "denied", "hoax", "misleading"))
        if is_disputed:
            for src in unique:
                if src in source_data:
                    source_data[src].disputed_count += 1

    # Phase 3: Compute raw scores
    results = []
    for src, score in source_data.items():
        if score.total_observations < min_observations:
            score.raw_score = 0.5  # Not enough data
            results.append(score)
            continue

        # Base: accuracy rate (50% default for unknowns)
        accuracy = score.accuracy_rate

        # Volume confidence: more observations = more confidence in the score
        volume_factor = min(1.0, math.log(score.total_observations + 1) / math.log(100))

        # Dispute penalty
        dispute_rate = score.disputed_count / max(1, score.total_observations)
        dispute_penalty = dispute_rate * 0.3

        # Recency: decay score if source hasn't been seen recently
        if score.last_seen:
            days_since = (now - score.last_seen).days
            recency_factor = max(0.5, 1.0 - (days_since / decay_days) * 0.3)
        else:
            recency_factor = 0.7

        # Combine
        raw = (accuracy * 0.5 + volume_factor * 0.2 + 0.3) * recency_factor - dispute_penalty
        score.raw_score = max(0.0, min(1.0, raw))
        results.append(score)

    results.sort(key=lambda s: s.raw_score, reverse=True)
    return results


def get_source_leaderboard(
    observations: list[dict[str, Any]],
    top_n: int = 50,
) -> dict[str, Any]:
    """Return a ranked leaderboard of source reliability."""
    scores = compute_source_scores(observations)
    top = scores[:top_n]
    _bottom = [s for s in scores if s.raw_score < 0.4]
    trusted = [s for s in scores if s.reliability_grade in ("A", "B")]
    untrusted = [s for s in scores if s.reliability_grade in ("D", "F")]

    return {
        "total_sources": len(scores),
        "trusted_count": len(trusted),
        "untrusted_count": len(untrusted),
        "leaderboard": [s.to_dict() for s in top],
        "flagged_unreliable": [s.to_dict() for s in untrusted[:20]],
        "grade_distribution": {
            grade: len([s for s in scores if s.reliability_grade == grade])
            for grade in ("A", "B", "C", "D", "F")
        },
    }
