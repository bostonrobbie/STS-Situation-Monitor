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
        accepted: list[Observation] = []
        dropped: list[Observation] = []

        for observation in observations:
            if observation.reliability_hint >= self.min_reliability:
                accepted.append(observation)
            else:
                dropped.append(observation)

        confidence = 0.0
        if accepted:
            confidence = sum(item.reliability_hint for item in accepted) / len(accepted)

        disputed_claims = self._find_disputed_claims(deduplicated)
        summary = (
            f"Topic '{topic}': {len(accepted)} high-signal observations retained, "
            f"{len(dropped)} filtered as low-confidence noise, "
            f"{len(disputed_claims)} disputed claim cluster(s)."
        summary = (
            f"Topic '{topic}': {len(accepted)} high-signal observations retained, "
            f"{len(dropped)} filtered as low-confidence noise."
        )

        return PipelineResult(
            accepted=accepted,
            dropped=dropped,
            deduplicated=deduplicated,
            disputed_claims=disputed_claims,
            summary=summary,
            confidence=round(confidence, 3),
        )
