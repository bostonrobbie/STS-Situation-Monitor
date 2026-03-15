"""Tests for report_generator module: LLM-powered and deterministic report generation."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from sts_monitor.pipeline import Observation, PipelineResult
from sts_monitor.report_generator import IntelligenceReport, ReportGenerator

pytestmark = pytest.mark.unit


# ── Helpers ────────────────────────────────────────────────────────────


def _make_observations(count: int = 3) -> list[Observation]:
    """Create a list of observations with varying reliability."""
    return [
        Observation(
            source=f"source-{i}:feed",
            claim=f"Claim number {i} about an important event",
            url=f"https://example.com/{i}",
            captured_at=datetime(2025, 1, 1, tzinfo=UTC),
            reliability_hint=round(0.9 - i * 0.1, 2),
        )
        for i in range(count)
    ]


def _make_pipeline_result(
    accepted_count: int = 3,
    dropped_count: int = 1,
    disputed: list[str] | None = None,
    confidence: float = 0.75,
) -> PipelineResult:
    accepted = _make_observations(accepted_count)
    dropped = _make_observations(dropped_count)
    # Shift dropped reliability below threshold
    for obs in dropped:
        obs.reliability_hint = 0.2
    return PipelineResult(
        accepted=accepted,
        dropped=dropped,
        deduplicated=[],
        disputed_claims=disputed or [],
        summary="Pipeline summary text.",
        confidence=confidence,
    )


VALID_LLM_JSON = json.dumps({
    "executive_summary": "LLM-generated executive summary of the situation.",
    "key_findings": [
        {"finding": "Finding A", "confidence": "high", "sources": "source-0"},
        {"finding": "Finding B", "confidence": "medium", "sources": "source-1"},
    ],
    "entity_analysis": "LLM entity analysis.",
    "geographic_overview": "LLM geographic overview.",
    "source_assessment": "LLM source assessment.",
    "intelligence_gaps": ["Gap from LLM 1", "Gap from LLM 2"],
    "recommended_actions": ["Action from LLM 1"],
})


class _FakeLLM:
    """Minimal mock for LocalLLMClient."""

    def __init__(self, response: str | None = None, error: Exception | None = None):
        self._response = response
        self._error = error
        self.calls: list[str] = []

    def summarize(self, prompt: str) -> str:
        self.calls.append(prompt)
        if self._error:
            raise self._error
        return self._response or ""


# ── IntelligenceReport ─────────────────────────────────────────────────


class TestIntelligenceReport:
    def _make_report(self) -> IntelligenceReport:
        return IntelligenceReport(
            investigation_id="inv-1",
            topic="Test Topic",
            generated_at=datetime(2025, 6, 15, 12, 0, tzinfo=UTC),
            executive_summary="Executive summary here.",
            key_findings=[
                {"finding": "Finding 1", "confidence": "high", "sources": "source-A"},
                {"finding": "Finding 2", "confidence": "low", "sources": ""},
            ],
            entity_analysis="Entities detected.",
            geographic_overview="Geographic info.",
            source_assessment="Source info.",
            intelligence_gaps=["Gap 1", "Gap 2"],
            recommended_actions=["Action 1"],
            metadata={"accepted_count": 5},
            generation_method="deterministic",
            generation_time_ms=42.0,
        )

    def test_to_dict(self):
        report = self._make_report()
        d = report.to_dict()
        assert d["investigation_id"] == "inv-1"
        assert d["topic"] == "Test Topic"
        assert d["generated_at"] == "2025-06-15T12:00:00+00:00"
        assert len(d["key_findings"]) == 2
        assert d["intelligence_gaps"] == ["Gap 1", "Gap 2"]
        assert d["generation_method"] == "deterministic"
        assert d["generation_time_ms"] == 42.0
        assert d["metadata"]["accepted_count"] == 5

    def test_to_markdown_structure(self):
        report = self._make_report()
        md = report.to_markdown()
        assert "# Intelligence Brief: Test Topic" in md
        assert "## Executive Summary" in md
        assert "Executive summary here." in md
        assert "## Key Findings" in md
        assert "1. **Finding 1**" in md
        assert "Confidence: high" in md
        assert "Sources: source-A" in md
        assert "2. **Finding 2**" in md
        assert "## Entity Analysis" in md
        assert "## Geographic Overview" in md
        assert "## Source Assessment" in md
        assert "## Intelligence Gaps" in md
        assert "- Gap 1" in md
        assert "## Recommended Actions" in md
        assert "- Action 1" in md

    def test_to_markdown_empty_confidence_and_sources_omitted(self):
        report = self._make_report()
        # Finding 2 has empty sources
        md = report.to_markdown()
        # confidence "low" should still appear for finding 2
        assert "Confidence: low" in md

    def test_to_markdown_generated_date(self):
        report = self._make_report()
        md = report.to_markdown()
        assert "2025-06-15 12:00 UTC" in md


# ── ReportGenerator: formatting helpers ────────────────────────────────


class TestReportGeneratorFormatting:
    def test_format_observations_sorts_by_reliability(self):
        gen = ReportGenerator()
        obs = _make_observations(3)
        text = gen._format_observations(obs)
        lines = text.strip().split("\n")
        assert len(lines) == 3
        # First line should be the highest reliability (source-0, 0.90)
        assert "source-0" in lines[0]
        assert "0.90" in lines[0]

    def test_format_observations_respects_limit(self):
        gen = ReportGenerator()
        obs = _make_observations(10)
        text = gen._format_observations(obs, limit=3)
        lines = text.strip().split("\n")
        assert len(lines) == 3

    def test_format_observations_empty(self):
        gen = ReportGenerator()
        assert gen._format_observations([]) == "No observations available."

    def test_format_entities_groups_by_type(self):
        gen = ReportGenerator()
        entities = [
            {"entity_type": "PERSON", "entity_text": "Alice"},
            {"entity_type": "PERSON", "entity_text": "Bob"},
            {"entity_type": "ORG", "entity_text": "FEMA"},
        ]
        text = gen._format_entities(entities)
        assert "PERSON" in text
        assert "Alice" in text
        assert "ORG" in text
        assert "FEMA" in text

    def test_format_entities_empty(self):
        gen = ReportGenerator()
        assert gen._format_entities([]) == "No entities extracted."

    def test_format_stories(self):
        gen = ReportGenerator()
        stories = [
            {"headline": "Earthquake cluster", "observation_count": 5, "source_count": 3, "avg_reliability": 0.8},
        ]
        text = gen._format_stories(stories)
        assert "Earthquake cluster" in text
        assert "5 observations" in text

    def test_format_stories_empty(self):
        gen = ReportGenerator()
        assert gen._format_stories([]) == "No story clusters identified."

    def test_format_convergence(self):
        gen = ReportGenerator()
        zones = [
            {"severity": "high", "center_lat": 34.05, "center_lon": -118.25, "signal_count": 8, "signal_types": ["earthquake", "fire"]},
        ]
        text = gen._format_convergence(zones)
        assert "HIGH" in text
        assert "34.05" in text
        assert "8 signals" in text

    def test_format_convergence_empty(self):
        gen = ReportGenerator()
        assert gen._format_convergence([]) == "No convergence zones detected."

    def test_format_source_breakdown(self):
        gen = ReportGenerator()
        obs = _make_observations(3)
        text = gen._format_source_breakdown(obs)
        # Each observation has a unique source family
        assert "source-0" in text
        assert "source-1" in text

    def test_format_source_breakdown_empty(self):
        gen = ReportGenerator()
        assert gen._format_source_breakdown([]) == "No source data."


# ── ReportGenerator: deterministic generation ──────────────────────────


class TestDeterministicGeneration:
    def test_basic_deterministic_report(self):
        gen = ReportGenerator(llm_client=None)
        pr = _make_pipeline_result(accepted_count=5, disputed=["claim X"])
        report = gen.generate("inv-1", "Test Topic", pr)

        assert isinstance(report, IntelligenceReport)
        assert report.investigation_id == "inv-1"
        assert report.topic == "Test Topic"
        assert report.generation_method == "deterministic"
        assert report.generation_time_ms > 0
        assert "Test Topic" in report.executive_summary
        assert "5 accepted observations" in report.executive_summary
        assert len(report.key_findings) > 0
        assert len(report.intelligence_gaps) > 0
        assert len(report.recommended_actions) > 0

    def test_deterministic_key_findings_confidence_levels(self):
        gen = ReportGenerator(llm_client=None)
        pr = _make_pipeline_result(accepted_count=5)
        report = gen.generate("inv-1", "Topic", pr)

        confidences = {f["confidence"] for f in report.key_findings}
        # With reliability 0.9, 0.8, 0.7, 0.6, 0.5 we expect high and medium
        assert "high" in confidences or "medium" in confidences

    def test_deterministic_deduplication(self):
        """Duplicate claims should be deduplicated in key findings."""
        gen = ReportGenerator(llm_client=None)
        obs = [
            Observation(source="s1:f", claim="Same claim", url="u1", reliability_hint=0.9),
            Observation(source="s2:f", claim="Same claim", url="u2", reliability_hint=0.8),
        ]
        pr = PipelineResult(
            accepted=obs, dropped=[], deduplicated=[], disputed_claims=[], summary="", confidence=0.8,
        )
        report = gen.generate("inv-1", "Topic", pr)
        # Should have only 1 finding since both claims are the same
        assert len(report.key_findings) == 1

    def test_deterministic_entity_analysis(self):
        gen = ReportGenerator(llm_client=None)
        pr = _make_pipeline_result()
        entities = [
            {"entity_type": "PERSON", "entity_text": "Alice"},
            {"entity_type": "ORG", "entity_text": "FEMA"},
        ]
        report = gen.generate("inv-1", "Topic", pr, entities=entities)
        assert "PERSON" in report.entity_analysis
        assert "Alice" in report.entity_analysis

    def test_deterministic_no_entities(self):
        gen = ReportGenerator(llm_client=None)
        pr = _make_pipeline_result()
        report = gen.generate("inv-1", "Topic", pr, entities=[])
        assert "No entities" in report.entity_analysis

    def test_deterministic_geographic_overview(self):
        gen = ReportGenerator(llm_client=None)
        pr = _make_pipeline_result()
        zones = [{"severity": "high", "center_lat": 34.0, "center_lon": -118.0}]
        report = gen.generate("inv-1", "Topic", pr, convergence_zones=zones)
        assert "convergence" in report.geographic_overview.lower()

    def test_deterministic_no_convergence(self):
        gen = ReportGenerator(llm_client=None)
        pr = _make_pipeline_result()
        report = gen.generate("inv-1", "Topic", pr, convergence_zones=[])
        assert "No geographic convergence" in report.geographic_overview

    def test_deterministic_gaps_disputed(self):
        gen = ReportGenerator(llm_client=None)
        pr = _make_pipeline_result(disputed=["claim A", "claim B"])
        report = gen.generate("inv-1", "Topic", pr)
        assert any("disputed" in g.lower() for g in report.intelligence_gaps)

    def test_deterministic_gaps_limited_sources(self):
        gen = ReportGenerator(llm_client=None)
        # Only 1 source family with 2 observations
        obs = [
            Observation(source="twitter:feed", claim="Claim 1", url="u1", reliability_hint=0.8),
            Observation(source="twitter:search", claim="Claim 2", url="u2", reliability_hint=0.7),
        ]
        pr = PipelineResult(
            accepted=obs, dropped=[], deduplicated=[], disputed_claims=[], summary="", confidence=0.8,
        )
        report = gen.generate("inv-1", "Topic", pr)
        assert any("source diversity" in g.lower() or "additional" in g.lower() for g in report.intelligence_gaps)

    def test_deterministic_actions_disputed(self):
        gen = ReportGenerator(llm_client=None)
        pr = _make_pipeline_result(disputed=["claim X"])
        report = gen.generate("inv-1", "Topic", pr)
        assert any("disputed" in a.lower() for a in report.recommended_actions)

    def test_deterministic_actions_convergence_zone(self):
        gen = ReportGenerator(llm_client=None)
        pr = _make_pipeline_result()
        zones = [{"severity": "critical", "center_lat": 34.0, "center_lon": -118.0}]
        report = gen.generate("inv-1", "Topic", pr, convergence_zones=zones)
        assert any("convergence" in a.lower() or "monitoring" in a.lower() for a in report.recommended_actions)

    def test_deterministic_metadata(self):
        gen = ReportGenerator(llm_client=None)
        pr = _make_pipeline_result(accepted_count=3, dropped_count=1, disputed=["x"])
        entities = [{"entity_type": "PERSON", "entity_text": "Alice"}]
        stories = [{"headline": "Story 1"}]
        zones = [{"severity": "low", "center_lat": 0, "center_lon": 0}]
        report = gen.generate("inv-1", "Topic", pr, entities=entities, stories=stories, convergence_zones=zones)
        assert report.metadata["accepted_count"] == 3
        assert report.metadata["dropped_count"] == 1
        assert report.metadata["disputed_count"] == 1
        assert report.metadata["entity_count"] == 1
        assert report.metadata["story_count"] == 1
        assert report.metadata["convergence_zone_count"] == 1


# ── ReportGenerator: LLM generation ───────────────────────────────────


class TestLLMGeneration:
    def test_llm_success(self):
        fake_llm = _FakeLLM(response=VALID_LLM_JSON)
        gen = ReportGenerator(llm_client=fake_llm)
        pr = _make_pipeline_result()
        report = gen.generate("inv-1", "Topic", pr)

        assert report.generation_method == "llm"
        assert report.executive_summary == "LLM-generated executive summary of the situation."
        assert len(report.key_findings) == 2
        assert report.key_findings[0]["finding"] == "Finding A"
        assert report.entity_analysis == "LLM entity analysis."
        assert report.intelligence_gaps == ["Gap from LLM 1", "Gap from LLM 2"]
        assert report.recommended_actions == ["Action from LLM 1"]
        assert report.generation_time_ms > 0
        assert len(fake_llm.calls) == 1

    def test_llm_with_markdown_fences(self):
        """LLM response wrapped in ```json ... ``` should be parsed."""
        wrapped = "```json\n" + VALID_LLM_JSON + "\n```"
        fake_llm = _FakeLLM(response=wrapped)
        gen = ReportGenerator(llm_client=fake_llm)
        pr = _make_pipeline_result()
        report = gen.generate("inv-1", "Topic", pr)
        assert report.generation_method == "llm"
        assert report.executive_summary == "LLM-generated executive summary of the situation."

    def test_llm_failure_falls_back_to_deterministic(self):
        fake_llm = _FakeLLM(error=RuntimeError("LLM is down"))
        gen = ReportGenerator(llm_client=fake_llm)
        pr = _make_pipeline_result()
        report = gen.generate("inv-1", "Topic", pr)

        assert report.generation_method == "deterministic"
        assert len(report.key_findings) > 0
        assert len(fake_llm.calls) == 1  # Tried once before falling back

    def test_llm_invalid_json_falls_back(self):
        fake_llm = _FakeLLM(response="This is not valid JSON at all")
        gen = ReportGenerator(llm_client=fake_llm)
        pr = _make_pipeline_result()
        report = gen.generate("inv-1", "Topic", pr)
        assert report.generation_method == "deterministic"

    def test_llm_prompt_contains_expected_data(self):
        fake_llm = _FakeLLM(response=VALID_LLM_JSON)
        gen = ReportGenerator(llm_client=fake_llm)
        entities = [{"entity_type": "PERSON", "entity_text": "Alice"}]
        stories = [{"headline": "Big story", "observation_count": 3, "source_count": 2, "avg_reliability": 0.8}]
        zones = [{"severity": "high", "center_lat": 34.0, "center_lon": -118.0, "signal_count": 5, "signal_types": ["fire"]}]
        pr = _make_pipeline_result(accepted_count=4, disputed=["disputed claim"])

        gen.generate("inv-42", "Wildfire Monitoring", pr, entities=entities, stories=stories, convergence_zones=zones)

        prompt = fake_llm.calls[0]
        assert "Wildfire Monitoring" in prompt
        assert "inv-42" in prompt
        assert "PERSON" in prompt or "Alice" in prompt
        assert "Big story" in prompt
        assert "HIGH" in prompt or "high" in prompt
        assert "source-0" in prompt

    def test_llm_metadata_matches_input(self):
        fake_llm = _FakeLLM(response=VALID_LLM_JSON)
        gen = ReportGenerator(llm_client=fake_llm)
        pr = _make_pipeline_result(accepted_count=2, dropped_count=1, disputed=["d1"], confidence=0.65)
        report = gen.generate("inv-1", "Topic", pr, entities=[{"entity_type": "X", "entity_text": "Y"}])
        assert report.metadata["accepted_count"] == 2
        assert report.metadata["dropped_count"] == 1
        assert report.metadata["disputed_count"] == 1
        assert report.metadata["confidence"] == 0.65
        assert report.metadata["entity_count"] == 1
