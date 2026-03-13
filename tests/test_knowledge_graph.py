"""Tests for the knowledge graph engine and API endpoints."""
from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from sts_monitor.database import Base, engine
from sts_monitor.main import app

AUTH = {"X-API-Key": "change-me"}


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch):
    import sts_monitor.rate_limit as rl
    monkeypatch.setattr(rl, "RATE_LIMIT_ENABLED", False)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


@pytest.fixture
def client():
    return TestClient(app)


def _create_inv(client, topic="test topic", **kwargs):
    resp = client.post("/investigations", json={"topic": topic, **kwargs}, headers=AUTH)
    assert resp.status_code == 200
    return resp.json()["id"]


def _ingest_and_run(client, inv_id, batch_size=20):
    resp = client.post(
        f"/investigations/{inv_id}/ingest/simulated",
        json={"batch_size": batch_size, "include_noise": True},
        headers=AUTH,
    )
    assert resp.status_code == 200
    resp = client.post(
        f"/investigations/{inv_id}/run",
        json={"use_llm": False},
        headers=AUTH,
    )
    assert resp.status_code == 200
    return resp.json()


def _extract_entities(client, inv_id):
    resp = client.post(
        f"/investigations/{inv_id}/extract-entities",
        json={},
        headers=AUTH,
    )
    assert resp.status_code == 200
    return resp.json()


def _cluster_stories(client, inv_id):
    resp = client.post(
        f"/investigations/{inv_id}/cluster-stories",
        json={"min_cluster_size": 2},
        headers=AUTH,
    )
    assert resp.status_code == 200
    return resp.json()


# ═══════════════════════════════════════════════════════════════════════
# Knowledge Graph Module Unit Tests
# ═══════════════════════════════════════════════════════════════════════

class TestKnowledgeGraphModule:
    def test_node_id_deterministic(self):
        from sts_monitor.knowledge_graph import _node_id
        a = _node_id("entity", "person:putin")
        b = _node_id("entity", "person:putin")
        assert a == b

    def test_node_id_different_types(self):
        from sts_monitor.knowledge_graph import _node_id
        a = _node_id("entity", "person:putin")
        b = _node_id("story", "person:putin")
        assert a != b

    def test_kg_node_to_dict(self):
        from sts_monitor.knowledge_graph import KGNode
        node = KGNode(id="n1", label="Test", node_type="entity", properties={"foo": "bar"})
        d = node.to_dict()
        assert d["id"] == "n1"
        assert d["label"] == "Test"
        assert d["type"] == "entity"
        assert d["foo"] == "bar"

    def test_kg_edge_to_dict(self):
        from sts_monitor.knowledge_graph import KGEdge
        edge = KGEdge(source="a", target="b", edge_type="co_occurs", weight=3.0)
        d = edge.to_dict()
        assert d["source"] == "a"
        assert d["target"] == "b"
        assert d["type"] == "co_occurs"
        assert d["weight"] == 3.0

    def test_knowledge_graph_to_dict(self):
        from sts_monitor.knowledge_graph import KnowledgeGraph, KGNode, KGEdge
        kg = KnowledgeGraph(
            nodes=[KGNode(id="n1", label="A", node_type="entity")],
            edges=[KGEdge(source="n1", target="n2", edge_type="mentions")],
        )
        d = kg.to_dict()
        assert d["node_count"] == 1
        assert d["edge_count"] == 1
        assert d["node_types"] == {"entity": 1}
        assert d["edge_types"] == {"mentions": 1}

    def test_empty_graph(self):
        from sts_monitor.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph()
        assert kg.node_count == 0
        assert kg.edge_count == 0
        d = kg.to_dict()
        assert d["nodes"] == []
        assert d["edges"] == []


# ═══════════════════════════════════════════════════════════════════════
# Knowledge Graph API Tests
# ═══════════════════════════════════════════════════════════════════════

class TestKnowledgeGraphAPI:
    def test_empty_graph(self, client):
        """KG with no investigations returns empty graph."""
        resp = client.post("/knowledge-graph", json={}, headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_count"] == 0
        assert data["edge_count"] == 0

    def test_graph_with_investigation(self, client):
        """KG with a single investigation returns at least the investigation node."""
        inv_id = _create_inv(client, "knowledge graph test")
        resp = client.post("/knowledge-graph", json={}, headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_count"] >= 1
        inv_nodes = [n for n in data["nodes"] if n["type"] == "investigation"]
        assert len(inv_nodes) >= 1
        assert any("knowledge graph test" in n["label"] for n in inv_nodes)

    def test_graph_with_data(self, client):
        """KG with ingested/processed data has entities and edges."""
        inv_id = _create_inv(client, "earthquake analysis")
        _ingest_and_run(client, inv_id, batch_size=25)
        _extract_entities(client, inv_id)
        _cluster_stories(client, inv_id)

        resp = client.post("/knowledge-graph", json={}, headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_count"] > 1
        # Should have investigation + entities at minimum
        node_types = data.get("node_types", {})
        assert "investigation" in node_types

    def test_graph_filter_by_investigation(self, client):
        """Can filter KG to specific investigations."""
        inv1 = _create_inv(client, "topic A")
        inv2 = _create_inv(client, "topic B")

        resp = client.post(
            "/knowledge-graph",
            json={"investigation_ids": [inv1]},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        inv_nodes = [n for n in data["nodes"] if n["type"] == "investigation"]
        assert len(inv_nodes) == 1
        assert inv_nodes[0]["label"] == "topic A"

    def test_graph_include_observations(self, client):
        """Include observations flag adds observation nodes."""
        inv_id = _create_inv(client, "obs test")
        _ingest_and_run(client, inv_id, batch_size=10)

        # Without observations
        resp1 = client.post(
            "/knowledge-graph",
            json={"include_observations": False},
            headers=AUTH,
        )
        data1 = resp1.json()
        obs_count1 = data1.get("node_types", {}).get("observation", 0)

        # With observations
        resp2 = client.post(
            "/knowledge-graph",
            json={"include_observations": True},
            headers=AUTH,
        )
        data2 = resp2.json()
        obs_count2 = data2.get("node_types", {}).get("observation", 0)

        assert obs_count2 > obs_count1

    def test_graph_min_entity_mentions(self, client):
        """Higher min_entity_mentions reduces entity nodes."""
        inv_id = _create_inv(client, "entity filter test")
        _ingest_and_run(client, inv_id, batch_size=30)
        _extract_entities(client, inv_id)

        resp1 = client.post(
            "/knowledge-graph",
            json={"min_entity_mentions": 1},
            headers=AUTH,
        )
        resp2 = client.post(
            "/knowledge-graph",
            json={"min_entity_mentions": 10},
            headers=AUTH,
        )
        data1 = resp1.json()
        data2 = resp2.json()
        ent1 = data1.get("node_types", {}).get("entity", 0)
        ent2 = data2.get("node_types", {}).get("entity", 0)
        assert ent1 >= ent2

    def test_graph_summary_endpoint(self, client):
        """Summary endpoint returns counts without full node data."""
        _create_inv(client, "summary test")
        resp = client.get("/knowledge-graph/summary", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "node_count" in data
        assert "edge_count" in data
        assert "stats" in data
        assert "nodes" not in data  # summary doesn't include full data

    def test_graph_no_auth(self, client):
        """KG endpoints require auth."""
        resp = client.post("/knowledge-graph", json={})
        assert resp.status_code == 401
        resp = client.get("/knowledge-graph/summary")
        assert resp.status_code == 401

    def test_graph_validation(self, client):
        """Request validation works."""
        resp = client.post(
            "/knowledge-graph",
            json={"max_entities": 5},  # below minimum of 10
            headers=AUTH,
        )
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════
# Cross-investigation Knowledge Graph Tests
# ═══════════════════════════════════════════════════════════════════════

class TestCrossInvestigationGraph:
    def test_multiple_investigations(self, client):
        """KG across multiple investigations shows all of them."""
        inv1 = _create_inv(client, "crisis in region A")
        inv2 = _create_inv(client, "response to crisis B")
        _ingest_and_run(client, inv1, batch_size=15)
        _ingest_and_run(client, inv2, batch_size=15)

        resp = client.post("/knowledge-graph", json={}, headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        inv_nodes = [n for n in data["nodes"] if n["type"] == "investigation"]
        assert len(inv_nodes) == 2

    def test_entities_link_to_investigations(self, client):
        """Entity nodes have edges to the investigations they appear in."""
        inv_id = _create_inv(client, "entity linking test")
        _ingest_and_run(client, inv_id, batch_size=20)
        _extract_entities(client, inv_id)

        resp = client.post(
            "/knowledge-graph",
            json={"min_entity_mentions": 1},
            headers=AUTH,
        )
        data = resp.json()
        mention_edges = [e for e in data["edges"] if e["type"] == "mentions"]
        # If entities exist, they should link to investigations
        entity_nodes = [n for n in data["nodes"] if n["type"] == "entity"]
        if entity_nodes:
            assert len(mention_edges) > 0

    def test_stories_link_to_investigations(self, client):
        """Story nodes have part_of edges to their investigations."""
        inv_id = _create_inv(client, "story linking test")
        _ingest_and_run(client, inv_id, batch_size=25)
        _cluster_stories(client, inv_id)

        resp = client.post("/knowledge-graph", json={}, headers=AUTH)
        data = resp.json()
        story_nodes = [n for n in data["nodes"] if n["type"] == "story"]
        if story_nodes:
            part_of_edges = [e for e in data["edges"] if e["type"] == "part_of"]
            assert len(part_of_edges) > 0


# ═══════════════════════════════════════════════════════════════════════
# Frontend Knowledge Graph Page Tests
# ═══════════════════════════════════════════════════════════════════════

class TestKnowledgeGraphFrontend:
    def test_spa_has_graph_page(self, client):
        resp = client.get("/static/index.html")
        assert resp.status_code == 200
        assert "KnowledgeGraphPage" in resp.text

    def test_spa_has_d3(self, client):
        resp = client.get("/static/index.html")
        assert "d3" in resp.text.lower()

    def test_spa_has_graph_nav(self, client):
        resp = client.get("/static/index.html")
        assert "graph" in resp.text

    def test_spa_has_force_simulation(self, client):
        resp = client.get("/static/index.html")
        assert "forceSimulation" in resp.text

    def test_spa_has_node_colors(self, client):
        resp = client.get("/static/index.html")
        for node_type in ["investigation", "entity", "story", "claim", "convergence_zone"]:
            assert node_type in resp.text

    def test_spa_calls_kg_api(self, client):
        resp = client.get("/static/index.html")
        assert "/knowledge-graph" in resp.text


# ═══════════════════════════════════════════════════════════════════════
# End-to-end Knowledge Graph Workflow
# ═══════════════════════════════════════════════════════════════════════

class TestKnowledgeGraphWorkflow:
    def test_full_workflow(self, client):
        """Create investigations, ingest data, extract entities, cluster stories, build KG."""
        # Create two investigations
        inv1 = _create_inv(client, "earthquake in Turkey")
        inv2 = _create_inv(client, "humanitarian response Turkey")

        # Ingest and process both
        _ingest_and_run(client, inv1, batch_size=20)
        _ingest_and_run(client, inv2, batch_size=20)

        # Extract entities
        _extract_entities(client, inv1)
        _extract_entities(client, inv2)

        # Cluster stories
        _cluster_stories(client, inv1)
        _cluster_stories(client, inv2)

        # Build knowledge graph across both
        resp = client.post(
            "/knowledge-graph",
            json={"min_entity_mentions": 1},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()

        # Validate structure
        assert data["node_count"] >= 2  # at least the 2 investigations
        assert "nodes" in data
        assert "edges" in data
        assert "node_types" in data
        assert "edge_types" in data
        assert "stats" in data

        # Validate node structure
        for node in data["nodes"]:
            assert "id" in node
            assert "label" in node
            assert "type" in node

        # Validate edge structure
        for edge in data["edges"]:
            assert "source" in edge
            assert "target" in edge
            assert "type" in edge

        # Summary should also work
        resp = client.get("/knowledge-graph/summary", headers=AUTH)
        assert resp.status_code == 200
        summary = resp.json()
        assert summary["node_count"] == data["node_count"]
        assert summary["edge_count"] == data["edge_count"]
