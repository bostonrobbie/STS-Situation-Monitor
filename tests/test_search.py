from __future__ import annotations

import pytest

from datetime import UTC, datetime

from sts_monitor.search import apply_context_boosts, build_query_plan, score_text


pytestmark = pytest.mark.unit


def test_build_query_plan_expands_synonyms_and_exclusions() -> None:
    plan = build_query_plan('airport "flight disruption" -rumor', extra_synonyms={"airport": ["runway"]})

    assert "airport" in plan.include_terms
    assert "flight" in plan.include_terms
    assert "runway" in plan.include_terms
    assert "rumor" in plan.exclude_terms
    assert "flight disruption" in plan.include_phrases


def test_score_text_rewards_matches_and_blocks_exclusions() -> None:
    plan = build_query_plan("airport disruption")
    positive = score_text(text="Airport disruption impacts flights", plan=plan, base_reliability=0.9)
    negative = score_text(text="unrelated weather bulletin", plan=plan, base_reliability=0.9)

    assert positive > 0.1
    assert negative == 0.0


def test_apply_context_boosts_prefers_recent_trusted_sources() -> None:
    base = 0.4
    now_score = apply_context_boosts(score=base, captured_at=datetime.now(UTC), source_trust=0.9)
    old_score = apply_context_boosts(
        score=base,
        captured_at=datetime(2020, 1, 1, tzinfo=UTC),
        source_trust=0.4,
    )

    assert now_score > old_score
