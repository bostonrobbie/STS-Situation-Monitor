from __future__ import annotations

from dataclasses import dataclass
import time

import httpx


@dataclass(slots=True)
class LLMHealth:
    reachable: bool
    model_available: bool
    detail: str


class LocalLLMClient:
    def __init__(self, base_url: str, model: str, timeout_s: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s

    def health(self) -> LLMHealth:
    latency_ms: float | None = None


class LocalLLMClient:
    def __init__(self, base_url: str, model: str, timeout_s: float = 10.0, max_retries: int = 2) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.max_retries = max_retries

    def health(self) -> LLMHealth:
        started = time.perf_counter()
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=self.timeout_s)
            response.raise_for_status()
            payload = response.json()
            models = payload.get("models", [])
            names = {item.get("name", "") for item in models}
            model_available = any(name.startswith(self.model) for name in names)
            detail = "ok" if model_available else f"model '{self.model}' not found"
            return LLMHealth(reachable=True, model_available=model_available, detail=detail)
        except Exception as exc:  # network/runtime defensive check
            return LLMHealth(reachable=False, model_available=False, detail=str(exc))
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            return LLMHealth(reachable=True, model_available=model_available, detail=detail, latency_ms=latency_ms)
        except Exception as exc:  # network/runtime defensive check
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            return LLMHealth(reachable=False, model_available=False, detail=str(exc), latency_ms=latency_ms)

    def summarize(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2},
        }
        response = httpx.post(f"{self.base_url}/api/generate", json=payload, timeout=self.timeout_s)
        response.raise_for_status()
        data = response.json()
        return str(data.get("response", "")).strip()
        last_error: str | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = httpx.post(f"{self.base_url}/api/generate", json=payload, timeout=self.timeout_s)
                response.raise_for_status()
                data = response.json()
                return str(data.get("response", "")).strip()
            except Exception as exc:
                last_error = str(exc)
                if attempt < self.max_retries:
                    time.sleep(0.25 * (attempt + 1))
        raise RuntimeError(last_error or "unknown llm error")
