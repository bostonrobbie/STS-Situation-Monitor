"""CLI entry point: python -m sts_monitor

Subcommands:
    cycle       Run one full intelligence cycle
    ingest      Collect from connectors only
    enrich      Run enrichment on existing observations
    deep-truth  Run Deep Truth forensic analysis on a topic
    surge       Analyze social media surge on a topic
    retention   Clean up old data
    serve       Start the API server
"""
from __future__ import annotations

import argparse
import json
import logging
import sys


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# ── cycle command ─────────────────────────────────────────────────────

def cmd_cycle(args: argparse.Namespace) -> int:
    from sts_monitor.cycle import run_cycle, build_default_connectors
    from sts_monitor.pipeline import SignalPipeline

    connectors = build_default_connectors(
        args.topic,
        include_social=not args.no_social,
        include_search=args.search,
    )

    result = run_cycle(
        topic=args.topic,
        connectors=connectors,
        pipeline=SignalPipeline(min_reliability=args.min_reliability),
        generate_report=not args.no_report,
    )

    print(result.summary())

    if args.json:
        output = {
            "cycle_id": result.cycle_id,
            "topic": result.topic,
            "duration_ms": result.duration_ms,
            "collected": result.total_observations_collected,
            "accepted": len(result.pipeline_result.accepted),
            "dropped": len(result.pipeline_result.dropped),
            "disputed": len(result.pipeline_result.disputed_claims),
            "confidence": result.pipeline_result.confidence,
            "entities": len(result.enrichment.entities),
            "stories": len(result.enrichment.stories),
            "anomalies": result.enrichment.anomalies.total_anomalies,
            "alerts": [{"rule": a.rule_name, "severity": a.severity, "message": a.message} for a in result.alerts_fired],
            "promoted_topics": [{"title": p.title, "score": p.score} for p in result.promoted_topics],
        }
        if result.report:
            output["report"] = result.report.to_dict()
        print(json.dumps(output, indent=2, default=str))

    if result.report and args.report_file:
        with open(args.report_file, "w") as f:
            f.write(result.report.to_markdown())
        print(f"\nReport written to {args.report_file}")

    return 0


# ── deep-truth command ────────────────────────────────────────────────

def cmd_deep_truth(args: argparse.Namespace) -> int:
    from sts_monitor.deep_truth import analyze_deep_truth
    from sts_monitor.cycle import build_default_connectors
    from sts_monitor.pipeline import SignalPipeline

    # Collect data first
    connectors = build_default_connectors(args.topic)
    all_obs = []
    for connector in connectors:
        try:
            result = connector.collect(args.topic)
            all_obs.extend(result.observations)
        except Exception as exc:
            logging.warning("Connector %s failed: %s", getattr(connector, "name", "?"), exc)

    if not all_obs:
        print(f"No observations collected for '{args.topic}'. Cannot run Deep Truth analysis.")
        return 1

    # Run through pipeline
    pipe = SignalPipeline()
    pipeline_result = pipe.run(all_obs, args.topic)

    # Convert to dicts
    obs_dicts = [
        {"source": o.source, "claim": o.claim, "url": o.url,
         "captured_at": o.captured_at, "reliability_hint": o.reliability_hint}
        for o in pipeline_result.accepted
    ]

    # Run deep truth
    verdict = analyze_deep_truth(obs_dicts, args.topic, claim=args.claim or args.topic)

    if args.json:
        print(json.dumps(verdict.to_dict(), indent=2, default=str))
    else:
        _print_deep_truth(verdict)

    return 0


def _print_deep_truth(verdict) -> None:
    """Pretty-print a Deep Truth verdict."""
    print(f"\n{'='*60}")
    print(f"DEEP TRUTH ANALYSIS: {verdict.claim[:80]}")
    print(f"{'='*60}")

    print(f"\nAuthority Weight: {verdict.authority_weight.score:.3f} ({verdict.authority_weight.skepticism_level})")
    if verdict.authority_weight.pejorative_labels:
        print(f"  Pejorative labels detected: {', '.join(verdict.authority_weight.pejorative_labels)}")
    if verdict.authority_weight.coordination_markers:
        print(f"  Coordination markers: {len(verdict.authority_weight.coordination_markers)}")

    print(f"\nProvenance entropy: {verdict.provenance.entropy_bits:.2f} bits")
    print(f"Source independence: {verdict.provenance.source_independence:.1%}")
    print(f"Primary source ratio: {verdict.provenance.primary_source_ratio:.1%}")

    print("\n--- Tracks ---")
    for track in verdict.tracks:
        survived = "SURVIVED" if track.survived_attack else "DESTROYED"
        print(f"  [{survived}] {track.track_name}: {track.probability:.0%} probability")
        for w in track.weaknesses:
            print(f"    - Weakness: {w}")

    if verdict.silence_gaps:
        print("\n--- Silence Gaps ---")
        for gap in verdict.silence_gaps:
            print(f"  [{gap.importance:.0%}] {gap.question}")

    print("\n--- Verdict ---")
    print(verdict.verdict_summary)
    print(f"\nProbability distribution: {json.dumps({k: f'{v:.0%}' for k, v in verdict.probability_distribution.items()})}")
    print(f"Meta-confidence in this analysis: {verdict.confidence_in_verdict:.0%}")

    if verdict.manufactured_consensus_detected:
        print("\n  *** MANUFACTURED CONSENSUS INDICATORS DETECTED ***")
    if verdict.active_suppression_detected:
        print("\n  *** ACTIVE SUPPRESSION INDICATORS DETECTED ***")


# ── surge command ─────────────────────────────────────────────────────

def cmd_surge(args: argparse.Namespace) -> int:
    from sts_monitor.surge_detector import analyze_surge
    from sts_monitor.connectors.nitter import NitterConnector, get_accounts_for_categories

    accounts = get_accounts_for_categories(args.categories.split(",") if args.categories else None)
    connector = NitterConnector(accounts=accounts)
    result = connector.collect(args.topic)

    obs_dicts = [
        {"source": o.source, "claim": o.claim, "url": o.url,
         "captured_at": o.captured_at, "reliability_hint": o.reliability_hint}
        for o in result.observations
    ]

    analysis = analyze_surge(obs_dicts, topic=args.topic)

    print(f"\nSurge Analysis: {args.topic}")
    print(f"  Processed: {analysis.total_processed} tweets")
    print(f"  Surge detected: {analysis.surge_detected}")
    print(f"  Alpha signals: {analysis.alpha_count}")
    print(f"  Noise: {analysis.noise_count}")
    print(f"  Disinfo: {analysis.disinfo_count}")

    for surge in analysis.surges:
        print(f"\n  Surge: {surge.tweet_count} tweets, {surge.velocity:.1f}/min")
        for tweet in surge.alpha_tweets[:5]:
            print(f"    [{tweet.alpha_score:.2f}] @{tweet.author}: {tweet.text[:120]}")

    if args.json:
        print(json.dumps({
            "surge_detected": analysis.surge_detected,
            "total": analysis.total_processed,
            "alpha": analysis.alpha_count,
            "noise": analysis.noise_count,
            "disinfo": analysis.disinfo_count,
            "surges": len(analysis.surges),
        }, indent=2))

    return 0


# ── retention command ─────────────────────────────────────────────────

def cmd_retention(args: argparse.Namespace) -> int:
    from sts_monitor.retention import run_retention

    result = run_retention(
        max_age_days=args.max_age,
        dry_run=args.dry_run,
    )

    print(f"Retention cleanup {'(DRY RUN)' if args.dry_run else ''}:")
    print(f"  Observations removed: {result.get('observations_removed', 0)}")
    print(f"  Ingestion runs removed: {result.get('ingestion_runs_removed', 0)}")
    print(f"  Alert events removed: {result.get('alert_events_removed', 0)}")
    print(f"  Jobs removed: {result.get('jobs_removed', 0)}")

    return 0


# ── serve command ─────────────────────────────────────────────────────

def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn
    uvicorn.run(
        "sts_monitor.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


# ── Argument parser ───────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sts_monitor",
        description="STS Situation Monitor — OSINT intelligence production platform",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # cycle
    p_cycle = sub.add_parser("cycle", help="Run one full intelligence cycle")
    p_cycle.add_argument("topic", help="Topic/situation to monitor")
    p_cycle.add_argument("--no-social", action="store_true", help="Exclude social media connectors")
    p_cycle.add_argument("--search", action="store_true", help="Include web search connector")
    p_cycle.add_argument("--no-report", action="store_true", help="Skip report generation")
    p_cycle.add_argument("--min-reliability", type=float, default=0.45, help="Pipeline min reliability")
    p_cycle.add_argument("--json", action="store_true", help="Output JSON")
    p_cycle.add_argument("--report-file", help="Write markdown report to file")

    # deep-truth
    p_dt = sub.add_parser("deep-truth", help="Run Deep Truth forensic analysis")
    p_dt.add_argument("topic", help="Topic to analyze")
    p_dt.add_argument("--claim", help="Specific claim to investigate")
    p_dt.add_argument("--json", action="store_true", help="Output JSON")

    # surge
    p_surge = sub.add_parser("surge", help="Analyze social media surge")
    p_surge.add_argument("topic", help="Topic/hashtag to monitor")
    p_surge.add_argument("--categories", help="OSINT categories (comma-separated)")
    p_surge.add_argument("--json", action="store_true", help="Output JSON")

    # retention
    p_ret = sub.add_parser("retention", help="Clean up old data")
    p_ret.add_argument("--max-age", type=int, default=90, help="Max age in days (default: 90)")
    p_ret.add_argument("--dry-run", action="store_true", help="Show what would be deleted")

    # serve
    p_serve = sub.add_parser("serve", help="Start the API server")
    p_serve.add_argument("--host", default="127.0.0.1", help="Bind host")
    p_serve.add_argument("--port", type=int, default=8080, help="Bind port")
    p_serve.add_argument("--reload", action="store_true", help="Auto-reload on changes")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    _setup_logging(args.verbose)

    if not args.command:
        parser.print_help()
        return 0

    commands = {
        "cycle": cmd_cycle,
        "deep-truth": cmd_deep_truth,
        "surge": cmd_surge,
        "retention": cmd_retention,
        "serve": cmd_serve,
    }

    handler = commands.get(args.command)
    if handler:
        return handler(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
