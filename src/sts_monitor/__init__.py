"""STS Situation Monitor — OSINT intelligence production platform.

Core modules:
    cycle           Unified intelligence cycle orchestrator
    enrichment      Enrichment chain (slop -> entities -> clusters -> corroboration -> ...)
    pipeline        Signal filtering, dedup, dispute detection
    surge_detector  Twitter/X surge intelligence and alpha extraction
    deep_truth      Forensic reasoning engine (Deep Truth Mode)

Usage:
    from sts_monitor import run_cycle, SignalPipeline, run_enrichment
    from sts_monitor import analyze_surge, analyze_deep_truth
"""
from sts_monitor.pipeline import Observation, PipelineResult, SignalPipeline
from sts_monitor.enrichment import EnrichmentResult, run_enrichment
from sts_monitor.cycle import CycleResult, run_cycle
from sts_monitor.surge_detector import (
    SurgeAnalysisResult,
    analyze_surge,
    get_account_store,
)
from sts_monitor.deep_truth import DeepTruthVerdict, analyze_deep_truth

__all__ = [
    # Pipeline
    "Observation",
    "PipelineResult",
    "SignalPipeline",
    # Enrichment
    "EnrichmentResult",
    "run_enrichment",
    # Cycle
    "CycleResult",
    "run_cycle",
    # Surge
    "SurgeAnalysisResult",
    "analyze_surge",
    "get_account_store",
    # Deep Truth
    "DeepTruthVerdict",
    "analyze_deep_truth",
]
