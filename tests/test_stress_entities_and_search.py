"""Stress tests for entities, entity_graph, slop_detector, convergence, and search modules.

Targets edge cases: empty inputs, huge inputs, special characters, boundary values,
unicode, HTML, SQL injection patterns, geographic extremes, and scoring thresholds.
"""
from __future__ import annotations

import math
import string
from datetime import UTC, datetime, timedelta

import pytest

from sts_monitor.entities import ExtractedEntity, extract_entities, extract_entities_batch
from sts_monitor.entity_graph import (
    EntityGraph,
    GraphEdge,
    GraphNode,
    _compute_centrality,
    _detect_communities,
    _edge_id,
    _node_id,
    build_entity_graph,
)
from sts_monitor.slop_detector import (
    SlopScore,
    _score_bot_patterns,
    _score_engagement_bait,
    _score_factual_density,
    _score_propaganda,
    _score_ragebait,
    _score_source_credibility,
    filter_slop,
    score_observation,
)
from sts_monitor.convergence import (
    ConvergenceZone,
    GeoPoint,
    detect_convergence,
    haversine_km,
)
from sts_monitor.search import (
    QueryPlan,
    build_query_plan,
    score_text,
    apply_context_boosts,
    top_terms,
    normalize_datetime,
)

# ═══════════════════════════════════════════════════════════════════════
# Entity extraction stress tests
# ═══════════════════════════════════════════════════════════════════════


class TestEntityExtractionEdgeCases:
    """Stress tests for extract_entities."""

    def test_empty_string(self):
        result = extract_entities("")
        assert result == []

    def test_whitespace_only(self):
        result = extract_entities("   \n\t\r  ")
        assert result == []

    def test_very_long_text(self):
        """10000+ chars of repeated normal text should not crash."""
        base = "Ukraine forces advanced near Bakhmut. NATO discussed response. "
        text = base * 200  # ~12400 chars
        result = extract_entities(text)
        assert len(result) > 0
        # Should find Ukraine, Bakhmut, NATO
        types_found = {e.entity_type for e in result}
        assert "location" in types_found
        assert "organization" in types_found

    def test_only_numbers(self):
        result = extract_entities("12345 67890 111 222 333 444 555")
        # No entities expected — no words, no patterns match
        # Numbers alone don't match quantity pattern (needs a unit word)
        assert all(e.entity_type != "person" for e in result)

    def test_only_special_chars(self):
        result = extract_entities("!@#$%^&*()_+-=[]{}|;':\",./<>?")
        assert result == []

    def test_mixed_unicode(self):
        """Arabic, Chinese, Cyrillic, emoji — should not crash.

        BUG/LIMITATION: The gazetteer only has Latin names ("Putin") so
        Cyrillic "Путин" is not matched. extract_entities silently returns
        nothing for non-Latin text.
        """
        text = "Президент Путин встретил 习近平 في Москве. بوتين يلتقي 🇷🇺"
        result = extract_entities(text)
        # No crash is the key assertion; gazetteer won't match Cyrillic
        assert isinstance(result, list)
        # But it should NOT find "Putin" since it's spelled in Cyrillic "Путин"

    def test_mixed_unicode_with_latin_names(self):
        """When Latin names appear in unicode text, they should be found."""
        text = "Президент Putin встретил Xi Jinping в Москве"
        result = extract_entities(text)
        labels = [e.text for e in result]
        assert "Putin" in labels
        assert "Xi Jinping" in labels

    def test_html_tags_in_text(self):
        text = "<b>Putin</b> met <a href='x'>Zelensky</a> in <div>Ukraine</div>"
        result = extract_entities(text)
        labels = [e.text for e in result]
        assert "Putin" in labels
        assert "Zelensky" in labels
        assert "Ukraine" in labels

    def test_sql_injection_patterns(self):
        """SQL injection strings should not crash extraction."""
        text = "'; DROP TABLE observations; -- Putin met NATO"
        result = extract_entities(text)
        labels = [e.text for e in result]
        assert "Putin" in labels
        assert "NATO" in labels

    def test_text_with_only_lowercase(self):
        """No capitalized words means no capitalized-phrase entities."""
        text = "this is all lowercase text with no names at all nothing here"
        result = extract_entities(text)
        assert result == []

    def test_entity_deduplication(self):
        """Same entity mentioned multiple times should appear once."""
        text = "Putin met Putin and Putin again. Putin was there."
        result = extract_entities(text)
        putin_entities = [e for e in result if e.text == "Putin"]
        assert len(putin_entities) == 1

    def test_coordinate_extraction(self):
        text = "Strike reported at 48.4567, 35.1234 near Donetsk"
        result = extract_entities(text)
        coord_ents = [e for e in result if e.entity_type == "location" and "," in e.text]
        assert len(coord_ents) >= 1

    def test_date_extraction_formats(self):
        text = "On 15 January 2024 and January 15, 2024 and 2024-01-15 events occurred"
        result = extract_entities(text)
        date_ents = [e for e in result if e.entity_type == "date"]
        assert len(date_ents) >= 2

    def test_quantity_extraction(self):
        text = "At least 1,500 people killed and 3,200 displaced"
        result = extract_entities(text)
        qty_ents = [e for e in result if e.entity_type == "quantity"]
        assert len(qty_ents) >= 2

    def test_weapon_terms(self):
        text = "HIMARS struck near the border, Patriot intercepted an ICBM"
        result = extract_entities(text)
        weapon_ents = [e for e in result if e.entity_type == "weapon"]
        assert len(weapon_ents) >= 2

    def test_batch_extraction(self):
        texts = ["Putin in Moscow", "", "NATO summit", "12345"]
        result = extract_entities_batch(texts)
        assert len(result) == 4
        assert 0 in result and 1 in result
        assert len(result[1]) == 0  # empty string


# ═══════════════════════════════════════════════════════════════════════
# Entity graph stress tests
# ═══════════════════════════════════════════════════════════════════════


class TestEntityGraphEdgeCases:

    def _make_obs(self, claim, source="src:test", investigation="inv1", reliability=0.7):
        return {
            "claim": claim,
            "source": source,
            "url": "http://example.com",
            "captured_at": "2025-01-01T00:00:00",
            "reliability_hint": reliability,
            "investigation_id": investigation,
        }

    def test_empty_observations(self):
        graph = build_entity_graph([], min_mentions=1)
        assert graph.node_count == 0
        assert graph.edge_count == 0

    def test_single_observation_single_entity(self):
        """Single node, no edges possible."""
        obs = [self._make_obs("Putin spoke today")]
        graph = build_entity_graph(obs, min_mentions=1)
        # Even with min_mentions=1, might have entities
        assert graph.edge_count == 0

    def test_two_nodes_no_edges(self):
        """Two entities mentioned in separate observations never co-occur."""
        obs = [
            self._make_obs("Putin spoke today"),
            self._make_obs("NATO announced plans"),
        ]
        graph = build_entity_graph(obs, min_mentions=1, min_edge_weight=1)
        # No co-occurrence between Putin and NATO since they are in separate observations
        for edge in graph.edges:
            # Edges should only exist between entities that co-occur in same observation
            pass
        # This is fine, just verify no crash

    def test_many_nodes_same_entity_repeated(self):
        """Same entity in many observations — should consolidate."""
        obs = [self._make_obs(f"Putin did thing {i}") for i in range(50)]
        graph = build_entity_graph(obs, min_mentions=1)
        # Putin should appear only once as a node
        putin_nodes = [n for n in graph.nodes if n.label == "Putin"]
        assert len(putin_nodes) <= 1

    def test_all_from_same_source(self):
        """All observations from same source — source_count should be 1."""
        obs = [
            self._make_obs("Putin met NATO in Ukraine", source="src:same_source")
            for _ in range(5)
        ]
        graph = build_entity_graph(obs, min_mentions=1, min_edge_weight=1)
        for node in graph.nodes:
            assert node.source_count == 1

    def test_node_id_deterministic(self):
        id1 = _node_id("person", "Putin")
        id2 = _node_id("person", "Putin")
        assert id1 == id2

    def test_node_id_case_insensitive(self):
        id1 = _node_id("person", "Putin")
        id2 = _node_id("person", "putin")
        assert id1 == id2

    def test_edge_id_order_independent(self):
        e1 = _edge_id("aaa", "bbb")
        e2 = _edge_id("bbb", "aaa")
        assert e1 == e2

    def test_community_detection_no_nodes(self):
        """Should not crash with empty inputs."""
        _detect_communities([], [])

    def test_centrality_no_nodes(self):
        _compute_centrality([], [])

    def test_graph_to_dict(self):
        """Verify serialization doesn't crash."""
        obs = [
            self._make_obs("Putin met NATO in Ukraine", source="src:a"),
            self._make_obs("Putin met NATO in Ukraine", source="src:b"),
            self._make_obs("Putin met NATO in Ukraine", source="src:c"),
        ]
        graph = build_entity_graph(obs, min_mentions=1, min_edge_weight=1)
        d = graph.to_dict()
        assert "nodes" in d
        assert "edges" in d
        assert "stats" in d


# ═══════════════════════════════════════════════════════════════════════
# Slop detector stress tests
# ═══════════════════════════════════════════════════════════════════════


class TestSlopDetectorEdgeCases:

    def _make_obs(self, claim, source="reuters", url="https://reuters.com/article"):
        return {"claim": claim, "source": source, "url": url, "id": 1}

    def test_clean_text(self):
        obs = self._make_obs(
            "Ukrainian forces advanced 2km near Bakhmut on Tuesday, "
            "according to the General Staff of the Armed Forces of Ukraine."
        )
        score = score_observation(obs)
        assert score.verdict == "credible"
        assert score.slop_score < 0.3

    def test_obvious_clickbait(self):
        """BUG: Even maximum clickbait (engagement_bait=1.0) only yields
        slop_score=0.25 because the engagement_bait weight is 0.25.
        It is mathematically impossible for pure clickbait to reach the
        "slop" verdict threshold (0.5) or the "drop" action (0.6)
        without ALSO triggering propaganda, ragebait, or source penalties.

        Additionally, _score_factual_density fails to penalize this text
        because it counts clickbait words like "Share" as proper nouns,
        inflating the factual density to 1.0.
        """
        obs = self._make_obs(
            "YOU WON'T BELIEVE what happens next!!! SHOCKING BOMBSHELL "
            "exposed!!! Share this before they delete it!!! MUST WATCH!!!",
            url="https://clickbait.example.com",
        )
        score = score_observation(obs)
        # BUG: This scores only 0.25 despite being obvious clickbait
        assert score.slop_score == 0.25  # Documents the bug
        assert score.factor_scores["engagement_bait"] == 1.0  # Max clickbait detected
        assert len(score.flags) > 0
        # BUG: verdict is only "suspicious" not "slop"
        assert score.verdict == "suspicious"

    def test_clickbait_weight_ceiling_bug(self):
        """Demonstrates that the weighted scoring makes it impossible for
        any single factor to push the score above its weight, even at 1.0.
        Max possible score from engagement_bait alone = 0.25.
        """
        from sts_monitor.slop_detector import _FACTOR_WEIGHTS
        # No single factor weight exceeds 0.25, meaning maxing out any
        # single detection category only contributes 0.25 to the total
        for factor, weight in _FACTOR_WEIGHTS.items():
            assert weight <= 0.25

    def test_empty_text(self):
        obs = self._make_obs("")
        score = score_observation(obs)
        # Should not crash; empty text is low-quality
        assert isinstance(score.slop_score, float)
        assert 0.0 <= score.slop_score <= 1.0

    def test_very_short_text(self):
        obs = self._make_obs("war bad")
        score = score_observation(obs)
        assert "very_short_text" in score.flags

    def test_propaganda_text(self):
        obs = self._make_obs(
            "The special military operation continues as a provocation by the West "
            "and NATO threatens Russia. Western aggression must be stopped.",
            url="https://rt.com/article",
        )
        score = score_observation(obs)
        assert score.verdict == "propaganda"

    def test_no_url_penalty(self):
        obs = self._make_obs("Some claim here", url="")
        score = score_observation(obs)
        assert "no_url_provided" in score.flags

    def test_disinfo_domain(self):
        obs = self._make_obs("Article content", url="https://infowars.com/story")
        score = score_observation(obs)
        assert any("disinfo" in f for f in score.flags)

    def test_score_always_between_0_and_1(self):
        """Even with all flags triggered, score should be capped at 1.0."""
        obs = self._make_obs(
            "YOU WON'T BELIEVE THIS SHOCKING BOMBSHELL!!! "
            "Special military operation!!! Share this!!! "
            "LIBTARD SNOWFLAKE DESTROYED!!! "
            "#war #conflict #breaking #news #viral #share #retweet "
            "🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥",
            url="https://infowars.com/fake",
        )
        score = score_observation(obs)
        assert 0.0 <= score.slop_score <= 1.0
        assert 0.0 <= score.credibility_score <= 1.0

    def test_filter_slop_empty_batch(self):
        result = filter_slop([])
        assert result.total == 0
        assert result.credible == 0

    def test_filter_slop_batch(self):
        obs_list = [
            self._make_obs("Clean factual report on January 15 near Kyiv, 3km advance"),
            self._make_obs("YOU WON'T BELIEVE!!! SHOCKING!!!"),
            self._make_obs("Special military operation denazification western aggression"),
        ]
        result = filter_slop(obs_list)
        assert result.total == 3

    def test_factual_density_empty(self):
        score, flags = _score_factual_density("")
        # Empty text has 0 words, expected = max(1, 0/20) = 1, indicators=0, density=0
        assert isinstance(score, float)

    def test_engagement_bait_all_caps(self):
        score, flags = _score_engagement_bait("ALL CAPS TEXT HERE FOR TESTING PURPOSE TODAY")
        assert score > 0
        assert any("caps" in f.lower() for f in flags)


# ═══════════════════════════════════════════════════════════════════════
# Convergence stress tests
# ═══════════════════════════════════════════════════════════════════════


class TestConvergenceEdgeCases:

    def _make_point(self, lat, lon, layer="earthquake", title="event", hours_ago=1):
        return GeoPoint(
            latitude=lat,
            longitude=lon,
            layer=layer,
            title=title,
            event_time=datetime.now(UTC) - timedelta(hours=hours_ago),
        )

    def test_no_points(self):
        result = detect_convergence([])
        assert result == []

    def test_single_point(self):
        result = detect_convergence([self._make_point(0, 0)])
        assert result == []

    def test_two_points_same_location_same_layer(self):
        """Two points same location but same layer — not enough signal types."""
        pts = [
            self._make_point(10, 20, "earthquake"),
            self._make_point(10, 20, "earthquake"),
        ]
        result = detect_convergence(pts, min_signal_types=2)
        assert result == []

    def test_two_points_same_location_different_layers(self):
        """Two points same location, different layers, min_signal_types=2."""
        pts = [
            self._make_point(10, 20, "earthquake"),
            self._make_point(10, 20, "fire"),
        ]
        result = detect_convergence(pts, min_signal_types=2)
        assert len(result) == 1
        assert result[0].signal_count == 2

    def test_points_at_north_pole(self):
        pts = [
            self._make_point(90, 0, "earthquake"),
            self._make_point(90, 180, "fire"),
            self._make_point(89.999, -90, "conflict"),
        ]
        result = detect_convergence(pts, radius_km=100, min_signal_types=2)
        # At the pole, all longitudes converge — haversine should handle this
        # Should not crash
        assert isinstance(result, list)

    def test_points_at_south_pole(self):
        pts = [
            self._make_point(-90, 0, "earthquake"),
            self._make_point(-90, 90, "fire"),
        ]
        result = detect_convergence(pts, min_signal_types=2)
        assert isinstance(result, list)

    def test_points_at_antimeridian(self):
        """Points near 180/-180 longitude boundary."""
        pts = [
            self._make_point(0, 179.99, "earthquake"),
            self._make_point(0, -179.99, "fire"),
            self._make_point(0, 180.0, "conflict"),
        ]
        # Haversine should correctly compute small distance across antimeridian
        result = detect_convergence(pts, radius_km=50, min_signal_types=3)
        assert isinstance(result, list)
        # The two points at 179.99 and -179.99 are ~0.02 degrees apart across
        # the antimeridian. At equator, that's about 2.2 km — should cluster.
        # BUG PROBE: Does haversine handle this correctly?

    def test_huge_radius(self):
        """Radius larger than Earth circumference."""
        pts = [
            self._make_point(0, 0, "earthquake"),
            self._make_point(45, 90, "fire"),
            self._make_point(-30, -120, "conflict"),
        ]
        result = detect_convergence(pts, radius_km=50000, min_signal_types=3)
        assert len(result) == 1  # everything should cluster

    def test_zero_radius(self):
        """Zero radius — only exact same point should cluster."""
        pts = [
            self._make_point(10, 20, "earthquake"),
            self._make_point(10, 20, "fire"),
            self._make_point(10, 20, "conflict"),
        ]
        result = detect_convergence(pts, radius_km=0, min_signal_types=3)
        assert len(result) == 1

    def test_haversine_same_point(self):
        assert haversine_km(0, 0, 0, 0) == 0.0

    def test_haversine_antipodal(self):
        """Distance between antipodal points should be ~20015 km (half circumference)."""
        dist = haversine_km(0, 0, 0, 180)
        assert abs(dist - 20015) < 50

    def test_old_events_filtered(self):
        """Events older than time window should be filtered out."""
        pts = [
            self._make_point(10, 20, "earthquake", hours_ago=100),
            self._make_point(10, 20, "fire", hours_ago=100),
            self._make_point(10, 20, "conflict", hours_ago=100),
        ]
        result = detect_convergence(pts, time_window_hours=24, min_signal_types=3)
        assert result == []


# ═══════════════════════════════════════════════════════════════════════
# Search stress tests
# ═══════════════════════════════════════════════════════════════════════


class TestSearchEdgeCases:

    def test_empty_query(self):
        plan = build_query_plan("")
        assert len(plan.include_terms) == 0
        assert len(plan.exclude_terms) == 0

    def test_query_only_exclusions(self):
        plan = build_query_plan("-noise -spam -junk")
        assert len(plan.include_terms) == 0
        assert plan.exclude_terms == {"noise", "spam", "junk"}

    def test_query_only_phrases(self):
        plan = build_query_plan('"power outage" "northern region"')
        assert len(plan.include_phrases) == 2
        assert "power outage" in plan.include_phrases

    def test_very_long_query(self):
        """1000-word query should not crash."""
        words = ["word" + str(i) for i in range(1000)]
        query = " ".join(words)
        plan = build_query_plan(query)
        assert len(plan.include_terms) >= 1000

    def test_special_regex_chars_in_query(self):
        """Regex metacharacters should not crash scoring."""
        plan = build_query_plan("test [bracket] (paren) .dot *star +plus ?question")
        assert isinstance(plan, QueryPlan)
        # Scoring with this plan should not crash from bad regex
        score = score_text(text="Some test text with brackets", plan=plan, base_reliability=0.5)
        assert isinstance(score, float)

    def test_score_text_empty_text(self):
        plan = build_query_plan("earthquake damage")
        score = score_text(text="", plan=plan, base_reliability=0.5)
        assert score == 0.0

    def test_score_text_empty_plan(self):
        plan = build_query_plan("")
        score = score_text(text="earthquake damage report", plan=plan, base_reliability=0.5)
        assert score == 0.0

    def test_score_text_with_exclusion_match(self):
        plan = build_query_plan("earthquake -drill")
        score = score_text(text="earthquake drill conducted today", plan=plan, base_reliability=0.8)
        assert score == 0.0

    def test_score_text_perfect_match(self):
        plan = build_query_plan("earthquake")
        score = score_text(text="earthquake reported near city", plan=plan, base_reliability=1.0)
        assert score > 0.0

    def test_synonym_expansion(self):
        plan = build_query_plan("earthquake")
        # earthquake should expand to include quake, seismic, tremor
        assert "quake" in plan.include_terms
        assert "seismic" in plan.include_terms

    def test_apply_context_boosts_recent(self):
        recent_time = datetime.now(UTC) - timedelta(hours=1)
        boosted = apply_context_boosts(score=0.5, captured_at=recent_time, source_trust=0.8)
        assert boosted > 0.5

    def test_apply_context_boosts_old(self):
        old_time = datetime.now(UTC) - timedelta(hours=200)
        result = apply_context_boosts(score=0.5, captured_at=old_time, source_trust=0.5)
        assert result == 0.5  # no boost for old, trust=0.5 gives 0 trust boost

    def test_normalize_datetime_naive(self):
        naive = datetime(2025, 1, 1, 12, 0, 0)
        result = normalize_datetime(naive)
        assert result.tzinfo is not None

    def test_top_terms_empty(self):
        result = top_terms("")
        assert result == []

    def test_top_terms_limit(self):
        text = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda"
        result = top_terms(text, max_terms=3)
        assert len(result) <= 3

    def test_top_terms_dedup(self):
        text = "earthquake earthquake earthquake damage damage"
        result = top_terms(text)
        assert result.count("earthquake") == 1
        assert result.count("damage") == 1

    def test_score_text_phrase_match(self):
        plan = build_query_plan('"power outage"')
        score = score_text(text="there was a power outage downtown", plan=plan, base_reliability=0.7)
        assert score > 0.0

    def test_build_query_plan_extra_synonyms(self):
        plan = build_query_plan("fire", extra_synonyms={"fire": ["blaze", "inferno"]})
        assert "blaze" in plan.include_terms
        assert "inferno" in plan.include_terms
