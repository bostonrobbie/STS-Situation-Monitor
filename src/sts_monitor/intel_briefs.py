"""Auto-generated intelligence briefs — daily/weekly PDF reports.

Generates structured intel briefs from autopilot findings with:
- Executive summary
- Key findings with confidence levels
- Source citations
- Entity analysis
- Timeline of events
- Recommendations
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)


def generate_intel_brief(
    investigation_id: str,
    topic: str,
    observations: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    stories: list[dict[str, Any]] | None = None,
    alerts: list[dict[str, Any]] | None = None,
    source_scores: dict[str, Any] | None = None,
    comparative: dict[str, Any] | None = None,
    pattern_analysis: dict[str, Any] | None = None,
    llm_client=None,
    period_label: str = "Daily",
) -> dict[str, Any]:
    """Generate a structured intelligence brief."""
    now = datetime.now(UTC)
    stories = stories or []
    alerts = alerts or []

    # ── Basic stats ────────────────────────────────────────────────
    total_obs = len(observations)
    sources = list({o.get("source", "unknown") for o in observations})
    source_count = len(sources)

    # Timestamps
    timestamps = []
    for o in observations:
        ts = o.get("captured_at")
        if isinstance(ts, datetime):
            timestamps.append(ts)
        elif isinstance(ts, str):
            try:
                timestamps.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
            except Exception:
                pass

    time_span = ""
    if timestamps:
        earliest = min(timestamps)
        latest = max(timestamps)
        hours = (latest - earliest).total_seconds() / 3600
        time_span = f"{hours:.1f} hours" if hours < 48 else f"{hours / 24:.1f} days"

    # ── Key findings ───────────────────────────────────────────────
    key_findings = []

    # From entities
    entity_counts = {}
    for e in entities:
        name = e.get("normalized") or e.get("entity_text", "")
        etype = e.get("entity_type", "")
        if name:
            entity_counts[f"{name} ({etype})"] = entity_counts.get(f"{name} ({etype})", 0) + 1

    top_entities = sorted(entity_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    if top_entities:
        key_findings.append({
            "finding": f"Top entities: {', '.join(e[0] for e in top_entities[:5])}",
            "confidence": "high",
            "source_count": source_count,
        })

    # From alerts
    if alerts:
        critical = [a for a in alerts if a.get("severity") == "critical"]
        if critical:
            key_findings.append({
                "finding": f"{len(critical)} critical alerts fired: {critical[0].get('message', '')}",
                "confidence": "high",
                "source_count": source_count,
            })

    # From comparative
    if comparative:
        contradictions = comparative.get("contradiction_count", 0)
        if contradictions > 0:
            key_findings.append({
                "finding": f"{contradictions} source contradictions detected — narrative integrity compromised",
                "confidence": "medium",
                "source_count": comparative.get("total_sources", 0),
            })
        divergence = comparative.get("narrative_divergence_score", 0)
        if divergence > 0.3:
            key_findings.append({
                "finding": f"Narrative divergence at {divergence:.0%} — sources significantly disagree",
                "confidence": "medium",
                "source_count": comparative.get("total_sources", 0),
            })

    # From patterns
    if pattern_analysis and pattern_analysis.get("top_match"):
        match = pattern_analysis["top_match"]
        key_findings.append({
            "finding": f"Pattern match: {match['pattern']} (similarity: {match['similarity']:.0%})",
            "confidence": "medium" if match["similarity"] > 0.5 else "low",
            "source_count": 0,
        })

    # General observation-based findings
    neg_words = {"false", "denied", "debunked", "retracted", "conflict", "attack", "crisis"}
    disputed = sum(1 for o in observations if any(w in (o.get("claim") or "").lower() for w in neg_words))
    if disputed > total_obs * 0.2:
        key_findings.append({
            "finding": f"{disputed}/{total_obs} observations contain disputed/conflict signals",
            "confidence": "medium",
            "source_count": source_count,
        })

    if not key_findings:
        key_findings.append({
            "finding": f"Collected {total_obs} observations from {source_count} sources. No critical signals detected.",
            "confidence": "high",
            "source_count": source_count,
        })

    # ── Executive summary ──────────────────────────────────────────
    if llm_client:
        try:
            obs_text = "\n".join(f"- [{o.get('source', '?')}] {(o.get('claim') or '')[:150]}" for o in observations[:20])
            findings_text = "\n".join(f"- {f['finding']}" for f in key_findings)

            prompt = (
                f"You are an intelligence analyst. Write a 3-4 sentence executive summary for a "
                f"{period_label.lower()} intelligence brief on '{topic}'.\n\n"
                f"Key findings:\n{findings_text}\n\n"
                f"Recent observations ({total_obs} total):\n{obs_text}\n\n"
                f"Be concise, factual, and highlight the most significant developments."
            )
            exec_summary = llm_client.summarize(prompt)
        except Exception as e:
            log.warning("LLM summary failed: %s", e)
            exec_summary = _deterministic_summary(topic, total_obs, source_count, key_findings, time_span)
    else:
        exec_summary = _deterministic_summary(topic, total_obs, source_count, key_findings, time_span)

    # ── Source assessment ──────────────────────────────────────────
    source_assessment = []
    if source_scores:
        for entry in source_scores.get("leaderboard", [])[:10]:
            source_assessment.append({
                "source": entry.get("source", "?"),
                "grade": entry.get("grade", "?"),
                "accuracy": entry.get("accuracy_rate", 0),
                "observations": entry.get("total_observations", 0),
            })

    # ── Recommendations ────────────────────────────────────────────
    recommendations = []
    if disputed > total_obs * 0.3:
        recommendations.append("High dispute rate detected — prioritize claim verification")
    if source_count < 3:
        recommendations.append("Low source diversity — expand monitoring to additional sources")
    if pattern_analysis and pattern_analysis.get("escalation_score", 0) > 0.5:
        recommendations.append("Escalation pattern detected — increase monitoring frequency")
    if comparative and comparative.get("silence_count", 0) > 0:
        recommendations.append("Source silences detected — investigate why sources went quiet")
    if not recommendations:
        recommendations.append("Continue monitoring at current tempo")

    # ── Build brief ────────────────────────────────────────────────
    return {
        "investigation_id": investigation_id,
        "topic": topic,
        "period": period_label,
        "generated_at": now.isoformat(),
        "time_span": time_span,
        "executive_summary": exec_summary,
        "key_findings": key_findings,
        "total_observations": total_obs,
        "source_count": source_count,
        "sources": sources[:20],
        "top_entities": [{"entity": e[0], "mentions": e[1]} for e in top_entities],
        "stories": [{"headline": s.get("headline", ""), "observation_count": s.get("observation_count", 0)} for s in stories[:10]],
        "alerts": alerts[:20],
        "source_assessment": source_assessment,
        "pattern_analysis": pattern_analysis,
        "recommendations": recommendations,
        "classification": "UNCLASSIFIED // OSINT",
    }


def _deterministic_summary(
    topic: str,
    total_obs: int,
    source_count: int,
    key_findings: list[dict[str, Any]],
    time_span: str,
) -> str:
    """Generate executive summary without LLM."""
    parts = [f"This brief covers {total_obs} observations from {source_count} sources"]
    if time_span:
        parts[0] += f" over {time_span}"
    parts[0] += f" regarding {topic}."

    if key_findings:
        parts.append(key_findings[0]["finding"] + ".")

    if len(key_findings) > 1:
        parts.append(f"Additionally: {key_findings[1]['finding']}.")

    return " ".join(parts)


def brief_to_markdown(brief: dict[str, Any]) -> str:
    """Convert an intel brief to formatted Markdown."""
    lines = [
        f"# {brief.get('period', 'Intelligence')} Brief: {brief.get('topic', 'Unknown')}",
        f"**Classification:** {brief.get('classification', 'UNCLASSIFIED')}",
        f"**Generated:** {brief.get('generated_at', '')}",
        f"**Period:** {brief.get('time_span', 'N/A')}",
        "",
        "## Executive Summary",
        brief.get("executive_summary", "No summary available."),
        "",
        "## Key Findings",
    ]

    for i, f in enumerate(brief.get("key_findings", []), 1):
        conf = f.get("confidence", "?").upper()
        lines.append(f"{i}. **[{conf}]** {f.get('finding', '')}")

    lines.extend([
        "",
        "## Statistics",
        f"- Total observations: {brief.get('total_observations', 0)}",
        f"- Sources monitored: {brief.get('source_count', 0)}",
        f"- Sources: {', '.join(brief.get('sources', [])[:10])}",
    ])

    if brief.get("top_entities"):
        lines.extend(["", "## Key Entities"])
        for e in brief["top_entities"][:10]:
            lines.append(f"- **{e['entity']}** ({e['mentions']} mentions)")

    if brief.get("source_assessment"):
        lines.extend(["", "## Source Reliability"])
        for s in brief["source_assessment"]:
            lines.append(f"- **{s['source']}**: Grade {s['grade']} (accuracy: {s['accuracy']:.0%})")

    if brief.get("recommendations"):
        lines.extend(["", "## Recommendations"])
        for r in brief["recommendations"]:
            lines.append(f"- {r}")

    if brief.get("alerts"):
        lines.extend(["", "## Alerts"])
        for a in brief["alerts"][:10]:
            lines.append(f"- **[{a.get('severity', '?').upper()}]** {a.get('message', '')}")

    lines.extend(["", "---", f"*Generated by STS Situation Monitor — {brief.get('generated_at', '')}*"])
    return "\n".join(lines)
