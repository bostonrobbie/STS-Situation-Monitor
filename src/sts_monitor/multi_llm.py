"""Multi-LLM routing — use different models for different tasks.

Routes tasks to the most appropriate model:
- Fast model (e.g., phi3, tinyllama): entity extraction, classification
- Medium model (e.g., mistral, llama3.1:8b): summarization, analysis
- Large model (e.g., llama3.1:70b, mixtral): deep reasoning, verification

Automatically detects available models and falls back gracefully.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sts_monitor.config import settings

log = logging.getLogger(__name__)


@dataclass
class ModelInfo:
    """Information about an available model."""
    name: str
    size_gb: float = 0.0
    parameter_count: str = ""
    tier: str = "medium"  # fast, medium, large
    available: bool = False
    avg_latency_ms: float = 0.0
    tasks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "size_gb": self.size_gb,
            "parameters": self.parameter_count,
            "tier": self.tier,
            "available": self.available,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "tasks": self.tasks,
        }


# ── Model tier classification ──────────────────────────────────────
_TIER_MAP = {
    # Fast models (< 4B params)
    "phi3": "fast", "phi3:mini": "fast", "phi": "fast",
    "tinyllama": "fast", "gemma:2b": "fast", "qwen2:1.5b": "fast",
    "llama3.2:1b": "fast", "llama3.2:3b": "fast",
    # Medium models (7-13B params)
    "llama3.1": "medium", "llama3.1:8b": "medium", "llama3:8b": "medium",
    "mistral": "medium", "mistral:7b": "medium",
    "gemma:7b": "medium", "qwen2:7b": "medium",
    "codellama": "medium", "deepseek-coder": "medium",
    "llama3.2": "medium",
    # Large models (30B+ params)
    "llama3.1:70b": "large", "llama3:70b": "large",
    "mixtral": "large", "mixtral:8x7b": "large",
    "qwen2:72b": "large", "codellama:70b": "large",
    "deepseek-coder:33b": "large", "llama3.1:405b": "large",
}

# ── Task → preferred tier ──────────────────────────────────────────
TASK_TIERS = {
    "entity_extraction": "fast",
    "classification": "fast",
    "summarization": "medium",
    "analysis": "medium",
    "claim_verification": "large",
    "deep_reasoning": "large",
    "intel_brief": "large",
    "rabbit_trail_analysis": "large",
    "general": "medium",
}


def _classify_model_tier(model_name: str) -> str:
    """Classify a model into fast/medium/large tier."""
    name_lower = model_name.lower().strip()
    # Exact match
    if name_lower in _TIER_MAP:
        return _TIER_MAP[name_lower]
    # Prefix match
    for key, tier in _TIER_MAP.items():
        if name_lower.startswith(key):
            return tier
    # Guess by size indicators in name
    if any(s in name_lower for s in ("70b", "72b", "8x7b", "405b", "33b")):
        return "large"
    if any(s in name_lower for s in ("1b", "2b", "3b", "mini", "tiny")):
        return "fast"
    return "medium"


class MultiLLMRouter:
    """Routes LLM tasks to appropriate models based on task type and model availability."""

    def __init__(self, base_url: str | None = None, default_model: str | None = None):
        self.base_url = base_url or settings.local_llm_url
        self.default_model = default_model or settings.local_llm_model
        self._models: dict[str, ModelInfo] = {}
        self._last_scan: float = 0
        self._scan_interval = 300  # re-scan every 5 minutes

    def scan_models(self) -> list[ModelInfo]:
        """Discover available models from Ollama."""
        try:
            import httpx
            resp = httpx.get(f"{self.base_url}/api/tags", timeout=5.0)
            if resp.status_code != 200:
                return []

            data = resp.json()
            models = []
            for m in data.get("models", []):
                name = m.get("name", "")
                size_gb = m.get("size", 0) / (1024 ** 3)
                tier = _classify_model_tier(name)
                info = ModelInfo(
                    name=name,
                    size_gb=round(size_gb, 2),
                    parameter_count=m.get("details", {}).get("parameter_size", ""),
                    tier=tier,
                    available=True,
                    tasks=list(k for k, v in TASK_TIERS.items() if v == tier),
                )
                self._models[name] = info
                models.append(info)

            self._last_scan = time.time()
            return models
        except Exception as e:
            log.debug("Model scan failed: %s", e)
            return []

    def get_model_for_task(self, task: str) -> str:
        """Get the best available model for a task type."""
        # Re-scan if stale
        if time.time() - self._last_scan > self._scan_interval:
            self.scan_models()

        preferred_tier = TASK_TIERS.get(task, "medium")

        # Try to find a model in the preferred tier
        for name, info in self._models.items():
            if info.tier == preferred_tier and info.available:
                return name

        # Fallback: try adjacent tiers
        tier_order = {"fast": ["fast", "medium", "large"],
                      "medium": ["medium", "large", "fast"],
                      "large": ["large", "medium", "fast"]}

        for tier in tier_order.get(preferred_tier, ["medium"]):
            for name, info in self._models.items():
                if info.tier == tier and info.available:
                    return name

        # Ultimate fallback: default model
        return self.default_model

    def call(self, task: str, prompt: str, timeout_s: float | None = None) -> str:
        """Route a task to the best model and execute."""
        model = self.get_model_for_task(task)
        timeout = timeout_s or settings.local_llm_timeout_s

        from sts_monitor.llm import LocalLLMClient
        client = LocalLLMClient(
            base_url=self.base_url,
            model=model,
            timeout_s=timeout,
        )

        t0 = time.time()
        result = client.summarize(prompt)
        latency = (time.time() - t0) * 1000

        # Update model latency stats
        if model in self._models:
            info = self._models[model]
            if info.avg_latency_ms == 0:
                info.avg_latency_ms = latency
            else:
                info.avg_latency_ms = info.avg_latency_ms * 0.7 + latency * 0.3

        return result

    def get_status(self) -> dict[str, Any]:
        """Get current router status and model availability."""
        if time.time() - self._last_scan > self._scan_interval:
            self.scan_models()

        models = list(self._models.values())
        tiers = {"fast": [], "medium": [], "large": []}
        for m in models:
            tiers[m.tier].append(m.name)

        return {
            "base_url": self.base_url,
            "default_model": self.default_model,
            "models_available": len(models),
            "models": [m.to_dict() for m in models],
            "tiers": tiers,
            "task_routing": {
                task: self.get_model_for_task(task)
                for task in TASK_TIERS
            },
        }


# Singleton router
_router: MultiLLMRouter | None = None


def get_router() -> MultiLLMRouter:
    global _router
    if _router is None:
        _router = MultiLLMRouter()
    return _router
