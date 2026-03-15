"""
LLM Enrichment Engine — uses Ollama (qwen2.5:14b) to enrich raw intelligence data.
All calls are async with a 30-second timeout.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

import httpx

log = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:14b"
OLLAMA_TIMEOUT = 30.0

# In-memory cache keyed by hash of text[:200]
_entity_cache: dict[str, dict] = {}


def _cache_key(text: str) -> str:
    return hashlib.md5(text[:200].encode()).hexdigest()


def _call_ollama_sync(prompt: str, timeout: float = OLLAMA_TIMEOUT) -> str:
    """Synchronous Ollama call. Returns the response text or empty string on failure."""
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                OLLAMA_URL,
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            )
            resp.raise_for_status()
            return resp.json().get("response", "")
    except Exception as exc:
        log.warning(f"Ollama call failed: {exc}")
        return ""


async def _call_ollama_async(prompt: str, timeout: float = OLLAMA_TIMEOUT) -> str:
    """Async Ollama call. Returns the response text or empty string on failure."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                OLLAMA_URL,
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            )
            resp.raise_for_status()
            return resp.json().get("response", "")
    except Exception as exc:
        log.warning(f"Ollama async call failed: {exc}")
        return ""


def _extract_json(text: str) -> dict | list | None:
    """Try to extract a JSON object or array from model output."""
    # Try direct parse first
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except Exception:
        pass
    # Try to find JSON block
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*```", stripped)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass
    # Try bare JSON object/array
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", stripped)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass
    return None


def extract_entities(text: str) -> dict:
    """
    Extract people, organizations, locations, events from text.
    Returns: {"people": [], "organizations": [], "locations": [], "event_type": "",
               "significance": 0-10, "summary": "", "language": "en"}
    Results are cached by hash of first 200 chars.
    """
    cache_key = _cache_key(text)
    if cache_key in _entity_cache:
        return _entity_cache[cache_key]

    default = {
        "people": [], "organizations": [], "locations": [],
        "event_type": "unknown", "significance": 5, "summary": text[:200], "language": "en"
    }

    prompt = f"""Extract intelligence entities from the following text. Respond with ONLY valid JSON.

Text: {text[:1500]}

Return JSON with this exact structure:
{{
  "people": ["name1", "name2"],
  "organizations": ["org1", "org2"],
  "locations": ["city/country1", "city/country2"],
  "event_type": "one of: conflict/disaster/political/cyber/health/military/economic/other",
  "significance": <integer 0-10 based on global impact>,
  "summary": "<one sentence summary>",
  "language": "<ISO 639-1 code, e.g. en>"
}}

JSON:"""

    raw = _call_ollama_sync(prompt)
    if not raw:
        _entity_cache[cache_key] = default
        return default

    parsed = _extract_json(raw)
    if isinstance(parsed, dict):
        result = {
            "people": parsed.get("people", []),
            "organizations": parsed.get("organizations", []),
            "locations": parsed.get("locations", []),
            "event_type": parsed.get("event_type", "unknown"),
            "significance": int(parsed.get("significance", 5)),
            "summary": parsed.get("summary", text[:200]),
            "language": parsed.get("language", "en"),
        }
        _entity_cache[cache_key] = result
        return result

    _entity_cache[cache_key] = default
    return default


def translate_to_english(text: str, source_lang: str) -> str:
    """Translate text from source_lang to English using Ollama."""
    if source_lang.lower() in ("en", "english"):
        return text

    prompt = f"""Translate the following {source_lang} text to English. Respond with ONLY the translated text, no explanations.

Text: {text[:2000]}

English translation:"""

    result = _call_ollama_sync(prompt)
    return result.strip() if result.strip() else text


def score_significance(title: str, text: str, layer: str) -> float:
    """Score significance of an event 0-10 using Ollama."""
    combined = f"Title: {title}\nLayer: {layer}\nContent: {text[:800]}"
    prompt = f"""Rate the global intelligence significance of this event on a scale of 0-10.
Consider: casualties, political impact, economic impact, geographic scope, escalation potential.

{combined}

Respond with ONLY a single number between 0 and 10 (e.g. 7.5):"""

    raw = _call_ollama_sync(prompt)
    if raw:
        match = re.search(r"\b(\d+(?:\.\d+)?)\b", raw.strip())
        if match:
            val = float(match.group(1))
            return min(10.0, max(0.0, val))
    return 5.0


def generate_daily_briefing(events: list[dict]) -> str:
    """
    Generate a 500-word intelligence briefing from top events.
    Returns markdown-formatted text.
    """
    if not events:
        return "## Intelligence Briefing\n\nNo significant events in the reporting period."

    # Build event summary for prompt
    top_events = events[:50]
    event_lines = []
    for i, ev in enumerate(top_events, 1):
        layer = ev.get("layer", "unknown")
        title = ev.get("title", "Untitled")[:200]
        mag = ev.get("magnitude", 0)
        props = ev.get("properties", {})
        loc = props.get("location_name", "") or props.get("country", "") or ""
        event_lines.append(f"{i}. [{layer.upper()}] {title} (sig={mag}) {loc}")

    events_text = "\n".join(event_lines)
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    prompt = f"""You are an intelligence analyst. Generate a concise daily intelligence briefing for {now} based on the following events. Write approximately 500 words in markdown format.

Structure the briefing with:
## Daily Intelligence Briefing — {now}
### Executive Summary (2-3 sentences)
### Key Developments by Domain
#### Conflict & Military
#### Political & Diplomatic
#### Natural Disasters & Health
#### Cyber & Technology
### Threat Assessment
### Analyst Notes

Events to analyze:
{events_text}

Write the briefing now:"""

    result = _call_ollama_sync(prompt, timeout=60.0)
    if result and len(result) > 100:
        return result

    # Fallback: simple markdown briefing
    lines = [f"## Daily Intelligence Briefing — {now}", "", "### Key Events", ""]
    for ev in top_events[:20]:
        layer = ev.get("layer", "unknown")
        title = ev.get("title", "Untitled")[:150]
        mag = ev.get("magnitude", 0)
        lines.append(f"- **[{layer.upper()}]** {title} *(significance: {mag})*")
    return "\n".join(lines)


_ZONE_CACHE: dict[str, tuple[str, float]] = {}  # zone -> (report, timestamp)


def generate_zone_report(zone_name: str, recent_events: list[dict]) -> str:
    """Generate a focused intelligence SITREP for a specific geographic zone.
    Results are cached for 1 hour.
    """
    import time
    cached = _ZONE_CACHE.get(zone_name)
    if cached and time.time() - cached[1] < 3600:
        return cached[0]

    events_text = "\n".join([
        f"- [{e.get('layer', '')}] {e.get('title', '')} (significance: {e.get('magnitude', 0):.1f})"
        for e in recent_events[:30]
    ]) or "- No recent signals available."

    prompt = f"""You are an intelligence analyst. Generate a concise 3-paragraph situation report for {zone_name}.

Recent intelligence signals:
{events_text}

Write a SITREP covering:
1. Current situation overview
2. Key threat indicators and developments
3. Outlook and recommended watch items

Format in clean markdown. Be specific, factual, cite the signals provided."""

    result = _call_ollama_sync(prompt, timeout=60.0)
    if not result or len(result) < 50:
        result = f"## {zone_name.upper()} SITREP\n\nInsufficient data or LLM unavailable. {len(recent_events)} signals reviewed."

    _ZONE_CACHE[zone_name] = (result, time.time())
    return result


def correlate_events(event_a: dict, event_b: dict) -> float:
    """
    Returns a similarity/correlation score 0-1 between two events.
    Uses lightweight heuristics + optional LLM scoring.
    """
    # Quick heuristic first to avoid LLM call for obviously unrelated events
    title_a = (event_a.get("title") or "").lower()
    title_b = (event_b.get("title") or "").lower()
    layer_a = event_a.get("layer", "")
    layer_b = event_b.get("layer", "")

    # Same layer = base 0.3
    base = 0.3 if layer_a == layer_b else 0.0

    # Shared words (excluding stop words)
    stop = {"the", "a", "an", "in", "of", "to", "is", "are", "and", "or", "for", "on", "at", "by"}
    words_a = set(title_a.split()) - stop
    words_b = set(title_b.split()) - stop
    if words_a and words_b:
        overlap = len(words_a & words_b) / max(len(words_a | words_b), 1)
        base += overlap * 0.7

    return min(1.0, base)
