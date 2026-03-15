"""Cross-investigation link detection.

Automatically detects when entities, claims, or patterns appear across
multiple investigations, creating links and alerts.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class CrossLink:
    """A detected link between two investigations."""
    entity_text: str
    entity_type: str
    investigation_ids: list[str]
    investigation_topics: list[str]
    mention_counts: dict[str, int]  # inv_id -> count
    total_mentions: int
    confidence: float
    link_type: str  # "shared_entity", "shared_claim", "shared_source", "geographic_proximity"
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    sample_claims: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity_text,
            "entity_type": self.entity_type,
            "investigations": self.investigation_ids,
            "topics": self.investigation_topics,
            "mention_counts": self.mention_counts,
            "total_mentions": self.total_mentions,
            "confidence": round(self.confidence, 3),
            "link_type": self.link_type,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "sample_claims": self.sample_claims[:5],
        }


@dataclass
class CrossInvestigationReport:
    """Full cross-investigation analysis."""
    investigations_analyzed: int
    total_links: int
    shared_entities: list[CrossLink]
    shared_sources: list[CrossLink]
    geographic_overlaps: list[CrossLink]
    high_priority_links: list[CrossLink]

    def to_dict(self) -> dict[str, Any]:
        return {
            "investigations_analyzed": self.investigations_analyzed,
            "total_links": self.total_links,
            "shared_entities": [link.to_dict() for link in self.shared_entities[:50]],
            "shared_sources": [link.to_dict() for link in self.shared_sources[:30]],
            "geographic_overlaps": [link.to_dict() for link in self.geographic_overlaps[:20]],
            "high_priority_links": [link.to_dict() for link in self.high_priority_links[:20]],
        }


def detect_cross_investigation_links(session) -> CrossInvestigationReport:
    """Scan all investigations for shared entities, sources, and geographic overlaps."""
    from sts_monitor.models import InvestigationORM, EntityMentionORM, ObservationORM

    investigations = session.query(InvestigationORM).all()
    if len(investigations) < 2:
        return CrossInvestigationReport(
            investigations_analyzed=len(investigations),
            total_links=0,
            shared_entities=[], shared_sources=[],
            geographic_overlaps=[], high_priority_links=[],
        )

    inv_map = {inv.id: inv.topic for inv in investigations}

    # ── Shared entities ────────────────────────────────────────────
    entity_inv_map: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    entity_type_map: dict[str, str] = {}
    entity_claims: dict[str, list[str]] = defaultdict(list)
    entity_times: dict[str, list[datetime]] = defaultdict(list)

    all_mentions = session.query(EntityMentionORM).all()
    for em in all_mentions:
        key = (em.normalized or em.entity_text).lower().strip()
        if len(key) < 3:
            continue
        entity_inv_map[key][em.investigation_id] += 1
        entity_type_map[key] = em.entity_type
        if em.observation_id:
            obs = session.get(ObservationORM, em.observation_id)
            if obs and obs.claim:
                entity_claims[key].append(obs.claim[:200])
                if obs.captured_at:
                    ts = obs.captured_at
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=UTC)
                    entity_times[key].append(ts)

    shared_entities: list[CrossLink] = []
    for entity, inv_counts in entity_inv_map.items():
        if len(inv_counts) < 2:
            continue
        inv_ids = list(inv_counts.keys())
        total = sum(inv_counts.values())
        times = entity_times.get(entity, [])
        shared_entities.append(CrossLink(
            entity_text=entity,
            entity_type=entity_type_map.get(entity, "unknown"),
            investigation_ids=inv_ids,
            investigation_topics=[inv_map.get(i, "?") for i in inv_ids],
            mention_counts=dict(inv_counts),
            total_mentions=total,
            confidence=min(1.0, total / 10.0),
            link_type="shared_entity",
            first_seen=min(times) if times else None,
            last_seen=max(times) if times else None,
            sample_claims=entity_claims.get(entity, [])[:5],
        ))

    shared_entities.sort(key=lambda link: link.total_mentions, reverse=True)

    # ── Shared sources ─────────────────────────────────────────────
    source_inv_map: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    all_obs = session.query(ObservationORM).all()
    for obs in all_obs:
        src = (obs.source or "").lower().strip()
        if not src or not obs.investigation_id:
            continue
        source_inv_map[src][obs.investigation_id] += 1

    shared_sources: list[CrossLink] = []
    for source, inv_counts in source_inv_map.items():
        if len(inv_counts) < 2:
            continue
        inv_ids = list(inv_counts.keys())
        total = sum(inv_counts.values())
        shared_sources.append(CrossLink(
            entity_text=source,
            entity_type="source",
            investigation_ids=inv_ids,
            investigation_topics=[inv_map.get(i, "?") for i in inv_ids],
            mention_counts=dict(inv_counts),
            total_mentions=total,
            confidence=min(1.0, len(inv_ids) / 3.0),
            link_type="shared_source",
        ))

    shared_sources.sort(key=lambda link: len(link.investigation_ids), reverse=True)

    # ── Geographic overlaps ────────────────────────────────────────
    geo_inv_map: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
    for obs in all_obs:
        if obs.latitude is not None and obs.longitude is not None and obs.investigation_id:
            # Round to 1 decimal (~11km grid)
            grid_key = f"{round(obs.latitude, 1)},{round(obs.longitude, 1)}"
            geo_inv_map[grid_key].append((obs.investigation_id, obs.latitude, obs.longitude))

    geographic_overlaps: list[CrossLink] = []
    for grid, entries in geo_inv_map.items():
        inv_ids_in_grid = list({e[0] for e in entries})
        if len(inv_ids_in_grid) < 2:
            continue
        geographic_overlaps.append(CrossLink(
            entity_text=f"geo:{grid}",
            entity_type="location",
            investigation_ids=inv_ids_in_grid,
            investigation_topics=[inv_map.get(i, "?") for i in inv_ids_in_grid],
            mention_counts={i: sum(1 for e in entries if e[0] == i) for i in inv_ids_in_grid},
            total_mentions=len(entries),
            confidence=min(1.0, len(entries) / 5.0),
            link_type="geographic_proximity",
        ))

    geographic_overlaps.sort(key=lambda link: link.total_mentions, reverse=True)

    # ── High priority: entities with high confidence across 3+ investigations ──
    high_priority = [
        link for link in shared_entities
        if len(link.investigation_ids) >= 3 or link.total_mentions >= 10
    ]
    high_priority.extend(
        link for link in geographic_overlaps
        if len(link.investigation_ids) >= 3
    )
    high_priority.sort(key=lambda link: link.confidence, reverse=True)

    total_links = len(shared_entities) + len(shared_sources) + len(geographic_overlaps)

    return CrossInvestigationReport(
        investigations_analyzed=len(investigations),
        total_links=total_links,
        shared_entities=shared_entities[:50],
        shared_sources=shared_sources[:30],
        geographic_overlaps=geographic_overlaps[:20],
        high_priority_links=high_priority[:20],
    )
