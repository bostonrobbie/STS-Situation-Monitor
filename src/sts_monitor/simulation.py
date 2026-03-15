from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sts_monitor.pipeline import Observation


def generate_simulated_observations(topic: str, batch_size: int = 20, include_noise: bool = True) -> list[Observation]:
    now = datetime.now(UTC)
    observations: list[Observation] = []

    for idx in range(batch_size):
        base_reliability = 0.75 if idx % 3 != 0 else 0.35
        claim = f"{topic} update #{idx}: source confirms activity near central area"
        if idx % 7 == 0:
            claim = f"{topic} update #{idx}: this rumor is false and debunked"

        observations.append(
            Observation(
                source=f"simulated:source-{idx % 5}",
                claim=claim,
                url=f"https://simulated.local/{topic.replace(' ', '-').lower()}/{idx % 10}",
                captured_at=now - timedelta(minutes=idx),
                reliability_hint=base_reliability,
            )
        )

    if include_noise:
        observations.extend(
            [
                Observation(
                    source="simulated:noise",
                    claim=f"{topic} !!! clickbait shocking secret",
                    url="https://simulated.local/noise/1",
                    captured_at=now,
                    reliability_hint=0.0,
                ),
                Observation(
                    source="simulated:noise",
                    claim=f"{topic} generic repost",
                    url="https://simulated.local/noise/2",
                    captured_at=now,
                    reliability_hint=1.0,
                ),
            ]
        )

    return observations
