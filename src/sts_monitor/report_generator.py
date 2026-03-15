"""Automated intelligence report generation via local LLM.

Takes an investigation's observations, entities, stories, convergence zones,
and pipeline results — then generates a structured intelligence brief with:
  - Executive summary
  - Key findings with source citations
  - Entity analysis
  - Geographic situation overview
  - Source reliability assessment
  - Intelligence gaps
  - Recommended actions

Supports both LLM-powered and deterministic (fallback) generation.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sts_monitor.llm import LocalLLMClient
from sts_monitor.pipeline import Observation, PipelineResult

logger = logging.getLogger(__name__)


# ── Report structure ────────────────────────────────────────────────────

@dataclass(slots=True)
class IntelligenceReport:
    """Structured intelligence product."""
    investigation_id: str
    topic: str
    generated_at: datetime
    executive_summary: str
    key_findings: list[dict[str, str]]  # [{finding, confidence, sources}]
    entity_analysis: str
    geographic_overview: str
    source_assessment: str
    intelligence_gaps: list[str]
    recommended_actions: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)
    generation_method: str = "deterministic"  # "llm" or "deterministic"
    generation_time_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "investigation_id": self.investigation_id,
            "topic": self.topic,
            "generated_at": self.generated_at.isoformat(),
            "executive_summary": self.executive_summary,
            "key_findings": self.key_findings,
            "entity_analysis": self.entity_analysis,
            "geographic_overview": self.geographic_overview,
            "source_assessment": self.source_assessment,
            "intelligence_gaps": self.intelligence_gaps,
            "recommended_actions": self.recommended_actions,
            "metadata": self.metadata,
            "generation_method": self.generation_method,
            "generation_time_ms": self.generation_time_ms,
        }

    def to_markdown(self) -> str:
        """Render as readable markdown."""
        sections = [
            f"# Intelligence Brief: {self.topic}",
            f"*Generated: {self.generated_at.strftime('%Y-%m-%d %H:%M UTC')}*\n",
            "## Executive Summary",
            self.executive_summary + "\n",
            "## Key Findings",
        ]
        for i, f in enumerate(self.key_findings, 1):
            sections.append(f"{i}. **{f.get('finding', '')}**")
            if f.get("confidence"):
                sections.append(f"   - Confidence: {f['confidence']}")
            if f.get("sources"):
                sections.append(f"   - Sources: {f['sources']}")
        sections.append("")
        sections.extend([
            "## Entity Analysis",
            self.entity_analysis + "\n",
            "## Geographic Overview",
            self.geographic_overview + "\n",
            "## Source Assessment",
            self.source_assessment + "\n",
            "## Intelligence Gaps",
        ])
        for gap in self.intelligence_gaps:
            sections.append(f"- {gap}")
        sections.append("")
        sections.append("## Recommended Actions")
        for action in self.recommended_actions:
            sections.append(f"- {action}")
        return "\n".join(sections)


# ── LLM prompts ─────────────────────────────────────────────────────────

REPORT_PROMPT = """You are an intelligence analyst producing a structured brief.

TOPIC: {topic}
INVESTIGATION ID: {investigation_id}

PIPELINE SUMMARY:
- Observations accepted: {accepted_count}
- Observations dropped (low confidence): {dropped_count}
- Disputed claims: {disputed_count}
- Overall confidence: {confidence}

TOP OBSERVATIONS (by reliability):
{top_observations}

ENTITIES DETECTED:
{entities}

STORIES/CLUSTERS:
{stories}

CONVERGENCE ZONES:
{convergence_zones}

SOURCE BREAKDOWN:
{source_breakdown}

Generate a structured intelligence brief. Respond with a JSON object:
{{
  "executive_summary": "2-3 sentence overview of the situation",
  "key_findings": [
    {{"finding": "concise finding", "confidence": "high/medium/low", "sources": "source list"}}
  ],
  "entity_analysis": "Analysis of key people, organizations, locations involved",
  "geographic_overview": "Spatial analysis of events and convergence zones",
  "source_assessment": "Reliability assessment of information sources",
  "intelligence_gaps": ["gap1", "gap2"],
  "recommended_actions": ["action1", "action2"]
}}

Rules:
- 3-7 key findings, most important first
- Be specific about confidence levels
- Cite sources for each finding
- Identify what we DON'T know (gaps)
- Actionable recommendations
- Return ONLY valid JSON"""


# ── Report generator ────────────────────────────────────────────────────

class ReportGenerator:
    """Generate intelligence reports from investigation data."""

    def __init__(self, llm_client: LocalLLMClient | None = None) -> None:
        self.llm = llm_client

    def _format_observations(self, observations: list[Observation], limit: int = 20) -> str:
        """Format top observations for the prompt."""
        sorted_obs = sorted(observations, key=lambda o: o.reliability_hint, reverse=True)
        lines = []
        for obs in sorted_obs[:limit]:
            lines.append(
                f"- [{obs.source}] (reliability: {obs.reliability_hint:.2f}) "
                f"{obs.claim[:200]} | {obs.url}"
            )
        return "\n".join(lines) or "No observations available."

    def _format_entities(self, entities: list[dict[str, Any]], limit: int = 30) -> str:
        """Format extracted entities for the prompt."""
        if not entities:
            return "No entities extracted."
        # Group by type
        by_type: dict[str, list[str]] = {}
        for e in entities[:limit]:
            etype = e.get("entity_type", "unknown")
            by_type.setdefault(etype, []).append(e.get("entity_text", e.get("text", "")))
        lines = []
        for etype, names in by_type.items():
            unique = list(dict.fromkeys(names))[:10]
            lines.append(f"  {etype}: {', '.join(unique)}")
        return "\n".join(lines)

    def _format_stories(self, stories: list[dict[str, Any]], limit: int = 10) -> str:
        """Format story clusters for the prompt."""
        if not stories:
            return "No story clusters identified."
        lines = []
        for s in stories[:limit]:
            lines.append(
                f"- {s.get('headline', 'Unknown')} "
                f"({s.get('observation_count', 0)} observations, "
                f"{s.get('source_count', 0)} sources, "
                f"reliability: {s.get('avg_reliability', 0):.2f})"
            )
        return "\n".join(lines)

    def _format_convergence(self, zones: list[dict[str, Any]]) -> str:
        """Format convergence zones for the prompt."""
        if not zones:
            return "No convergence zones detected."
        lines = []
        for z in zones:
            lines.append(
                f"- {z.get('severity', 'unknown').upper()} zone at "
                f"({z.get('center_lat', 0):.2f}, {z.get('center_lon', 0):.2f}): "
                f"{z.get('signal_count', 0)} signals, "
                f"types: {z.get('signal_types', [])}"
            )
        return "\n".join(lines)

    def _format_source_breakdown(self, observations: list[Observation]) -> str:
        """Summarize sources and their reliability."""
        source_stats: dict[str, dict[str, Any]] = {}
        for obs in observations:
            family = obs.source.split(":")[0]
            stats = source_stats.setdefault(family, {"count": 0, "total_reliability": 0.0})
            stats["count"] += 1
            stats["total_reliability"] += obs.reliability_hint

        lines = []
        for source, stats in sorted(source_stats.items(), key=lambda x: x[1]["count"], reverse=True):
            avg_rel = stats["total_reliability"] / max(1, stats["count"])
            lines.append(f"  {source}: {stats['count']} observations (avg reliability: {avg_rel:.2f})")
        return "\n".join(lines) or "No source data."

    def _generate_deterministic(
        self,
        investigation_id: str,
        topic: str,
        pipeline_result: PipelineResult,
        entities: list[dict[str, Any]],
        stories: list[dict[str, Any]],
        convergence_zones: list[dict[str, Any]],
    ) -> IntelligenceReport:
        """Generate a report without LLM (deterministic fallback)."""
        # Key findings from top observations
        top_obs = sorted(pipeline_result.accepted, key=lambda o: o.reliability_hint, reverse=True)
        key_findings = []
        seen_claims: set[str] = set()
        for obs in top_obs[:7]:
            normalized = obs.claim[:100].lower().strip()
            if normalized in seen_claims:
                continue
            seen_claims.add(normalized)
            key_findings.append({
                "finding": obs.claim[:200],
                "confidence": "high" if obs.reliability_hint >= 0.7 else "medium" if obs.reliability_hint >= 0.5 else "low",
                "sources": obs.source,
            })

        # Entity summary
        entity_types: dict[str, list[str]] = {}
        for e in entities[:50]:
            etype = e.get("entity_type", "unknown")
            entity_types.setdefault(etype, []).append(e.get("entity_text", e.get("text", "")))
        entity_parts = []
        for etype, names in entity_types.items():
            unique = list(dict.fromkeys(names))[:5]
            entity_parts.append(f"{etype}: {', '.join(unique)}")
        entity_analysis = "; ".join(entity_parts) if entity_parts else "No entities extracted."

        # Geographic overview
        geo_parts = []
        for z in convergence_zones:
            geo_parts.append(
                f"{z.get('severity', 'unknown')} convergence at "
                f"({z.get('center_lat', 0):.1f}, {z.get('center_lon', 0):.1f})"
            )
        geographic_overview = ". ".join(geo_parts) if geo_parts else "No geographic convergence detected."

        # Source assessment
        source_families: dict[str, list[float]] = {}
        for obs in pipeline_result.accepted:
            family = obs.source.split(":")[0]
            source_families.setdefault(family, []).append(obs.reliability_hint)
        source_lines = []
        for fam, scores in sorted(source_families.items(), key=lambda x: -sum(x[1]) / len(x[1])):
            avg = sum(scores) / len(scores)
            source_lines.append(f"{fam} ({len(scores)} obs, avg reliability {avg:.2f})")
        source_assessment = "; ".join(source_lines) if source_lines else "No source data."

        # Gaps
        gaps = []
        if not convergence_zones:
            gaps.append("No geographic convergence detected — events may be geographically dispersed")
        if pipeline_result.disputed_claims:
            gaps.append(f"{len(pipeline_result.disputed_claims)} disputed claims require verification")
        if len(source_families) < 3:
            gaps.append("Limited source diversity — consider activating additional connectors")
        if not entities:
            gaps.append("No entities extracted — entity recognition may need enhancement")
        if not gaps:
            gaps.append("Coverage appears adequate for current assessment")

        # Recommendations
        actions = []
        if pipeline_result.disputed_claims:
            actions.append("Investigate disputed claims with additional sources")
        if convergence_zones:
            for z in convergence_zones[:2]:
                if z.get("severity") in ("high", "critical"):
                    actions.append(f"Prioritize monitoring of {z.get('severity')} convergence zone")
        if len(source_families) < 3:
            actions.append("Expand collection to additional data sources for triangulation")
        actions.append("Schedule follow-up assessment in 4-6 hours")

        exec_summary = (
            f"Analysis of '{topic}' based on {len(pipeline_result.accepted)} accepted observations "
            f"from {len(source_families)} source families. "
            f"Overall confidence: {pipeline_result.confidence:.1%}. "
            f"{len(pipeline_result.disputed_claims)} disputed claim(s) identified."
        )

        return IntelligenceReport(
            investigation_id=investigation_id,
            topic=topic,
            generated_at=datetime.now(UTC),
            executive_summary=exec_summary,
            key_findings=key_findings,
            entity_analysis=entity_analysis,
            geographic_overview=geographic_overview,
            source_assessment=source_assessment,
            intelligence_gaps=gaps,
            recommended_actions=actions,
            metadata={
                "accepted_count": len(pipeline_result.accepted),
                "dropped_count": len(pipeline_result.dropped),
                "disputed_count": len(pipeline_result.disputed_claims),
                "confidence": pipeline_result.confidence,
                "entity_count": len(entities),
                "story_count": len(stories),
                "convergence_zone_count": len(convergence_zones),
            },
            generation_method="deterministic",
        )

    def generate(
        self,
        investigation_id: str,
        topic: str,
        pipeline_result: PipelineResult,
        entities: list[dict[str, Any]] | None = None,
        stories: list[dict[str, Any]] | None = None,
        convergence_zones: list[dict[str, Any]] | None = None,
    ) -> IntelligenceReport:
        """Generate a full intelligence report, using LLM if available."""
        started = time.perf_counter()
        entities = entities or []
        stories = stories or []
        convergence_zones = convergence_zones or []

        # Try LLM generation
        if self.llm:
            try:
                prompt = REPORT_PROMPT.format(
                    topic=topic,
                    investigation_id=investigation_id,
                    accepted_count=len(pipeline_result.accepted),
                    dropped_count=len(pipeline_result.dropped),
                    disputed_count=len(pipeline_result.disputed_claims),
                    confidence=f"{pipeline_result.confidence:.1%}",
                    top_observations=self._format_observations(pipeline_result.accepted),
                    entities=self._format_entities(entities),
                    stories=self._format_stories(stories),
                    convergence_zones=self._format_convergence(convergence_zones),
                    source_breakdown=self._format_source_breakdown(pipeline_result.accepted),
                )

                raw = self.llm.summarize(prompt) or ""
                # Parse JSON response
                cleaned = raw.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[-1]
                if cleaned.endswith("```"):
                    cleaned = cleaned.rsplit("```", 1)[0]
                data = json.loads(cleaned.strip())

                elapsed = round((time.perf_counter() - started) * 1000, 2)
                return IntelligenceReport(
                    investigation_id=investigation_id,
                    topic=topic,
                    generated_at=datetime.now(UTC),
                    executive_summary=data.get("executive_summary", ""),
                    key_findings=data.get("key_findings", []),
                    entity_analysis=data.get("entity_analysis", ""),
                    geographic_overview=data.get("geographic_overview", ""),
                    source_assessment=data.get("source_assessment", ""),
                    intelligence_gaps=data.get("intelligence_gaps", []),
                    recommended_actions=data.get("recommended_actions", []),
                    metadata={
                        "accepted_count": len(pipeline_result.accepted),
                        "dropped_count": len(pipeline_result.dropped),
                        "disputed_count": len(pipeline_result.disputed_claims),
                        "confidence": pipeline_result.confidence,
                        "entity_count": len(entities),
                        "story_count": len(stories),
                        "convergence_zone_count": len(convergence_zones),
                    },
                    generation_method="llm",
                    generation_time_ms=elapsed,
                )
            except Exception as exc:
                logger.warning("LLM report generation failed, falling back to deterministic: %s", exc)

        # Deterministic fallback
        report = self._generate_deterministic(
            investigation_id, topic, pipeline_result, entities, stories, convergence_zones,
        )
        report.generation_time_ms = round((time.perf_counter() - started) * 1000, 2)
        return report
