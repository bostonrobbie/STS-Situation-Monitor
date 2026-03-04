from __future__ import annotations

from dataclasses import dataclass, field
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
    summary: str
    confidence: float


class SignalPipeline:
    """Simple ranking/filtering pipeline for rapid prototyping."""

    def __init__(self, min_reliability: float = 0.45) -> None:
        self.min_reliability = min_reliability

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

        summary = (
            f"Topic '{topic}': {len(accepted)} high-signal observations retained, "
            f"{len(dropped)} filtered as low-confidence noise."
        )

        return PipelineResult(
            accepted=accepted,
            dropped=dropped,
            summary=summary,
            confidence=round(confidence, 3),
        )
