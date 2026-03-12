"""Cross-source corroboration engine.

Scores claims based on how many *independent* sources report the same thing.
A single-source claim with no corroboration is weak. Four independent source
families reporting the same event is strong signal.

Scoring factors:
  - Number of independent source families confirming a claim
  - Source diversity (different connector types: rss vs gdelt vs acled vs nitter)
  - Temporal spread (rapid independent reporting = higher confidence)
  - Geographic consistency (sources from different vantage points)
  - Source tier (tier-1 wire services vs social media vs unknown blogs)

Output: CorroborationScore per claim cluster, with breakdown of WHY.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass(slots=True)
class CorroborationEvidence:
    """One piece of evidence supporting or contradicting a claim."""
    source: str
    source_family: str
    source_tier: str  # tier1, tier2, tier3, social, unknown
    connector_type: str  # rss, gdelt, acled, nitter, usgs, etc.
    claim_text: str
    url: str
    captured_at: datetime
    reliability_hint: float


@dataclass(slots=True)
class CorroborationScore:
    """Score for how well-corroborated a claim cluster is."""
    claim_summary: str
    score: float  # 0.0-1.0 overall corroboration
    independent_sources: int
    source_families: list[str]
    connector_types: list[str]
    source_tiers: dict[str, int]  # tier -> count
    temporal_spread_hours: float
    first_reported_by: str
    first_reported_at: datetime
    evidence: list[CorroborationEvidence]
    breakdown: dict[str, float]  # factor -> contribution to score
    verdict: str  # "well_corroborated", "partially_corroborated", "single_source", "contested"


@dataclass(slots=True)
class CorroborationResult:
    """Result of corroboration analysis across all claims."""
    total_claims: int
    well_corroborated: int
    partially_corroborated: int
    single_source: int
    contested: int
    scores: list[CorroborationScore]
    overall_corroboration_rate: float


# ── Source tier classification ─────────────────────────────────────────

# Tier-1: wire services and major international outlets with editorial standards
_TIER1_DOMAINS: set[str] = {
    "reuters.com", "apnews.com", "afp.com", "bbc.com", "bbc.co.uk",
    "aljazeera.com", "france24.com", "dw.com", "nhk.or.jp",
    "theguardian.com", "nytimes.com", "washingtonpost.com",
    "ft.com", "economist.com", "wsj.com",
}

# Tier-2: major national outlets and reputable regional sources
_TIER2_DOMAINS: set[str] = {
    "cnn.com", "foxnews.com", "nbcnews.com", "abcnews.go.com", "cbsnews.com",
    "sky.com", "telegraph.co.uk", "independent.co.uk", "times.co.uk",
    "spiegel.de", "lemonde.fr", "elpais.com", "corriere.it",
    "haaretz.com", "timesofisrael.com", "jpost.com",
    "scmp.com", "straitstimes.com", "japantimes.co.jp",
    "thehindu.com", "ndtv.com", "dawn.com",
    "abc.net.au", "cbc.ca", "rnz.co.nz",
}

# Tier-3: known state media and outlets with editorial concerns
_TIER3_DOMAINS: set[str] = {
    "rt.com", "sputniknews.com", "tass.com",
    "xinhua.net", "globaltimes.cn", "cgtn.com",
    "presstv.ir", "almayadeen.net",
    "infowars.com", "breitbart.com", "dailycaller.com",
    "zerohedge.com", "thegatewaypundit.com",
    "occupydemocrats.com", "dailykos.com",
}

# Government/authoritative data sources (treated as tier-1 for their domain)
_AUTHORITATIVE_CONNECTORS: set[str] = {
    "usgs", "nasa_firms", "nws", "fema", "reliefweb", "acled",
}

_SOCIAL_CONNECTORS: set[str] = {
    "nitter", "reddit",
}


def classify_source_tier(source: str, url: str, connector_type: str = "") -> str:
    """Classify a source into reliability tiers."""
    if connector_type in _AUTHORITATIVE_CONNECTORS:
        return "tier1"
    if connector_type in _SOCIAL_CONNECTORS:
        return "social"

    # Extract domain from URL
    domain = _extract_domain(url)

    if domain in _TIER1_DOMAINS:
        return "tier1"
    if domain in _TIER2_DOMAINS:
        return "tier2"
    if domain in _TIER3_DOMAINS:
        return "tier3"

    # Check source string for known patterns
    source_lower = source.lower()
    if any(d.split(".")[0] in source_lower for d in _TIER1_DOMAINS):
        return "tier1"
    if any(d.split(".")[0] in source_lower for d in _TIER2_DOMAINS):
        return "tier2"

    return "unknown"


def _extract_domain(url: str) -> str:
    """Extract domain from URL."""
    url = url.lower().strip()
    if "://" in url:
        url = url.split("://", 1)[1]
    url = url.split("/", 1)[0]
    url = url.split("?", 1)[0]
    # Strip www.
    if url.startswith("www."):
        url = url[4:]
    return url


def _extract_connector_type(source: str) -> str:
    """Extract connector type from source string like 'rss:bbc.com' or 'gdelt'."""
    return source.split(":", 1)[0].strip().lower()


def _source_family(source: str) -> str:
    """Extract source family for independence check."""
    parts = source.split(":", 1)
    if len(parts) > 1:
        domain = parts[1].split("/")[0].strip().lower()
        # Strip www
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    return source.strip().lower()


# ── Claim normalization for matching ───────────────────────────────────

_NOISE_WORDS = re.compile(
    r"\b(breaking|update|just\s+in|developing|confirmed|unconfirmed|"
    r"reports?\s+say|sources?\s+say|according\s+to|exclusive|alert)\b",
    re.I,
)


def normalize_claim(text: str) -> str:
    """Normalize claim text for matching similar claims across sources."""
    text = text.lower().strip()
    text = _NOISE_WORDS.sub("", text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = " ".join(text.split())
    return text


def claim_similarity(a: str, b: str) -> float:
    """Jaccard similarity between normalized claim terms."""
    terms_a = set(normalize_claim(a).split())
    terms_b = set(normalize_claim(b).split())
    if not terms_a or not terms_b:
        return 0.0
    intersection = terms_a & terms_b
    union = terms_a | terms_b
    return len(intersection) / len(union)


# ── Claim clustering ──────────────────────────────────────────────────

def cluster_claims(
    observations: list[dict[str, Any]],
    similarity_threshold: float = 0.25,
) -> list[list[dict[str, Any]]]:
    """Group observations into claim clusters based on textual similarity.

    Each observation dict should have: source, claim, url, captured_at, reliability_hint
    """
    if not observations:
        return []

    clusters: list[list[dict[str, Any]]] = []
    cluster_representatives: list[str] = []  # Normalized text of first item

    for obs in observations:
        norm = normalize_claim(obs.get("claim", ""))
        if not norm:
            continue

        best_cluster = -1
        best_sim = similarity_threshold

        for i, rep in enumerate(cluster_representatives):
            sim = claim_similarity(obs.get("claim", ""), clusters[i][0].get("claim", ""))
            if sim > best_sim:
                best_sim = sim
                best_cluster = i

        if best_cluster >= 0:
            clusters[best_cluster].append(obs)
        else:
            clusters.append([obs])
            cluster_representatives.append(norm)

    return clusters


# ── Corroboration scoring ──────────────────────────────────────────────

_TIER_WEIGHTS: dict[str, float] = {
    "tier1": 1.0,
    "tier2": 0.7,
    "tier3": 0.3,
    "social": 0.2,
    "unknown": 0.4,
}


def score_cluster(cluster: list[dict[str, Any]]) -> CorroborationScore:
    """Score a single claim cluster for corroboration strength."""
    evidence: list[CorroborationEvidence] = []
    families: set[str] = set()
    connectors: set[str] = set()
    tier_counts: Counter = Counter()
    timestamps: list[datetime] = []

    for obs in cluster:
        source = obs.get("source", "")
        url = obs.get("url", "")
        connector = _extract_connector_type(source)
        family = _source_family(source)
        tier = classify_source_tier(source, url, connector)
        captured = obs.get("captured_at", datetime.now(UTC))
        if isinstance(captured, str):
            try:
                captured = datetime.fromisoformat(captured)
            except (ValueError, TypeError):
                captured = datetime.now(UTC)

        ev = CorroborationEvidence(
            source=source,
            source_family=family,
            source_tier=tier,
            connector_type=connector,
            claim_text=obs.get("claim", ""),
            url=url,
            captured_at=captured,
            reliability_hint=obs.get("reliability_hint", 0.5),
        )
        evidence.append(ev)
        families.add(family)
        connectors.add(connector)
        tier_counts[tier] += 1
        timestamps.append(captured)

    # Sort evidence by time
    evidence.sort(key=lambda e: e.captured_at)
    timestamps.sort()

    # ── Factor scoring ──────────────────────────────────────────────
    breakdown: dict[str, float] = {}

    # 1. Independent source count (max 0.35)
    n_families = len(families)
    source_score = min(0.35, n_families * 0.09)
    breakdown["independent_sources"] = round(source_score, 3)

    # 2. Connector diversity (max 0.15) — different connector types = different vantage points
    n_connectors = len(connectors)
    connector_score = min(0.15, n_connectors * 0.05)
    breakdown["connector_diversity"] = round(connector_score, 3)

    # 3. Source tier quality (max 0.25) — tier-1 sources weigh more
    weighted_tier = sum(
        _TIER_WEIGHTS.get(tier, 0.3) * count
        for tier, count in tier_counts.items()
    )
    tier_score = min(0.25, weighted_tier * 0.05)
    breakdown["source_quality"] = round(tier_score, 3)

    # 4. Temporal convergence (max 0.15) — rapid independent reporting is strong
    if len(timestamps) >= 2:
        spread_hours = (timestamps[-1] - timestamps[0]).total_seconds() / 3600
        # Sweet spot: reported by multiple sources within 1-6 hours
        if 0 < spread_hours <= 6:
            temporal_score = 0.15
        elif spread_hours <= 24:
            temporal_score = 0.10
        elif spread_hours <= 72:
            temporal_score = 0.05
        else:
            temporal_score = 0.02
    else:
        spread_hours = 0.0
        temporal_score = 0.0
    breakdown["temporal_convergence"] = round(temporal_score, 3)

    # 5. Base reliability average (max 0.10)
    avg_reliability = sum(e.reliability_hint for e in evidence) / max(1, len(evidence))
    reliability_score = min(0.10, avg_reliability * 0.12)
    breakdown["avg_reliability"] = round(reliability_score, 3)

    total_score = min(1.0, sum(breakdown.values()))

    # ── Verdict ─────────────────────────────────────────────────────
    if n_families >= 3 and total_score >= 0.5:
        verdict = "well_corroborated"
    elif n_families >= 2 and total_score >= 0.3:
        verdict = "partially_corroborated"
    elif n_families == 1:
        verdict = "single_source"
    else:
        verdict = "contested"

    # Claim summary = first/best claim
    best = max(evidence, key=lambda e: e.reliability_hint) if evidence else None
    claim_summary = best.claim_text[:300] if best else ""

    first = evidence[0] if evidence else None

    return CorroborationScore(
        claim_summary=claim_summary,
        score=round(total_score, 3),
        independent_sources=n_families,
        source_families=sorted(families),
        connector_types=sorted(connectors),
        source_tiers=dict(tier_counts),
        temporal_spread_hours=round(spread_hours, 2),
        first_reported_by=first.source if first else "",
        first_reported_at=first.captured_at if first else datetime.now(UTC),
        evidence=evidence,
        breakdown=breakdown,
        verdict=verdict,
    )


# ── Main entry point ──────────────────────────────────────────────────

def analyze_corroboration(
    observations: list[dict[str, Any]],
    similarity_threshold: float = 0.25,
) -> CorroborationResult:
    """Analyze corroboration across all observations.

    Groups observations into claim clusters, then scores each cluster
    for cross-source corroboration strength.
    """
    clusters = cluster_claims(observations, similarity_threshold)

    scores: list[CorroborationScore] = []
    well = partial = single = contested = 0

    for cluster in clusters:
        cs = score_cluster(cluster)
        scores.append(cs)

        if cs.verdict == "well_corroborated":
            well += 1
        elif cs.verdict == "partially_corroborated":
            partial += 1
        elif cs.verdict == "single_source":
            single += 1
        else:
            contested += 1

    total = len(scores)
    corroboration_rate = well / max(1, total)

    # Sort by score descending
    scores.sort(key=lambda s: s.score, reverse=True)

    return CorroborationResult(
        total_claims=total,
        well_corroborated=well,
        partially_corroborated=partial,
        single_source=single,
        contested=contested,
        scores=scores,
        overall_corroboration_rate=round(corroboration_rate, 3),
    )
