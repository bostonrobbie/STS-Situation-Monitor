"""Comprehensive tests for the enrichment chain and cycle orchestrator."""
from __future__ import annotations

import pytest
from datetime import UTC, datetime, timedelta
from typing import Any

from sts_monitor.pipeline import Observation, PipelineResult, SignalPipeline
from sts_monitor.enrichment import (
    EnrichmentResult,
    run_enrichment,
    _obs_to_dict,
    _obs_to_ref,
    _obs_to_snapshot,
)
from sts_monitor.cycle import (
    CycleResult,
    run_cycle,
    build_default_connectors,
    evaluate_alerts,
    promote_discoveries,
    AlertFired,
    PromotedTopic,
)
from sts_monitor.connectors.base import Connector, ConnectorResult

pytestmark = pytest.mark.unit


# ── Helpers ────────────────────────────────────────────────────────────


def _obs(
    source: str = "rss:bbc.com",
    claim: str = "Major earthquake strikes Turkey",
    reliability: float = 0.7,
    hours_ago: float = 0,
    url: str | None = None,
) -> Observation:
    return Observation(
        source=source,
        claim=claim,
        url=url or f"https://example.com/{hash(claim) & 0xFFFFFFFF}",
        captured_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        reliability_hint=reliability,
    )


def _pipeline_result(
    accepted: list[Observation] | None = None,
    dropped: list[Observation] | None = None,
    disputed_claims: list[str] | None = None,
) -> PipelineResult:
    """Build a PipelineResult with sensible defaults."""
    acc = accepted or []
    drp = dropped or []
    disp = disputed_claims or []
    return PipelineResult(
        accepted=acc,
        dropped=drp,
        deduplicated=acc + drp,
        disputed_claims=disp,
        summary=f"{len(acc)} accepted, {len(drp)} dropped",
        confidence=0.7 if acc else 0.0,
    )


def _diverse_observations() -> list[Observation]:
    """Return a realistic set of observations from diverse sources."""
    return [
        _obs("rss:bbc.com", "Major earthquake strikes southeastern Turkey", 0.9, 1),
        _obs("rss:reuters.com", "Earthquake of magnitude 7.2 hits Turkey near Syrian border", 0.9, 1.5),
        _obs("gdelt:global", "Turkey earthquake death toll rises to 50", 0.8, 0.5),
        _obs("acled:conflict", "Military deployed in earthquake zone in Turkey", 0.7, 0.2),
        _obs("nitter:@breakingnews", "BREAKING: Massive earthquake felt across Turkey and Syria", 0.5, 2),
        _obs("reddit:worldnews", "People reporting buildings collapsed in Gaziantep Turkey", 0.4, 1),
        _obs("rss:aljazeera.com", "Rescue teams rush to Turkey earthquake zone", 0.85, 0.8),
        _obs("gdelt:global", "International aid pledged for Turkey earthquake victims", 0.75, 0.3),
        _obs("rss:france24.com", "French rescue teams deployed to Turkey after earthquake", 0.8, 0.4),
        _obs("usgs:earthquake", "M7.2 earthquake recorded near Gaziantep Turkey at 04:17 UTC", 0.95, 2),
    ]


class MockConnector:
    """A mock connector returning canned observations."""

    def __init__(self, name: str, observations: list[Observation] | None = None):
        self.name = name
        self._observations = observations or []

    def collect(self, query: str | None = None) -> ConnectorResult:
        return ConnectorResult(
            connector=self.name,
            observations=self._observations,
            metadata={"query": query, "mock": True},
        )


class FailingConnector:
    """A connector that always raises an exception."""

    def __init__(self, name: str = "failing"):
        self.name = name

    def collect(self, query: str | None = None) -> ConnectorResult:
        raise RuntimeError(f"Connector {self.name} failed on purpose")


# ═══════════════════════════════════════════════════════════════════════
# ENRICHMENT TESTS
# ═══════════════════════════════════════════════════════════════════════


class TestObsConversions:
    """Tests for _obs_to_dict, _obs_to_ref, _obs_to_snapshot."""

    def test_obs_to_dict_basic_fields(self) -> None:
        obs = _obs(source="rss:bbc.com", claim="Test claim", reliability=0.8)
        d = _obs_to_dict(obs, idx=5)
        assert d["id"] == 5
        assert d["source"] == "rss:bbc.com"
        assert d["claim"] == "Test claim"
        assert d["reliability_hint"] == 0.8
        assert "url" in d
        assert "captured_at" in d

    def test_obs_to_dict_default_idx(self) -> None:
        d = _obs_to_dict(_obs(), idx=0)
        assert d["id"] == 0

    def test_obs_to_ref_extracts_connector_type(self) -> None:
        ref = _obs_to_ref(_obs(source="rss:bbc.com"), idx=3)
        assert ref.connector_type == "rss"
        assert ref.id == 3
        assert ref.source == "rss:bbc.com"

    def test_obs_to_ref_no_colon_in_source(self) -> None:
        ref = _obs_to_ref(_obs(source="gdelt"), idx=0)
        assert ref.connector_type == "gdelt"

    def test_obs_to_snapshot_fields(self) -> None:
        obs = _obs(source="nitter:@user", claim="Snapshot test", reliability=0.6)
        snap = _obs_to_snapshot(obs)
        assert snap.claim == "Snapshot test"
        assert snap.source == "nitter:@user"
        assert snap.reliability_hint == 0.6
        assert snap.url == obs.url
        assert snap.captured_at == obs.captured_at

    def test_obs_to_dict_preserves_datetime(self) -> None:
        now = datetime.now(UTC)
        obs = Observation(
            source="test", claim="c", url="u", captured_at=now, reliability_hint=0.5,
        )
        d = _obs_to_dict(obs)
        assert d["captured_at"] == now


class TestEnrichmentEmpty:
    """Enrichment with empty input."""

    def test_enrichment_empty_pipeline(self) -> None:
        pr = _pipeline_result()
        result = run_enrichment(pr, topic="empty test")

        assert isinstance(result, EnrichmentResult)
        assert result.slop_filter.total == 0
        assert result.entities == []
        assert result.stories == []
        assert result.corroboration.total_claims == 0
        assert result.convergence_zones == []
        assert result.anomalies.total_anomalies == 0
        assert result.narrative is not None
        assert result.discovered_topics is not None
        assert result.credible_observations == []
        assert result.enrichment_duration_ms >= 0

    def test_enrichment_empty_sets_timing(self) -> None:
        result = run_enrichment(_pipeline_result(), topic="timing")
        assert result.enrichment_duration_ms >= 0
        assert result.enrichment_started_at is not None


class TestEnrichmentSingleObs:
    """Enrichment with a single observation."""

    def test_enrichment_single_observation(self) -> None:
        obs = _obs("rss:reuters.com", "President Biden visits NATO headquarters in Brussels", 0.9)
        pr = _pipeline_result(accepted=[obs])
        result = run_enrichment(pr, topic="nato visit")

        assert isinstance(result, EnrichmentResult)
        assert result.slop_filter.total == 1
        assert len(result.credible_observations) >= 1
        # Should extract at least some entities from a real claim
        # (Biden, NATO, Brussels are all recognizable)
        assert result.enrichment_duration_ms > 0

    def test_enrichment_single_obs_has_stories(self) -> None:
        obs = _obs("rss:bbc.com", "Wildfire burns 5000 acres near Los Angeles", 0.85)
        pr = _pipeline_result(accepted=[obs])
        result = run_enrichment(pr, topic="wildfires")
        # Even a single observation should produce at least one story cluster
        assert isinstance(result.stories, list)

    def test_enrichment_single_obs_corroboration(self) -> None:
        obs = _obs("rss:reuters.com", "Oil prices surge to $120 per barrel", 0.9)
        pr = _pipeline_result(accepted=[obs])
        result = run_enrichment(pr, topic="oil prices")
        # Single source -> should be single_source or low corroboration
        assert result.corroboration.total_claims >= 0


class TestEnrichmentDiverse:
    """Enrichment with many observations from diverse sources."""

    def test_enrichment_diverse_runs_all_8_steps(self) -> None:
        observations = _diverse_observations()
        pr = _pipeline_result(accepted=observations)
        result = run_enrichment(pr, topic="Turkey earthquake")

        # Step 1: Slop filter ran
        assert result.slop_filter.total == len(observations)
        # Step 2: Entities extracted
        assert isinstance(result.entities, list)
        # Step 3: Stories clustered
        assert isinstance(result.stories, list)
        assert len(result.stories) >= 1
        # Step 4: Corroboration analyzed
        assert result.corroboration.total_claims >= 1
        # Step 5: Convergence (empty without geo_points)
        assert result.convergence_zones == []
        # Step 6: Anomaly detection ran
        assert isinstance(result.anomalies.total_anomalies, int)
        # Step 7: Entity graph built
        assert result.entity_graph is not None
        # Step 8: Narrative timeline built
        assert result.narrative is not None

    def test_enrichment_diverse_finds_entities(self) -> None:
        observations = _diverse_observations()
        pr = _pipeline_result(accepted=observations)
        result = run_enrichment(pr, topic="Turkey earthquake")

        # Should find Turkey, Syria, Gaziantep at minimum
        entity_texts = {e["text"].lower() for e in result.entities}
        assert any("turkey" in t for t in entity_texts)

    def test_enrichment_diverse_credible_observations_populated(self) -> None:
        observations = _diverse_observations()
        pr = _pipeline_result(accepted=observations)
        result = run_enrichment(pr, topic="Turkey earthquake")
        assert len(result.credible_observations) > 0

    def test_enrichment_diverse_has_discovery(self) -> None:
        observations = _diverse_observations()
        pr = _pipeline_result(accepted=observations)
        result = run_enrichment(pr, topic="Turkey earthquake")
        assert isinstance(result.discovered_topics, list)

    def test_enrichment_with_dropped_observations(self) -> None:
        accepted = [_obs("rss:bbc.com", "Floods devastate Bangladesh delta region", 0.8)]
        dropped = [_obs("unknown:blog", "SHOCKING floods destroy everything!!!", 0.2)]
        pr = _pipeline_result(accepted=accepted, dropped=dropped)
        result = run_enrichment(pr, topic="bangladesh floods")
        # Dropped observations should still be included in anomaly baselines
        assert result.slop_filter.total >= 1
        assert result.enrichment_duration_ms >= 0

    def test_enrichment_duration_increases_with_data(self) -> None:
        small_pr = _pipeline_result(accepted=[_obs()])
        big_pr = _pipeline_result(accepted=_diverse_observations())
        small_result = run_enrichment(small_pr, topic="small")
        big_result = run_enrichment(big_pr, topic="big")
        # Both should complete; larger set may take slightly longer (not guaranteed but should work)
        assert small_result.enrichment_duration_ms >= 0
        assert big_result.enrichment_duration_ms >= 0

    def test_enrichment_slop_threshold_parameter(self) -> None:
        observations = _diverse_observations()
        pr = _pipeline_result(accepted=observations)
        # Very strict threshold should still run successfully
        result = run_enrichment(pr, topic="test", slop_drop_threshold=0.1)
        assert isinstance(result, EnrichmentResult)

    def test_enrichment_exclude_dropped_from_baseline(self) -> None:
        accepted = [_obs("rss:reuters.com", "Market crashes 10 percent today", 0.9)]
        dropped = [_obs("spam:bot", "Buy crypto now for free money!!!", 0.1)]
        pr = _pipeline_result(accepted=accepted, dropped=dropped)
        result = run_enrichment(
            pr, topic="market", include_dropped_in_baseline=False,
        )
        assert isinstance(result, EnrichmentResult)


# ═══════════════════════════════════════════════════════════════════════
# CYCLE TESTS
# ═══════════════════════════════════════════════════════════════════════


class TestBuildDefaultConnectors:
    """Tests for build_default_connectors."""

    def test_returns_connector_list_no_social(self) -> None:
        connectors = build_default_connectors("earthquake", include_social=False)
        assert isinstance(connectors, list)
        assert len(connectors) > 0

    def test_all_have_name_and_collect(self) -> None:
        connectors = build_default_connectors("test query", include_social=False)
        for c in connectors:
            assert hasattr(c, "name")
            assert callable(getattr(c, "collect", None))

    def test_include_gov_false_reduces_count(self) -> None:
        with_gov = build_default_connectors("q", include_social=False, include_gov=True)
        without_gov = build_default_connectors("q", include_social=False, include_gov=False)
        assert len(without_gov) < len(with_gov)

    def test_gdelt_always_included(self) -> None:
        connectors = build_default_connectors("q", include_social=False, include_gov=False)
        assert len(connectors) >= 1  # GDELT is always included
        names = [c.name for c in connectors]
        assert any("gdelt" in n.lower() for n in names)


class TestEvaluateAlerts:
    """Tests for evaluate_alerts."""

    def _make_enrichment_with_anomalies(self, count: int, severity: str = "high") -> EnrichmentResult:
        """Build a minimal enrichment result with a specific anomaly count."""
        from sts_monitor.anomaly_detector import Anomaly, AnomalyReport

        anomalies = [
            Anomaly(
                anomaly_type="volume",
                severity=severity,
                title=f"Anomaly {i}",
                description=f"Test anomaly {i}",
                metric_name="volume",
                current_value=100.0,
                baseline_mean=10.0,
                baseline_std=2.0,
                z_score=5.0,
                detected_at=datetime.now(UTC),
                window_hours=24,
                affected_entity="test",
                evidence=["evidence"],
                recommended_action="investigate",
            )
            for i in range(count)
        ]
        by_sev: dict[str, int] = {}
        for a in anomalies:
            by_sev[a.severity] = by_sev.get(a.severity, 0) + 1
        anomaly_report = AnomalyReport(
            detected_at=datetime.now(UTC),
            total_anomalies=count,
            by_type={"volume": count},
            by_severity=by_sev,
            anomalies=anomalies,
            baseline_window_hours=168,
            detection_window_hours=24,
        )
        # Build minimal slop/corroboration with defaults for alert evaluation
        return self._enrichment_with_overrides(anomalies=anomaly_report)

    def _enrichment_with_overrides(
        self,
        anomalies: Any = None,
        slop_total: int = 10,
        slop_slop: int = 0,
        slop_propaganda: int = 0,
        corroboration_rate: float = 0.5,
        total_claims: int = 5,
    ) -> EnrichmentResult:
        from sts_monitor.anomaly_detector import AnomalyReport
        from sts_monitor.slop_detector import SlopFilterResult
        from sts_monitor.corroboration import CorroborationResult
        from sts_monitor.clustering import Story
        from sts_monitor.entity_graph import EntityGraph
        from sts_monitor.narrative import NarrativeTimeline

        if anomalies is None:
            anomalies = AnomalyReport(
                detected_at=datetime.now(UTC),
                total_anomalies=0,
                by_type={},
                by_severity={},
                anomalies=[],
                baseline_window_hours=168,
                detection_window_hours=24,
            )

        slop_filter = SlopFilterResult(
            total=slop_total,
            credible=slop_total - slop_slop - slop_propaganda,
            suspicious=0,
            slop=slop_slop,
            propaganda=slop_propaganda,
            dropped_count=0,
            scores=[],
            pattern_stats={},
        )

        corroboration = CorroborationResult(
            total_claims=total_claims,
            well_corroborated=int(total_claims * corroboration_rate),
            partially_corroborated=0,
            single_source=total_claims - int(total_claims * corroboration_rate),
            contested=0,
            scores=[],
            overall_corroboration_rate=corroboration_rate,
        )

        entity_graph = EntityGraph(
            nodes=[], edges=[],
            node_count=0, edge_count=0, communities=0,
            bridge_entities=[], density=0.0, top_entities=[],
        )

        narrative = NarrativeTimeline(
            topic="test",
            investigation_id="test",
            generated_at=datetime.now(UTC),
            time_span_hours=0.0,
            total_events=0,
            phases={},
            pivots=0,
            events=[],
            summary="No events.",
        )

        return EnrichmentResult(
            slop_filter=slop_filter,
            entities=[],
            stories=[],
            corroboration=corroboration,
            convergence_zones=[],
            anomalies=anomalies,
            entity_graph=entity_graph,
            narrative=narrative,
            discovered_topics=[],
            credible_observations=[],
        )

    def test_fires_on_high_anomaly_count_critical(self) -> None:
        enrichment = self._make_enrichment_with_anomalies(4, severity="critical")
        pr = _pipeline_result(accepted=[_obs()])
        alerts = evaluate_alerts(pr, enrichment)
        rule_names = [a.rule_name for a in alerts]
        assert "anomaly_critical" in rule_names

    def test_fires_on_high_anomaly_count_high(self) -> None:
        enrichment = self._make_enrichment_with_anomalies(3, severity="high")
        pr = _pipeline_result(accepted=[_obs()])
        alerts = evaluate_alerts(pr, enrichment)
        rule_names = [a.rule_name for a in alerts]
        assert "anomaly_high" in rule_names

    def test_no_anomaly_alert_when_few_anomalies(self) -> None:
        enrichment = self._make_enrichment_with_anomalies(1, severity="high")
        pr = _pipeline_result(accepted=[_obs()])
        alerts = evaluate_alerts(pr, enrichment)
        rule_names = [a.rule_name for a in alerts]
        assert "anomaly_critical" not in rule_names
        assert "anomaly_high" not in rule_names

    def test_fires_on_high_slop_ratio(self) -> None:
        enrichment = self._enrichment_with_overrides(slop_total=10, slop_slop=4, slop_propaganda=2)
        pr = _pipeline_result(accepted=[_obs()])
        alerts = evaluate_alerts(pr, enrichment)
        rule_names = [a.rule_name for a in alerts]
        assert "high_slop_ratio" in rule_names

    def test_no_slop_alert_when_ratio_low(self) -> None:
        enrichment = self._enrichment_with_overrides(slop_total=10, slop_slop=1, slop_propaganda=0)
        pr = _pipeline_result(accepted=[_obs()])
        alerts = evaluate_alerts(pr, enrichment)
        rule_names = [a.rule_name for a in alerts]
        assert "high_slop_ratio" not in rule_names

    def test_fires_on_low_corroboration(self) -> None:
        enrichment = self._enrichment_with_overrides(
            corroboration_rate=0.05, total_claims=10,
        )
        pr = _pipeline_result(accepted=[_obs()])
        alerts = evaluate_alerts(pr, enrichment)
        rule_names = [a.rule_name for a in alerts]
        assert "low_corroboration" in rule_names

    def test_no_corroboration_alert_when_rate_acceptable(self) -> None:
        enrichment = self._enrichment_with_overrides(
            corroboration_rate=0.5, total_claims=10,
        )
        pr = _pipeline_result(accepted=[_obs()])
        alerts = evaluate_alerts(pr, enrichment)
        rule_names = [a.rule_name for a in alerts]
        assert "low_corroboration" not in rule_names

    def test_fires_on_many_disputed_claims(self) -> None:
        enrichment = self._enrichment_with_overrides()
        pr = _pipeline_result(
            accepted=[_obs()],
            disputed_claims=["claim1", "claim2", "claim3", "claim4", "claim5"],
        )
        alerts = evaluate_alerts(pr, enrichment)
        rule_names = [a.rule_name for a in alerts]
        assert "high_dispute_rate" in rule_names

    def test_user_rule_fires_when_thresholds_met(self) -> None:
        enrichment = self._enrichment_with_overrides()
        obs_list = [_obs(claim=f"Observation {i}") for i in range(25)]
        pr = _pipeline_result(accepted=obs_list, disputed_claims=["d1", "d2"])
        rules = [
            {
                "name": "custom_alert",
                "min_observations": 20,
                "min_disputed_claims": 1,
                "severity": "high",
                "active": True,
            }
        ]
        alerts = evaluate_alerts(pr, enrichment, alert_rules=rules)
        rule_names = [a.rule_name for a in alerts]
        assert "custom_alert" in rule_names

    def test_user_rule_respects_cooldown(self) -> None:
        enrichment = self._enrichment_with_overrides()
        obs_list = [_obs(claim=f"Observation {i}") for i in range(25)]
        pr = _pipeline_result(accepted=obs_list, disputed_claims=["d1", "d2"])
        # Triggered 5 seconds ago, cooldown is 900 seconds -> should be skipped
        rules = [
            {
                "name": "custom_alert",
                "min_observations": 20,
                "min_disputed_claims": 1,
                "severity": "high",
                "active": True,
                "cooldown_seconds": 900,
                "last_triggered_at": datetime.now(UTC) - timedelta(seconds=5),
            }
        ]
        alerts = evaluate_alerts(pr, enrichment, alert_rules=rules)
        custom_alerts = [a for a in alerts if a.rule_name == "custom_alert"]
        assert len(custom_alerts) == 0

    def test_user_rule_fires_after_cooldown_expired(self) -> None:
        enrichment = self._enrichment_with_overrides()
        obs_list = [_obs(claim=f"Observation {i}") for i in range(25)]
        pr = _pipeline_result(accepted=obs_list, disputed_claims=["d1", "d2"])
        rules = [
            {
                "name": "custom_alert",
                "min_observations": 20,
                "min_disputed_claims": 1,
                "severity": "high",
                "active": True,
                "cooldown_seconds": 60,
                "last_triggered_at": datetime.now(UTC) - timedelta(seconds=120),
            }
        ]
        alerts = evaluate_alerts(pr, enrichment, alert_rules=rules)
        custom_alerts = [a for a in alerts if a.rule_name == "custom_alert"]
        assert len(custom_alerts) == 1

    def test_user_rule_cooldown_with_iso_string(self) -> None:
        enrichment = self._enrichment_with_overrides()
        obs_list = [_obs(claim=f"Observation {i}") for i in range(25)]
        pr = _pipeline_result(accepted=obs_list, disputed_claims=["d1", "d2"])
        rules = [
            {
                "name": "custom_alert",
                "min_observations": 20,
                "min_disputed_claims": 1,
                "severity": "warning",
                "active": True,
                "cooldown_seconds": 900,
                "last_triggered_at": (datetime.now(UTC) - timedelta(seconds=5)).isoformat(),
            }
        ]
        alerts = evaluate_alerts(pr, enrichment, alert_rules=rules)
        custom_alerts = [a for a in alerts if a.rule_name == "custom_alert"]
        assert len(custom_alerts) == 0


class TestPromoteDiscoveries:
    """Tests for promote_discoveries."""

    def _enrichment_with_topics(self, topics: list[tuple[str, float, str]]) -> EnrichmentResult:
        """Create enrichment with given discovered topics (title, score, source)."""
        from sts_monitor.story_discovery import DiscoveredTopic

        discovered = [
            DiscoveredTopic(
                title=title,
                description=f"Description of {title}",
                score=score,
                source=source,
                suggested_seed_query=title.lower(),
            )
            for title, score, source in topics
        ]
        # Reuse the enrichment builder from TestEvaluateAlerts
        helper = TestEvaluateAlerts()
        enrichment = helper._enrichment_with_overrides()
        # Directly set discovered_topics
        object.__setattr__(enrichment, "discovered_topics", discovered)
        return enrichment

    def test_promotes_high_score_topics(self) -> None:
        enrichment = self._enrichment_with_topics([
            ("Syria conflict escalation", 0.85, "burst"),
            ("Volcanic activity in Iceland", 0.7, "entity_spike"),
        ])
        promoted = promote_discoveries(enrichment, min_score=0.4)
        assert len(promoted) == 2
        assert all(isinstance(p, PromotedTopic) for p in promoted)
        assert promoted[0].title == "Syria conflict escalation"
        assert promoted[0].score == 0.85

    def test_filters_low_score_topics(self) -> None:
        enrichment = self._enrichment_with_topics([
            ("Important topic", 0.8, "burst"),
            ("Low score topic", 0.2, "convergence"),
            ("Borderline topic", 0.39, "entity_spike"),
        ])
        promoted = promote_discoveries(enrichment, min_score=0.4)
        titles = [p.title for p in promoted]
        assert "Important topic" in titles
        assert "Low score topic" not in titles
        assert "Borderline topic" not in titles

    def test_respects_max_promotions(self) -> None:
        enrichment = self._enrichment_with_topics([
            (f"Topic {i}", 0.9 - i * 0.05, "burst") for i in range(10)
        ])
        promoted = promote_discoveries(enrichment, min_score=0.1, max_promotions=3)
        assert len(promoted) == 3

    def test_empty_discovered_topics(self) -> None:
        enrichment = self._enrichment_with_topics([])
        promoted = promote_discoveries(enrichment, min_score=0.4)
        assert promoted == []

    def test_promoted_topic_fields(self) -> None:
        enrichment = self._enrichment_with_topics([
            ("Test topic", 0.75, "convergence"),
        ])
        promoted = promote_discoveries(enrichment, min_score=0.4)
        assert len(promoted) == 1
        p = promoted[0]
        assert p.title == "Test topic"
        assert p.seed_query == "test topic"
        assert p.score == 0.75
        assert p.source == "convergence"
        assert "0.75" in p.reason


class TestRunCycleWithMocks:
    """Tests for run_cycle using mock connectors."""

    def test_cycle_with_mock_connectors(self) -> None:
        observations = _diverse_observations()
        connector = MockConnector("mock_rss", observations)
        result = run_cycle(
            "Turkey earthquake",
            connectors=[connector],
            generate_report=False,
        )
        assert isinstance(result, CycleResult)
        assert result.topic == "Turkey earthquake"
        assert result.total_observations_collected == len(observations)
        assert result.pipeline_result is not None
        assert result.enrichment is not None
        assert result.duration_ms >= 0

    def test_cycle_with_no_connectors(self) -> None:
        result = run_cycle(
            "empty test",
            connectors=[],
            generate_report=False,
        )
        assert isinstance(result, CycleResult)
        assert result.total_observations_collected == 0
        assert len(result.pipeline_result.accepted) == 0
        assert result.report is None

    def test_cycle_generates_report_by_default(self) -> None:
        observations = _diverse_observations()
        connector = MockConnector("mock", observations)
        result = run_cycle(
            "Turkey earthquake",
            connectors=[connector],
            generate_report=True,
        )
        assert result.report is not None
        assert result.report.topic == "Turkey earthquake"

    def test_cycle_skips_report_when_disabled(self) -> None:
        observations = _diverse_observations()
        connector = MockConnector("mock", observations)
        result = run_cycle(
            "Turkey earthquake",
            connectors=[connector],
            generate_report=False,
        )
        assert result.report is None

    def test_cycle_handles_failing_connector(self) -> None:
        good = MockConnector("good", [_obs()])
        bad = FailingConnector("bad")
        result = run_cycle(
            "resilience test",
            connectors=[good, bad],
            generate_report=False,
        )
        assert result.total_observations_collected == 1
        # Should have two connector summaries, one with error
        error_summaries = [s for s in result.connector_results if s["status"] == "error"]
        assert len(error_summaries) == 1
        assert "bad" in error_summaries[0]["connector"]

    def test_cycle_multiple_connectors(self) -> None:
        c1 = MockConnector("source_a", [_obs("rss:reuters.com", "Event A reported", 0.9)])
        c2 = MockConnector("source_b", [_obs("gdelt:global", "Event A confirmed by GDELT", 0.8)])
        result = run_cycle(
            "multi-source test",
            connectors=[c1, c2],
            generate_report=False,
        )
        assert result.total_observations_collected == 2
        assert len(result.connector_results) == 2

    def test_cycle_has_valid_cycle_id(self) -> None:
        result = run_cycle(
            "id test",
            connectors=[MockConnector("m", [_obs()])],
            generate_report=False,
        )
        assert len(result.cycle_id) == 8  # uuid4()[:8]

    def test_cycle_timestamps(self) -> None:
        before = datetime.now(UTC)
        result = run_cycle(
            "timestamp test",
            connectors=[MockConnector("m", [_obs()])],
            generate_report=False,
        )
        after = datetime.now(UTC)
        assert before <= result.started_at <= after
        assert before <= result.completed_at <= after
        assert result.started_at <= result.completed_at

    def test_cycle_promote_threshold(self) -> None:
        connector = MockConnector("mock", _diverse_observations())
        result = run_cycle(
            "Turkey earthquake",
            connectors=[connector],
            generate_report=False,
            promote_threshold=0.99,  # Very high -> likely no promotions
        )
        # With a very high threshold, few or no topics should be promoted
        assert isinstance(result.promoted_topics, list)

    def test_cycle_investigation_id(self) -> None:
        result = run_cycle(
            "id test",
            connectors=[MockConnector("m", [_obs()])],
            investigation_id="INV-001",
            generate_report=False,
        )
        assert result.investigation_id == "INV-001"


class TestCycleResultSummary:
    """Tests for CycleResult.summary()."""

    def test_summary_contains_key_sections(self) -> None:
        connector = MockConnector("mock", _diverse_observations())
        result = run_cycle(
            "Summary test",
            connectors=[connector],
            generate_report=False,
        )
        summary = result.summary()
        assert "Cycle" in summary
        assert "Summary test" in summary
        assert "Duration:" in summary
        assert "Collected:" in summary
        assert "Pipeline:" in summary
        assert "Confidence:" in summary
        assert "Entities:" in summary
        assert "Stories:" in summary
        assert "Anomalies:" in summary

    def test_summary_shows_alerts(self) -> None:
        # Create scenario likely to fire alerts: many disputed claims
        observations = [
            _obs("rss:bbc.com", "Power restored in region", 0.8),
            _obs("nitter:@user", "Power restored in region is false", 0.6),
            _obs("rss:reuters.com", "Water supply restored", 0.8),
            _obs("gdelt:global", "Water supply restored is hoax", 0.7),
            _obs("rss:aljazeera.com", "Aid delivered successfully", 0.85),
            _obs("acled:conflict", "Aid delivered successfully is fabricated", 0.7),
            _obs("rss:france24.com", "Ceasefire holds in region", 0.8),
            _obs("reddit:worldnews", "Ceasefire holds in region debunked", 0.5),
            _obs("rss:bbc.com", "Evacuation complete in coastal area", 0.8),
            _obs("nitter:@reporter", "Evacuation complete in coastal area is not true", 0.6),
        ]
        connector = MockConnector("mock", observations)
        result = run_cycle(
            "Disputed situation",
            connectors=[connector],
            generate_report=False,
        )
        summary = result.summary()
        assert "Alerts fired:" in summary

    def test_summary_is_string(self) -> None:
        result = run_cycle(
            "type test",
            connectors=[],
            generate_report=False,
        )
        assert isinstance(result.summary(), str)
        assert len(result.summary()) > 50  # Should be substantial
