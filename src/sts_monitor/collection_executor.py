"""Collection plan executor.

Takes a CollectionRequirement (or list of them) and actually runs the
corresponding connectors, returning collected observations.

This bridges the gap between collection_plan.py (what to collect) and
the connectors (how to collect).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sts_monitor.collection_plan import CollectionRequirement
from sts_monitor.connectors.base import Connector
from sts_monitor.pipeline import Observation

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CollectionRunResult:
    """Result of executing a collection plan."""
    requirement_name: str
    investigation_id: str
    query: str
    connectors_attempted: int
    connectors_succeeded: int
    total_observations: int
    observations: list[Observation]
    connector_results: list[dict[str, Any]]
    started_at: datetime
    duration_ms: float
    errors: list[str]


def _build_connector(connector_name: str, requirement: CollectionRequirement) -> Connector | None:
    """Instantiate a connector by name with optional requirement-specific config."""
    from sts_monitor.connectors import (
        GDELTConnector, USGSEarthquakeConnector, NASAFIRMSConnector,
        ACLEDConnector, NWSAlertConnector, FEMADisasterConnector,
        ReliefWebConnector, RSSConnector, RedditConnector,
        NitterConnector, SearchConnector, OpenSkyConnector,
        WebcamConnector, ADSBExchangeConnector, MarineTrafficConnector,
        TelegramConnector, InternetArchiveConnector, WebScraperConnector,
    )

    connector_map: dict[str, type] = {
        "gdelt": GDELTConnector,
        "usgs": USGSEarthquakeConnector,
        "nasa_firms": NASAFIRMSConnector,
        "acled": ACLEDConnector,
        "nws": NWSAlertConnector,
        "fema": FEMADisasterConnector,
        "reliefweb": ReliefWebConnector,
        "rss": RSSConnector,
        "reddit": RedditConnector,
        "nitter": NitterConnector,
        "search": SearchConnector,
        "opensky": OpenSkyConnector,
        "webcam": WebcamConnector,
        "adsb": ADSBExchangeConnector,
        "marine": MarineTrafficConnector,
        "telegram": TelegramConnector,
        "archive": InternetArchiveConnector,
        "web_scraper": WebScraperConnector,
    }

    cls = connector_map.get(connector_name)
    if cls is None:
        logger.warning("Unknown connector: %s", connector_name)
        return None

    # Pass requirement-specific filters as kwargs where possible
    filters = requirement.filters.get(connector_name, {})
    try:
        return cls(**filters) if filters else cls()
    except TypeError:
        # Some connectors require specific args (e.g. RSSConnector needs feed_urls)
        logger.warning("Cannot instantiate %s without specific config", connector_name)
        return None


def execute_requirement(requirement: CollectionRequirement) -> CollectionRunResult:
    """Execute a single collection requirement — run all its connectors."""
    import time
    started = datetime.now(UTC)
    t0 = time.perf_counter()

    all_observations: list[Observation] = []
    connector_results: list[dict[str, Any]] = []
    errors: list[str] = []
    succeeded = 0

    for connector_name in requirement.connectors:
        connector = _build_connector(connector_name, requirement)
        if connector is None:
            errors.append(f"Unknown connector: {connector_name}")
            continue

        try:
            result = connector.collect(requirement.query)
            all_observations.extend(result.observations)
            connector_results.append({
                "connector": connector_name,
                "observations": len(result.observations),
                "status": "ok",
            })
            succeeded += 1
            logger.info("  %s: %d observations for '%s'",
                       connector_name, len(result.observations), requirement.query)
        except Exception as exc:
            errors.append(f"{connector_name}: {exc}")
            connector_results.append({
                "connector": connector_name,
                "observations": 0,
                "status": "error",
                "error": str(exc),
            })
            logger.warning("  %s failed: %s", connector_name, exc)

    elapsed = round((time.perf_counter() - t0) * 1000, 2)

    return CollectionRunResult(
        requirement_name=requirement.name,
        investigation_id=requirement.investigation_id,
        query=requirement.query,
        connectors_attempted=len(requirement.connectors),
        connectors_succeeded=succeeded,
        total_observations=len(all_observations),
        observations=all_observations,
        connector_results=connector_results,
        started_at=started,
        duration_ms=elapsed,
        errors=errors,
    )


def execute_plan(requirements: list[CollectionRequirement]) -> list[CollectionRunResult]:
    """Execute all active requirements in a collection plan."""
    results: list[CollectionRunResult] = []
    active = [r for r in requirements if r.active]

    # Sort by priority (highest first)
    active.sort(key=lambda r: r.priority, reverse=True)

    logger.info("Executing collection plan: %d active requirements", len(active))
    for req in active:
        logger.info("Collecting: %s (query=%r, connectors=%s)", req.name, req.query, req.connectors)
        result = execute_requirement(req)
        results.append(result)

    total_obs = sum(r.total_observations for r in results)
    logger.info("Collection plan complete: %d observations from %d requirements", total_obs, len(results))

    return results
