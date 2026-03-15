"""Comparative analysis — cross-source contradiction and agreement detection.

Compares how different sources report the same events, identifying:
- Contradictions between sources
- Agreement/corroboration clusters
- Narrative divergence over time
- Potential cover-ups (sudden silence from sources that were reporting)
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass
class SourceReport:
    """How a specific source reported a specific claim/event."""
    source: str
    claim: str
    captured_at: datetime
    reliability_hint: float = 0.5
    observation_id: int | None = None
    url: str = ""
    sentiment: float = 0.0  # -1 negative, 0 neutral, 1 positive


@dataclass
class Contradiction:
    """Detected contradiction between two source reports."""
    claim_a: str
    source_a: str
    claim_b: str
    source_b: str
    contradiction_type: str  # "direct", "omission", "framing", "timing"
    confidence: float = 0.5
    description: str = ""
    captured_at_a: datetime | None = None
    captured_at_b: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_a": self.claim_a,
            "source_a": self.source_a,
            "claim_b": self.claim_b,
            "source_b": self.source_b,
            "type": self.contradiction_type,
            "confidence": round(self.confidence, 3),
            "description": self.description,
            "time_a": self.captured_at_a.isoformat() if self.captured_at_a else None,
            "time_b": self.captured_at_b.isoformat() if self.captured_at_b else None,
        }


@dataclass
class AgreementCluster:
    """Group of sources that agree on a claim."""
    representative_claim: str
    sources: list[str]
    observation_count: int
    avg_reliability: float
    first_reported: datetime | None = None
    last_reported: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.representative_claim,
            "sources": self.sources,
            "source_count": len(self.sources),
            "observation_count": self.observation_count,
            "avg_reliability": round(self.avg_reliability, 3),
            "first_reported": self.first_reported.isoformat() if self.first_reported else None,
            "last_reported": self.last_reported.isoformat() if self.last_reported else None,
        }


@dataclass
class SilenceEvent:
    """A source that was actively reporting but suddenly went silent."""
    source: str
    last_seen: datetime
    hours_silent: float
    was_reporting_topic: str
    avg_prior_frequency_per_day: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "last_seen": self.last_seen.isoformat(),
            "hours_silent": round(self.hours_silent, 1),
            "was_reporting": self.was_reporting_topic,
            "prior_frequency_per_day": round(self.avg_prior_frequency_per_day, 2),
        }


@dataclass
class ComparativeReport:
    """Full comparative analysis result."""
    total_observations: int
    total_sources: int
    contradictions: list[Contradiction]
    agreements: list[AgreementCluster]
    silences: list[SilenceEvent]
    narrative_divergence_score: float  # 0-1, how much sources disagree

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_observations": self.total_observations,
            "total_sources": self.total_sources,
            "contradiction_count": len(self.contradictions),
            "agreement_count": len(self.agreements),
            "silence_count": len(self.silences),
            "narrative_divergence_score": round(self.narrative_divergence_score, 3),
            "contradictions": [c.to_dict() for c in self.contradictions[:50]],
            "agreements": [a.to_dict() for a in self.agreements[:30]],
            "silences": [s.to_dict() for s in self.silences[:20]],
        }


# ── Negation / contradiction keywords ──────────────────────────────────
_NEGATION_WORDS = {"not", "no", "never", "false", "denied", "debunked", "incorrect",
                    "untrue", "fake", "hoax", "retracted", "wrong", "misleading",
                    "disproven", "refuted", "contradicted"}

_CONFIRMATION_WORDS = {"confirmed", "verified", "proven", "established", "corroborated",
                        "authenticated", "validated", "substantiated"}


def _normalize_claim(claim: str) -> str:
    """Basic claim normalization for comparison."""
    c = claim.lower().strip()
    c = re.sub(r'[^\w\s]', ' ', c)
    c = re.sub(r'\s+', ' ', c)
    return c.strip()


def _claim_words(claim: str) -> set[str]:
    """Extract significant words from a claim."""
    stop = {"the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "to", "for",
            "of", "and", "or", "but", "this", "that", "it", "be", "has", "have", "had",
            "with", "from", "by", "as", "its", "their", "our", "your", "his", "her"}
    words = set(_normalize_claim(claim).split())
    return words - stop


def _claim_similarity(claim_a: str, claim_b: str) -> float:
    """Jaccard similarity of claim word sets."""
    words_a = _claim_words(claim_a)
    words_b = _claim_words(claim_b)
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union) if union else 0.0


def _has_negation(claim: str) -> bool:
    """Check if claim contains negation signals."""
    words = set(claim.lower().split())
    return bool(words & _NEGATION_WORDS)


def _has_confirmation(claim: str) -> bool:
    """Check if claim contains confirmation signals."""
    words = set(claim.lower().split())
    return bool(words & _CONFIRMATION_WORDS)


def _source_family(source: str) -> str:
    s = source.lower().strip()
    for prefix in ("rss:", "reddit:", "gdelt:", "nitter:", "telegram:"):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
    for remove in ("http://", "https://", "www."):
        s = s.replace(remove, "")
    if "/" in s:
        s = s.split("/")[0]
    return s or source


def detect_contradictions(observations: list[dict[str, Any]]) -> list[Contradiction]:
    """Find contradictions between sources reporting similar topics."""
    contradictions = []

    # Group by similar claims
    claims = []
    for obs in observations:
        claim = obs.get("claim") or ""
        if len(claim) < 10:
            continue
        src = _source_family(obs.get("source", "unknown"))
        ts_raw = obs.get("captured_at")
        try:
            if isinstance(ts_raw, str):
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            elif isinstance(ts_raw, datetime):
                ts = ts_raw if ts_raw.tzinfo else ts_raw.replace(tzinfo=UTC)
            else:
                ts = datetime.now(UTC)
        except Exception:
            ts = datetime.now(UTC)
        claims.append(SourceReport(
            source=src, claim=claim, captured_at=ts,
            reliability_hint=obs.get("reliability_hint", 0.5),
            observation_id=obs.get("id"),
            url=obs.get("url", ""),
        ))

    # Compare pairs for contradictions
    for i, a in enumerate(claims):
        for b in claims[i + 1:]:
            if a.source == b.source:
                continue
            similarity = _claim_similarity(a.claim, b.claim)
            if similarity < 0.15:
                continue  # Unrelated claims

            # Check for direct contradiction (similar topic, opposite sentiment)
            a_neg = _has_negation(a.claim)
            b_neg = _has_negation(b.claim)
            a_conf = _has_confirmation(a.claim)
            b_conf = _has_confirmation(b.claim)

            if similarity > 0.3 and ((a_neg and b_conf) or (b_neg and a_conf)):
                contradictions.append(Contradiction(
                    claim_a=a.claim[:300], source_a=a.source,
                    claim_b=b.claim[:300], source_b=b.source,
                    contradiction_type="direct",
                    confidence=min(0.95, similarity + 0.2),
                    description=f"Sources '{a.source}' and '{b.source}' directly contradict each other on a similar topic",
                    captured_at_a=a.captured_at, captured_at_b=b.captured_at,
                ))
            elif similarity > 0.4 and a_neg != b_neg:
                contradictions.append(Contradiction(
                    claim_a=a.claim[:300], source_a=a.source,
                    claim_b=b.claim[:300], source_b=b.source,
                    contradiction_type="framing",
                    confidence=similarity * 0.7,
                    description="Sources frame the same event differently — one negates while the other doesn't",
                    captured_at_a=a.captured_at, captured_at_b=b.captured_at,
                ))

    contradictions.sort(key=lambda c: c.confidence, reverse=True)
    return contradictions[:100]


def detect_agreements(observations: list[dict[str, Any]], min_sources: int = 2) -> list[AgreementCluster]:
    """Find agreement clusters — multiple sources reporting the same thing."""
    claim_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for obs in observations:
        claim = _normalize_claim(obs.get("claim") or "")
        if len(claim) < 10:
            continue
        # Use first 8 significant words as group key
        words = [w for w in claim.split() if len(w) > 2][:8]
        key = " ".join(sorted(words))
        if key:
            claim_groups[key].append(obs)

    clusters = []
    for _, obs_list in claim_groups.items():
        sources = list({_source_family(o.get("source", "")) for o in obs_list})
        if len(sources) < min_sources:
            continue
        timestamps = []
        reliabilities = []
        for o in obs_list:
            ts_raw = o.get("captured_at")
            try:
                if isinstance(ts_raw, str):
                    timestamps.append(datetime.fromisoformat(ts_raw.replace("Z", "+00:00")))
                elif isinstance(ts_raw, datetime):
                    timestamps.append(ts_raw if ts_raw.tzinfo else ts_raw.replace(tzinfo=UTC))
            except Exception:
                pass
            reliabilities.append(o.get("reliability_hint", 0.5))

        clusters.append(AgreementCluster(
            representative_claim=obs_list[0].get("claim", "")[:300],
            sources=sorted(sources),
            observation_count=len(obs_list),
            avg_reliability=sum(reliabilities) / len(reliabilities) if reliabilities else 0.5,
            first_reported=min(timestamps) if timestamps else None,
            last_reported=max(timestamps) if timestamps else None,
        ))

    clusters.sort(key=lambda c: c.observation_count, reverse=True)
    return clusters[:50]


def detect_silences(
    observations: list[dict[str, Any]],
    silence_threshold_hours: int = 12,
    min_prior_frequency: float = 1.0,
) -> list[SilenceEvent]:
    """Detect sources that were actively reporting but suddenly stopped."""
    now = datetime.now(UTC)
    source_timeline: dict[str, list[datetime]] = defaultdict(list)
    source_topics: dict[str, list[str]] = defaultdict(list)

    for obs in observations:
        src = _source_family(obs.get("source", "unknown"))
        ts_raw = obs.get("captured_at")
        try:
            if isinstance(ts_raw, str):
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            elif isinstance(ts_raw, datetime):
                ts = ts_raw if ts_raw.tzinfo else ts_raw.replace(tzinfo=UTC)
            else:
                continue
        except Exception:
            continue
        source_timeline[src].append(ts)
        claim = obs.get("claim", "")
        if claim:
            source_topics[src].append(claim[:100])

    silences = []
    for src, timestamps in source_timeline.items():
        if len(timestamps) < 3:
            continue
        timestamps.sort()
        last = timestamps[-1]
        hours_since = (now - last).total_seconds() / 3600

        if hours_since < silence_threshold_hours:
            continue

        # Calculate prior frequency
        time_span = (timestamps[-1] - timestamps[0]).total_seconds() / 86400  # days
        if time_span < 1:
            continue
        freq_per_day = len(timestamps) / time_span

        if freq_per_day < min_prior_frequency:
            continue

        # This source was active but went quiet
        topics = source_topics.get(src, [])
        representative_topic = topics[-1] if topics else "unknown"

        silences.append(SilenceEvent(
            source=src,
            last_seen=last,
            hours_silent=hours_since,
            was_reporting_topic=representative_topic,
            avg_prior_frequency_per_day=freq_per_day,
        ))

    silences.sort(key=lambda s: s.hours_silent, reverse=True)
    return silences[:30]


def run_comparative_analysis(
    observations: list[dict[str, Any]],
    silence_threshold_hours: int = 12,
) -> ComparativeReport:
    """Run full comparative analysis across all observations."""
    sources = {_source_family(o.get("source", "")) for o in observations}

    contradictions = detect_contradictions(observations)
    agreements = detect_agreements(observations)
    silences = detect_silences(observations, silence_threshold_hours=silence_threshold_hours)

    # Narrative divergence: ratio of contradictions to agreements
    total_signals = len(contradictions) + len(agreements) + 1
    divergence = len(contradictions) / total_signals

    return ComparativeReport(
        total_observations=len(observations),
        total_sources=len(sources),
        contradictions=contradictions,
        agreements=agreements,
        silences=silences,
        narrative_divergence_score=min(1.0, divergence),
    )
