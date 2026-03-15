"""Shared dependencies and singletons for the STS Situation Monitor API."""
from __future__ import annotations

from sts_monitor.config import settings
from sts_monitor.llm import LocalLLMClient
from sts_monitor.pipeline import SignalPipeline

pipeline = SignalPipeline()

llm_client = LocalLLMClient(
    base_url=settings.local_llm_url,
    model=settings.local_llm_model,
    timeout_s=settings.local_llm_timeout_s,
    max_retries=settings.local_llm_max_retries,
)
