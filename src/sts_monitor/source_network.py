"""Source network analysis — map relationships between information sources.

Detects:
- Which sources cite each other or report simultaneously
- Who breaks stories first (first-mover analysis)
- Coordinated narrative patterns (multiple "independent" sources reporting same thing at same time)
- Source clustering by topic/timing
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class SourceNode:
    """A source in the network."""
    name: str
    total_observations: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    topics: list[str] = field(default_factory=list)
    breaks_first_count: int = 0  # times this source reported something first
    follows_count: int = 0  # times this source followed another
    avg_reliability: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "total_observations": self.total_observations,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "breaks_first": self.breaks_first_count,
            "follows": self.follows_count,
            "avg_reliability": round(self.avg_reliability, 3),
        }


@dataclass
class SourceEdge:
    """Relationship between two sources."""
    source_a: str
    source_b: str
    co_report_count: int  # times they reported same thing
    avg_time_gap_hours: float  # avg time between their reports on same topic
    relationship_type: str  # "co-reporter", "follower", "leader", "synchronized"
    confidence: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_a": self.source_a,
            "source_b": self.source_b,
            "co_reports": self.co_report_count,
            "avg_time_gap_hours": round(self.avg_time_gap_hours, 2),
            "relationship": self.relationship_type,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class CoordinatedNarrative:
    """Multiple sources reporting the same thing at suspiciously similar times."""
    claim_summary: str
    sources: list[str]
    timestamps: list[str]
    time_spread_minutes: float
    coordination_score: float  # 0-1, higher = more suspicious

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim_summary[:300],
            "sources": self.sources,
            "source_count": len(self.sources),
            "time_spread_minutes": round(self.time_spread_minutes, 1),
            "coordination_score": round(self.coordination_score, 3),
        }


@dataclass
class SourceNetworkReport:
    """Full source network analysis."""
    total_sources: int
    total_observations: int
    nodes: list[SourceNode]
    edges: list[SourceEdge]
    first_movers: list[dict[str, Any]]
    coordinated_narratives: list[CoordinatedNarrative]
    network_density: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_sources": self.total_sources,
            "total_observations": self.total_observations,
            "nodes": [n.to_dict() for n in self.nodes[:50]],
            "edges": [e.to_dict() for e in self.edges[:100]],
            "first_movers": self.first_movers[:20],
            "coordinated_narratives": [c.to_dict() for c in self.coordinated_narratives[:20]],
            "network_density": round(self.network_density, 4),
        }


def _source_family(source: str) -> str:
    s = source.lower().strip()
    for prefix in ("rss:", "reddit:", "gdelt:", "nitter:", "telegram:"):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
    for remove in ("http://", "https://", "www."):
        s = s.replace(remove, "")
    if "/" in s:
        s = s.split("/")[0]
    return s or source


def _normalize_claim_key(claim: str) -> str:
    """Create rough grouping key for claims."""
    words = [w.lower() for w in claim.split() if len(w) > 3][:6]
    return " ".join(sorted(words))


def _parse_ts(val) -> datetime:
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=UTC)
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except Exception:
            pass
    return datetime.min.replace(tzinfo=UTC)


def analyze_source_network(
    observations: list[dict[str, Any]],
    co_report_window_hours: int = 6,
    min_co_reports: int = 2,
    coordination_window_minutes: int = 30,
) -> SourceNetworkReport:
    """Build a source relationship network from observations."""

    # ── Build source nodes ─────────────────────────────────────────
    source_data: dict[str, SourceNode] = {}
    claim_timeline: dict[str, list[tuple[str, datetime]]] = defaultdict(list)  # claim_key -> [(source, ts)]

    for obs in observations:
        src = _source_family(obs.get("source", "unknown"))
        claim = obs.get("claim") or ""
        ts = _parse_ts(obs.get("captured_at"))
        reliability = obs.get("reliability_hint", 0.5)

        if src not in source_data:
            source_data[src] = SourceNode(name=src, first_seen=ts, last_seen=ts)
        node = source_data[src]
        node.total_observations += 1
        node.avg_reliability = (node.avg_reliability * (node.total_observations - 1) + reliability) / node.total_observations

        if ts != datetime.min.replace(tzinfo=UTC):
            if node.first_seen is None or ts < node.first_seen:
                node.first_seen = ts
            if node.last_seen is None or ts > node.last_seen:
                node.last_seen = ts

        if len(claim) > 15:
            key = _normalize_claim_key(claim)
            if key:
                claim_timeline[key].append((src, ts))

    # ── Analyze co-reporting and first-mover dynamics ──────────────
    co_reports: dict[tuple[str, str], list[float]] = defaultdict(list)  # (a,b) -> [time_gap_hours]
    first_mover_count: dict[str, int] = defaultdict(int)
    follower_count: dict[str, int] = defaultdict(int)

    for key, entries in claim_timeline.items():
        if len(entries) < 2:
            continue
        # Sort by time
        entries.sort(key=lambda e: e[1])
        sources_in_claim = list({e[0] for e in entries})
        if len(sources_in_claim) < 2:
            continue

        # First source to report = first mover
        first_mover_count[entries[0][0]] += 1

        # Build co-report edges
        for i, (src_a, ts_a) in enumerate(entries):
            for src_b, ts_b in entries[i + 1:]:
                if src_a == src_b:
                    continue
                gap_hours = abs((ts_b - ts_a).total_seconds()) / 3600
                if gap_hours <= co_report_window_hours:
                    pair = tuple(sorted([src_a, src_b]))
                    co_reports[pair].append(gap_hours)
                    follower_count[src_b] += 1

    # ── Build edges ────────────────────────────────────────────────
    edges = []
    for (src_a, src_b), gaps in co_reports.items():
        if len(gaps) < min_co_reports:
            continue
        avg_gap = sum(gaps) / len(gaps)

        if avg_gap < 0.5:
            rel_type = "synchronized"
        elif first_mover_count.get(src_a, 0) > first_mover_count.get(src_b, 0):
            rel_type = "leader"
        else:
            rel_type = "co-reporter"

        edges.append(SourceEdge(
            source_a=src_a,
            source_b=src_b,
            co_report_count=len(gaps),
            avg_time_gap_hours=avg_gap,
            relationship_type=rel_type,
            confidence=min(1.0, len(gaps) / 10.0),
        ))

    edges.sort(key=lambda e: e.co_report_count, reverse=True)

    # Update node first-mover/follower counts
    for src, node in source_data.items():
        node.breaks_first_count = first_mover_count.get(src, 0)
        node.follows_count = follower_count.get(src, 0)

    # ── Detect coordinated narratives ──────────────────────────────
    coordinated = []
    for key, entries in claim_timeline.items():
        if len(entries) < 3:
            continue
        entries.sort(key=lambda e: e[1])
        sources = list({e[0] for e in entries})
        if len(sources) < 3:
            continue

        # Check time spread
        times = [e[1] for e in entries]
        spread_minutes = (max(times) - min(times)).total_seconds() / 60

        if spread_minutes <= coordination_window_minutes:
            score = min(1.0, len(sources) / 5.0) * max(0.1, 1.0 - spread_minutes / coordination_window_minutes)
            coordinated.append(CoordinatedNarrative(
                claim_summary=entries[0][0] + ": " + key,
                sources=sources,
                timestamps=[e[1].isoformat() for e in entries],
                time_spread_minutes=spread_minutes,
                coordination_score=score,
            ))

    coordinated.sort(key=lambda c: c.coordination_score, reverse=True)

    # ── First mover leaderboard ────────────────────────────────────
    first_movers = sorted(
        [{"source": src, "breaks_first": count, "total_obs": source_data[src].total_observations,
          "first_mover_rate": round(count / max(1, source_data[src].total_observations), 3)}
         for src, count in first_mover_count.items() if count >= 2],
        key=lambda x: x["breaks_first"],
        reverse=True,
    )

    # ── Network density ────────────────────────────────────────────
    n = len(source_data)
    max_edges = n * (n - 1) / 2 if n > 1 else 1
    density = len(edges) / max_edges if max_edges > 0 else 0

    nodes = sorted(source_data.values(), key=lambda n: n.total_observations, reverse=True)

    return SourceNetworkReport(
        total_sources=len(source_data),
        total_observations=len(observations),
        nodes=nodes,
        edges=edges,
        first_movers=first_movers,
        coordinated_narratives=coordinated,
        network_density=density,
    )
