"""Autopilot — automatic intelligence cycling with real connectors and local LLMs.

Runs in the background, periodically processing all active investigations:
1. Collect from real OSINT connectors (GDELT, NWS, USGS, RSS, Reddit)
2. Fall back to simulated data if all connectors fail
3. Run signal pipeline
4. Extract entities
5. Run rabbit trail analysis for flagged investigations
6. Evaluate alert rules
7. Broadcast events via EventBus

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
AUTOPILOT_USE_REAL_CONNECTORS = os.getenv("STS_AUTOPILOT_USE_REAL_CONNECTORS", "true").lower() in {"1", "true", "yes"}


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
    connectors_used: list[str] = field(default_factory=list)
    connectors_failed: list[str] = field(default_factory=list)
    alerts_fired: int = 0
    rabbit_trail: bool = False
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


def _collect_from_real_connectors(topic: str, seed_query: str | None = None) -> tuple[list[Any], list[str], list[str]]:
    """Try collecting from real OSINT connectors. Returns (observations, used, failed)."""
    from sts_monitor.connectors import (
        GDELTConnector, NWSAlertConnector, USGSEarthquakeConnector,
        RedditConnector, ReliefWebConnector,
    )

    query = seed_query or topic
    all_obs: list[Any] = []
    used: list[str] = []
    failed: list[str] = []

    # GDELT — global event data
    try:
        gdelt = GDELTConnector(
            max_records=AUTOPILOT_BATCH_SIZE,
            timeout_s=settings.gdelt_timeout_s,
        )
        result = gdelt.collect(query=query)
        if result.observations:
            all_obs.extend(result.observations)
            used.append("gdelt")
        else:
            failed.append("gdelt:empty")
    except Exception as e:
        failed.append(f"gdelt:{e}")
        log.debug("GDELT connector failed: %s", e)

    # USGS Earthquakes
    try:
        usgs = USGSEarthquakeConnector(
            min_magnitude=settings.usgs_min_magnitude,
            timeout_s=settings.usgs_timeout_s,
        )
        result = usgs.collect(query=query)
        if result.observations:
            all_obs.extend(result.observations)
            used.append("usgs")
    except Exception as e:
        failed.append(f"usgs:{e}")
        log.debug("USGS connector failed: %s", e)

    # NWS Weather Alerts
    try:
        nws = NWSAlertConnector(
            severity_filter=settings.nws_severity_filter,
            timeout_s=settings.nws_timeout_s,
        )
        result = nws.collect(query=query)
        if result.observations:
            all_obs.extend(result.observations)
            used.append("nws")
    except Exception as e:
        failed.append(f"nws:{e}")
        log.debug("NWS connector failed: %s", e)

    # ReliefWeb
    try:
        rw = ReliefWebConnector(timeout_s=settings.reliefweb_timeout_s)
        result = rw.collect(query=query)
        if result.observations:
            all_obs.extend(result.observations)
            used.append("reliefweb")
    except Exception as e:
        failed.append(f"reliefweb:{e}")
        log.debug("ReliefWeb connector failed: %s", e)

    # Reddit OSINT subreddits
    try:
        reddit = RedditConnector(
            subreddits=["worldnews", "geopolitics", "IntelligenceNews"],
            per_subreddit_limit=5,
            timeout_s=settings.reddit_timeout_s,
        )
        result = reddit.collect(query=query)
        if result.observations:
            all_obs.extend(result.observations)
            used.append("reddit")
    except Exception as e:
        failed.append(f"reddit:{e}")
        log.debug("Reddit connector failed: %s", e)

    return all_obs, used, failed


def _run_cycle_for_investigation(inv_id: str, inv_topic: str, llm_ok: bool, seed_query: str | None = None) -> CycleLog:
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
            # 1. Collect observations — try real connectors first, fall back to simulated
            real_obs: list[Any] = []
            if AUTOPILOT_USE_REAL_CONNECTORS:
                try:
                    real_obs, used, failed = _collect_from_real_connectors(inv_topic, seed_query)
                    cycle_log.connectors_used = used
                    cycle_log.connectors_failed = failed
                except Exception as e:
                    log.warning("Real connector collection failed for %s: %s", inv_id, e)

            if real_obs:
                # Store real observations
                for item in real_obs:
                    session.add(ObservationORM(
                        investigation_id=inv_id,
                        source=item.source,
                        claim=item.claim,
                        url=item.url,
                        captured_at=item.captured_at,
                        reliability_hint=item.reliability_hint,
                        connector_type=getattr(item, 'connector_type', 'real'),
                        latitude=getattr(item, 'latitude', None),
                        longitude=getattr(item, 'longitude', None),
                    ))
                session.commit()
                cycle_log.observations_ingested = len(real_obs)
            else:
                # Fall back to simulated
                sim_obs = generate_simulated_observations(
                    topic=inv_topic,
                    batch_size=AUTOPILOT_BATCH_SIZE,
                    include_noise=True,
                )
                for item in sim_obs:
                    session.add(ObservationORM(
                        investigation_id=inv_id,
                        source=item.source,
                        claim=item.claim,
                        url=item.url,
                        captured_at=item.captured_at,
                        reliability_hint=item.reliability_hint,
                    ))
                session.commit()
                cycle_log.observations_ingested = len(sim_obs)
                cycle_log.connectors_used = ["simulated"]

            # 2. Run pipeline on recent observations
            try:
                recent_obs = session.query(ObservationORM).filter_by(
                    investigation_id=inv_id
                ).order_by(ObservationORM.captured_at.desc()).limit(200).all()
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
                    for o in recent_obs
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

            # 4. Evaluate alert rules
            try:
                from sts_monitor.alert_engine import evaluate_rules, get_default_rules
                obs_dicts = [
                    {
                        "id": o.id,
                        "claim": o.claim,
                        "source": o.source,
                        "captured_at": o.captured_at,
                        "reliability_hint": o.reliability_hint,
                        "investigation_id": o.investigation_id,
                    }
                    for o in session.query(ObservationORM).filter_by(
                        investigation_id=inv_id
                    ).order_by(ObservationORM.captured_at.desc()).limit(500).all()
                ]
                rules = get_default_rules(investigation_id=inv_id)
                alerts = evaluate_rules(rules, obs_dicts)
                cycle_log.alerts_fired = len(alerts)

                # Store alert events
                if alerts:
                    from sts_monitor.models import AlertEventORM
                    import json
                    for alert in alerts:
                        session.add(AlertEventORM(
                            investigation_id=inv_id,
                            severity=alert.severity,
                            message=alert.message[:500],
                            detail_json=json.dumps(alert.details),
                        ))
                    session.commit()
            except Exception as e:
                log.warning("Alert evaluation failed for %s: %s", inv_id, e)

            # 5. Geofence check
            try:
                from sts_monitor.geofence import check_observations_against_zones
                geo_obs = [
                    {"id": o.id, "source": o.source, "claim": o.claim,
                     "latitude": o.latitude, "longitude": o.longitude}
                    for o in session.query(ObservationORM).filter_by(
                        investigation_id=inv_id
                    ).all()
                    if o.latitude is not None and o.longitude is not None
                ]
                if geo_obs:
                    geo_alerts = check_observations_against_zones(geo_obs, investigation_id=inv_id)
                    if geo_alerts:
                        log.info("Geofence: %d alerts for %s", len(geo_alerts), inv_id)
            except Exception as e:
                log.debug("Geofence check failed for %s: %s", inv_id, e)

            # 6. Semantic indexing (best-effort)
            try:
                from sts_monitor.semantic_index import index_observations_batch
                recent = session.query(ObservationORM).filter_by(
                    investigation_id=inv_id
                ).order_by(ObservationORM.captured_at.desc()).limit(50).all()
                idx_obs = [
                    {"id": o.id, "investigation_id": inv_id, "source": o.source,
                     "claim": o.claim, "url": o.url or "",
                     "captured_at": o.captured_at.isoformat() if o.captured_at else ""}
                    for o in recent if o.claim
                ]
                if idx_obs:
                    index_observations_batch(idx_obs)
            except Exception as e:
                log.debug("Semantic indexing failed for %s: %s", inv_id, e)

            # 7. Auto rabbit trail (if contradictions or interesting signals detected)
            try:
                if cycle_log.alerts_fired > 0 or cycle_log.entities_extracted > 5:
                    from sts_monitor.rabbit_trail import run_rabbit_trail, store_trail_session
                    all_obs_dicts = [
                        {"source": o.source, "claim": o.claim, "captured_at": o.captured_at,
                         "reliability_hint": o.reliability_hint}
                        for o in session.query(ObservationORM).filter_by(
                            investigation_id=inv_id
                        ).order_by(ObservationORM.captured_at.desc()).limit(200).all()
                    ]
                    ent_list = list({
                        em.normalized or em.entity_text
                        for em in session.query(EntityMentionORM).filter_by(
                            investigation_id=inv_id
                        ).limit(100).all()
                    })
                    if all_obs_dicts and ent_list:
                        trail = run_rabbit_trail(
                            investigation_id=inv_id,
                            topic=inv_topic,
                            observations=all_obs_dicts,
                            entities=ent_list,
                            max_depth=5,
                        )
                        store_trail_session(trail)
                        cycle_log.rabbit_trail = True
            except Exception as e:
                log.debug("Auto rabbit trail failed for %s: %s", inv_id, e)

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
                    .filter(InvestigationORM.status.in_(["active", "monitoring", "open"]))
                    .order_by(InvestigationORM.priority.desc())
                    .limit(AUTOPILOT_MAX_INVESTIGATIONS)
                    .all()
                )
                inv_list = [(inv.id, inv.topic, inv.seed_query) for inv in investigations]

            if not inv_list:
                log.debug("Autopilot: no active investigations, sleeping")
                continue

            # Run cycle for each investigation
            for inv_id, inv_topic, seed_query in inv_list:
                cycle_log = await asyncio.to_thread(
                    _run_cycle_for_investigation, inv_id, inv_topic, llm_ok, seed_query
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
                    "connectors_used": cycle_log.connectors_used,
                    "connectors_failed": cycle_log.connectors_failed,
                    "alerts_fired": cycle_log.alerts_fired,
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
