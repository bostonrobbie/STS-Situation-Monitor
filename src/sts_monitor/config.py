from dataclasses import dataclass
import os


@dataclass(slots=True)
class Settings:
    env: str = os.getenv("STS_ENV", "dev")
    api_port: int = int(os.getenv("STS_API_PORT", "8080"))
    database_url: str = os.getenv("STS_DATABASE_URL", "sqlite:///./sts_monitor.db")
    database_url: str = os.getenv("STS_DATABASE_URL", "postgresql://sts:sts@localhost:5432/sts")
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
    job_max_attempts: int = int(os.getenv("STS_JOB_MAX_ATTEMPTS", "3"))
    job_retry_backoff_s: int = int(os.getenv("STS_JOB_RETRY_BACKOFF_S", "10"))


settings = Settings()
