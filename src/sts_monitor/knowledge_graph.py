"""Cross-investigation knowledge graph.

Unifies entities, stories, claims, observations, convergence zones, and
investigations into a single navigable graph. This lets analysts see how
everything connects — which entities appear across investigations, which
stories share common threads, which geographic zones overlap, and which
claims corroborate or contradict each other.

Node types:
  - investigation: A monitored topic/situation
  - entity: A person, org, location, weapon, or event
  - story: A cluster of related observations
  - claim: A verified/disputed claim from pipeline output
  - convergence_zone: A geographic area with multi-signal convergence
  - observation: An individual data point (optional, can be omitted for clarity)

Edge types:
  - mentions: entity appears in story/observation/investigation
  - part_of: story/claim belongs to investigation
  - corroborates: two claims support each other
  - contradicts: two claims conflict
  - overlaps: geographic/temporal overlap between zones or stories
  - co_occurs: entities appear together (from entity_graph)
  - links_to: cross-investigation connection (shared entity/story pattern)
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from sts_monitor.models import (
    ClaimORM,
    ConvergenceZoneORM,
    EntityMentionORM,
    InvestigationORM,
    ObservationORM,
    StoryObservationORM,
    StoryORM,
)


def _node_id(node_type: str, label: str) -> str:
    """Deterministic node ID from type + label."""
    raw = f"{node_type}:{label}".lower()
    return hashlib.md5(raw.encode()).hexdigest()[:14]


@dataclass
class KGNode:
    """A node in the knowledge graph."""
    id: str
    label: str
    node_type: str  # investigation, entity, story, claim, convergence_zone
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "type": self.node_type,
            **self.properties,
        }


@dataclass
class KGEdge:
    """An edge in the knowledge graph."""
    source: str
    target: str
    edge_type: str  # mentions, part_of, corroborates, contradicts, overlaps, co_occurs, links_to
    weight: float = 1.0
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "type": self.edge_type,
            "weight": self.weight,
            **self.properties,
        }


@dataclass
class KnowledgeGraph:
    """A unified knowledge graph across investigations."""
    nodes: list[KGNode] = field(default_factory=list)
    edges: list[KGEdge] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    def to_dict(self) -> dict[str, Any]:
        node_types = Counter(n.node_type for n in self.nodes)
        edge_types = Counter(e.edge_type for e in self.edges)
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "node_types": dict(node_types),
            "edge_types": dict(edge_types),
            "stats": self.stats,
        }


def build_knowledge_graph(
    session: Session,
    investigation_ids: list[str] | None = None,
    include_observations: bool = False,
    max_entities: int = 200,
    min_entity_mentions: int = 2,
    max_stories: int = 100,
) -> KnowledgeGraph:
    """Build a unified knowledge graph from the database.

    Args:
        session: SQLAlchemy session
        investigation_ids: Limit to specific investigations (None = all)
        include_observations: Include individual observations as nodes (can be noisy)
        max_entities: Max entity nodes to include
        min_entity_mentions: Min mentions for entity to appear
        max_stories: Max story nodes to include
    """
    kg = KnowledgeGraph()
    node_index: dict[str, KGNode] = {}
    edge_set: set[tuple[str, str, str]] = set()  # (source, target, type) dedup

    def add_node(node: KGNode) -> str:
        if node.id not in node_index:
            node_index[node.id] = node
            kg.nodes.append(node)
        return node.id

    def add_edge(source: str, target: str, edge_type: str, weight: float = 1.0, **props: Any) -> None:
        key = (min(source, target), max(source, target), edge_type)
        if key in edge_set:
            return
        edge_set.add(key)
        kg.edges.append(KGEdge(source=source, target=target, edge_type=edge_type, weight=weight, properties=props))

    # ── 1. Investigation nodes ────────────────────────────────────────
    inv_query = session.query(InvestigationORM)
    if investigation_ids:
        inv_query = inv_query.filter(InvestigationORM.id.in_(investigation_ids))
    investigations = inv_query.all()

    for inv in investigations:
        add_node(KGNode(
            id=f"inv:{inv.id}",
            label=inv.topic,
            node_type="investigation",
            properties={
                "status": inv.status,
                "priority": inv.priority,
                "owner": inv.owner or "",
                "created_at": inv.created_at.isoformat() if inv.created_at else "",
            },
        ))

    inv_ids = [inv.id for inv in investigations]
    if not inv_ids:
        return kg

    # ── 2. Entity nodes + mentions edges ──────────────────────────────
    # Aggregate entities by normalized form
    entity_agg = (
        session.query(
            EntityMentionORM.normalized,
            EntityMentionORM.entity_type,
            func.count(EntityMentionORM.id).label("mention_count"),
            func.avg(EntityMentionORM.confidence).label("avg_confidence"),
        )
        .filter(EntityMentionORM.investigation_id.in_(inv_ids))
        .group_by(EntityMentionORM.normalized, EntityMentionORM.entity_type)
        .having(func.count(EntityMentionORM.id) >= min_entity_mentions)
        .order_by(func.count(EntityMentionORM.id).desc())
        .limit(max_entities)
        .all()
    )

    entity_node_ids: dict[str, str] = {}  # normalized -> node_id
    for ent in entity_agg:
        node_id = _node_id("entity", f"{ent.entity_type}:{ent.normalized}")
        entity_node_ids[ent.normalized] = node_id
        add_node(KGNode(
            id=node_id,
            label=ent.normalized,
            node_type="entity",
            properties={
                "entity_type": ent.entity_type,
                "mention_count": ent.mention_count,
                "avg_confidence": round(float(ent.avg_confidence or 0), 3),
            },
        ))

    # Link entities to investigations they appear in
    entity_inv_links = (
        session.query(
            EntityMentionORM.normalized,
            EntityMentionORM.investigation_id,
            func.count(EntityMentionORM.id).label("cnt"),
        )
        .filter(
            EntityMentionORM.investigation_id.in_(inv_ids),
            EntityMentionORM.normalized.in_(list(entity_node_ids.keys())),
        )
        .group_by(EntityMentionORM.normalized, EntityMentionORM.investigation_id)
        .all()
    )
    for link in entity_inv_links:
        ent_nid = entity_node_ids.get(link.normalized)
        if ent_nid:
            add_edge(ent_nid, f"inv:{link.investigation_id}", "mentions", weight=link.cnt)

    # ── 3. Entity co-occurrence edges ─────────────────────────────────
    # Find entities that appear in the same observation
    entity_obs = (
        session.query(
            EntityMentionORM.observation_id,
            EntityMentionORM.normalized,
        )
        .filter(
            EntityMentionORM.investigation_id.in_(inv_ids),
            EntityMentionORM.normalized.in_(list(entity_node_ids.keys())),
        )
        .all()
    )

    obs_entities: dict[int, list[str]] = defaultdict(list)
    for row in entity_obs:
        obs_entities[row.observation_id].append(row.normalized)

    co_occur: Counter[tuple[str, str]] = Counter()
    for obs_id, entities in obs_entities.items():
        unique = sorted(set(entities))
        for i, a in enumerate(unique):
            for b in unique[i + 1:]:
                co_occur[(a, b)] += 1

    for (a, b), count in co_occur.most_common(500):
        nid_a = entity_node_ids.get(a)
        nid_b = entity_node_ids.get(b)
        if nid_a and nid_b and count >= 2:
            add_edge(nid_a, nid_b, "co_occurs", weight=count)

    # ── 4. Story nodes + edges ────────────────────────────────────────
    stories = (
        session.query(StoryORM)
        .filter(StoryORM.investigation_id.in_(inv_ids))
        .order_by(StoryORM.trending_score.desc())
        .limit(max_stories)
        .all()
    )

    for story in stories:
        story_nid = f"story:{story.id}"
        add_node(KGNode(
            id=story_nid,
            label=story.headline[:120] if story.headline else f"Story #{story.id}",
            node_type="story",
            properties={
                "observation_count": story.observation_count,
                "source_count": story.source_count,
                "avg_reliability": round(story.avg_reliability, 3),
                "trending_score": round(story.trending_score, 3),
                "first_seen": story.first_seen.isoformat() if story.first_seen else "",
                "last_seen": story.last_seen.isoformat() if story.last_seen else "",
            },
        ))

        # Story → Investigation
        if story.investigation_id:
            add_edge(story_nid, f"inv:{story.investigation_id}", "part_of")

        # Story → Entities (from entities_json)
        try:
            story_entities = json.loads(story.entities_json) if story.entities_json else []
        except (json.JSONDecodeError, TypeError):
            story_entities = []
        for ent_str in story_entities[:10]:
            # Format is "type:text"
            if ":" in ent_str:
                ent_text = ent_str.split(":", 1)[1].strip().lower()
            else:
                ent_text = ent_str.strip().lower()
            ent_nid = entity_node_ids.get(ent_text)
            if ent_nid:
                add_edge(story_nid, ent_nid, "mentions")

    # ── 5. Claim nodes + edges ────────────────────────────────────────
    claims = (
        session.query(ClaimORM)
        .filter(ClaimORM.investigation_id.in_(inv_ids))
        .order_by(ClaimORM.created_at.desc())
        .limit(200)
        .all()
    )

    for claim in claims:
        claim_nid = f"claim:{claim.id}"
        stance_label = claim.stance or "unknown"
        add_node(KGNode(
            id=claim_nid,
            label=claim.claim_text[:120] if claim.claim_text else f"Claim #{claim.id}",
            node_type="claim",
            properties={
                "stance": stance_label,
                "confidence": round(claim.confidence, 3),
            },
        ))

        # Claim → Investigation
        add_edge(claim_nid, f"inv:{claim.investigation_id}", "part_of")

    # ── 6. Convergence zone nodes + edges ─────────────────────────────
    zones = (
        session.query(ConvergenceZoneORM)
        .filter(ConvergenceZoneORM.resolved_at.is_(None))
        .order_by(ConvergenceZoneORM.last_updated_at.desc())
        .limit(50)
        .all()
    )

    for zone in zones:
        zone_nid = f"zone:{zone.id}"
        try:
            signal_types = json.loads(zone.signal_types_json) if zone.signal_types_json else []
        except (json.JSONDecodeError, TypeError):
            signal_types = []

        add_node(KGNode(
            id=zone_nid,
            label=f"Zone ({zone.center_lat:.1f}, {zone.center_lon:.1f}) [{zone.severity}]",
            node_type="convergence_zone",
            properties={
                "center_lat": zone.center_lat,
                "center_lon": zone.center_lon,
                "radius_km": zone.radius_km,
                "signal_count": zone.signal_count,
                "signal_types": signal_types,
                "severity": zone.severity,
            },
        ))

        # Zone → Investigation
        if zone.investigation_id:
            add_edge(zone_nid, f"inv:{zone.investigation_id}", "part_of")

    # ── 7. Cross-investigation links ──────────────────────────────────
    # Find entities that appear in multiple investigations
    if len(inv_ids) > 1:
        cross_inv = (
            session.query(
                EntityMentionORM.normalized,
                func.count(func.distinct(EntityMentionORM.investigation_id)).label("inv_count"),
            )
            .filter(
                EntityMentionORM.investigation_id.in_(inv_ids),
                EntityMentionORM.normalized.in_(list(entity_node_ids.keys())),
            )
            .group_by(EntityMentionORM.normalized)
            .having(func.count(func.distinct(EntityMentionORM.investigation_id)) > 1)
            .all()
        )

        for row in cross_inv:
            ent_nid = entity_node_ids.get(row.normalized)
            if ent_nid:
                # Already have mentions edges; mark the entity as cross-investigation
                node = node_index.get(ent_nid)
                if node:
                    node.properties["cross_investigation"] = True
                    node.properties["investigation_count"] = row.inv_count

    # ── 8. Observations (optional, for detail view) ───────────────────
    if include_observations:
        obs_query = (
            session.query(ObservationORM)
            .filter(ObservationORM.investigation_id.in_(inv_ids))
            .order_by(ObservationORM.captured_at.desc())
            .limit(500)
        )
        for obs in obs_query.all():
            obs_nid = f"obs:{obs.id}"
            add_node(KGNode(
                id=obs_nid,
                label=obs.claim[:80] if obs.claim else f"Obs #{obs.id}",
                node_type="observation",
                properties={
                    "source": obs.source,
                    "reliability": round(obs.reliability_hint, 3),
                    "connector_type": obs.connector_type,
                },
            ))
            add_edge(obs_nid, f"inv:{obs.investigation_id}", "part_of")

    # ── Stats ─────────────────────────────────────────────────────────
    node_types = Counter(n.node_type for n in kg.nodes)
    edge_types = Counter(e.edge_type for e in kg.edges)
    cross_inv_entities = sum(1 for n in kg.nodes if n.properties.get("cross_investigation"))

    kg.stats = {
        "investigations": len(inv_ids),
        "node_types": dict(node_types),
        "edge_types": dict(edge_types),
        "cross_investigation_entities": cross_inv_entities,
        "total_co_occurrences": sum(c for c in co_occur.values() if c >= 2),
    }

    return kg
