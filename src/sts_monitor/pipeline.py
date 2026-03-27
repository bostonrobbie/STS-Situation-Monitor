from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from datetime import datetime, UTC
from typing import Iterable


@dataclass(slots=True)
class Observation:
    source: str
    claim: str
    url: str
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    reliability_hint: float = 0.5


@dataclass(slots=True)
class PipelineResult:
    accepted: list[Observation]
    dropped: list[Observation]
    deduplicated: list[Observation]
    disputed_claims: list[str]
    summary: str
    confidence: float


class SignalPipeline:
    """Prototype ranking/filtering pipeline with basic dedup and dispute detection."""
    """Simple ranking/filtering pipeline for rapid prototyping."""

    contradiction_markers = ["false", "hoax", "debunked", "not true", "fabricated", "fake", "denied", "refuted"]

    def __init__(self, min_reliability: float = 0.45) -> None:
        self.min_reliability = min_reliability

    @staticmethod
    def _normalize_claim(text: str) -> str:
        return " ".join(text.strip().lower().split())

    contradiction_markers = ["false", "hoax", "debunked", "not true", "fabricated", "fake"]

    @classmethod
    def _is_contradiction(cls, text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in cls.contradiction_markers)

    @classmethod
    def _claim_cluster_key(cls, text: str) -> str:
        normalized = cls._normalize_claim(text)
        for marker in cls.contradiction_markers:
            normalized = normalized.replace(f"is {marker}", "")
            normalized = normalized.replace(marker, "")
        return " ".join(normalized.split())

    @staticmethod
    def _source_family(source: str) -> str:
        return source.split(":", 1)[0].strip().lower()

    def _deduplicate(self, observations: Iterable[Observation]) -> list[Observation]:
        by_key: dict[tuple[str, str], Observation] = {}
        for item in observations:
            key = (item.url.strip().lower(), self._normalize_claim(item.claim))
            existing = by_key.get(key)
            if existing is None or item.reliability_hint > existing.reliability_hint:
                by_key[key] = item
        return list(by_key.values())

    def _find_disputed_claims(self, observations: Iterable[Observation]) -> list[str]:
        groups: dict[str, dict[str, int]] = {}
        for item in observations:
            normalized = self._claim_cluster_key(item.claim)
            signal = groups.setdefault(normalized, {"support": 0, "contradict": 0})
            if self._is_contradiction(item.claim):
                signal["contradict"] += 1
            else:
                signal["support"] += 1

        disputed: list[str] = []
        for claim, counts in groups.items():
            if counts["support"] > 0 and counts["contradict"] > 0:
                disputed.append(claim)
        return disputed

    def run(self, observations: Iterable[Observation], topic: str) -> PipelineResult:
        deduplicated = self._deduplicate(observations)
        accepted: list[Observation] = []
        dropped: list[Observation] = []

        for observation in deduplicated:
            bounded_reliability = max(0.0, min(1.0, observation.reliability_hint))
            adjusted = Observation(
                source=observation.source,
                claim=observation.claim,
                url=observation.url,
                captured_at=observation.captured_at,
                reliability_hint=bounded_reliability,
            )
            if adjusted.reliability_hint >= self.min_reliability:
                accepted.append(adjusted)
            else:
                dropped.append(adjusted)
    def run(self, observations: Iterable[Observation], topic: str) -> PipelineResult:
        deduplicated = self._deduplicate(observations)
        accepted: list[Observation] = []
        dropped: list[Observation] = []

        for observation in deduplicated:
            bounded_reliability = max(0.0, min(1.0, observation.reliability_hint))
            adjusted = Observation(
                source=observation.source,
                claim=observation.claim,
                url=observation.url,
                captured_at=observation.captured_at,
                reliability_hint=bounded_reliability,
            )
            if adjusted.reliability_hint >= self.min_reliability:
                accepted.append(adjusted)
            else:
                dropped.append(adjusted)
        groups: dict[str, dict[str, object]] = {}
        for item in observations:
            normalized = self._claim_cluster_key(item.claim)
            signal = groups.setdefault(
                normalized,
                {
                    "support_families": set(),
                    "contradict_families": set(),
                    "latest_support": None,
                    "latest_contradict": None,
                },
            )
            source_family = self._source_family(item.source)
            if self._is_contradiction(item.claim):
                cast_set = signal["contradict_families"]
                assert isinstance(cast_set, set)
                cast_set.add(source_family)
                latest = signal["latest_contradict"]
                if latest is None or item.captured_at > latest:
                    signal["latest_contradict"] = item.captured_at
            else:
                cast_set = signal["support_families"]
                assert isinstance(cast_set, set)
                cast_set.add(source_family)
                latest = signal["latest_support"]
                if latest is None or item.captured_at > latest:
                    signal["latest_support"] = item.captured_at

        disputed: list[str] = []
        for claim, detail in groups.items():
            support_families = detail["support_families"]
            contradict_families = detail["contradict_families"]
            assert isinstance(support_families, set)
            assert isinstance(contradict_families, set)
            if not support_families or not contradict_families:
                continue

            independent_sources = len(support_families.union(contradict_families))
            latest_support = detail["latest_support"]
            latest_contradict = detail["latest_contradict"]
            temporal_flip = False
            if isinstance(latest_support, datetime) and isinstance(latest_contradict, datetime):
                temporal_flip = abs((latest_support - latest_contradict).total_seconds()) > 3600

            if independent_sources >= 2 or temporal_flip:
                disputed.append(claim)
        return disputed

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _compute_confidence(self, accepted: list[Observation], disputed_claims: list[str]) -> float:
        if not accepted:
            return 0.0

        base = sum(item.reliability_hint for item in accepted) / len(accepted)
        if len(accepted) < 3:
            return round(base, 3)

        source_diversity = len({self._source_family(item.source) for item in accepted})
        diversity_boost = min(0.1, source_diversity * 0.02)
        contradiction_penalty = min(0.15, len(disputed_claims) * 0.03)

        newest = max(self._as_utc(item.captured_at) for item in accepted)
        hours_old = max(0.0, (datetime.now(UTC) - newest).total_seconds() / 3600)
        freshness_penalty = 0.0 if hours_old <= 12 else min(0.1, (hours_old - 12) / 240)

        score = max(0.0, min(1.0, base + diversity_boost - contradiction_penalty - freshness_penalty))
        return round(score, 3)

    def run(self, observations: Iterable[Observation], topic: str) -> PipelineResult:
        deduplicated = self._deduplicate(observations)
        accepted: list[Observation] = []
        dropped: list[Observation] = []

        for observation in deduplicated:
            bounded_reliability = max(0.0, min(1.0, observation.reliability_hint))
            adjusted = Observation(
                source=observation.source,
                claim=observation.claim,
                url=observation.url,
                captured_at=observation.captured_at,
                reliability_hint=bounded_reliability,
            )
            if adjusted.reliability_hint >= self.min_reliability:
                accepted.append(adjusted)
            else:
                dropped.append(adjusted)

        disputed_claims = self._find_disputed_claims(deduplicated)
        confidence = self._compute_confidence(accepted, disputed_claims)
        summary = (
            f"Topic '{topic}': {len(accepted)} high-signal observations retained, "
            f"{len(dropped)} filtered as low-confidence noise, "
            f"{len(disputed_claims)} disputed claim cluster(s)."
        )

        return PipelineResult(
            accepted=accepted,
            dropped=dropped,
            deduplicated=deduplicated,
            disputed_claims=disputed_claims,
            summary=summary,
            confidence=confidence,
        )
