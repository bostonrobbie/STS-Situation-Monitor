from sts_monitor.pipeline import Observation, SignalPipeline


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
