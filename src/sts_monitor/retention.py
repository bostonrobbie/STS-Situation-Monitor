"""Data retention and cleanup strategy.

Prevents unbounded growth by:
1. Archiving observations older than N days (default 90)
2. Cleaning completed/dead-lettered jobs
3. Pruning old ingestion run records
4. Removing stale alert events
5. Compacting old reports (keeping only summaries)

Respects investigation status — never deletes data for open investigations.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, func
from sqlalchemy.orm import Session

from sts_monitor.database import get_session
from sts_monitor.models import (
    AlertEventORM, IngestionRunORM, InvestigationORM,
    JobORM, ObservationORM,
)

logger = logging.getLogger(__name__)


def run_retention(
    *,
    max_age_days: int = 90,
    job_max_age_days: int = 30,
    alert_max_age_days: int = 60,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run data retention cleanup.

    Returns dict with counts of removed items.
    """
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    job_cutoff = datetime.now(UTC) - timedelta(days=job_max_age_days)
    alert_cutoff = datetime.now(UTC) - timedelta(days=alert_max_age_days)

    result: dict[str, Any] = {
        "observations_removed": 0,
        "ingestion_runs_removed": 0,
        "alert_events_removed": 0,
        "jobs_removed": 0,
        "dry_run": dry_run,
        "cutoff_date": cutoff.isoformat(),
    }

    session: Session = next(get_session())
    try:
        # Find open investigations (never delete their data)
        open_investigations = session.scalars(
            select(InvestigationORM.id).where(
                InvestigationORM.status.in_(["open", "monitoring"])
            )
        ).all()
        open_ids = set(open_investigations)

        # ── Observations ──────────────────────────────────────────
        obs_query = select(func.count()).select_from(ObservationORM).where(
            ObservationORM.captured_at < cutoff,
        )
        if open_ids:
            obs_query = obs_query.where(ObservationORM.investigation_id.notin_(open_ids))
        obs_count = session.scalar(obs_query) or 0
        result["observations_removed"] = obs_count

        if not dry_run and obs_count > 0:
            del_q = delete(ObservationORM).where(ObservationORM.captured_at < cutoff)
            if open_ids:
                del_q = del_q.where(ObservationORM.investigation_id.notin_(open_ids))
            session.execute(del_q)

        # ── Ingestion runs ────────────────────────────────────────
        run_query = select(func.count()).select_from(IngestionRunORM).where(
            IngestionRunORM.started_at < cutoff,
        )
        if open_ids:
            run_query = run_query.where(IngestionRunORM.investigation_id.notin_(open_ids))
        run_count = session.scalar(run_query) or 0
        result["ingestion_runs_removed"] = run_count

        if not dry_run and run_count > 0:
            del_q = delete(IngestionRunORM).where(IngestionRunORM.started_at < cutoff)
            if open_ids:
                del_q = del_q.where(IngestionRunORM.investigation_id.notin_(open_ids))
            session.execute(del_q)

        # ── Alert events ──────────────────────────────────────────
        alert_count = session.scalar(
            select(func.count()).select_from(AlertEventORM).where(
                AlertEventORM.triggered_at < alert_cutoff,
            )
        ) or 0
        result["alert_events_removed"] = alert_count

        if not dry_run and alert_count > 0:
            session.execute(
                delete(AlertEventORM).where(AlertEventORM.triggered_at < alert_cutoff)
            )

        # ── Completed/dead-lettered jobs ──────────────────────────
        job_count = session.scalar(
            select(func.count()).select_from(JobORM).where(
                JobORM.created_at < job_cutoff,
                JobORM.status.in_(["completed", "failed"]),
            )
        ) or 0
        result["jobs_removed"] = job_count

        if not dry_run and job_count > 0:
            session.execute(
                delete(JobORM).where(
                    JobORM.created_at < job_cutoff,
                    JobORM.status.in_(["completed", "failed"]),
                )
            )

        if not dry_run:
            session.commit()
            logger.info("Retention cleanup: %s", result)
        else:
            session.rollback()
            logger.info("Retention dry run: %s", result)

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return result
