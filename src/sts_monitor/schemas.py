"""Pydantic request/response schemas for the STS Situation Monitor API."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class InvestigationCreate(BaseModel):
    topic: str = Field(min_length=3, max_length=300)
    seed_query: str | None = None
    priority: int = Field(default=50, ge=1, le=100)
    owner: str | None = Field(default=None, max_length=120)
    status: str = Field(default="open", pattern="^(open|monitoring|resolved|closed)$")
    sla_due_at: datetime | None = None


class InvestigationUpdateRequest(BaseModel):
    priority: int | None = Field(default=None, ge=1, le=100)
    owner: str | None = Field(default=None, max_length=120)
    status: str | None = Field(default=None, pattern="^(open|monitoring|resolved|closed)$")
    sla_due_at: datetime | None = None


class Investigation(BaseModel):
    id: str
    topic: str
    seed_query: str | None = None
    priority: int
    owner: str | None = None
    status: str
    sla_due_at: datetime | None = None
    created_at: datetime


class RSSIngestRequest(BaseModel):
    feed_urls: list[str] = Field(min_length=1)
    query: str | None = None
    per_feed_limit: int = Field(default=10, ge=1, le=50)


class SimulatedIngestRequest(BaseModel):
    batch_size: int = Field(default=20, ge=1, le=500)
    include_noise: bool = True


class RedditIngestRequest(BaseModel):
    subreddits: list[str] = Field(min_length=1, max_length=20)
    query: str | None = None
    per_subreddit_limit: int = Field(default=25, ge=1, le=100)
    sort: str = Field(default="new", pattern="^(new|hot|top)$")


class TrendingResearchRequest(BaseModel):
    geo: str = Field(default="US", min_length=2, max_length=3)
    max_topics: int = Field(default=10, ge=1, le=50)
    per_topic_limit: int = Field(default=5, ge=1, le=20)


class GDELTIngestRequest(BaseModel):
    query: str | None = None
    timespan: str = Field(default="3h", pattern=r"^\d+[hd]$")
    max_records: int = Field(default=75, ge=1, le=250)
    mode: str = Field(default="ArtList", pattern="^(ArtList|TimelineVol|TimelineSourceCountry)$")
    source_country: str | None = None
    source_lang: str | None = None


class USGSIngestRequest(BaseModel):
    query: str | None = None
    min_magnitude: float = Field(default=4.0, ge=0.0, le=10.0)
    lookback_hours: int = Field(default=24, ge=1, le=720)
    max_events: int = Field(default=100, ge=1, le=500)
    use_summary_feed: bool = False
    summary_feed: str = Field(default="significant_hour", pattern="^(significant_hour|m4\\.5_day|m2\\.5_day|all_hour)$")


class NASAFIRMSIngestRequest(BaseModel):
    query: str | None = None
    country_code: str | None = Field(default=None, min_length=3, max_length=3)
    days: int = Field(default=1, ge=1, le=10)
    min_confidence: str = Field(default="nominal", pattern="^(low|nominal|high)$")


class ACLEDIngestRequest(BaseModel):
    query: str | None = None
    lookback_days: int = Field(default=7, ge=1, le=365)
    limit: int = Field(default=100, ge=1, le=5000)
    country: str | None = None
    region: int | None = None
    event_type: str | None = None


class NWSIngestRequest(BaseModel):
    query: str | None = None
    severity_filter: str = Field(default="Extreme,Severe")
    status: str = Field(default="actual", pattern="^(actual|exercise|system|test|draft)$")
    urgency: str | None = None
    area: str | None = Field(default=None, min_length=2, max_length=2)


class FEMAIngestRequest(BaseModel):
    query: str | None = None
    lookback_days: int = Field(default=30, ge=1, le=365)
    limit: int = Field(default=100, ge=1, le=1000)
    state: str | None = Field(default=None, min_length=2, max_length=2)
    declaration_type: str | None = Field(default=None, pattern="^(DR|EM|FM|FS)$")


class ReliefWebIngestRequest(BaseModel):
    query: str | None = None
    lookback_days: int = Field(default=7, ge=1, le=365)
    limit: int = Field(default=50, ge=1, le=500)
    country: str | None = None
    disaster_type: str | None = None
    content_format: str | None = None


class OpenSkyIngestRequest(BaseModel):
    query: str | None = None
    bbox_lamin: float | None = None
    bbox_lomin: float | None = None
    bbox_lamax: float | None = None
    bbox_lomax: float | None = None


class WebcamIngestRequest(BaseModel):
    query: str | None = None
    regions: list[str] | None = None
    nearby_lat: float | None = None
    nearby_lon: float | None = None
    nearby_radius_km: int = Field(default=50, ge=1, le=500)


class ADSBIngestRequest(BaseModel):
    query: str | None = None
    lat: float = Field(default=0.0, ge=-90.0, le=90.0)
    lon: float = Field(default=0.0, ge=-180.0, le=180.0)
    dist_nm: int = Field(default=250, ge=10, le=500)
    military_only: bool = False


class MarineIngestRequest(BaseModel):
    query: str | None = None
    bbox_lat_min: float = Field(default=-90.0, ge=-90.0, le=90.0)
    bbox_lat_max: float = Field(default=90.0, ge=-90.0, le=90.0)
    bbox_lon_min: float = Field(default=-180.0, ge=-180.0, le=180.0)
    bbox_lon_max: float = Field(default=180.0, ge=-180.0, le=180.0)
    vessel_types: list[str] | None = None


class TelegramIngestRequest(BaseModel):
    query: str | None = None
    channels: list[str] | None = None
    max_posts_per_channel: int = Field(default=20, ge=1, le=100)


class ArchiveIngestRequest(BaseModel):
    query: str | None = None
    urls: list[str] = Field(min_length=1, max_length=20)
    max_snapshots: int = Field(default=5, ge=1, le=20)


class CollectionPlanCreateRequest(BaseModel):
    investigation_id: str
    name: str = Field(min_length=3, max_length=200)
    connectors: list[str] = Field(min_length=1)
    query: str = Field(min_length=2, max_length=500)
    priority: int = Field(default=50, ge=1, le=100)
    interval_seconds: int = Field(default=3600, ge=60, le=86_400)
    filters: dict[str, Any] = Field(default_factory=dict)
    auto_generate: bool = False


class PromoteTopicRequest(BaseModel):
    priority: int = Field(default=50, ge=1, le=100)
    owner: str | None = None


class RunRequest(BaseModel):
    use_llm: bool = False


class FeedbackRequest(BaseModel):
    label: str = Field(min_length=2, max_length=50)
    notes: str = Field(min_length=2, max_length=5000)


class LocalObservationInput(BaseModel):
    source: str = Field(min_length=2, max_length=600)
    claim: str = Field(min_length=2, max_length=10000)
    url: str = Field(min_length=3, max_length=1200)
    reliability_hint: float = Field(default=0.5, ge=0.0, le=1.0)
    captured_at: datetime | None = None


class LocalIngestRequest(BaseModel):
    observations: list[LocalObservationInput] = Field(min_length=1, max_length=2000)


class EnqueueRunJobRequest(BaseModel):
    use_llm: bool = False
    priority: int = Field(default=60, ge=1, le=100)
    max_attempts: int = Field(default=3, ge=1, le=10)


class EnqueueSimulatedJobRequest(BaseModel):
    batch_size: int = Field(default=20, ge=1, le=500)
    include_noise: bool = True
    priority: int = Field(default=50, ge=1, le=100)
    max_attempts: int = Field(default=3, ge=1, le=10)


class CreateScheduleRequest(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    job_type: str = Field(pattern="^(ingest_simulated|run_pipeline)$")
    payload: dict[str, Any]
    interval_seconds: int = Field(default=300, ge=10, le=86_400)
    priority: int = Field(default=50, ge=1, le=100)


class ProcessBatchRequest(BaseModel):
    high_quota: int = Field(default=2, ge=0, le=50)
    normal_quota: int = Field(default=2, ge=0, le=50)
    low_quota: int = Field(default=1, ge=0, le=50)


class ResearchSourceCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    source_type: str = Field(min_length=2, max_length=40)
    base_url: str = Field(min_length=5, max_length=1200)
    trust_score: float = Field(default=0.5, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)


class DiscoveryRequest(BaseModel):
    use_llm: bool = False


class AlertRuleCreateRequest(BaseModel):
    investigation_id: str
    name: str = Field(min_length=3, max_length=120)
    min_observations: int = Field(default=20, ge=1, le=10000)
    min_disputed_claims: int = Field(default=1, ge=0, le=1000)
    cooldown_seconds: int = Field(default=900, ge=60, le=86_400)
    active: bool = True


class APIKeyCreateRequest(BaseModel):
    label: str = Field(min_length=3, max_length=120)
    role: str = Field(default="analyst", pattern="^(admin|analyst|viewer)$")


class SearchProfileCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    investigation_id: str | None = None
    include_terms: list[str] = Field(default_factory=list, max_length=100)
    exclude_terms: list[str] = Field(default_factory=list, max_length=100)
    synonyms: dict[str, list[str]] = Field(default_factory=dict)


class SearchQueryRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    investigation_id: str | None = None
    profile_name: str | None = None
    source_prefix: str | None = None
    stance: str | None = Field(default=None, pattern="^(supported|disputed|unknown|monitor)$")
    min_reliability: float = Field(default=0.0, ge=0.0, le=1.0)
    since: datetime | None = None
    until: datetime | None = None
    include_observations: bool = True
    include_claims: bool = True
    min_score: float = Field(default=0.1, ge=0.0, le=1.0)
    limit: int = Field(default=50, ge=1, le=500)


class RelatedInvestigationsRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=10, ge=1, le=100)
    min_score: float = Field(default=0.1, ge=0.0, le=1.0)


class ResearchAgentRequest(BaseModel):
    investigation_id: str
    depth: int = Field(default=3, ge=1, le=10)
    max_sources: int = Field(default=5, ge=1, le=20)
    auto_ingest: bool = True


class ScrapeRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=10)
    investigation_id: str | None = None
    auto_ingest: bool = False


class TwitterSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    investigation_id: str | None = None
    max_results: int = Field(default=25, ge=1, le=100)
    auto_ingest: bool = False


class SemanticIndexRequest(BaseModel):
    investigation_id: str
    batch_size: int = Field(default=100, ge=1, le=1000)


class SemanticSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    investigation_id: str | None = None
    top_k: int = Field(default=10, ge=1, le=100)
    min_score: float = Field(default=0.3, ge=0.0, le=1.0)


class GenerateReportRequest(BaseModel):
    investigation_id: str
    focus: str | None = None
    format: str = Field(default="structured", pattern="^(structured|narrative|brief)$")


class ScheduleResearchRequest(BaseModel):
    investigation_id: str
    interval_hours: int = Field(default=6, ge=1, le=168)
    depth: int = Field(default=3, ge=1, le=10)
    max_sources: int = Field(default=5, ge=1, le=20)
    auto_ingest: bool = True


class CorroborationRequest(BaseModel):
    investigation_id: str
    min_sources: int = Field(default=2, ge=1, le=20)
    time_window_hours: int = Field(default=24, ge=1, le=720)


class SlopFilterRequest(BaseModel):
    investigation_id: str
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    action: str = Field(default="flag", pattern="^(flag|remove|report)$")
    show_scores: bool = False


class EntityGraphRequest(BaseModel):
    investigation_id: str
    min_mentions: int = Field(default=2, ge=1, le=100)
    include_relationships: bool = True
    max_entities: int = Field(default=50, ge=1, le=500)


class NarrativeTimelineRequest(BaseModel):
    investigation_id: str
    max_events: int = Field(default=50, ge=1, le=500)
    include_entities: bool = True


class AnomalyDetectionRequest(BaseModel):
    investigation_id: str
    sensitivity: float = Field(default=0.5, ge=0.0, le=1.0)
    min_observations: int = Field(default=10, ge=1, le=1000)
    time_window_hours: int = Field(default=24, ge=1, le=720)


class UserRegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=6, max_length=200)
    email: str | None = Field(default=None, max_length=200)
    role: str = Field(default="analyst", pattern="^(admin|analyst|viewer)$")


class UserLoginRequest(BaseModel):
    username: str
    password: str
    mfa_code: str | None = None


class KnowledgeGraphRequest(BaseModel):
    investigation_ids: list[str] = Field(min_length=1, max_length=20)
    min_connections: int = Field(default=1, ge=1, le=50)
    include_entities: bool = True
    include_claims: bool = True
    include_observations: bool = False


class AlertRuleCreate(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    condition_type: str = Field(default="threshold", pattern="^(threshold|anomaly|keyword)$")
    condition_value: dict[str, Any] = Field(default_factory=dict)
    severity: str = Field(default="medium", pattern="^(critical|high|medium|low)$")
    cooldown_seconds: int = Field(default=900, ge=60, le=86_400)
    active: bool = True


class TemplateApplyRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=300)
    owner: str | None = None
