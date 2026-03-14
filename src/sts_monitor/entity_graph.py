"""Entity relationship graph engine.

Builds a link chart showing connections between entities (people, organizations,
locations, weapons) based on co-occurrence in observations. This is what
intelligence analysts actually use — who is connected to whom, what organizations
are linked to which events, which locations share actors.

Graph construction:
  1. Extract entities from all observations
  2. Build co-occurrence edges (entities mentioned in same observation)
  3. Weight edges by frequency + source diversity + reliability
  4. Detect communities (densely connected subgraphs)
  5. Identify bridge entities (connecting otherwise separate clusters)
  6. Export as nodes + edges for visualization

Output format is designed for force-directed graph rendering (D3/vis.js/Cytoscape).
"""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from sts_monitor.entities import extract_entities


@dataclass(slots=True)
class GraphNode:
    """A node in the entity relationship graph."""
    id: str
    label: str
    entity_type: str  # person, organization, location, weapon, event
    mention_count: int
    source_count: int  # how many different sources mention this
    avg_reliability: float
    first_seen: str
    last_seen: str
    investigations: list[str]  # which investigations reference this
    community: int = -1  # community detection cluster ID
    centrality: float = 0.0  # betweenness-like centrality
    is_bridge: bool = False  # connects otherwise separate clusters


@dataclass(slots=True)
class GraphEdge:
    """An edge connecting two entities that co-occur."""
    source_id: str
    target_id: str
    weight: float  # co-occurrence strength
    co_occurrence_count: int
    shared_sources: int  # how many sources mention both
    shared_investigations: list[str]
    sample_claims: list[str]  # example observations containing both
    relationship_type: str  # "co-occurrence", "same_event", "same_location"


@dataclass(slots=True)
class EntityGraph:
    """Complete entity relationship graph."""
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    node_count: int
    edge_count: int
    communities: int  # number of detected communities
    bridge_entities: list[str]  # entities connecting communities
    density: float  # graph density: edges / possible edges
    top_entities: list[dict[str, Any]]  # ranked by centrality

    def to_dict(self) -> dict[str, Any]:
        """Export for JSON serialization / visualization."""
        return {
            "nodes": [
                {
                    "id": n.id,
                    "label": n.label,
                    "type": n.entity_type,
                    "mentions": n.mention_count,
                    "sources": n.source_count,
                    "reliability": round(n.avg_reliability, 3),
                    "community": n.community,
                    "centrality": round(n.centrality, 4),
                    "is_bridge": n.is_bridge,
                    "investigations": n.investigations,
                }
                for n in self.nodes
            ],
            "edges": [
                {
                    "source": e.source_id,
                    "target": e.target_id,
                    "weight": round(e.weight, 3),
                    "co_occurrences": e.co_occurrence_count,
                    "shared_sources": e.shared_sources,
                    "relationship": e.relationship_type,
                    "sample_claims": e.sample_claims[:3],
                }
                for e in self.edges
            ],
            "stats": {
                "nodes": self.node_count,
                "edges": self.edge_count,
                "communities": self.communities,
                "density": round(self.density, 4),
                "bridge_entities": self.bridge_entities,
            },
            "top_entities": self.top_entities,
        }


# ── Node ID generation ─────────────────────────────────────────────────

def _node_id(entity_type: str, text: str) -> str:
    """Deterministic node ID from entity type + normalized text."""
    normalized = f"{entity_type}:{text.lower().strip()}"
    return hashlib.md5(normalized.encode()).hexdigest()[:12]


def _edge_id(source_id: str, target_id: str) -> str:
    """Deterministic edge ID — order-independent."""
    pair = tuple(sorted([source_id, target_id]))
    return f"{pair[0]}-{pair[1]}"


# ── Graph construction ─────────────────────────────────────────────────

def _extract_source_family(source: str) -> str:
    parts = source.split(":", 1)
    if len(parts) > 1:
        return parts[1].split("/")[0].strip().lower()
    return source.strip().lower()


def build_entity_graph(
    observations: list[dict[str, Any]],
    min_mentions: int = 2,
    min_edge_weight: int = 2,
    max_nodes: int = 200,
    entity_confidence_threshold: float = 0.5,
) -> EntityGraph:
    """Build entity relationship graph from observations.

    Each observation dict should have: claim, source, url, captured_at,
    reliability_hint, and optionally investigation_id.
    """
    # ── Step 1: Extract entities from all observations ──────────────
    node_data: dict[str, dict[str, Any]] = {}  # node_id -> metadata
    edge_data: dict[str, dict[str, Any]] = {}  # edge_id -> metadata
    obs_entities: list[tuple[dict[str, Any], list[str]]] = []  # (obs, [node_ids])

    for obs in observations:
        claim = obs.get("claim", "")
        source = obs.get("source", "")
        _url = obs.get("url", "")
        reliability = obs.get("reliability_hint", 0.5)
        captured = obs.get("captured_at", "")
        investigation = obs.get("investigation_id", "")
        source_family = _extract_source_family(source)

        entities = extract_entities(claim)
        # Filter by confidence
        entities = [e for e in entities if e.confidence >= entity_confidence_threshold]

        node_ids: list[str] = []

        for ent in entities:
            nid = _node_id(ent.entity_type, ent.text)
            node_ids.append(nid)

            if nid not in node_data:
                node_data[nid] = {
                    "label": ent.text,
                    "entity_type": ent.entity_type,
                    "mentions": 0,
                    "sources": set(),
                    "reliabilities": [],
                    "first_seen": captured,
                    "last_seen": captured,
                    "investigations": set(),
                }

            nd = node_data[nid]
            nd["mentions"] += 1
            nd["sources"].add(source_family)
            nd["reliabilities"].append(reliability)
            nd["last_seen"] = captured
            if investigation:
                nd["investigations"].add(investigation)

        obs_entities.append((obs, node_ids))

    # ── Step 2: Build co-occurrence edges ───────────────────────────
    for obs, node_ids in obs_entities:
        unique_ids = list(set(node_ids))
        source_family = _extract_source_family(obs.get("source", ""))
        investigation = obs.get("investigation_id", "")
        claim = obs.get("claim", "")

        for id_a, id_b in combinations(unique_ids, 2):
            eid = _edge_id(id_a, id_b)

            if eid not in edge_data:
                edge_data[eid] = {
                    "source_id": min(id_a, id_b),
                    "target_id": max(id_a, id_b),
                    "count": 0,
                    "sources": set(),
                    "investigations": set(),
                    "claims": [],
                }

            ed = edge_data[eid]
            ed["count"] += 1
            ed["sources"].add(source_family)
            if investigation:
                ed["investigations"].add(investigation)
            if len(ed["claims"]) < 5:
                ed["claims"].append(claim[:200])

    # ── Step 3: Filter by minimum thresholds ────────────────────────
    filtered_nodes = {
        nid: nd for nid, nd in node_data.items()
        if nd["mentions"] >= min_mentions
    }

    # Rank by mentions * source_diversity and take top N
    ranked = sorted(
        filtered_nodes.items(),
        key=lambda x: x[1]["mentions"] * len(x[1]["sources"]),
        reverse=True,
    )[:max_nodes]
    active_node_ids = {nid for nid, _ in ranked}

    # Build node objects
    nodes: list[GraphNode] = []
    for nid, nd in ranked:
        reliabilities = nd["reliabilities"]
        avg_rel = sum(reliabilities) / len(reliabilities) if reliabilities else 0.5
        nodes.append(GraphNode(
            id=nid,
            label=nd["label"],
            entity_type=nd["entity_type"],
            mention_count=nd["mentions"],
            source_count=len(nd["sources"]),
            avg_reliability=avg_rel,
            first_seen=str(nd["first_seen"]),
            last_seen=str(nd["last_seen"]),
            investigations=sorted(nd["investigations"]),
        ))

    # Build edge objects (only between active nodes)
    edges: list[GraphEdge] = []
    for eid, ed in edge_data.items():
        if ed["source_id"] not in active_node_ids or ed["target_id"] not in active_node_ids:
            continue
        if ed["count"] < min_edge_weight:
            continue

        # Weight = co-occurrence count * source diversity bonus
        weight = ed["count"] * (1.0 + 0.2 * len(ed["sources"]))

        # Determine relationship type
        source_node = node_data.get(ed["source_id"], {})
        target_node = node_data.get(ed["target_id"], {})
        source_type = source_node.get("entity_type", "")
        target_type = target_node.get("entity_type", "")

        if source_type == "location" and target_type == "location":
            rel_type = "same_event"
        elif "location" in (source_type, target_type):
            rel_type = "same_location"
        else:
            rel_type = "co-occurrence"

        edges.append(GraphEdge(
            source_id=ed["source_id"],
            target_id=ed["target_id"],
            weight=round(weight, 2),
            co_occurrence_count=ed["count"],
            shared_sources=len(ed["sources"]),
            shared_investigations=sorted(ed["investigations"]),
            sample_claims=ed["claims"][:3],
            relationship_type=rel_type,
        ))

    # ── Step 4: Community detection (simple label propagation) ──────
    _detect_communities(nodes, edges)

    # ── Step 5: Centrality + bridge detection ───────────────────────
    _compute_centrality(nodes, edges)

    bridge_entities = [n.label for n in nodes if n.is_bridge]

    # Stats
    n_nodes = len(nodes)
    n_edges = len(edges)
    possible_edges = n_nodes * (n_nodes - 1) / 2 if n_nodes > 1 else 1
    density = n_edges / possible_edges if possible_edges > 0 else 0.0
    n_communities = len({n.community for n in nodes if n.community >= 0})

    # Top entities by centrality
    top_entities = sorted(nodes, key=lambda n: n.centrality, reverse=True)[:10]
    top_list = [
        {
            "label": n.label,
            "type": n.entity_type,
            "centrality": round(n.centrality, 4),
            "mentions": n.mention_count,
            "sources": n.source_count,
            "community": n.community,
            "is_bridge": n.is_bridge,
        }
        for n in top_entities
    ]

    return EntityGraph(
        nodes=nodes,
        edges=edges,
        node_count=n_nodes,
        edge_count=n_edges,
        communities=n_communities,
        bridge_entities=bridge_entities,
        density=density,
        top_entities=top_list,
    )


# ── Community detection (label propagation) ────────────────────────────

def _detect_communities(nodes: list[GraphNode], edges: list[GraphEdge]) -> None:
    """Simple label propagation community detection.

    Each node starts in its own community, then iteratively adopts the most
    common community among its neighbors. Converges quickly for small graphs.
    """
    if not nodes:
        return

    node_map = {n.id: i for i, n in enumerate(nodes)}
    # Initialize: each node in its own community
    communities = list(range(len(nodes)))

    # Build adjacency list
    adj: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for edge in edges:
        i = node_map.get(edge.source_id)
        j = node_map.get(edge.target_id)
        if i is not None and j is not None:
            adj[i].append((j, edge.weight))
            adj[j].append((i, edge.weight))

    # Iterate (max 20 rounds)
    for _ in range(20):
        changed = False
        for i in range(len(nodes)):
            if not adj[i]:
                continue

            # Count weighted community votes from neighbors
            votes: Counter = Counter()
            for j, w in adj[i]:
                votes[communities[j]] += w

            if votes:
                best = votes.most_common(1)[0][0]
                if best != communities[i]:
                    communities[i] = best
                    changed = True

        if not changed:
            break

    # Normalize community IDs to 0..N
    unique = sorted(set(communities))
    remap = {old: new for new, old in enumerate(unique)}
    for i, node in enumerate(nodes):
        node.community = remap[communities[i]]


# ── Centrality + bridge detection ──────────────────────────────────────

def _compute_centrality(nodes: list[GraphNode], edges: list[GraphEdge]) -> None:
    """Compute degree centrality and detect bridge entities.

    Bridge entities: nodes that connect two or more different communities.
    These are the most strategically important entities in the graph.
    """
    if not nodes:
        return

    node_map = {n.id: i for i, n in enumerate(nodes)}

    # Degree centrality: normalized by max possible degree
    degree: Counter = Counter()
    neighbor_communities: dict[int, set[int]] = defaultdict(set)

    for edge in edges:
        i = node_map.get(edge.source_id)
        j = node_map.get(edge.target_id)
        if i is not None and j is not None:
            degree[i] += edge.weight
            degree[j] += edge.weight
            neighbor_communities[i].add(nodes[j].community)
            neighbor_communities[j].add(nodes[i].community)

    max_degree = max(degree.values()) if degree else 1.0

    for i, node in enumerate(nodes):
        node.centrality = degree.get(i, 0) / max_degree

        # Bridge: connected to 2+ different communities
        connected_comms = neighbor_communities.get(i, set()).copy()
        # Include own community
        connected_comms.add(node.community)
        if len(connected_comms) >= 3:
            node.is_bridge = True
