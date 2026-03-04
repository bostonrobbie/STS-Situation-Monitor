from __future__ import annotations

from dataclasses import dataclass

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
