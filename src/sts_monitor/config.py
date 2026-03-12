from dataclasses import dataclass
import os


@dataclass(slots=True)
class Settings:
    env: str = os.getenv("STS_ENV", "dev")
    api_port: int = int(os.getenv("STS_API_PORT", "8080"))
    database_url: str = os.getenv("STS_DATABASE_URL", "sqlite:///./sts_monitor.db")
    redis_url: str = os.getenv("STS_REDIS_URL", "redis://localhost:6379/0")
    qdrant_url: str = os.getenv("STS_QDRANT_URL", "http://localhost:6333")
    local_llm_url: str = os.getenv("STS_LOCAL_LLM_URL", "http://localhost:11434")
    local_llm_model: str = os.getenv("STS_LOCAL_LLM_MODEL", "llama3.1")
    local_llm_timeout_s: float = float(os.getenv("STS_LOCAL_LLM_TIMEOUT_S", "10"))
    local_llm_max_retries: int = int(os.getenv("STS_LOCAL_LLM_MAX_RETRIES", "2"))
    workspace_root: str = os.getenv("STS_WORKSPACE_ROOT", ".")
    auth_api_key: str = os.getenv("STS_AUTH_API_KEY", "change-me")
    enforce_auth: bool = os.getenv("STS_ENFORCE_AUTH", "true").lower() in {"1", "true", "yes"}
    rss_timeout_s: float = float(os.getenv("STS_RSS_TIMEOUT_S", "10"))
    rss_max_retries: int = int(os.getenv("STS_RSS_MAX_RETRIES", "2"))
    reddit_timeout_s: float = float(os.getenv("STS_REDDIT_TIMEOUT_S", "10"))
    reddit_user_agent: str = os.getenv("STS_REDDIT_USER_AGENT", "STS-Situation-Monitor/0.6")
    job_max_attempts: int = int(os.getenv("STS_JOB_MAX_ATTEMPTS", "3"))
    job_retry_backoff_s: int = int(os.getenv("STS_JOB_RETRY_BACKOFF_S", "10"))
    public_base_url: str = os.getenv("STS_PUBLIC_BASE_URL", "http://localhost:8080")
    cors_origins: str = os.getenv("STS_CORS_ORIGINS", "")
    trusted_hosts: str = os.getenv("STS_TRUSTED_HOSTS", "*")
    alert_webhook_url: str = os.getenv("STS_ALERT_WEBHOOK_URL", "")
    alert_webhook_timeout_s: float = float(os.getenv("STS_ALERT_WEBHOOK_TIMEOUT_S", "5"))
    enforce_report_lineage_gate: bool = os.getenv("STS_ENFORCE_REPORT_LINEAGE_GATE", "false").lower() in {"1", "true", "yes"}
    report_min_lineage_coverage: float = float(os.getenv("STS_REPORT_MIN_LINEAGE_COVERAGE", "0.7"))
    # --- New data source connectors ---
    gdelt_timeout_s: float = float(os.getenv("STS_GDELT_TIMEOUT_S", "15"))
    gdelt_default_timespan: str = os.getenv("STS_GDELT_DEFAULT_TIMESPAN", "3h")
    gdelt_max_records: int = int(os.getenv("STS_GDELT_MAX_RECORDS", "75"))
    usgs_min_magnitude: float = float(os.getenv("STS_USGS_MIN_MAGNITUDE", "4.0"))
    usgs_timeout_s: float = float(os.getenv("STS_USGS_TIMEOUT_S", "10"))
    usgs_lookback_hours: int = int(os.getenv("STS_USGS_LOOKBACK_HOURS", "24"))
    nasa_firms_map_key: str = os.getenv("STS_NASA_FIRMS_MAP_KEY", "")
    nasa_firms_timeout_s: float = float(os.getenv("STS_NASA_FIRMS_TIMEOUT_S", "15"))
    nasa_firms_sensor: str = os.getenv("STS_NASA_FIRMS_SENSOR", "VIIRS_NOAA20_NRT")
    acled_api_key: str = os.getenv("STS_ACLED_API_KEY", "")
    acled_email: str = os.getenv("STS_ACLED_EMAIL", "")
    acled_timeout_s: float = float(os.getenv("STS_ACLED_TIMEOUT_S", "15"))
    acled_lookback_days: int = int(os.getenv("STS_ACLED_LOOKBACK_DAYS", "7"))
    nws_severity_filter: str = os.getenv("STS_NWS_SEVERITY_FILTER", "Extreme,Severe")
    nws_timeout_s: float = float(os.getenv("STS_NWS_TIMEOUT_S", "10"))
    fema_timeout_s: float = float(os.getenv("STS_FEMA_TIMEOUT_S", "10"))
    fema_lookback_days: int = int(os.getenv("STS_FEMA_LOOKBACK_DAYS", "30"))
    reliefweb_timeout_s: float = float(os.getenv("STS_RELIEFWEB_TIMEOUT_S", "15"))
    opensky_timeout_s: float = float(os.getenv("STS_OPENSKY_TIMEOUT_S", "15"))
    # --- Webcam / Camera monitoring ---
    windy_api_key: str = os.getenv("STS_WINDY_API_KEY", "")
    webcam_timeout_s: float = float(os.getenv("STS_WEBCAM_TIMEOUT_S", "10"))
    webcam_default_regions: str = os.getenv("STS_WEBCAM_DEFAULT_REGIONS", "")
    # --- Nitter / Twitter ---
    nitter_instances: str = os.getenv("STS_NITTER_INSTANCES", "")  # comma-separated
    nitter_timeout_s: float = float(os.getenv("STS_NITTER_TIMEOUT_S", "12"))
    nitter_categories: str = os.getenv("STS_NITTER_CATEGORIES", "")  # comma-separated
    # --- Web scraper ---
    scraper_timeout_s: float = float(os.getenv("STS_SCRAPER_TIMEOUT_S", "15"))
    scraper_max_depth: int = int(os.getenv("STS_SCRAPER_MAX_DEPTH", "2"))
    scraper_max_pages: int = int(os.getenv("STS_SCRAPER_MAX_PAGES", "50"))
    scraper_delay_s: float = float(os.getenv("STS_SCRAPER_DELAY_S", "1.0"))
    # --- Search engine ---
    search_max_results: int = int(os.getenv("STS_SEARCH_MAX_RESULTS", "20"))
    search_timeout_s: float = float(os.getenv("STS_SEARCH_TIMEOUT_S", "15"))
    # --- Autonomous research agent ---
    agent_max_iterations: int = int(os.getenv("STS_AGENT_MAX_ITERATIONS", "5"))
    agent_max_observations: int = int(os.getenv("STS_AGENT_MAX_OBSERVATIONS", "500"))
    agent_inter_iteration_delay_s: float = float(os.getenv("STS_AGENT_INTER_ITERATION_DELAY_S", "5"))
    agent_llm_timeout_s: float = float(os.getenv("STS_AGENT_LLM_TIMEOUT_S", "60"))


settings = Settings()
