"""Rabbit Trail — autonomous deep investigation system.

When an investigation is flagged for rabbit-trail mode, this system:
1. Analyzes all existing observations and entities
2. Identifies threads worth pulling (contradictions, anomalies, gaps)
3. Generates follow-up queries and connector configs
4. Logs every step of the trail for full audit
5. Uses local LLM when available, falls back to heuristics

Designed to follow leads wherever they go — across sources, topics,
and connections — logging everything for human review.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class TrailStep:
    """One step in the rabbit trail investigation."""
    step_number: int
    timestamp: str
    action: str  # "query", "analyze", "follow_lead", "flag_contradiction", "dead_end"
    description: str
    query_used: str = ""
    sources_checked: list[str] = field(default_factory=list)
    observations_found: int = 0
    entities_discovered: list[str] = field(default_factory=list)
    contradictions_found: int = 0
    leads_generated: list[str] = field(default_factory=list)
    confidence: float = 0.5
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step_number,
            "timestamp": self.timestamp,
            "action": self.action,
            "description": self.description,
            "query": self.query_used,
            "sources_checked": self.sources_checked,
            "observations_found": self.observations_found,
            "entities_discovered": self.entities_discovered,
            "contradictions_found": self.contradictions_found,
            "leads_generated": self.leads_generated,
            "confidence": round(self.confidence, 3),
            "notes": self.notes,
        }


@dataclass
class TrailSession:
    """Complete rabbit trail investigation session."""
    session_id: str
    investigation_id: str
    topic: str
    started_at: str
    status: str = "running"  # running, paused, completed, dead_end
    steps: list[TrailStep] = field(default_factory=list)
    total_observations_collected: int = 0
    total_leads_followed: int = 0
    total_contradictions: int = 0
    total_entities: int = 0
    depth: int = 0  # how deep down the trail we've gone
    all_queries: list[str] = field(default_factory=list)
    key_findings: list[str] = field(default_factory=list)
    dead_ends: list[str] = field(default_factory=list)
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "investigation_id": self.investigation_id,
            "topic": self.topic,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "depth": self.depth,
            "total_steps": len(self.steps),
            "total_observations": self.total_observations_collected,
            "total_leads_followed": self.total_leads_followed,
            "total_contradictions": self.total_contradictions,
            "total_entities": self.total_entities,
            "key_findings": self.key_findings,
            "dead_ends": self.dead_ends,
            "steps": [s.to_dict() for s in self.steps[-50:]],
        }


# ── Query generation heuristics ─────────────────────────────────────────

# Patterns that suggest there's more to investigate
_INTERESTING_PATTERNS = [
    "classified", "redacted", "sealed", "undisclosed", "unnamed sources",
    "declined to comment", "no comment", "could not be reached",
    "sources say", "allegedly", "reportedly", "unconfirmed",
    "conflicting reports", "disputed", "denied", "retracted",
    "whistleblower", "leaked", "exposed", "revealed",
    "cover up", "coverup", "suppressed", "censored",
    "money trail", "financial ties", "funded by", "lobbying",
    "shell company", "offshore", "dark money",
]

# Follow-up query templates
_FOLLOW_UP_TEMPLATES = [
    "{entity} connections ties funding",
    "{entity} controversy scandal investigation",
    "{entity} timeline history background",
    "{entity} contradictions disputed claims",
    "{entity} {related_entity} relationship connection",
    "who benefits {topic}",
    "{topic} money trail funding source",
    "{topic} whistleblower insider leak",
    "{topic} documents evidence proof",
    "{topic} timeline events chronology",
]


def _generate_session_id(investigation_id: str, topic: str) -> str:
    h = hashlib.md5(f"{investigation_id}:{topic}:{time.time()}".encode()).hexdigest()[:12]
    return f"trail-{h}"


def _extract_interesting_signals(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find observations that contain signals worth investigating."""
    signals = []
    for obs in observations:
        claim = (obs.get("claim") or "").lower()
        score = 0
        triggers = []
        for pattern in _INTERESTING_PATTERNS:
            if pattern in claim:
                score += 1
                triggers.append(pattern)
        if score > 0:
            signals.append({
                "observation": obs,
                "score": score,
                "triggers": triggers,
            })
    signals.sort(key=lambda s: s["score"], reverse=True)
    return signals


def _generate_follow_up_queries(
    topic: str,
    entities: list[str],
    existing_queries: list[str],
    max_queries: int = 5,
) -> list[str]:
    """Generate new follow-up queries based on discovered entities."""
    queries = []
    existing_lower = {q.lower() for q in existing_queries}

    # Entity-focused queries
    for entity in entities[:10]:
        for template in _FOLLOW_UP_TEMPLATES:
            q = template.format(
                entity=entity,
                topic=topic,
                related_entity=entities[1] if len(entities) > 1 else topic,
            )
            if q.lower() not in existing_lower:
                queries.append(q)
                existing_lower.add(q.lower())
            if len(queries) >= max_queries:
                break
        if len(queries) >= max_queries:
            break

    # Topic-based deep dives
    if len(queries) < max_queries:
        deep_queries = [
            f"{topic} cover up evidence",
            f"{topic} who profits benefits",
            f"{topic} suppressed information hidden",
            f"{topic} official vs independent reports",
            f"{topic} FOIA documents declassified",
        ]
        for q in deep_queries:
            if q.lower() not in existing_lower and len(queries) < max_queries:
                queries.append(q)

    return queries[:max_queries]


def run_rabbit_trail(
    investigation_id: str,
    topic: str,
    observations: list[dict[str, Any]],
    entities: list[str],
    max_depth: int = 10,
    llm_client: Any = None,
) -> TrailSession:
    """Run a rabbit trail investigation session.

    Analyzes existing data, identifies leads, generates follow-up queries,
    and logs everything. The actual data collection happens in the autopilot
    cycle using the generated queries.
    """
    session = TrailSession(
        session_id=_generate_session_id(investigation_id, topic),
        investigation_id=investigation_id,
        topic=topic,
        started_at=datetime.now(UTC).isoformat(),
        total_observations_collected=len(observations),
    )

    # Step 1: Analyze existing observations for interesting signals
    signals = _extract_interesting_signals(observations)
    interesting_obs = [s["observation"] for s in signals[:20]]

    step1 = TrailStep(
        step_number=1,
        timestamp=datetime.now(UTC).isoformat(),
        action="analyze",
        description=f"Analyzed {len(observations)} observations, found {len(signals)} with investigative signals",
        observations_found=len(signals),
        notes=f"Top triggers: {', '.join(signals[0]['triggers']) if signals else 'none'}",
        confidence=min(1.0, len(signals) / max(1, len(observations)) * 5),
    )
    session.steps.append(step1)

    # Step 2: Identify contradictions in the data
    contradiction_count = 0
    contradiction_notes = []
    seen_claims: dict[str, list[str]] = {}

    for obs in observations:
        claim = (obs.get("claim") or "").strip().lower()
        source = obs.get("source", "unknown")
        if len(claim) < 15:
            continue

        # Simple contradiction detection
        claim_key = " ".join(sorted(claim.split()[:5]))
        if claim_key in seen_claims:
            prev_sources = seen_claims[claim_key]
            if source not in prev_sources:
                # Check if claims are conflicting
                has_negation = any(w in claim for w in ("not", "false", "denied", "no"))
                if has_negation:
                    contradiction_count += 1
                    contradiction_notes.append(f"{source} vs {prev_sources[0]}: {claim[:80]}")
            prev_sources.append(source)
        else:
            seen_claims[claim_key] = [source]

    step2 = TrailStep(
        step_number=2,
        timestamp=datetime.now(UTC).isoformat(),
        action="flag_contradiction",
        description=f"Found {contradiction_count} potential contradictions across sources",
        contradictions_found=contradiction_count,
        notes="; ".join(contradiction_notes[:5]) if contradiction_notes else "No contradictions detected",
        confidence=0.6 if contradiction_count > 0 else 0.3,
    )
    session.steps.append(step2)
    session.total_contradictions = contradiction_count

    # Step 3: Generate follow-up queries based on entities and signals
    follow_up_queries = _generate_follow_up_queries(
        topic=topic,
        entities=entities,
        existing_queries=session.all_queries,
        max_queries=max_depth,
    )

    step3 = TrailStep(
        step_number=3,
        timestamp=datetime.now(UTC).isoformat(),
        action="query",
        description=f"Generated {len(follow_up_queries)} follow-up research queries",
        leads_generated=follow_up_queries,
        entities_discovered=entities[:20],
        confidence=0.7,
        notes="Queries designed to follow financial trails, find contradictions, and uncover suppressed info",
    )
    session.steps.append(step3)
    session.all_queries.extend(follow_up_queries)
    session.total_leads_followed = len(follow_up_queries)
    session.total_entities = len(entities)

    # Step 4: LLM-powered analysis if available
    if llm_client:
        try:
            obs_summaries = "\n".join(
                f"- [{o.get('source', '?')}] {(o.get('claim') or '')[:150]}"
                for o in interesting_obs[:15]
            )
            entity_list = ", ".join(entities[:20])

            prompt = (
                f"You are an investigative intelligence analyst. Analyze these observations about '{topic}' "
                f"and identify the most promising leads to follow. Be aggressive — look for cover-ups, "
                f"contradictions, money trails, and suppressed information.\n\n"
                f"Key entities: {entity_list}\n\n"
                f"Most interesting observations:\n{obs_summaries}\n\n"
                f"Contradictions found: {contradiction_count}\n\n"
                f"Provide:\n"
                f"1. Key findings (what we know)\n"
                f"2. Suspicious gaps (what's missing)\n"
                f"3. Follow-up leads (what to investigate next)\n"
                f"4. Confidence assessment"
            )

            llm_response = llm_client.summarize(prompt)

            step4 = TrailStep(
                step_number=4,
                timestamp=datetime.now(UTC).isoformat(),
                action="analyze",
                description="LLM analysis of investigation leads",
                notes=llm_response[:2000],
                confidence=0.8,
            )
            session.steps.append(step4)

            # Extract key findings from LLM response
            for line in llm_response.split("\n"):
                line = line.strip()
                if line and len(line) > 20 and not line.startswith("#"):
                    session.key_findings.append(line[:300])
                    if len(session.key_findings) >= 10:
                        break
        except Exception as e:
            log.warning("LLM analysis failed in rabbit trail: %s", e)
            step4 = TrailStep(
                step_number=4,
                timestamp=datetime.now(UTC).isoformat(),
                action="analyze",
                description="LLM unavailable — using heuristic analysis only",
                notes=str(e),
                confidence=0.4,
            )
            session.steps.append(step4)
    else:
        # Heuristic key findings
        if contradiction_count > 3:
            session.key_findings.append(
                f"High contradiction rate ({contradiction_count}) suggests conflicting narratives — "
                "warrants deeper investigation"
            )
        if len(signals) > len(observations) * 0.3:
            session.key_findings.append(
                "Over 30% of observations contain investigative signals (classified, denied, leaked, etc.)"
            )
        if entities:
            session.key_findings.append(
                f"Key entities to track: {', '.join(entities[:5])}"
            )

    # Finalize session
    session.depth = len(session.steps)
    session.status = "completed"
    session.finished_at = datetime.now(UTC).isoformat()

    return session


# ── Trail session storage (in-memory) ──────────────────────────────────
_trail_sessions: dict[str, TrailSession] = {}


def store_trail_session(session: TrailSession) -> None:
    _trail_sessions[session.session_id] = session
    # Keep bounded
    if len(_trail_sessions) > 100:
        oldest = sorted(_trail_sessions.keys())[:50]
        for k in oldest:
            del _trail_sessions[k]


def get_trail_session(session_id: str) -> TrailSession | None:
    return _trail_sessions.get(session_id)


def list_trail_sessions(investigation_id: str | None = None) -> list[dict[str, Any]]:
    sessions = _trail_sessions.values()
    if investigation_id:
        sessions = [s for s in sessions if s.investigation_id == investigation_id]
    return [
        {
            "session_id": s.session_id,
            "investigation_id": s.investigation_id,
            "topic": s.topic,
            "status": s.status,
            "depth": s.depth,
            "total_observations": s.total_observations_collected,
            "total_contradictions": s.total_contradictions,
            "key_findings_count": len(s.key_findings),
            "started_at": s.started_at,
            "finished_at": s.finished_at,
        }
        for s in sorted(sessions, key=lambda x: x.started_at, reverse=True)
    ]
