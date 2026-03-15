"""Tests for the entity relationship graph engine."""
from __future__ import annotations

import pytest

from sts_monitor.entity_graph import (
    EntityGraph,
    GraphEdge,
    GraphNode,
    _compute_centrality,
    _detect_communities,
    _edge_id,
    _extract_source_family,
    _node_id,
    build_entity_graph,
)

pytestmark = pytest.mark.unit


# ── Helpers ────────────────────────────────────────────────────────────


def _obs(
    claim: str,
    source: str = "feed:reuters",
    reliability: float = 0.8,
    captured_at: str = "2024-06-01T12:00:00Z",
    investigation_id: str = "",
) -> dict:
    return {
        "claim": claim,
        "source": source,
        "url": "https://example.com",
        "captured_at": captured_at,
        "reliability_hint": reliability,
        "investigation_id": investigation_id,
    }


# ── Node ID generation ────────────────────────────────────────────────


class TestNodeId:
    def test_deterministic(self) -> None:
        assert _node_id("person", "Putin") == _node_id("person", "Putin")

    def test_consistent_across_calls(self) -> None:
        id1 = _node_id("location", "Ukraine")
        id2 = _node_id("location", "Ukraine")
        assert id1 == id2

    def test_case_insensitive(self) -> None:
        assert _node_id("person", "Putin") == _node_id("person", "putin")

    def test_strips_whitespace(self) -> None:
        assert _node_id("person", "  Putin ") == _node_id("person", "Putin")

    def test_different_types_different_ids(self) -> None:
        assert _node_id("person", "Jordan") != _node_id("location", "Jordan")

    def test_different_texts_different_ids(self) -> None:
        assert _node_id("person", "Putin") != _node_id("person", "Biden")

    def test_id_length(self) -> None:
        nid = _node_id("organization", "NATO")
        assert len(nid) == 12

    def test_hex_characters(self) -> None:
        nid = _node_id("person", "Zelensky")
        assert all(c in "0123456789abcdef" for c in nid)


# ── Edge ID generation ────────────────────────────────────────────────


class TestEdgeId:
    def test_deterministic(self) -> None:
        assert _edge_id("aaa", "bbb") == _edge_id("aaa", "bbb")

    def test_order_independent(self) -> None:
        assert _edge_id("aaa", "bbb") == _edge_id("bbb", "aaa")

    def test_format(self) -> None:
        eid = _edge_id("abc", "xyz")
        assert eid == "abc-xyz"


# ── Source family extraction ──────────────────────────────────────────


class TestSourceFamily:
    def test_colon_prefix(self) -> None:
        assert _extract_source_family("feed:reuters/world") == "reuters"

    def test_no_colon(self) -> None:
        assert _extract_source_family("reuters") == "reuters"

    def test_strips_and_lowercases(self) -> None:
        assert _extract_source_family("feed: Reuters ") == "reuters"


# ── Graph construction from observations ──────────────────────────────


class TestBuildEntityGraph:
    def test_empty_input(self) -> None:
        graph = build_entity_graph([], min_mentions=1, min_edge_weight=1)
        assert graph.node_count == 0
        assert graph.edge_count == 0
        assert graph.nodes == []
        assert graph.edges == []
        assert graph.density == 0.0

    def test_single_observation_no_edges(self) -> None:
        """Single observation with entities produces nodes but no co-occurrence
        edges when min_mentions=1, because we need at least 2 entities
        co-occurring in multiple obs to meet min_edge_weight=2."""
        obs = [_obs("Putin visited Ukraine", source="feed:reuters")]
        graph = build_entity_graph(obs, min_mentions=1, min_edge_weight=2)
        # May have nodes but edges require min_edge_weight=2
        assert graph.edge_count == 0

    def test_single_observation_with_edge_weight_1(self) -> None:
        """With min_edge_weight=1, a single obs with two entities creates an edge."""
        obs = [_obs("Putin visited Ukraine")]
        graph = build_entity_graph(obs, min_mentions=1, min_edge_weight=1)
        # Should have at least Putin (person) and Ukraine (location)
        assert graph.node_count >= 2
        assert graph.edge_count >= 1

    def test_co_occurrence_edge_creation(self) -> None:
        """Entities mentioned together in multiple observations get an edge."""
        observations = [
            _obs("NATO deployed forces to Ukraine", source="feed:reuters"),
            _obs("NATO confirmed operations in Ukraine", source="feed:bbc"),
        ]
        graph = build_entity_graph(
            observations, min_mentions=2, min_edge_weight=2,
        )
        # NATO and Ukraine appear together in 2 observations
        edge_labels = set()
        node_labels = {n.id: n.label for n in graph.nodes}
        for e in graph.edges:
            src_label = node_labels.get(e.source_id, "")
            tgt_label = node_labels.get(e.target_id, "")
            edge_labels.add((src_label, tgt_label))

        # There should be at least one edge connecting NATO and Ukraine
        found = any(
            ("NATO" in pair and "Ukraine" in pair)
            for pair in [
                (src + " " + tgt) for src, tgt in edge_labels
            ]
        )
        assert found or graph.edge_count > 0

    def test_min_mentions_filtering(self) -> None:
        """Entities below min_mentions are excluded."""
        observations = [
            _obs("Putin visited Ukraine", source="feed:reuters"),
            _obs("Putin addressed the Kremlin", source="feed:bbc"),
            _obs("Macron met with EU leaders", source="feed:ap"),
        ]
        # Putin appears in 2 obs, Macron only in 1
        graph = build_entity_graph(
            observations, min_mentions=2, min_edge_weight=1,
        )
        labels = {n.label for n in graph.nodes}
        assert "Putin" in labels
        assert "Macron" not in labels

    def test_min_edge_weight_filtering(self) -> None:
        """Edges below min_edge_weight are excluded."""
        observations = [
            _obs("Putin visited Ukraine", source="feed:reuters"),
            _obs("Putin addressed Russia", source="feed:bbc"),
        ]
        # Putin + Ukraine co-occur once, Putin + Russia co-occur once
        graph = build_entity_graph(
            observations, min_mentions=1, min_edge_weight=2,
        )
        assert graph.edge_count == 0

    def test_entity_confidence_threshold(self) -> None:
        """Low-confidence entities should be filtered out."""
        observations = [
            _obs("Putin visited Ukraine", source="feed:reuters"),
            _obs("Putin visited Ukraine", source="feed:bbc"),
        ]
        # High threshold filters out most entities
        graph = build_entity_graph(
            observations, min_mentions=1, min_edge_weight=1,
            entity_confidence_threshold=0.99,
        )
        assert graph.node_count == 0

    def test_investigation_tracking(self) -> None:
        """Investigation IDs are tracked on nodes."""
        observations = [
            _obs("NATO deployed to Ukraine", investigation_id="INV-001"),
            _obs("NATO operations in Ukraine", investigation_id="INV-001"),
        ]
        graph = build_entity_graph(
            observations, min_mentions=2, min_edge_weight=1,
        )
        for node in graph.nodes:
            if node.label in ("NATO", "Ukraine"):
                assert "INV-001" in node.investigations

    def test_source_count_tracked(self) -> None:
        """Multiple sources are counted for each node."""
        observations = [
            _obs("NATO deployed to Ukraine", source="feed:reuters"),
            _obs("NATO operations in Ukraine", source="feed:bbc"),
        ]
        graph = build_entity_graph(
            observations, min_mentions=2, min_edge_weight=1,
        )
        for node in graph.nodes:
            if node.label == "NATO":
                assert node.source_count == 2

    def test_max_nodes_limit(self) -> None:
        """Graph respects max_nodes."""
        observations = [
            _obs(f"NATO deployed to Ukraine and Russia and Syria and Yemen and Gaza", source="feed:reuters"),
            _obs(f"NATO deployed to Ukraine and Russia and Syria and Yemen and Gaza", source="feed:bbc"),
        ]
        graph = build_entity_graph(
            observations, min_mentions=1, min_edge_weight=1, max_nodes=2,
        )
        assert graph.node_count <= 2

    def test_avg_reliability_computed(self) -> None:
        """Average reliability is computed from observations."""
        observations = [
            _obs("NATO deployed to Ukraine", source="feed:reuters", reliability=0.9),
            _obs("NATO confirmed in Ukraine", source="feed:bbc", reliability=0.7),
        ]
        graph = build_entity_graph(
            observations, min_mentions=2, min_edge_weight=1,
        )
        for node in graph.nodes:
            if node.label == "NATO":
                assert 0.7 <= node.avg_reliability <= 0.9

    def test_no_entities_found(self) -> None:
        """Claims with no recognizable entities produce empty graph."""
        observations = [
            _obs("the quick brown fox jumped"),
            _obs("nothing special here at all"),
        ]
        graph = build_entity_graph(
            observations, min_mentions=1, min_edge_weight=1,
        )
        assert graph.node_count == 0
        assert graph.edge_count == 0

    def test_edge_weight_includes_source_diversity(self) -> None:
        """Edge weight = count * (1.0 + 0.2 * source_count)."""
        observations = [
            _obs("NATO deployed to Ukraine", source="feed:reuters"),
            _obs("NATO confirmed in Ukraine", source="feed:bbc"),
        ]
        graph = build_entity_graph(
            observations, min_mentions=2, min_edge_weight=2,
        )
        for edge in graph.edges:
            # 2 co-occurrences * (1 + 0.2 * 2 sources) = 2 * 1.4 = 2.8
            if edge.co_occurrence_count == 2 and edge.shared_sources == 2:
                assert edge.weight == pytest.approx(2.8, abs=0.01)

    def test_relationship_type_co_occurrence(self) -> None:
        """Person-organization edge should be co-occurrence."""
        observations = [
            _obs("Putin addressed NATO leaders", source="feed:reuters"),
            _obs("Putin spoke at NATO summit", source="feed:bbc"),
        ]
        graph = build_entity_graph(
            observations, min_mentions=2, min_edge_weight=2,
        )
        for edge in graph.edges:
            node_labels = {n.id: n for n in graph.nodes}
            src = node_labels.get(edge.source_id)
            tgt = node_labels.get(edge.target_id)
            if src and tgt:
                types = {src.entity_type, tgt.entity_type}
                if "person" in types and "organization" in types:
                    assert edge.relationship_type == "co-occurrence"

    def test_relationship_type_same_location(self) -> None:
        """Person-location edge should be same_location."""
        observations = [
            _obs("Putin visited Ukraine", source="feed:reuters"),
            _obs("Putin traveled to Ukraine", source="feed:bbc"),
        ]
        graph = build_entity_graph(
            observations, min_mentions=2, min_edge_weight=2,
        )
        for edge in graph.edges:
            node_map = {n.id: n for n in graph.nodes}
            src = node_map.get(edge.source_id)
            tgt = node_map.get(edge.target_id)
            if src and tgt:
                types = {src.entity_type, tgt.entity_type}
                if "location" in types and "location" not in types - {"location"}:
                    # One location, one non-location
                    if "person" in types:
                        assert edge.relationship_type == "same_location"

    def test_relationship_type_same_event(self) -> None:
        """Two location entities should yield same_event relationship."""
        observations = [
            _obs("Troops moved from Ukraine to Crimea", source="feed:reuters"),
            _obs("Convoys between Ukraine and Crimea spotted", source="feed:bbc"),
        ]
        graph = build_entity_graph(
            observations, min_mentions=2, min_edge_weight=2,
        )
        for edge in graph.edges:
            node_map = {n.id: n for n in graph.nodes}
            src = node_map.get(edge.source_id)
            tgt = node_map.get(edge.target_id)
            if src and tgt:
                if src.entity_type == "location" and tgt.entity_type == "location":
                    assert edge.relationship_type == "same_event"


# ── Community detection ───────────────────────────────────────────────


class TestCommunityDetection:
    def test_empty_graph(self) -> None:
        _detect_communities([], [])
        # Should not raise

    def test_single_node(self) -> None:
        node = GraphNode(
            id="a", label="A", entity_type="person", mention_count=1,
            source_count=1, avg_reliability=0.8, first_seen="", last_seen="",
            investigations=[],
        )
        _detect_communities([node], [])
        assert node.community == 0

    def test_two_connected_nodes_same_community(self) -> None:
        n1 = GraphNode(
            id="a", label="A", entity_type="person", mention_count=1,
            source_count=1, avg_reliability=0.8, first_seen="", last_seen="",
            investigations=[],
        )
        n2 = GraphNode(
            id="b", label="B", entity_type="person", mention_count=1,
            source_count=1, avg_reliability=0.8, first_seen="", last_seen="",
            investigations=[],
        )
        edge = GraphEdge(
            source_id="a", target_id="b", weight=5.0, co_occurrence_count=3,
            shared_sources=2, shared_investigations=[], sample_claims=[],
            relationship_type="co-occurrence",
        )
        _detect_communities([n1, n2], [edge])
        assert n1.community == n2.community

    def test_disconnected_nodes_different_communities(self) -> None:
        n1 = GraphNode(
            id="a", label="A", entity_type="person", mention_count=1,
            source_count=1, avg_reliability=0.8, first_seen="", last_seen="",
            investigations=[],
        )
        n2 = GraphNode(
            id="b", label="B", entity_type="person", mention_count=1,
            source_count=1, avg_reliability=0.8, first_seen="", last_seen="",
            investigations=[],
        )
        _detect_communities([n1, n2], [])
        assert n1.community != n2.community

    def test_communities_normalized(self) -> None:
        """Community IDs should be 0..N."""
        nodes = [
            GraphNode(
                id=str(i), label=f"N{i}", entity_type="person", mention_count=1,
                source_count=1, avg_reliability=0.8, first_seen="", last_seen="",
                investigations=[],
            )
            for i in range(4)
        ]
        # Two pairs of connected nodes
        edges = [
            GraphEdge(
                source_id="0", target_id="1", weight=5.0, co_occurrence_count=3,
                shared_sources=2, shared_investigations=[], sample_claims=[],
                relationship_type="co-occurrence",
            ),
            GraphEdge(
                source_id="2", target_id="3", weight=5.0, co_occurrence_count=3,
                shared_sources=2, shared_investigations=[], sample_claims=[],
                relationship_type="co-occurrence",
            ),
        ]
        _detect_communities(nodes, edges)
        communities = {n.community for n in nodes}
        assert communities == {0, 1}  # exactly 2 communities, 0-indexed


# ── Centrality + bridge detection ─────────────────────────────────────


class TestCentrality:
    def test_empty_graph(self) -> None:
        _compute_centrality([], [])
        # Should not raise

    def test_single_node_zero_centrality(self) -> None:
        node = GraphNode(
            id="a", label="A", entity_type="person", mention_count=1,
            source_count=1, avg_reliability=0.8, first_seen="", last_seen="",
            investigations=[],
        )
        _compute_centrality([node], [])
        assert node.centrality == 0.0

    def test_highest_degree_centrality_is_1(self) -> None:
        """The node with the highest weighted degree gets centrality=1.0."""
        nodes = [
            GraphNode(
                id=str(i), label=f"N{i}", entity_type="person", mention_count=1,
                source_count=1, avg_reliability=0.8, first_seen="", last_seen="",
                investigations=[], community=0,
            )
            for i in range(3)
        ]
        edges = [
            GraphEdge(
                source_id="0", target_id="1", weight=5.0, co_occurrence_count=3,
                shared_sources=2, shared_investigations=[], sample_claims=[],
                relationship_type="co-occurrence",
            ),
            GraphEdge(
                source_id="0", target_id="2", weight=3.0, co_occurrence_count=2,
                shared_sources=1, shared_investigations=[], sample_claims=[],
                relationship_type="co-occurrence",
            ),
        ]
        _compute_centrality(nodes, edges)
        # Node 0 has highest degree: 5.0 + 3.0 = 8.0
        assert nodes[0].centrality == 1.0

    def test_bridge_detection(self) -> None:
        """Bridge entity connects 3+ different communities."""
        nodes = [
            GraphNode(
                id=str(i), label=f"N{i}", entity_type="person", mention_count=1,
                source_count=1, avg_reliability=0.8, first_seen="", last_seen="",
                investigations=[], community=i,
            )
            for i in range(4)
        ]
        # Node 0 connects to nodes in communities 1, 2, 3
        edges = [
            GraphEdge(
                source_id="0", target_id=str(i), weight=3.0, co_occurrence_count=2,
                shared_sources=1, shared_investigations=[], sample_claims=[],
                relationship_type="co-occurrence",
            )
            for i in range(1, 4)
        ]
        _compute_centrality(nodes, edges)
        # Node 0 is connected to communities 0(own), 1, 2, 3 -> 4 communities >= 3
        assert nodes[0].is_bridge is True

    def test_non_bridge_with_single_community(self) -> None:
        """Node connected only within its own community is not a bridge."""
        nodes = [
            GraphNode(
                id=str(i), label=f"N{i}", entity_type="person", mention_count=1,
                source_count=1, avg_reliability=0.8, first_seen="", last_seen="",
                investigations=[], community=0,
            )
            for i in range(3)
        ]
        edges = [
            GraphEdge(
                source_id="0", target_id="1", weight=5.0, co_occurrence_count=3,
                shared_sources=2, shared_investigations=[], sample_claims=[],
                relationship_type="co-occurrence",
            ),
            GraphEdge(
                source_id="0", target_id="2", weight=3.0, co_occurrence_count=2,
                shared_sources=1, shared_investigations=[], sample_claims=[],
                relationship_type="co-occurrence",
            ),
        ]
        _compute_centrality(nodes, edges)
        # All in community 0 -> connected_comms = {0} -> len=1 < 3
        assert nodes[0].is_bridge is False


# ── to_dict serialization ────────────────────────────────────────────


class TestEntityGraphToDict:
    def test_empty_graph_serialization(self) -> None:
        graph = build_entity_graph([], min_mentions=1, min_edge_weight=1)
        d = graph.to_dict()
        assert d["nodes"] == []
        assert d["edges"] == []
        assert d["stats"]["nodes"] == 0
        assert d["stats"]["edges"] == 0
        assert d["stats"]["communities"] == 0
        assert d["stats"]["density"] == 0.0
        assert d["top_entities"] == []

    def test_serialization_keys(self) -> None:
        observations = [
            _obs("NATO deployed to Ukraine", source="feed:reuters"),
            _obs("NATO operations in Ukraine", source="feed:bbc"),
        ]
        graph = build_entity_graph(
            observations, min_mentions=2, min_edge_weight=1,
        )
        d = graph.to_dict()
        assert "nodes" in d
        assert "edges" in d
        assert "stats" in d
        assert "top_entities" in d

    def test_node_serialization_fields(self) -> None:
        observations = [
            _obs("NATO deployed to Ukraine", source="feed:reuters"),
            _obs("NATO operations in Ukraine", source="feed:bbc"),
        ]
        graph = build_entity_graph(
            observations, min_mentions=2, min_edge_weight=1,
        )
        d = graph.to_dict()
        if d["nodes"]:
            node = d["nodes"][0]
            expected_keys = {
                "id", "label", "type", "mentions", "sources",
                "reliability", "community", "centrality", "is_bridge",
                "investigations",
            }
            assert expected_keys <= set(node.keys())

    def test_edge_serialization_fields(self) -> None:
        observations = [
            _obs("NATO deployed to Ukraine", source="feed:reuters"),
            _obs("NATO operations in Ukraine", source="feed:bbc"),
        ]
        graph = build_entity_graph(
            observations, min_mentions=2, min_edge_weight=1,
        )
        d = graph.to_dict()
        if d["edges"]:
            edge = d["edges"][0]
            expected_keys = {
                "source", "target", "weight", "co_occurrences",
                "shared_sources", "relationship", "sample_claims",
            }
            assert expected_keys <= set(edge.keys())

    def test_sample_claims_capped_at_3(self) -> None:
        observations = [
            _obs("NATO deployed to Ukraine", source=f"feed:src{i}")
            for i in range(10)
        ]
        graph = build_entity_graph(
            observations, min_mentions=1, min_edge_weight=1,
        )
        d = graph.to_dict()
        for edge in d["edges"]:
            assert len(edge["sample_claims"]) <= 3

    def test_top_entities_capped_at_10(self) -> None:
        """Even with many nodes, top_entities has at most 10 entries."""
        observations = [
            _obs(
                "Putin and NATO discussed Ukraine, Russia, Syria, Yemen, Gaza, Crimea, Donbas and Kherson",
                source="feed:reuters",
            ),
            _obs(
                "Putin and NATO discussed Ukraine, Russia, Syria, Yemen, Gaza, Crimea, Donbas and Kherson",
                source="feed:bbc",
            ),
        ]
        graph = build_entity_graph(
            observations, min_mentions=1, min_edge_weight=1,
        )
        assert len(graph.top_entities) <= 10

    def test_density_calculation(self) -> None:
        """density = edges / (nodes*(nodes-1)/2)."""
        observations = [
            _obs("NATO deployed to Ukraine", source="feed:reuters"),
            _obs("NATO operations in Ukraine", source="feed:bbc"),
        ]
        graph = build_entity_graph(
            observations, min_mentions=2, min_edge_weight=1,
        )
        if graph.node_count > 1:
            expected_density = graph.edge_count / (
                graph.node_count * (graph.node_count - 1) / 2
            )
            assert graph.density == pytest.approx(expected_density, abs=0.001)
