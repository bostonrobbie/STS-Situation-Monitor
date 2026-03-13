"""Unified cycle orchestrator.

Ties all system components into a single ``run_cycle()`` call:

    connectors → pipeline → enrich → alert → report → discover → promote

This is the missing connective tissue that turns independent modules into
an intelligence production loop.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sts_monitor.connectors.base import Connector, ConnectorResult
from sts_monitor.deep_truth import DeepTruthVerdict, analyze_deep_truth
from sts_monitor.enrichment import EnrichmentResult, run_enrichment
from sts_monitor.pipeline import Observation, PipelineResult, SignalPipeline
from sts_monitor.report_generator import IntelligenceReport, ReportGenerator
from sts_monitor.surge_detector import SurgeAnalysisResult, analyze_surge

logger = logging.getLogger(__name__)


# ── Connector registry ────────────────────────────────────────────────

def build_default_connectors(
    query: str | None = None,
    *,
    include_social: bool = True,
    include_gov: bool = True,
    include_search: bool = False,
    nitter_accounts: list[str] | None = None,
    rss_feeds: list[str] | None = None,
) -> list[Connector]:
    """Build the default connector set based on feature flags.

    Returns instantiated connectors ready for ``collect(query)``.
    """
    from sts_monitor.connectors import (
        GDELTConnector, USGSEarthquakeConnector, NASAFIRMSConnector,
        ACLEDConnector, NWSAlertConnector, FEMADisasterConnector,
        ReliefWebConnector, RSSConnector, RedditConnector,
        NitterConnector, SearchConnector,
    )

    connectors: list[Connector] = []

    # Always include GDELT (global event monitoring)
    connectors.append(GDELTConnector())

    if include_gov:
        connectors.append(USGSEarthquakeConnector())
        connectors.append(NASAFIRMSConnector())
        connectors.append(ACLEDConnector())
        connectors.append(NWSAlertConnector())
        connectors.append(FEMADisasterConnector())
        connectors.append(ReliefWebConnector())

    if rss_feeds:
        connectors.append(RSSConnector(feeds=rss_feeds))

    if include_social:
        connectors.append(RedditConnector())
        accounts = nitter_accounts or []
        connectors.append(NitterConnector(accounts=accounts))

    if include_search:
        connectors.append(SearchConnector())

    return connectors


# ── Alert evaluation ──────────────────────────────────────────────────

@dataclass(slots=True)
class AlertFired:
    """Record of an alert that fired during a cycle."""
    rule_name: str
    severity: str
    message: str
    triggered_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def evaluate_alerts(
    pipeline_result: PipelineResult,
    enrichment: EnrichmentResult,
    alert_rules: list[dict[str, Any]] | None = None,
) -> list[AlertFired]:
    """Evaluate alert rules against cycle results.

    Parameters
    ----------
    alert_rules : list[dict]
        Each rule: {name, min_observations, min_disputed_claims, severity,
                     cooldown_seconds, last_triggered_at, ...}
        If None, uses built-in heuristic alerts.
    """
    fired: list[AlertFired] = []
    now = datetime.now(UTC)

    # ── Built-in heuristic alerts (always active) ─────────────────
    # High anomaly count
    if enrichment.anomalies.total_anomalies >= 3:
        critical = enrichment.anomalies.by_severity.get("critical", 0)
        high = enrichment.anomalies.by_severity.get("high", 0)
        if critical > 0:
            fired.append(AlertFired(
                rule_name="anomaly_critical",
                severity="critical",
                message=f"{critical} critical anomalies detected: "
                        + "; ".join(a.title for a in enrichment.anomalies.anomalies if a.severity == "critical"),
            ))
        elif high > 0:
            fired.append(AlertFired(
                rule_name="anomaly_high",
                severity="high",
                message=f"{high} high-severity anomalies detected",
            ))

    # High propaganda/slop ratio
    if enrichment.slop_filter.total > 0:
        slop_ratio = (enrichment.slop_filter.slop + enrichment.slop_filter.propaganda) / enrichment.slop_filter.total
        if slop_ratio >= 0.5:
            fired.append(AlertFired(
                rule_name="high_slop_ratio",
                severity="warning",
                message=f"{slop_ratio:.0%} of observations flagged as slop/propaganda — possible coordinated campaign",
            ))

    # Low corroboration
    if enrichment.corroboration.total_claims > 0 and enrichment.corroboration.overall_corroboration_rate < 0.1:
        fired.append(AlertFired(
            rule_name="low_corroboration",
            severity="warning",
            message=f"Only {enrichment.corroboration.overall_corroboration_rate:.0%} of claims are well-corroborated",
        ))

    # Many disputed claims
    if len(pipeline_result.disputed_claims) >= 5:
        fired.append(AlertFired(
            rule_name="high_dispute_rate",
            severity="warning",
            message=f"{len(pipeline_result.disputed_claims)} disputed claim clusters detected",
        ))

    # Convergence zone with high severity
    for zone in enrichment.convergence_zones:
        if zone.severity in ("high", "critical"):
            fired.append(AlertFired(
                rule_name="convergence_zone",
                severity=zone.severity,
                message=f"Convergence zone: {', '.join(zone.signal_types)} near "
                        f"({zone.center_lat:.2f}, {zone.center_lon:.2f})",
            ))

    # ── User-defined alert rules ──────────────────────────────────
    if alert_rules:
        for rule in alert_rules:
            if not rule.get("active", True):
                continue
            # Check cooldown
            last_triggered = rule.get("last_triggered_at")
            cooldown = rule.get("cooldown_seconds", 900)
            if last_triggered:
                if isinstance(last_triggered, str):
                    try:
                        last_triggered = datetime.fromisoformat(last_triggered)
                    except (ValueError, TypeError):
                        last_triggered = None
                if last_triggered and (now - last_triggered).total_seconds() < cooldown:
                    continue

            # Evaluate thresholds
            min_obs = rule.get("min_observations", 20)
            min_disputed = rule.get("min_disputed_claims", 1)

            if (len(pipeline_result.accepted) >= min_obs
                    and len(pipeline_result.disputed_claims) >= min_disputed):
                fired.append(AlertFired(
                    rule_name=rule.get("name", "custom_rule"),
                    severity=rule.get("severity", "warning"),
                    message=f"Rule '{rule.get('name', 'custom')}' triggered: "
                            f"{len(pipeline_result.accepted)} observations, "
                            f"{len(pipeline_result.disputed_claims)} disputed claims",
                ))

    return fired


# ── Story → Investigation promotion ───────────────────────────────────

@dataclass(slots=True)
class PromotedTopic:
    """A discovered topic recommended for investigation."""
    title: str
    seed_query: str
    score: float
    source: str  # burst, convergence, entity_spike
    reason: str


def promote_discoveries(
    enrichment: EnrichmentResult,
    *,
    min_score: float = 0.4,
    max_promotions: int = 5,
) -> list[PromotedTopic]:
    """Identify discovered topics worth promoting to investigations."""
    promoted: list[PromotedTopic] = []

    for topic in enrichment.discovered_topics:
        if topic.score < min_score:
            continue
        promoted.append(PromotedTopic(
            title=topic.title,
            seed_query=topic.suggested_seed_query,
            score=topic.score,
            source=topic.source,
            reason=f"Score {topic.score:.2f} from {topic.source}: {topic.description[:200]}",
        ))
        if len(promoted) >= max_promotions:
            break

    return promoted


# ── Cycle result ──────────────────────────────────────────────────────

@dataclass(slots=True)
class CycleResult:
    """Complete output of one intelligence cycle."""
    # Identification
    cycle_id: str
    topic: str
    investigation_id: str
    started_at: datetime
    completed_at: datetime
    duration_ms: float

    # Ingestion
    connector_results: list[dict[str, Any]]
    total_observations_collected: int

    # Pipeline
    pipeline_result: PipelineResult

    # Enrichment
    enrichment: EnrichmentResult

    # Alerts
    alerts_fired: list[AlertFired]

    # Report
    report: IntelligenceReport | None

    # Surge intelligence (social media analysis)
    surge_analysis: SurgeAnalysisResult | None

    # Deep Truth forensic analysis
    deep_truth_verdict: DeepTruthVerdict | None

    # Discovery & promotion
    promoted_topics: list[PromotedTopic]

    def summary(self) -> str:
        """Human-readable cycle summary."""
        lines = [
            f"=== Cycle {self.cycle_id} ===",
            f"Topic: {self.topic}",
            f"Duration: {self.duration_ms:.0f}ms",
            f"Collected: {self.total_observations_collected} observations from {len(self.connector_results)} connectors",
            f"Pipeline: {len(self.pipeline_result.accepted)} accepted, {len(self.pipeline_result.dropped)} dropped, "
            f"{len(self.pipeline_result.disputed_claims)} disputed",
            f"Confidence: {self.pipeline_result.confidence:.1%}",
            f"Slop filter: {self.enrichment.slop_filter.credible} credible, "
            f"{self.enrichment.slop_filter.slop} slop, {self.enrichment.slop_filter.propaganda} propaganda",
            f"Entities: {len(self.enrichment.entities)}",
            f"Stories: {len(self.enrichment.stories)}",
            f"Corroboration: {self.enrichment.corroboration.well_corroborated} well-corroborated / "
            f"{self.enrichment.corroboration.total_claims} claims",
            f"Anomalies: {self.enrichment.anomalies.total_anomalies}",
            f"Convergence zones: {len(self.enrichment.convergence_zones)}",
            f"Alerts fired: {len(self.alerts_fired)}",
            f"Topics promoted: {len(self.promoted_topics)}",
        ]
        if self.surge_analysis and self.surge_analysis.total_processed > 0:
            sa = self.surge_analysis
            lines.append(f"Surge analysis: {sa.total_processed} social posts — "
                         f"{sa.alpha_count} alpha, {sa.noise_count} noise, {sa.disinfo_count} disinfo")
            if sa.surge_detected:
                lines.append(f"  SURGE DETECTED: {len(sa.surges)} surge event(s)")
        if self.deep_truth_verdict:
            dt = self.deep_truth_verdict
            lines.append(f"Deep Truth: authority={dt.authority_weight.score:.2f} "
                         f"({dt.authority_weight.skepticism_level}), "
                         f"provenance={dt.provenance.entropy_bits:.1f} bits")
            if dt.manufactured_consensus_detected:
                lines.append("  *** MANUFACTURED CONSENSUS INDICATORS ***")
            if dt.active_suppression_detected:
                lines.append("  *** ACTIVE SUPPRESSION INDICATORS ***")
        if self.report:
            lines.append(f"Report: {self.report.generation_method} ({self.report.generation_time_ms:.0f}ms)")
        for alert in self.alerts_fired:
            lines.append(f"  ALERT [{alert.severity}] {alert.rule_name}: {alert.message}")
        for promo in self.promoted_topics:
            lines.append(f"  PROMOTE [{promo.source}] {promo.title} (score: {promo.score:.2f})")
        return "\n".join(lines)


# ── Main cycle runner ─────────────────────────────────────────────────

def run_cycle(
    topic: str,
    *,
    investigation_id: str = "",
    connectors: list[Connector] | None = None,
    query: str | None = None,
    pipeline: SignalPipeline | None = None,
    report_generator: ReportGenerator | None = None,
    alert_rules: list[dict[str, Any]] | None = None,
    generate_report: bool = True,
    promote_threshold: float = 0.4,
    run_surge_analysis: bool = True,
    run_deep_truth: bool = True,
) -> CycleResult:
    """Run one complete intelligence cycle.

    connectors → pipeline → enrich → alert → report → discover → promote

    Parameters
    ----------
    topic : str
        The topic/situation to monitor.
    investigation_id : str
        Optional investigation ID for tracking.
    connectors : list[Connector]
        Data source connectors. Defaults to build_default_connectors().
    query : str | None
        Search query passed to connectors. Defaults to topic.
    pipeline : SignalPipeline | None
        Pipeline instance. Defaults to SignalPipeline().
    report_generator : ReportGenerator | None
        Report generator. Defaults to ReportGenerator() (deterministic).
    alert_rules : list[dict] | None
        User-defined alert rules.
    generate_report : bool
        Whether to generate an intelligence report.
    promote_threshold : float
        Minimum score for topic promotion.
    """
    from uuid import uuid4

    cycle_id = str(uuid4())[:8]
    started = datetime.now(UTC)
    t0 = time.perf_counter()
    search_query = query or topic

    logger.info("Cycle %s started: topic=%r query=%r", cycle_id, topic, search_query)

    # ── 1. Ingest from connectors ─────────────────────────────────
    if connectors is None:
        connectors = build_default_connectors(search_query)

    all_observations: list[Observation] = []
    connector_summaries: list[dict[str, Any]] = []

    for connector in connectors:
        try:
            result: ConnectorResult = connector.collect(search_query)
            all_observations.extend(result.observations)
            connector_summaries.append({
                "connector": result.connector,
                "observations": len(result.observations),
                "status": "ok",
                "metadata": result.metadata,
            })
            logger.info("  Connector %s: %d observations", result.connector, len(result.observations))
        except Exception as exc:
            connector_summaries.append({
                "connector": getattr(connector, "name", "unknown"),
                "observations": 0,
                "status": "error",
                "error": str(exc),
            })
            logger.warning("  Connector %s failed: %s", getattr(connector, "name", "unknown"), exc)

    total_collected = len(all_observations)
    logger.info("Cycle %s: collected %d observations from %d connectors",
                cycle_id, total_collected, len(connectors))

    # ── 2. Signal pipeline ────────────────────────────────────────
    pipe = pipeline or SignalPipeline()
    pipeline_result = pipe.run(all_observations, topic)
    logger.info("Cycle %s: pipeline accepted %d, dropped %d, disputed %d",
                cycle_id, len(pipeline_result.accepted), len(pipeline_result.dropped),
                len(pipeline_result.disputed_claims))

    # ── 3. Enrichment chain ───────────────────────────────────────
    enrichment = run_enrichment(
        pipeline_result, topic=topic, investigation_id=investigation_id,
    )

    # ── 4. Alert evaluation ───────────────────────────────────────
    alerts = evaluate_alerts(pipeline_result, enrichment, alert_rules)
    for alert in alerts:
        logger.info("Cycle %s ALERT [%s] %s: %s", cycle_id, alert.severity, alert.rule_name, alert.message)

    # ── 5. Surge analysis on social media observations ──────────
    surge_result: SurgeAnalysisResult | None = None
    if run_surge_analysis:
        social_connectors = {"nitter", "reddit"}
        social_obs = [
            {"source": o.source, "claim": o.claim, "url": o.url,
             "captured_at": o.captured_at, "reliability_hint": o.reliability_hint}
            for o in pipeline_result.accepted
            if o.source.split(":")[0] in social_connectors
        ]
        if social_obs:
            surge_result = analyze_surge(social_obs, topic=topic)
            logger.info("Cycle %s: surge analysis — %d processed, %d alpha, %d disinfo",
                        cycle_id, surge_result.total_processed,
                        surge_result.alpha_count, surge_result.disinfo_count)

    # ── 6. Deep Truth forensic analysis ───────────────────────────
    deep_truth: DeepTruthVerdict | None = None
    if run_deep_truth and len(pipeline_result.accepted) >= 5:
        obs_dicts = [
            {"source": o.source, "claim": o.claim, "url": o.url,
             "captured_at": o.captured_at, "reliability_hint": o.reliability_hint}
            for o in pipeline_result.accepted
        ]
        deep_truth = analyze_deep_truth(obs_dicts, topic)
        logger.info("Cycle %s: deep truth — authority=%.2f, provenance=%.1f bits, "
                     "manufactured=%s, suppression=%s",
                    cycle_id, deep_truth.authority_weight.score,
                    deep_truth.provenance.entropy_bits,
                    deep_truth.manufactured_consensus_detected,
                    deep_truth.active_suppression_detected)

    # ── 7. Report generation ──────────────────────────────────────
    report: IntelligenceReport | None = None
    if generate_report and pipeline_result.accepted:
        gen = report_generator or ReportGenerator()
        # Convert enrichment products to dict format for report generator
        stories_dicts = [
            {
                "headline": s.headline,
                "observation_count": s.observation_count,
                "source_count": s.source_count,
                "avg_reliability": s.avg_reliability,
            }
            for s in enrichment.stories
        ]
        convergence_dicts = [
            {
                "center_lat": z.center_lat,
                "center_lon": z.center_lon,
                "radius_km": z.radius_km,
                "signal_types": z.signal_types,
                "severity": z.severity,
                "signal_count": z.signal_count,
            }
            for z in enrichment.convergence_zones
        ]
        report = gen.generate(
            investigation_id=investigation_id or cycle_id,
            topic=topic,
            pipeline_result=pipeline_result,
            entities=enrichment.entities,
            stories=stories_dicts,
            convergence_zones=convergence_dicts,
        )

    # ── 8. Story/topic promotion ──────────────────────────────────
    promoted = promote_discoveries(enrichment, min_score=promote_threshold)

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
    completed = datetime.now(UTC)

    logger.info("Cycle %s complete in %.0fms", cycle_id, elapsed_ms)

    return CycleResult(
        cycle_id=cycle_id,
        topic=topic,
        investigation_id=investigation_id,
        started_at=started,
        completed_at=completed,
        duration_ms=elapsed_ms,
        connector_results=connector_summaries,
        total_observations_collected=total_collected,
        pipeline_result=pipeline_result,
        enrichment=enrichment,
        alerts_fired=alerts,
        report=report,
        surge_analysis=surge_result,
        deep_truth_verdict=deep_truth,
        promoted_topics=promoted,
    )
