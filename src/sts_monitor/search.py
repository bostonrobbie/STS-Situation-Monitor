from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re

TOKEN_RE = re.compile(r"[a-z0-9_\-]{3,}")
QUOTED_RE = re.compile(r'"([^"]+)"')


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


@dataclass(slots=True)
class QueryPlan:
    include_terms: set[str]
    include_phrases: set[str]
    exclude_terms: set[str]


DEFAULT_SYNONYMS: dict[str, set[str]] = {
    "power": {"grid", "electricity", "outage", "blackout"},
    "airport": {"runway", "aviation", "flight"},
    "earthquake": {"quake", "seismic", "tremor"},
    "flood": {"flooding", "inundation"},
}


def build_query_plan(query: str, extra_synonyms: dict[str, list[str]] | None = None) -> QueryPlan:
    raw = _norm(query)
    phrases = {_norm(match) for match in QUOTED_RE.findall(raw) if match.strip()}

    without_quotes = QUOTED_RE.sub(" ", raw)
    include_terms: set[str] = set()
    exclude_terms: set[str] = set()

    for token in without_quotes.split():
        if token.startswith("-") and len(token) > 1:
            cleaned = token[1:].strip()
            if cleaned:
                exclude_terms.add(cleaned)
            continue
        include_terms.add(token.strip())

    synonyms: dict[str, set[str]] = {k: set(v) for k, v in DEFAULT_SYNONYMS.items()}
    for key, vals in (extra_synonyms or {}).items():
        synonyms.setdefault(_norm(key), set()).update({_norm(v) for v in vals if v.strip()})

    expanded: set[str] = set(include_terms)
    for term in list(include_terms):
        expanded.update(synonyms.get(term, set()))

    return QueryPlan(include_terms=expanded, include_phrases=phrases, exclude_terms=exclude_terms)


def score_text(*, text: str, plan: QueryPlan, base_reliability: float) -> float:
    content = _norm(text)
    tokens = set(TOKEN_RE.findall(content))

    if plan.exclude_terms:
        if plan.exclude_terms & tokens:
            return 0.0

    term_hits = len(plan.include_terms.intersection(tokens))
    phrase_hits = sum(1 for phrase in plan.include_phrases if phrase in content)

    if term_hits == 0 and phrase_hits == 0:
        return 0.0

    term_score = min(1.0, term_hits / max(1, len(plan.include_terms)))
    if plan.include_phrases:
        phrase_score = min(1.0, phrase_hits / max(1, len(plan.include_phrases)))
        combined = (0.65 * term_score) + (0.35 * phrase_score)
    else:
        combined = term_score
    reliability = max(0.0, min(1.0, base_reliability))
    return round((0.75 * combined) + (0.25 * reliability), 4)


def apply_context_boosts(*, score: float, captured_at: datetime, source_trust: float) -> float:
    ts = normalize_datetime(captured_at)
    hours_old = max(0.0, (datetime.now(UTC) - ts).total_seconds() / 3600)
    freshness_boost = 0.08 if hours_old <= 24 else 0.03 if hours_old <= 72 else 0.0
    trust_boost = max(0.0, min(0.12, (source_trust - 0.5) * 0.24))
    return round(max(0.0, min(1.0, score + freshness_boost + trust_boost)), 4)


def top_terms(text: str, *, min_len: int = 4, max_terms: int = 8) -> list[str]:
    tokens = [tok for tok in TOKEN_RE.findall(_norm(text)) if len(tok) >= min_len]
    seen: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.append(token)
        if len(seen) >= max_terms:
            break
    return seen


def normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
