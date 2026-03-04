from dataclasses import dataclass
import os


@dataclass(slots=True)
class Settings:
    env: str = os.getenv("STS_ENV", "dev")
    api_port: int = int(os.getenv("STS_API_PORT", "8080"))
    database_url: str = os.getenv("STS_DATABASE_URL", "postgresql://sts:sts@localhost:5432/sts")
    redis_url: str = os.getenv("STS_REDIS_URL", "redis://localhost:6379/0")
    qdrant_url: str = os.getenv("STS_QDRANT_URL", "http://localhost:6333")
    local_llm_url: str = os.getenv("STS_LOCAL_LLM_URL", "http://localhost:11434")
    local_llm_model: str = os.getenv("STS_LOCAL_LLM_MODEL", "llama3.1")


settings = Settings()
