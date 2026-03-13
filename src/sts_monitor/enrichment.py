"""Unified enrichment chain.

Runs all enrichment processors in the correct order on pipeline output:
1. Slop/quality filtering  (drops garbage before anything else sees it)
2. Entity extraction        (who/what/where)
3. Story clustering         (group related observations)
4. Corroboration analysis   (cross-source verification)
5. Convergence detection    (geographic multi-signal clustering)
6. Anomaly detection        (pattern breaks)
7. Entity graph             (relationship mapping)
8. Narrative timeline       (chronological story arc)

Input:  PipelineResult + raw observation dicts
Output: EnrichmentResult with all analysis products
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sts_monitor.anomaly_detector import AnomalyReport, run_anomaly_detection
from sts_monitor.clustering import ObservationRef, Story, cluster_observations, enrich_stories_with_entities
from sts_monitor.convergence import ConvergenceZone, GeoPoint, detect_convergence
from sts_monitor.corroboration import CorroborationResult, analyze_corroboration
from sts_monitor.entities import extract_entities
from sts_monitor.entity_graph import EntityGraph, build_entity_graph
from sts_monitor.narrative import NarrativeTimeline, build_narrative_timeline
from sts_monitor.pipeline import Observation, PipelineResult
from sts_monitor.slop_detector import SlopFilterResult, filter_slop
from sts_monitor.story_discovery import DiscoveredTopic, ObservationSnapshot, run_discovery

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EnrichmentResult:
    """All enrichment products from a single cycle."""
    # Quality filtering
    slop_filter: SlopFilterResult

    # Entity extraction (flat list of dicts for downstream consumers)
    entities: list[dict[str, Any]]

    # Story clustering
    stories: list[Story]

    # Corroboration
    corroboration: CorroborationResult

    # Geographic convergence
    convergence_zones: list[ConvergenceZone]

    # Anomaly detection
    anomalies: AnomalyReport

    # Entity relationship graph
    entity_graph: EntityGraph

    # Narrative timeline
    narrative: NarrativeTimeline

    # Story/topic discovery
    discovered_topics: list[DiscoveredTopic]

    # Observations after slop filtering (credible only)
    credible_observations: list[dict[str, Any]]

    # Timing
    enrichment_started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    enrichment_duration_ms: float = 0.0


def _obs_to_dict(obs: Observation, idx: int = 0) -> dict[str, Any]:
    """Convert pipeline Observation to dict for enrichment modules."""
    return {
        "id": idx,
        "source": obs.source,
        "claim": obs.claim,
        "url": obs.url,
        "captured_at": obs.captured_at,
        "reliability_hint": obs.reliability_hint,
    }


def _obs_to_ref(obs: Observation, idx: int = 0) -> ObservationRef:
    """Convert pipeline Observation to ObservationRef for clustering."""
    connector_type = obs.source.split(":")[0] if ":" in obs.source else obs.source
    return ObservationRef(
        id=idx,
        source=obs.source,
        claim=obs.claim,
        url=obs.url,
        captured_at=obs.captured_at,
        reliability_hint=obs.reliability_hint,
        connector_type=connector_type,
    )


def _obs_to_snapshot(obs: Observation) -> ObservationSnapshot:
    """Convert pipeline Observation to ObservationSnapshot for discovery."""
    return ObservationSnapshot(
        claim=obs.claim,
        source=obs.source,
        captured_at=obs.captured_at,
        url=obs.url,
        reliability_hint=obs.reliability_hint,
    )


def run_enrichment(
    pipeline_result: PipelineResult,
    topic: str = "",
    investigation_id: str = "",
    *,
    geo_points: list[GeoPoint] | None = None,
    slop_drop_threshold: float = 0.6,
    include_dropped_in_baseline: bool = True,
) -> EnrichmentResult:
    """Run the full enrichment chain on pipeline output.

    Parameters
    ----------
    pipeline_result : PipelineResult
        Output from SignalPipeline.run()
    topic : str
        Topic/investigation name for labeling
    investigation_id : str
        Investigation ID for labeling
    geo_points : list[GeoPoint] | None
        Optional geographic points for convergence detection.
        If None, convergence is skipped.
    slop_drop_threshold : float
        Slop score above which observations are dropped.
    include_dropped_in_baseline : bool
        Whether to include pipeline-dropped observations in anomaly baselines.
    """
    import time
    started = time.perf_counter()

    accepted = pipeline_result.accepted
    dropped = pipeline_result.dropped

    # Convert to dicts for enrichment modules
    obs_dicts = [_obs_to_dict(obs, i) for i, obs in enumerate(accepted)]
    all_obs_dicts = obs_dicts[:]
    if include_dropped_in_baseline:
        all_obs_dicts.extend(
            _obs_to_dict(obs, len(accepted) + i) for i, obs in enumerate(dropped)
        )

    # ── Step 1: Slop filtering ────────────────────────────────────────
    logger.info("Enrichment [1/8]: Slop filtering %d observations", len(obs_dicts))
    slop_result = filter_slop(obs_dicts, drop_threshold=slop_drop_threshold)

    # Build credible-only list (observations not flagged for drop)
    credible_ids = set()
    for score in slop_result.scores:
        if score.recommended_action != "drop":
            credible_ids.add(score.observation_id)
    credible_obs = [d for d in obs_dicts if d.get("id") in credible_ids]
    # If slop filtering drops everything, fall back to full accepted set
    if not credible_obs:
        credible_obs = obs_dicts

    # ── Step 2: Entity extraction ─────────────────────────────────────
    logger.info("Enrichment [2/8]: Entity extraction from %d observations", len(credible_obs))
    all_entities: list[dict[str, Any]] = []
    for obs_d in credible_obs:
        entities = extract_entities(obs_d.get("claim", ""))
        for ent in entities:
            all_entities.append({
                "entity_type": ent.entity_type,
                "text": ent.text,
                "confidence": ent.confidence,
                "source": obs_d.get("source", ""),
                "observation_id": obs_d.get("id"),
            })

    # ── Step 3: Story clustering ──────────────────────────────────────
    logger.info("Enrichment [3/8]: Story clustering")
    obs_refs = [_obs_to_ref(obs, i) for i, obs in enumerate(accepted) if i in credible_ids or not credible_ids]
    if not obs_refs:
        obs_refs = [_obs_to_ref(obs, i) for i, obs in enumerate(accepted)]
    stories = cluster_observations(obs_refs)
    # Enrich stories with entities
    enrich_stories_with_entities(stories, extract_entities)

    # ── Step 4: Corroboration analysis ────────────────────────────────
    logger.info("Enrichment [4/8]: Corroboration analysis")
    corroboration = analyze_corroboration(credible_obs)

    # ── Step 5: Convergence detection ─────────────────────────────────
    logger.info("Enrichment [5/8]: Convergence detection")
    convergence_zones: list[ConvergenceZone] = []
    if geo_points:
        convergence_zones = detect_convergence(geo_points)

    # ── Step 6: Anomaly detection ─────────────────────────────────────
    logger.info("Enrichment [6/8]: Anomaly detection")
    anomalies = run_anomaly_detection(all_obs_dicts)

    # ── Step 7: Entity graph ──────────────────────────────────────────
    logger.info("Enrichment [7/8]: Entity graph construction")
    entity_graph = build_entity_graph(credible_obs)

    # ── Step 8: Narrative timeline ────────────────────────────────────
    logger.info("Enrichment [8/8]: Narrative timeline")
    narrative = build_narrative_timeline(
        credible_obs, topic=topic, investigation_id=investigation_id,
    )

    # ── Story/topic discovery ─────────────────────────────────────────
    snapshots = [_obs_to_snapshot(obs) for obs in accepted]
    convergence_dicts = [
        {
            "center_lat": z.center_lat,
            "center_lon": z.center_lon,
            "radius_km": z.radius_km,
            "signal_types": z.signal_types,
            "severity": z.severity,
            "signal_count": z.signal_count,
        }
        for z in convergence_zones
    ]
    discovered_topics = run_discovery(snapshots, convergence_dicts or None)

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    logger.info("Enrichment complete in %.1fms: %d entities, %d stories, %d anomalies, %d topics",
                elapsed_ms, len(all_entities), len(stories), anomalies.total_anomalies, len(discovered_topics))

    return EnrichmentResult(
        slop_filter=slop_result,
        entities=all_entities,
        stories=stories,
        corroboration=corroboration,
        convergence_zones=convergence_zones,
        anomalies=anomalies,
        entity_graph=entity_graph,
        narrative=narrative,
        discovered_topics=discovered_topics,
        credible_observations=credible_obs,
        enrichment_duration_ms=elapsed_ms,
    )
