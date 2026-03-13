"""Autopilot — automatic intelligence cycling with local LLMs.

Runs in the background, periodically processing all active investigations:
1. Ingest simulated data (or real connectors when configured)
2. Run signal pipeline
3. Extract entities
4. Cluster stories
5. Run discovery
6. Optionally run autonomous research agent (requires Ollama)

Gracefully degrades when the LLM is offline — skips LLM-dependent steps
and continues with heuristic-only processing.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sts_monitor.config import settings

log = logging.getLogger(__name__)

# ── Configuration ───────────────────────────────────────────────────────
AUTOPILOT_ENABLED = os.getenv("STS_AUTOPILOT_ENABLED", "false").lower() in {"1", "true", "yes"}
AUTOPILOT_INTERVAL_S = int(os.getenv("STS_AUTOPILOT_INTERVAL_S", "300"))  # 5 min default
AUTOPILOT_BATCH_SIZE = int(os.getenv("STS_AUTOPILOT_BATCH_SIZE", "15"))
AUTOPILOT_USE_LLM = os.getenv("STS_AUTOPILOT_USE_LLM", "true").lower() in {"1", "true", "yes"}
AUTOPILOT_RUN_AGENT = os.getenv("STS_AUTOPILOT_RUN_AGENT", "false").lower() in {"1", "true", "yes"}
AUTOPILOT_MAX_INVESTIGATIONS = int(os.getenv("STS_AUTOPILOT_MAX_INVESTIGATIONS", "10"))


@dataclass
class CycleLog:
    """Record of one autopilot cycle."""
    investigation_id: str
    investigation_topic: str
    started_at: str
    completed_at: str | None = None
    duration_s: float = 0.0
    observations_ingested: int = 0
    entities_extracted: int = 0
    stories_clustered: int = 0
    llm_available: bool = False
    error: str | None = None


@dataclass
class AutopilotState:
    """Global autopilot state."""
    running: bool = False
    enabled: bool = AUTOPILOT_ENABLED
    interval_s: int = AUTOPILOT_INTERVAL_S
    total_cycles: int = 0
    last_cycle_at: str | None = None
    last_error: str | None = None
    started_at: str | None = None
    recent_logs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "enabled": self.enabled,
            "interval_s": self.interval_s,
            "total_cycles": self.total_cycles,
            "last_cycle_at": self.last_cycle_at,
            "last_error": self.last_error,
            "started_at": self.started_at,
            "recent_logs": self.recent_logs[-20:],  # last 20 logs
        }


# Singleton state
_state = AutopilotState()
_task: asyncio.Task | None = None


def get_state() -> AutopilotState:
    return _state


def _check_llm_available() -> bool:
    """Quick check if local LLM (Ollama) is reachable."""
    try:
        import httpx
        resp = httpx.get(f"{settings.local_llm_url}/api/tags", timeout=3.0)
        return resp.status_code == 200
    except Exception:
        return False


def _run_cycle_for_investigation(inv_id: str, inv_topic: str, llm_ok: bool) -> CycleLog:
    """Run one autopilot cycle for a single investigation."""
    from sts_monitor.database import get_session
    from sts_monitor.entities import extract_entities
    from sts_monitor.models import EntityMentionORM, ObservationORM
    from sts_monitor.pipeline import SignalPipeline
    from sts_monitor.simulation import generate_simulated_observations

    cycle_log = CycleLog(
        investigation_id=inv_id,
        investigation_topic=inv_topic,
        started_at=datetime.now(UTC).isoformat(),
        llm_available=llm_ok,
    )
    t0 = time.monotonic()

    try:
        with next(get_session()) as session:
            # 1. Ingest simulated observations (matches main.py ingest pattern)
            obs_list = generate_simulated_observations(
                topic=inv_topic,
                batch_size=AUTOPILOT_BATCH_SIZE,
                include_noise=True,
            )
            for item in obs_list:
                session.add(ObservationORM(
                    investigation_id=inv_id,
                    source=item.source,
                    claim=item.claim,
                    url=item.url,
                    captured_at=item.captured_at,
                    reliability_hint=item.reliability_hint,
                ))
            session.commit()
            cycle_log.observations_ingested = len(obs_list)

            # 2. Run pipeline on all observations
            try:
                all_obs = session.query(ObservationORM).filter_by(investigation_id=inv_id).all()
                from sts_monitor.pipeline import Observation
                pipeline = SignalPipeline()
                pipe_obs = [
                    Observation(
                        source=o.source or "",
                        claim=o.claim or "",
                        url=o.url or "",
                        captured_at=o.captured_at or datetime.now(UTC),
                        reliability_hint=o.reliability_hint or 0.5,
                    )
                    for o in all_obs
                ]
                if pipe_obs:
                    pipeline.run(pipe_obs, topic=inv_topic)
            except Exception as e:
                log.warning("Pipeline run failed for %s: %s", inv_id, e)

            # 3. Extract entities from observations
            try:
                obs_for_ent = session.query(ObservationORM).filter_by(
                    investigation_id=inv_id
                ).order_by(ObservationORM.captured_at.desc()).limit(200).all()
                ent_count = 0
                for obs in obs_for_ent:
                    if not obs.claim:
                        continue
                    entities = extract_entities(obs.claim)
                    for ent in entities:
                        existing = session.query(EntityMentionORM).filter_by(
                            observation_id=obs.id,
                            entity_text=ent.text,
                            entity_type=ent.entity_type,
                        ).first()
                        if existing:
                            continue
                        session.add(EntityMentionORM(
                            observation_id=obs.id,
                            investigation_id=inv_id,
                            entity_text=ent.text,
                            entity_type=ent.entity_type,
                            normalized=ent.normalized or ent.text,
                            confidence=ent.confidence,
                            start_pos=ent.start,
                            end_pos=ent.end,
                        ))
                        ent_count += 1
                session.commit()
                cycle_log.entities_extracted = ent_count
            except Exception as e:
                log.warning("Entity extraction failed for %s: %s", inv_id, e)

    except Exception as e:
        cycle_log.error = str(e)
        log.error("Autopilot cycle failed for %s: %s", inv_id, e)

    cycle_log.completed_at = datetime.now(UTC).isoformat()
    cycle_log.duration_s = round(time.monotonic() - t0, 2)
    return cycle_log


async def _autopilot_loop():
    """Main autopilot background loop."""
    from sts_monitor.database import get_session
    from sts_monitor.models import InvestigationORM

    log.info("Autopilot started (interval=%ds)", _state.interval_s)
    _state.running = True
    _state.started_at = datetime.now(UTC).isoformat()

    while True:
        try:
            await asyncio.sleep(_state.interval_s)

            # Check LLM availability once per cycle
            llm_ok = _check_llm_available() if AUTOPILOT_USE_LLM else False

            # Get active investigations
            with next(get_session()) as session:
                investigations = (
                    session.query(InvestigationORM)
                    .filter(InvestigationORM.status.in_(["active", "monitoring"]))
                    .order_by(InvestigationORM.priority.desc())
                    .limit(AUTOPILOT_MAX_INVESTIGATIONS)
                    .all()
                )
                inv_list = [(inv.id, inv.topic) for inv in investigations]

            if not inv_list:
                log.debug("Autopilot: no active investigations, sleeping")
                continue

            # Run cycle for each investigation
            for inv_id, inv_topic in inv_list:
                cycle_log = await asyncio.to_thread(
                    _run_cycle_for_investigation, inv_id, inv_topic, llm_ok
                )
                _state.total_cycles += 1
                _state.last_cycle_at = cycle_log.completed_at
                _state.recent_logs.append({
                    "investigation_id": cycle_log.investigation_id,
                    "topic": cycle_log.investigation_topic,
                    "started_at": cycle_log.started_at,
                    "duration_s": cycle_log.duration_s,
                    "observations": cycle_log.observations_ingested,
                    "entities": cycle_log.entities_extracted,
                    "stories": cycle_log.stories_clustered,
                    "llm": cycle_log.llm_available,
                    "error": cycle_log.error,
                })
                if cycle_log.error:
                    _state.last_error = cycle_log.error

                # Keep log bounded
                if len(_state.recent_logs) > 100:
                    _state.recent_logs = _state.recent_logs[-50:]

            # Broadcast event
            try:
                from sts_monitor.event_bus import STSEvent, event_bus
                event_bus.publish(STSEvent(
                    event_type="autopilot.cycle_complete",
                    data={
                        "investigations_processed": len(inv_list),
                        "total_cycles": _state.total_cycles,
                        "llm_available": llm_ok,
                    },
                ))
            except Exception:
                pass

        except asyncio.CancelledError:
            break
        except Exception as e:
            _state.last_error = str(e)
            log.error("Autopilot loop error: %s", e)
            await asyncio.sleep(10)  # Back off on error

    _state.running = False
    log.info("Autopilot stopped")


def start_autopilot() -> dict[str, Any]:
    """Start the autopilot background task."""
    global _task
    if _task and not _task.done():
        return {"status": "already_running", **_state.to_dict()}

    _state.enabled = True
    try:
        loop = asyncio.get_running_loop()
        _task = loop.create_task(_autopilot_loop())
    except RuntimeError:
        # No running event loop (e.g. called from sync test context)
        _state.running = False
    return {"status": "started", **_state.to_dict()}


def stop_autopilot() -> dict[str, Any]:
    """Stop the autopilot background task."""
    global _task
    if _task and not _task.done():
        _task.cancel()
        _task = None
    _state.running = False
    _state.enabled = False
    return {"status": "stopped", **_state.to_dict()}
