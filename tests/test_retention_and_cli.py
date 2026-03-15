"""Tests for data retention and CLI modules."""
from __future__ import annotations

import pytest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from sts_monitor.__main__ import build_parser, main
from sts_monitor.collection_executor import (
    execute_requirement, execute_plan, _build_connector,
)
from sts_monitor.collection_plan import CollectionRequirement


# ── CLI parser tests ──────────────────────────────────────────────────

class TestCLIParser:
    def test_build_parser(self):
        parser = build_parser()
        assert parser is not None

    def test_cycle_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["cycle", "earthquake turkey"])
        assert args.command == "cycle"
        assert args.topic == "earthquake turkey"
        assert args.no_social is False
        assert args.search is False
        assert args.no_report is False
        assert args.min_reliability == 0.45

    def test_cycle_with_flags(self):
        parser = build_parser()
        args = parser.parse_args([
            "cycle", "conflict", "--no-social", "--search",
            "--no-report", "--min-reliability", "0.6", "--json",
        ])
        assert args.no_social is True
        assert args.search is True
        assert args.no_report is True
        assert args.min_reliability == 0.6
        assert args.json is True

    def test_deep_truth_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["deep-truth", "climate change"])
        assert args.command == "deep-truth"
        assert args.topic == "climate change"
        assert args.claim is None

    def test_deep_truth_with_claim(self):
        parser = build_parser()
        args = parser.parse_args(["deep-truth", "pandemic", "--claim", "lab leak theory"])
        assert args.claim == "lab leak theory"

    def test_surge_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["surge", "breaking news"])
        assert args.command == "surge"
        assert args.topic == "breaking news"

    def test_surge_with_categories(self):
        parser = build_parser()
        args = parser.parse_args(["surge", "conflict", "--categories", "conflict,osint"])
        assert args.categories == "conflict,osint"

    def test_retention_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["retention", "--max-age", "60", "--dry-run"])
        assert args.command == "retention"
        assert args.max_age == 60
        assert args.dry_run is True

    def test_retention_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["retention"])
        assert args.max_age == 90
        assert args.dry_run is False

    def test_serve_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["serve", "--host", "0.0.0.0", "--port", "9090"])
        assert args.command == "serve"
        assert args.host == "0.0.0.0"
        assert args.port == 9090

    def test_serve_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["serve"])
        assert args.host == "127.0.0.1"
        assert args.port == 8080
        assert args.reload is False

    def test_no_command_returns_0(self):
        """No command should print help and return 0."""
        with patch("sys.argv", ["sts_monitor"]):
            result = main()
        assert result == 0

    def test_verbose_flag(self):
        parser = build_parser()
        args = parser.parse_args(["-v", "cycle", "test"])
        assert args.verbose is True


# ── Collection executor tests ─────────────────────────────────────────

class TestCollectionExecutor:
    def _req(self, connectors=None, query="test query", **kwargs):
        return CollectionRequirement(
            name="test_req",
            description="Test requirement",
            investigation_id="inv-1",
            connectors=connectors if connectors is not None else ["gdelt"],
            query=query,
            **kwargs,
        )

    def test_build_connector_known(self):
        req = self._req(connectors=["gdelt"])
        c = _build_connector("gdelt", req)
        assert c is not None
        assert hasattr(c, "collect")

    def test_build_connector_unknown(self):
        req = self._req()
        c = _build_connector("nonexistent_connector_xyz", req)
        assert c is None

    def test_build_connector_rss_without_config_returns_none(self):
        """RSS requires feed_urls, so bare instantiation returns None."""
        req = self._req(connectors=["rss"])
        c = _build_connector("rss", req)
        assert c is None

    def test_build_connector_nitter(self):
        req = self._req(connectors=["nitter"])
        c = _build_connector("nitter", req)
        assert c is not None

    def test_build_connector_usgs(self):
        req = self._req(connectors=["usgs"])
        c = _build_connector("usgs", req)
        assert c is not None

    def test_execute_requirement_with_failing_connector(self):
        """If a connector raises, the executor should catch and continue."""
        req = self._req(connectors=["nonexistent_connector_xyz", "gdelt"])
        # The nonexistent one will fail, gdelt will also fail (network)
        # but the function itself should not raise
        result = execute_requirement(req)
        assert result.requirement_name == "test_req"
        assert result.investigation_id == "inv-1"
        assert len(result.errors) >= 1  # At least the nonexistent one

    def test_execute_plan_sorts_by_priority(self):
        req1 = self._req(priority=10)
        req1.name = "low_priority"
        req2 = self._req(priority=90)
        req2.name = "high_priority"
        req3 = self._req(active=False)
        req3.name = "inactive"

        # Mock execute_requirement to just return a stub
        with patch("sts_monitor.collection_executor.execute_requirement") as mock_exec:
            mock_exec.return_value = MagicMock(total_observations=0)
            results = execute_plan([req1, req2, req3])

        # Should only execute 2 (inactive excluded)
        assert mock_exec.call_count == 2
        # High priority should be first
        first_call_req = mock_exec.call_args_list[0][0][0]
        assert first_call_req.name == "high_priority"

    def test_execute_plan_empty(self):
        results = execute_plan([])
        assert results == []

    def test_collection_run_result_fields(self):
        req = self._req(connectors=[])
        result = execute_requirement(req)
        assert result.requirement_name == "test_req"
        assert result.total_observations == 0
        assert result.connectors_attempted == 0
        assert result.connectors_succeeded == 0
        assert isinstance(result.duration_ms, float)


# ── Package exports test ──────────────────────────────────────────────

class TestPackageExports:
    def test_pipeline_exports(self):
        from sts_monitor import Observation, PipelineResult, SignalPipeline
        assert Observation is not None
        assert PipelineResult is not None
        assert SignalPipeline is not None

    def test_enrichment_exports(self):
        from sts_monitor import EnrichmentResult, run_enrichment
        assert EnrichmentResult is not None
        assert callable(run_enrichment)

    def test_cycle_exports(self):
        from sts_monitor import CycleResult, run_cycle
        assert CycleResult is not None
        assert callable(run_cycle)

    def test_surge_exports(self):
        from sts_monitor import SurgeAnalysisResult, analyze_surge, get_account_store
        assert SurgeAnalysisResult is not None
        assert callable(analyze_surge)
        assert callable(get_account_store)

    def test_deep_truth_exports(self):
        from sts_monitor import DeepTruthVerdict, analyze_deep_truth
        assert DeepTruthVerdict is not None
        assert callable(analyze_deep_truth)

    def test_all_list(self):
        import sts_monitor
        assert len(sts_monitor.__all__) >= 10
