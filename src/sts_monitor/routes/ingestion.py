"""Ingestion route handlers for the STS Situation Monitor API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sts_monitor.config import settings
from sts_monitor.connectors import (
    ACLEDConnector,
    ADSBExchangeConnector,
    FEMADisasterConnector,
    GDELTConnector,
    InternetArchiveConnector,
    MarineTrafficConnector,
    NASAFIRMSConnector,
    NWSAlertConnector,
    OpenSkyConnector,
    RedditConnector,
    ReliefWebConnector,
    RSSConnector,
    TelegramConnector,
    USGSEarthquakeConnector,
    WebcamConnector,
)
from sts_monitor.database import get_session
from sts_monitor.helpers import ingest_with_geo_connector, record_audit, record_ingestion_run
from sts_monitor.models import InvestigationORM, ObservationORM
from sts_monitor.research import TrendingResearchScanner
from sts_monitor.schemas import (
    ACLEDIngestRequest,
    ADSBIngestRequest,
    ArchiveIngestRequest,
    FEMAIngestRequest,
    GDELTIngestRequest,
    LocalIngestRequest,
    MarineIngestRequest,
    NASAFIRMSIngestRequest,
    NWSIngestRequest,
    OpenSkyIngestRequest,
    RedditIngestRequest,
    ReliefWebIngestRequest,
    RSSIngestRequest,
    SimulatedIngestRequest,
    TelegramIngestRequest,
    TrendingResearchRequest,
    USGSIngestRequest,
    WebcamIngestRequest,
)
from sts_monitor.security import AuthContext, now_utc, require_analyst, require_api_key
from sts_monitor.simulation import generate_simulated_observations

router = APIRouter()


# ── RSS Connector Endpoint ─────────────────────────────────────────────


@router.post("/investigations/{investigation_id}/ingest/rss")
def ingest_rss(
    investigation_id: str,
    payload: RSSIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    connector = RSSConnector(
        feed_urls=payload.feed_urls,
        per_feed_limit=payload.per_feed_limit,
        timeout_s=settings.rss_timeout_s,
        max_retries=settings.rss_max_retries,
    )
    result = connector.collect(query=payload.query or investigation.seed_query or investigation.topic)

    for item in result.observations:
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

    failed = result.metadata.get("failed_feeds", [])
    status = "partial" if failed else "success"
    record_ingestion_run(
        session=session,
        investigation_id=investigation_id,
        connector="rss",
        ingested_count=len(result.observations),
        failed_count=len(failed),
        status=status,
        detail=result.metadata,
    )

    session.commit()
    stored_count = session.scalar(
        select(func.count(ObservationORM.id)).where(ObservationORM.investigation_id == investigation_id)
    )

    return {
        "investigation_id": investigation_id,
        "connector": result.connector,
        "ingested_count": len(result.observations),
        "stored_count": stored_count or 0,
        "failed_feeds": failed,
    }


# ── Simulated Connector Endpoint ───────────────────────────────────────


@router.post("/investigations/{investigation_id}/ingest/simulated")
def ingest_simulated(
    investigation_id: str,
    payload: SimulatedIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    generated = generate_simulated_observations(
        topic=investigation.topic,
        batch_size=payload.batch_size,
        include_noise=payload.include_noise,
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

    record_ingestion_run(
        session=session,
        investigation_id=investigation_id,
        connector="simulated",
        ingested_count=len(generated),
        failed_count=0,
        status="success",
        detail={"include_noise": payload.include_noise, "batch_size": payload.batch_size},
    )
    session.commit()

    return {
        "investigation_id": investigation_id,
        "connector": "simulated",
        "ingested_count": len(generated),
    }


# ── Reddit Connector Endpoint ─────────────────────────────────────────


@router.post("/investigations/{investigation_id}/ingest/reddit")
def ingest_reddit(
    investigation_id: str,
    payload: RedditIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    connector = RedditConnector(
        subreddits=payload.subreddits,
        per_subreddit_limit=payload.per_subreddit_limit,
        sort=payload.sort,
        timeout_s=settings.reddit_timeout_s,
        user_agent=settings.reddit_user_agent,
    )
    result = connector.collect(query=payload.query or investigation.seed_query or investigation.topic)

    for item in result.observations:
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

    failed = result.metadata.get("failed_subreddits", [])
    status = "partial" if failed else "success"
    record_ingestion_run(
        session=session,
        investigation_id=investigation_id,
        connector="reddit",
        ingested_count=len(result.observations),
        failed_count=len(failed),
        status=status,
        detail=result.metadata,
    )
    record_audit(session, actor=auth, action="ingest.reddit", resource_type="investigation", resource_id=investigation_id, detail={"ingested": len(result.observations), "failed": len(failed)})

    session.commit()
    stored_count = session.scalar(select(func.count(ObservationORM.id)).where(ObservationORM.investigation_id == investigation_id))

    return {
        "investigation_id": investigation_id,
        "connector": result.connector,
        "ingested_count": len(result.observations),
        "stored_count": stored_count or 0,
        "failed_subreddits": failed,
    }


# ── Local JSON Ingest Endpoint ─────────────────────────────────────────


@router.post("/investigations/{investigation_id}/ingest/local-json")
def ingest_local_json(
    investigation_id: str,
    payload: LocalIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    for item in payload.observations:
        session.add(
            ObservationORM(
                investigation_id=investigation_id,
                source=item.source,
                claim=item.claim,
                url=item.url,
                captured_at=item.captured_at or now_utc(),
                reliability_hint=item.reliability_hint,
            )
        )

    record_ingestion_run(
        session=session,
        investigation_id=investigation_id,
        connector="local-json",
        ingested_count=len(payload.observations),
        failed_count=0,
        status="success",
        detail={"count": len(payload.observations)},
    )
    record_audit(
        session,
        actor=auth,
        action="ingest.local-json",
        resource_type="investigation",
        resource_id=investigation_id,
        detail={"count": len(payload.observations)},
    )
    session.commit()
    return {"investigation_id": investigation_id, "connector": "local-json", "ingested_count": len(payload.observations)}


# ── Trending Research Endpoint ─────────────────────────────────────────


@router.post("/investigations/{investigation_id}/ingest/trending")
def ingest_trending(
    investigation_id: str,
    payload: TrendingResearchRequest,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(InvestigationORM, investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    scanner = TrendingResearchScanner(max_topics=payload.max_topics, per_topic_limit=payload.per_topic_limit)
    observations, metadata = scanner.collect_observations(geo=payload.geo)

    for item in observations:
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

    failed_topics = metadata.get("failed_topics", [])
    status = "partial" if failed_topics else "success"
    record_ingestion_run(
        session=session,
        investigation_id=investigation_id,
        connector="trending",
        ingested_count=len(observations),
        failed_count=len(failed_topics),
        status=status,
        detail=metadata,
    )
    session.commit()

    return {
        "investigation_id": investigation_id,
        "connector": "trending",
        "ingested_count": len(observations),
        "topics_scanned": metadata.get("topics_scanned", 0),
        "failed_topics": failed_topics,
    }


# ── GDELT Connector Endpoint ──────────────────────────────────────────


@router.post("/investigations/{investigation_id}/ingest/gdelt")
def ingest_gdelt(
    investigation_id: str,
    payload: GDELTIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = GDELTConnector(
        timespan=payload.timespan,
        max_records=payload.max_records,
        mode=payload.mode,
        source_country=payload.source_country,
        source_lang=payload.source_lang,
        timeout_s=settings.gdelt_timeout_s,
    )
    return ingest_with_geo_connector(
        session, investigation_id, "gdelt", connector, payload.query, auth,
    )


# ── USGS Earthquake Connector Endpoint ────────────────────────────────


@router.post("/investigations/{investigation_id}/ingest/usgs")
def ingest_usgs(
    investigation_id: str,
    payload: USGSIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = USGSEarthquakeConnector(
        min_magnitude=payload.min_magnitude,
        lookback_hours=payload.lookback_hours,
        max_events=payload.max_events,
        use_summary_feed=payload.summary_feed if payload.use_summary_feed else None,
        timeout_s=settings.usgs_timeout_s,
    )
    return ingest_with_geo_connector(
        session, investigation_id, "usgs", connector, payload.query, auth,
    )


# ── NASA FIRMS Fire Connector Endpoint ────────────────────────────────


@router.post("/investigations/{investigation_id}/ingest/nasa-firms")
def ingest_nasa_firms(
    investigation_id: str,
    payload: NASAFIRMSIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if not settings.nasa_firms_map_key:
        raise HTTPException(status_code=503, detail="NASA FIRMS MAP_KEY not configured")
    connector = NASAFIRMSConnector(
        map_key=settings.nasa_firms_map_key,
        sensor=settings.nasa_firms_sensor,
        country_code=payload.country_code,
        days=payload.days,
        min_confidence=payload.min_confidence,
        timeout_s=settings.nasa_firms_timeout_s,
    )
    return ingest_with_geo_connector(
        session, investigation_id, "nasa_firms", connector, payload.query, auth,
    )


# ── ACLED Conflict Connector Endpoint ─────────────────────────────────


@router.post("/investigations/{investigation_id}/ingest/acled")
def ingest_acled(
    investigation_id: str,
    payload: ACLEDIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if not settings.acled_api_key or not settings.acled_email:
        raise HTTPException(status_code=503, detail="ACLED API key/email not configured")
    connector = ACLEDConnector(
        api_key=settings.acled_api_key,
        email=settings.acled_email,
        lookback_days=payload.lookback_days,
        limit=payload.limit,
        country=payload.country,
        region=payload.region,
        event_type=payload.event_type,
        timeout_s=settings.acled_timeout_s,
    )
    return ingest_with_geo_connector(
        session, investigation_id, "acled", connector, payload.query, auth,
    )


# ── NWS Weather Alerts Connector Endpoint ─────────────────────────────


@router.post("/investigations/{investigation_id}/ingest/nws")
def ingest_nws(
    investigation_id: str,
    payload: NWSIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = NWSAlertConnector(
        severity_filter=payload.severity_filter,
        status=payload.status,
        urgency=payload.urgency,
        area=payload.area,
        timeout_s=settings.nws_timeout_s,
    )
    return ingest_with_geo_connector(
        session, investigation_id, "nws", connector, payload.query, auth,
    )


# ── FEMA Disaster Connector Endpoint ──────────────────────────────────


@router.post("/investigations/{investigation_id}/ingest/fema")
def ingest_fema(
    investigation_id: str,
    payload: FEMAIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = FEMADisasterConnector(
        lookback_days=payload.lookback_days,
        limit=payload.limit,
        state=payload.state,
        declaration_type=payload.declaration_type,
        timeout_s=settings.fema_timeout_s,
    )
    return ingest_with_geo_connector(
        session, investigation_id, "fema", connector, payload.query, auth,
    )


# ── ReliefWeb Connector Endpoint ──────────────────────────────────────


@router.post("/investigations/{investigation_id}/ingest/reliefweb")
def ingest_reliefweb(
    investigation_id: str,
    payload: ReliefWebIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = ReliefWebConnector(
        lookback_days=payload.lookback_days,
        limit=payload.limit,
        country=payload.country,
        disaster_type=payload.disaster_type,
        content_format=payload.content_format,
        timeout_s=settings.reliefweb_timeout_s,
    )
    return ingest_with_geo_connector(
        session, investigation_id, "reliefweb", connector, payload.query, auth,
    )


# ── OpenSky Aircraft Connector Endpoint ────────────────────────────────


@router.post("/investigations/{investigation_id}/ingest/opensky")
def ingest_opensky(
    investigation_id: str,
    payload: OpenSkyIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    bbox = None
    if all(v is not None for v in [payload.bbox_lamin, payload.bbox_lomin, payload.bbox_lamax, payload.bbox_lomax]):
        bbox = (payload.bbox_lamin, payload.bbox_lomin, payload.bbox_lamax, payload.bbox_lomax)
    connector = OpenSkyConnector(bbox=bbox, timeout_s=settings.opensky_timeout_s)
    return ingest_with_geo_connector(
        session, investigation_id, "opensky", connector, payload.query, auth,
    )


# ── Webcam / Camera Connector Endpoint ─────────────────────────────────


@router.post("/investigations/{investigation_id}/ingest/webcams")
def ingest_webcams(
    investigation_id: str,
    payload: WebcamIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = WebcamConnector(
        windy_api_key=settings.windy_api_key or None,
        regions=payload.regions,
        nearby_lat=payload.nearby_lat,
        nearby_lon=payload.nearby_lon,
        nearby_radius_km=payload.nearby_radius_km,
    )
    return ingest_with_geo_connector(
        session, investigation_id, "webcams", connector, payload.query, auth,
    )


# ── ADS-B Aircraft Connector Endpoint ──────────────────────────────────


@router.post("/investigations/{investigation_id}/ingest/adsb")
def ingest_adsb(
    investigation_id: str,
    payload: ADSBIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Ingest ADS-B aircraft tracking data (includes military flights)."""
    connector = ADSBExchangeConnector(
        center_lat=payload.lat,
        center_lon=payload.lon,
        radius_nm=payload.dist_nm,
        military_only=payload.military_only,
    )
    return ingest_with_geo_connector(
        session, investigation_id, "adsb", connector, payload.query, auth,
    )


# ── Marine / AIS Vessel Connector Endpoint ─────────────────────────────


@router.post("/investigations/{investigation_id}/ingest/marine")
def ingest_marine(
    investigation_id: str,
    payload: MarineIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Ingest AIS marine vessel tracking data."""
    connector = MarineTrafficConnector(
        bbox=(
            payload.bbox_lat_min,
            payload.bbox_lon_min,
            payload.bbox_lat_max,
            payload.bbox_lon_max,
        ),
        vessel_types=payload.vessel_types,
    )
    return ingest_with_geo_connector(
        session, investigation_id, "marine", connector, payload.query, auth,
    )


# ── Telegram Public Channel Connector Endpoint ─────────────────────────


@router.post("/investigations/{investigation_id}/ingest/telegram")
def ingest_telegram(
    investigation_id: str,
    payload: TelegramIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Scrape public Telegram channels for OSINT intelligence."""
    # TelegramConnector expects list[dict] with handle/name/category fields
    channel_dicts = (
        [{"handle": h, "name": h, "category": "custom"} for h in payload.channels]
        if payload.channels
        else None
    )
    connector = TelegramConnector(
        channels=channel_dicts,
        per_channel_limit=payload.max_posts_per_channel,
    )
    return ingest_with_geo_connector(
        session, investigation_id, "telegram", connector, payload.query, auth,
    )


# ── Internet Archive / Wayback Machine Connector Endpoint ──────────────


@router.post("/investigations/{investigation_id}/ingest/archive")
def ingest_archive(
    investigation_id: str,
    payload: ArchiveIngestRequest,
    auth: AuthContext = Depends(require_analyst),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Retrieve archived/cached versions of URLs from the Wayback Machine."""
    connector = InternetArchiveConnector(
        urls_to_check=payload.urls,
        search_query=payload.query,
        max_snapshots=payload.max_snapshots,
    )
    return ingest_with_geo_connector(
        session, investigation_id, "archive", connector, payload.query, auth,
    )
