from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, desc, select
from sqlalchemy.orm import Session

from sts_monitor.llm import LocalLLMClient
from sts_monitor.models import CollectionPlanORM, IngestionRunORM, InvestigationORM, JobORM, JobScheduleORM, ObservationORM, ReportORM
from sts_monitor.pipeline import Observation, SignalPipeline
from sts_monitor.simulation import generate_simulated_observations


def enqueue_job(
    session: Session,
    *,
    job_type: str,
    payload: dict[str, Any],
    run_at: datetime | None = None,
    priority: int = 50,
    max_attempts: int = 3,
) -> JobORM:
    bounded_priority = max(1, min(100, priority))
    bounded_attempts = max(1, min(10, max_attempts))
    job = JobORM(
        job_type=job_type,
        payload_json=json.dumps(payload),
        status="pending",
        priority=bounded_priority,
        attempts=0,
        max_attempts=bounded_attempts,
        dead_lettered=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        run_at=run_at or datetime.now(UTC),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def create_schedule(
    session: Session,
    *,
    name: str,
    job_type: str,
    payload: dict[str, Any],
    interval_seconds: int,
    priority: int = 50,
) -> JobScheduleORM:
    schedule = JobScheduleORM(
        name=name,
        job_type=job_type,
        payload_json=json.dumps(payload),
        interval_seconds=max(10, interval_seconds),
        priority=max(1, min(100, priority)),
        active=True,
        last_enqueued_at=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(schedule)
    session.commit()
    session.refresh(schedule)
    return schedule


def tick_schedules(session: Session, now: datetime | None = None, default_max_attempts: int = 3) -> int:
    current = now or datetime.now(UTC)
    schedules = session.scalars(select(JobScheduleORM).where(JobScheduleORM.active.is_(True))).all()
    enqueued = 0

    for schedule in schedules:
        if schedule.last_enqueued_at is None:
            due = True
        else:
            elapsed = (current - schedule.last_enqueued_at).total_seconds()
            due = elapsed >= schedule.interval_seconds

        if not due:
            continue

        enqueue_job(
            session,
            job_type=schedule.job_type,
            payload=json.loads(schedule.payload_json),
            run_at=current,
            priority=schedule.priority,
            max_attempts=default_max_attempts,
        )
        schedule.last_enqueued_at = current
        schedule.updated_at = current
        session.commit()
        enqueued += 1

    return enqueued


def _record_ingestion_run(
    session: Session,
    investigation_id: str,
    connector: str,
    ingested_count: int,
    failed_count: int,
    status: str,
    detail: dict[str, Any],
) -> None:
    session.add(
        IngestionRunORM(
            investigation_id=investigation_id,
            connector=connector,
            started_at=datetime.now(UTC),
            ingested_count=ingested_count,
            failed_count=failed_count,
            status=status,
            detail_json=json.dumps(detail),
        )
    )


def _fetch_next_pending_job(session: Session, priority_min: int = 1, priority_max: int = 100) -> JobORM | None:
    now = datetime.now(UTC)
    return session.scalars(
        select(JobORM)
        .where(JobORM.status == "pending")
        .where(JobORM.run_at <= now)
        .where(JobORM.dead_lettered.is_(False))
        .where(and_(JobORM.priority >= priority_min, JobORM.priority <= priority_max))
        .order_by(desc(JobORM.priority), JobORM.created_at.asc())
        .limit(1)
    ).first()


def _execute_job(session: Session, job: JobORM, pipeline: SignalPipeline, llm_client: LocalLLMClient) -> dict[str, Any]:
    payload = json.loads(job.payload_json)
    job.status = "running"
    job.attempts += 1
    job.updated_at = datetime.now(UTC)
    session.commit()

    if job.job_type == "ingest_simulated":
        investigation_id = payload["investigation_id"]
        investigation = session.get(InvestigationORM, investigation_id)
        if not investigation:
            raise ValueError("Investigation not found")

        generated = generate_simulated_observations(
            topic=investigation.topic,
            batch_size=int(payload.get("batch_size", 20)),
            include_noise=bool(payload.get("include_noise", True)),
        )
        for item in generated:
            session.add(
                ObservationORM(
                    investigation_id=investigation_id,
                    source=item.source,
                    claim=item.claim,
                    url=item.url,
                    captured_at=item.captured_at,
                    reliability_hint=item.reliability_hint,
                )
            )

        _record_ingestion_run(
            session,
            investigation_id=investigation_id,
            connector="simulated_job",
            ingested_count=len(generated),
            failed_count=0,
            status="success",
            detail={"job_id": job.id},
        )
        return {"job_type": job.job_type, "ingested_count": len(generated)}

    if job.job_type == "run_pipeline":
        investigation_id = payload["investigation_id"]
        use_llm = bool(payload.get("use_llm", False))
        investigation = session.get(InvestigationORM, investigation_id)
        if not investigation:
            raise ValueError("Investigation not found")

        db_observations = session.scalars(
            select(ObservationORM).where(ObservationORM.investigation_id == investigation_id)
        ).all()
        if not db_observations:
            raise ValueError("No observations available")

        observations = [
            Observation(
                source=item.source,
                claim=item.claim,
                url=item.url,
                captured_at=item.captured_at,
                reliability_hint=item.reliability_hint,
            )
            for item in db_observations
        ]
        pipeline_result = pipeline.run(observations, topic=investigation.topic)

        llm_summary: str | None = None
        llm_fallback_used = False
        if use_llm:
            try:
                llm_summary = llm_client.summarize(
                    f"Topic: {investigation.topic}\nSummary: {pipeline_result.summary}\nOutput concise brief."
                )
            except Exception as exc:
                llm_fallback_used = True
                llm_summary = f"LLM unavailable, fallback to deterministic summary: {exc}"

        report = ReportORM(
            investigation_id=investigation_id,
            generated_at=datetime.now(UTC),
            summary=llm_summary or pipeline_result.summary,
            confidence=pipeline_result.confidence,
            accepted_json=json.dumps([asdict(item) for item in pipeline_result.accepted], default=str),
            dropped_json=json.dumps([asdict(item) for item in pipeline_result.dropped], default=str),
        )
        session.add(report)
        return {
            "job_type": job.job_type,
            "report_id": report.id,
            "confidence": pipeline_result.confidence,
            "llm_fallback_used": llm_fallback_used,
        }

    if job.job_type == "execute_collection_plan":
        plan_id = payload["plan_id"]
        plan = session.get(CollectionPlanORM, plan_id)
        if not plan:
            raise ValueError(f"Collection plan {plan_id} not found")
        if not plan.active:
            raise ValueError(f"Collection plan {plan_id} is not active")

        connectors_list = json.loads(plan.connectors_json)
        total_ingested = 0

        # Lazy imports to avoid circular dependency
        from sts_monitor.connectors import (
            GDELTConnector, USGSEarthquakeConnector, NASAFIRMSConnector,
            ACLEDConnector, NWSAlertConnector, FEMADisasterConnector,
            ReliefWebConnector, OpenSkyConnector, WebcamConnector,
            NitterConnector, WebScraperConnector, SearchConnector,
        )
        from sts_monitor.connectors.nitter import get_accounts_for_categories
        from sts_monitor.config import settings as _settings

        plan_filters = json.loads(plan.filters_json) if hasattr(plan, "filters_json") and plan.filters_json else {}

        connector_map: dict[str, Any] = {
            "gdelt": lambda: GDELTConnector(),
            "usgs": lambda: USGSEarthquakeConnector(),
            "nasa_firms": lambda: NASAFIRMSConnector(map_key=_settings.nasa_firms_map_key or None),
            "acled": lambda: ACLEDConnector(api_key=_settings.acled_api_key or None, email=_settings.acled_email or None),
            "nws": lambda: NWSAlertConnector(),
            "fema": lambda: FEMADisasterConnector(),
            "reliefweb": lambda: ReliefWebConnector(),
            "opensky": lambda: OpenSkyConnector(),
            "webcams": lambda: WebcamConnector(windy_api_key=_settings.windy_api_key or None),
            "nitter": lambda: NitterConnector(
                accounts=get_accounts_for_categories(plan_filters.get("categories")),
            ),
            "web_scraper": lambda: WebScraperConnector(
                seed_urls=plan_filters.get("seed_urls", []),
                max_depth=_settings.scraper_max_depth,
                max_pages=_settings.scraper_max_pages,
                delay_between_requests_s=_settings.scraper_delay_s,
            ),
            "search": lambda: SearchConnector(max_results=_settings.search_max_results),
        }

        for connector_name in connectors_list:
            factory = connector_map.get(connector_name)
            if not factory:
                continue
            try:
                connector_obj = factory()
                result = connector_obj.collect(query=plan.query)
                for obs in result.observations:
                    session.add(ObservationORM(
                        investigation_id=plan.investigation_id,
                        source=obs.source,
                        claim=obs.claim,
                        url=obs.url,
                        captured_at=obs.captured_at,
                        reliability_hint=obs.reliability_hint,
                        connector_type=connector_name,
                    ))
                total_ingested += len(result.observations)
                _record_ingestion_run(
                    session, investigation_id=plan.investigation_id,
                    connector=connector_name, ingested_count=len(result.observations),
                    failed_count=0, status="success", detail={"plan_id": plan_id},
                )
            except Exception:
                pass  # Individual connector failures don't fail the whole plan

        plan.last_collected_at = datetime.now(UTC)
        plan.total_collected = (plan.total_collected or 0) + total_ingested
        session.commit()
        return {"job_type": job.job_type, "plan_id": plan_id, "ingested_count": total_ingested}

    if job.job_type == "run_research_agent":
        from sts_monitor.research_agent import ResearchAgent
        from sts_monitor.config import settings as _settings
        from sts_monitor.online_tools import parse_csv_env

        topic = payload["topic"]
        seed_query = payload.get("seed_query")
        agent_session_id = payload.get("session_id", f"job-{job.id}")

        nitter_instances = parse_csv_env(_settings.nitter_instances) or None
        nitter_cats = parse_csv_env(_settings.nitter_categories) or None

        agent_llm = LocalLLMClient(
            base_url=_settings.local_llm_url,
            model=_settings.local_llm_model,
            timeout_s=_settings.agent_llm_timeout_s,
            max_retries=_settings.local_llm_max_retries,
        )
        agent = ResearchAgent(
            llm_client=agent_llm,
            max_iterations=_settings.agent_max_iterations,
            max_observations=_settings.agent_max_observations,
            nitter_instances=nitter_instances,
            nitter_categories=nitter_cats,
            inter_iteration_delay_s=_settings.agent_inter_iteration_delay_s,
            scraper_max_depth=_settings.scraper_max_depth,
            scraper_max_pages=_settings.scraper_max_pages,
            scraper_delay_s=_settings.scraper_delay_s,
            search_max_results=_settings.search_max_results,
        )
        result_session = agent.run(agent_session_id, topic, seed_query)

        # Store observations in the investigation if linked
        investigation_id = payload.get("investigation_id")
        if investigation_id:
            for obs in result_session.all_observations:
                session.add(ObservationORM(
                    investigation_id=investigation_id,
                    source=obs.source,
                    claim=obs.claim,
                    url=obs.url,
                    captured_at=obs.captured_at,
                    reliability_hint=obs.reliability_hint,
                    connector_type="research_agent",
                ))
            _record_ingestion_run(
                session, investigation_id=investigation_id,
                connector="research_agent",
                ingested_count=len(result_session.all_observations),
                failed_count=0, status=result_session.status,
                detail={"session_id": agent_session_id, "iterations": len(result_session.iterations)},
            )

        return {
            "job_type": job.job_type,
            "session_id": agent_session_id,
            "status": result_session.status,
            "iterations": len(result_session.iterations),
            "observations": len(result_session.all_observations),
            "findings": len(result_session.all_findings),
        }

    raise ValueError(f"Unsupported job type: {job.job_type}")


def process_job(session: Session, job: JobORM, pipeline: SignalPipeline, llm_client: LocalLLMClient, retry_backoff_s: int = 10) -> dict[str, Any]:
    try:
        result = _execute_job(session, job, pipeline, llm_client)
        job.status = "completed"
        job.last_error = None
        job.updated_at = datetime.now(UTC)
        session.commit()
        return {"job_id": job.id, "status": job.status, "result": result}
    except Exception as exc:
        session.rollback()
        job.last_error = str(exc)
        job.updated_at = datetime.now(UTC)
        if job.attempts >= job.max_attempts:
            job.status = "dead_letter"
            job.dead_lettered = True
        else:
            job.status = "pending"
            job.run_at = datetime.now(UTC) + timedelta(seconds=max(1, retry_backoff_s))
        session.commit()
        return {"job_id": job.id, "status": job.status, "error": str(exc), "attempts": job.attempts}


def process_next_job(
    session: Session,
    pipeline: SignalPipeline,
    llm_client: LocalLLMClient,
    retry_backoff_s: int = 10,
) -> dict[str, Any] | None:
    job = _fetch_next_pending_job(session)
    if not job:
        return None
    return process_job(session, job, pipeline, llm_client, retry_backoff_s=retry_backoff_s)


def process_job_batch(
    session: Session,
    pipeline: SignalPipeline,
    llm_client: LocalLLMClient,
    *,
    high_quota: int = 2,
    normal_quota: int = 2,
    low_quota: int = 1,
    retry_backoff_s: int = 10,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for _ in range(max(0, high_quota)):
        job = _fetch_next_pending_job(session, priority_min=70, priority_max=100)
        if not job:
            break
        results.append(process_job(session, job, pipeline, llm_client, retry_backoff_s=retry_backoff_s))

    for _ in range(max(0, normal_quota)):
        job = _fetch_next_pending_job(session, priority_min=40, priority_max=69)
        if not job:
            break
        results.append(process_job(session, job, pipeline, llm_client, retry_backoff_s=retry_backoff_s))

    for _ in range(max(0, low_quota)):
        job = _fetch_next_pending_job(session, priority_min=1, priority_max=39)
        if not job:
            break
        results.append(process_job(session, job, pipeline, llm_client, retry_backoff_s=retry_backoff_s))

    return results


def requeue_dead_letter(session: Session, job_id: int) -> JobORM | None:
    job = session.get(JobORM, job_id)
    if not job or not job.dead_lettered:
        return None

    job.dead_lettered = False
    job.status = "pending"
    job.attempts = 0
    job.last_error = None
    job.run_at = datetime.now(UTC)
    job.updated_at = datetime.now(UTC)
    session.commit()
    session.refresh(job)
    return job
