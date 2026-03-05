import pytest

from sts_monitor.pipeline import Observation, SignalPipeline

pytestmark = pytest.mark.unit


def test_pipeline_filters_low_reliability_items() -> None:
    pipeline = SignalPipeline(min_reliability=0.5)
    result = pipeline.run(
        [
            Observation(source="a", claim="c1", url="u1", reliability_hint=0.8),
            Observation(source="b", claim="c2", url="u2", reliability_hint=0.2),
        ],
        topic="topic",
    )

    assert len(result.accepted) == 1
    assert len(result.dropped) == 1
    assert result.confidence == 0.8


def test_pipeline_deduplicates_and_clamps_reliability() -> None:
    pipeline = SignalPipeline(min_reliability=0.5)
    result = pipeline.run(
        [
            Observation(source="a", claim="same", url="u", reliability_hint=1.7),
            Observation(source="b", claim="same", url="u", reliability_hint=0.3),
        ],
        topic="topic",
    )

    assert len(result.deduplicated) == 1
    assert result.accepted[0].reliability_hint == 1.0


def test_pipeline_finds_disputed_claim_clusters() -> None:
    pipeline = SignalPipeline(min_reliability=0.1)
    result = pipeline.run(
        [
            Observation(source="a", claim="Power restored in district", url="u1", reliability_hint=0.9),
            Observation(source="b", claim="Power restored in district is false", url="u2", reliability_hint=0.9),
        ],
        topic="topic",
    )

    assert len(result.disputed_claims) >= 1


def test_pipeline_confidence_penalizes_disputes_on_large_batches() -> None:
    pipeline = SignalPipeline(min_reliability=0.1)
    result = pipeline.run(
        [
            Observation(source="rss:a", claim="Bridge reopened", url="u1", reliability_hint=0.9),
            Observation(source="reddit:b", claim="Bridge reopened", url="u2", reliability_hint=0.8),
            Observation(source="rss:c", claim="Bridge reopened is false", url="u3", reliability_hint=0.85),
        ],
        topic="topic",
    )

    assert len(result.disputed_claims) >= 1
    assert result.confidence < 0.9
