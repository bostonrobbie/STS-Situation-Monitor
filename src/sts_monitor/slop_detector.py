"""Slop, propaganda, and engagement-bait detection filter.

Classifies observations as credible signal vs garbage using multiple heuristics:
  - Engagement bait patterns (ALL CAPS, excessive punctuation, clickbait phrases)
  - Propaganda markers (state media talking points, coordinated messaging patterns)
  - Bot/coordination signals (identical text from multiple accounts, timing patterns)
  - Political rage-bait (partisan framing, emotional manipulation, tribal signaling)
  - Source credibility cross-check (known disinfo outlets, content farms)
  - Factual density (real intel has specifics: names, places, dates, numbers)

Each observation gets a SlopScore with per-factor breakdown so you can see
exactly WHY something was flagged.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True)
class SlopScore:
    """Quality assessment for a single observation."""
    observation_id: int | str
    source: str
    claim_preview: str  # first 200 chars
    slop_score: float  # 0.0 = clean, 1.0 = pure slop
    credibility_score: float  # 1.0 - slop_score (convenience)
    verdict: str  # "credible", "suspicious", "slop", "propaganda"
    flags: list[str]  # human-readable reasons
    factor_scores: dict[str, float]  # factor -> 0.0-1.0 penalty
    recommended_action: str  # "accept", "downweight", "flag", "drop"


@dataclass(slots=True)
class SlopFilterResult:
    """Result of filtering a batch of observations."""
    total: int
    credible: int
    suspicious: int
    slop: int
    propaganda: int
    dropped_count: int
    scores: list[SlopScore]
    pattern_stats: dict[str, int]  # which patterns triggered most


# ── Pattern definitions ────────────────────────────────────────────────

# Engagement bait: designed to provoke clicks/shares, not inform
_CLICKBAIT_PHRASES: list[re.Pattern] = [
    re.compile(p, re.I) for p in [
        r"you won'?t believe",
        r"what happens next",
        r"the truth about",
        r"they don'?t want you to know",
        r"exposed!?\b",
        r"shocking!?\b",
        r"bombshell!?\b",
        r"breaking:?\s*this is (?:huge|big|massive)",
        r"this changes everything",
        r"share (?:this|before|if you)",
        r"wake up\b.*sheeple",
        r"mainstream media (?:won'?t|doesn'?t|refuses to)",
        r"what (?:they|msm|media) (?:aren'?t|isn'?t) telling you",
        r"exposed:?\s",
        r"must (?:watch|read|see|share)",
        r"\bviral\b",
        r"like and (?:share|subscribe|retweet)",
    ]
]

# Political rage-bait: tribal signaling designed to provoke, not inform
_RAGEBAIT_PHRASES: list[re.Pattern] = [
    re.compile(p, re.I) for p in [
        r"\b(?:libtard|conservatard|snowflake|cuck|sheeple|woke mob|maga cult)\b",
        r"\b(?:owned|destroyed|obliterated|demolished|annihilated)\b.*\b(?:liberal|conservative|democrat|republican|left|right)\b",
        r"(?:liberal|conservative|democrat|republican)s?\s+(?:are|is)\s+(?:destroying|ruining|killing)",
        r"this is (?:why|how) (?:the left|the right|liberals|conservatives|democrats|republicans) (?:are|will)",
        r"\b(?:trump derangement|biden derangement)\b",
        r"(?:deep state|globalist|elitist)s?\s+(?:agenda|plan|plot|scheme)",
    ]
]

# Propaganda markers: state media patterns and coordinated messaging
_PROPAGANDA_PHRASES: list[re.Pattern] = [
    re.compile(p, re.I) for p in [
        r"special military operation",  # Russian state euphemism
        r"denazification",
        r"provocation by (?:the west|nato|ukraine)",
        r"(?:western|american) aggression",
        r"(?:western|american|nato) puppet",
        r"internal affairs of (?:china|russia)",
        r"hurting the feelings of.*chinese people",
        r"separatist[s]?\s+(?:province|region)",  # re: Taiwan
        r"so-?called (?:genocide|massacre|invasion|occupation)",
        r"(?:zionist|jewish) (?:conspiracy|plot|regime|entity)",
        r"anti-(?:russia|china|iran) propaganda",
        r"color revolution",
        r"(?:foreign|outside) interference",
    ]
]

# Known disinfo / content farm domains
_DISINFO_DOMAINS: set[str] = {
    "rt.com", "sputniknews.com", "tass.com",
    "globalresearch.ca", "mintpressnews.com", "strategic-culture.org",
    "southfront.org", "theduran.com", "zerohedge.com",
    "infowars.com", "naturalnews.com", "beforeitsnews.com",
    "thegatewaypundit.com", "100percentfedup.com",
    "occupydemocrats.com", "palmerreport.com", "bipartisanreport.com",
    "yournewswire.com", "neonnettle.com", "newspunch.com",
}

_CONTENT_FARM_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.I) for p in [
        r"\.blogspot\.",
        r"wordpress\.com/\d{4}/\d{2}",
        r"medium\.com/@[a-z0-9]{20,}",  # Random-looking Medium handles
        r"substack\.com",  # Not always bad, but flag for review
    ]
]


# ── Analysis functions ─────────────────────────────────────────────────

def _extract_domain(url: str) -> str:
    """Extract domain from URL."""
    url = url.lower().strip()
    if "://" in url:
        url = url.split("://", 1)[1]
    url = url.split("/", 1)[0].split("?", 1)[0]
    if url.startswith("www."):
        url = url[4:]
    return url


def _score_engagement_bait(text: str) -> tuple[float, list[str]]:
    """Score text for engagement-bait patterns. Returns (score, flags)."""
    flags: list[str] = []
    score = 0.0

    # ALL CAPS ratio
    words = text.split()
    if words:
        caps_words = sum(1 for w in words if w.isupper() and len(w) > 2)
        caps_ratio = caps_words / len(words)
        if caps_ratio > 0.5:
            score += 0.4
            flags.append(f"excessive_caps ({caps_ratio:.0%} words all-caps)")
        elif caps_ratio > 0.25:
            score += 0.2
            flags.append(f"high_caps ({caps_ratio:.0%} words all-caps)")

    # Excessive punctuation (!!!, ???, !!??)
    exclaim_count = text.count("!")
    question_count = text.count("?")
    if exclaim_count >= 3:
        score += 0.3
        flags.append(f"excessive_exclamation ({exclaim_count} excl. marks)")
    if question_count >= 3:
        score += 0.1
        flags.append(f"excessive_questions ({question_count} q marks)")

    # Clickbait phrases
    for pattern in _CLICKBAIT_PHRASES:
        if pattern.search(text):
            score += 0.25
            flags.append(f"clickbait: '{pattern.pattern[:50]}'")

    return min(1.0, score), flags


def _score_ragebait(text: str) -> tuple[float, list[str]]:
    """Score text for political rage-bait. Returns (score, flags)."""
    flags: list[str] = []
    score = 0.0

    for pattern in _RAGEBAIT_PHRASES:
        if pattern.search(text):
            score += 0.35
            flags.append(f"ragebait: '{pattern.pattern[:50]}'")

    return min(1.0, score), flags


def _score_propaganda(text: str) -> tuple[float, list[str]]:
    """Score text for propaganda markers. Returns (score, flags)."""
    flags: list[str] = []
    score = 0.0

    for pattern in _PROPAGANDA_PHRASES:
        if pattern.search(text):
            score += 0.4
            flags.append(f"propaganda: '{pattern.pattern[:50]}'")

    return min(1.0, score), flags


def _score_source_credibility(source: str, url: str) -> tuple[float, list[str]]:
    """Score source credibility. Returns (penalty_score, flags)."""
    flags: list[str] = []
    score = 0.0
    domain = _extract_domain(url)

    if domain in _DISINFO_DOMAINS:
        score += 0.6
        flags.append(f"known_disinfo_outlet: {domain}")

    for pattern in _CONTENT_FARM_PATTERNS:
        if pattern.search(url):
            score += 0.2
            flags.append(f"content_farm_pattern: {domain}")
            break

    # No URL at all is suspicious
    if not url or url == "":
        score += 0.15
        flags.append("no_url_provided")

    return min(1.0, score), flags


def _score_factual_density(text: str) -> tuple[float, list[str]]:
    """Score how factually dense text is. Low density = likely slop.

    Real intelligence has specifics: names, places, dates, numbers, coordinates.
    Slop has vague emotional language with no verifiable details.

    Returns (penalty_score, flags) where higher = less factual = more slop.
    """
    flags: list[str] = []

    # Count factual indicators
    indicators = 0

    # Numbers (dates, quantities, coordinates)
    numbers = re.findall(r"\b\d+(?:[.,]\d+)?\b", text)
    indicators += min(3, len(numbers))

    # Proper nouns (capitalized words not at sentence start)
    proper_nouns = re.findall(r"(?<=[.!?]\s)[A-Z][a-z]+|(?<=\s)[A-Z][a-z]{2,}", text)
    indicators += min(3, len(proper_nouns))

    # Specific locations
    location_words = re.findall(
        r"\b(?:km|miles?|kilometers?|province|district|city|region|airport|port|border|coast)\b",
        text, re.I,
    )
    indicators += min(2, len(location_words))

    # Temporal specificity
    time_words = re.findall(
        r"\b(?:\d{1,2}:\d{2}|(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)day|"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2})\b",
        text, re.I,
    )
    indicators += min(2, len(time_words))

    # Score: text length matters — longer text should have more indicators
    word_count = len(text.split())
    expected = max(1, word_count / 20)  # Expect ~1 indicator per 20 words
    density = indicators / expected

    if density < 0.3:
        score = 0.3
        flags.append("very_low_factual_density")
    elif density < 0.6:
        score = 0.15
        flags.append("low_factual_density")
    else:
        score = 0.0

    return score, flags


def _score_bot_patterns(text: str, source: str) -> tuple[float, list[str]]:
    """Score text for bot/automated posting patterns. Returns (score, flags)."""
    flags: list[str] = []
    score = 0.0

    # Very short text with no substance
    if len(text.split()) < 5:
        score += 0.15
        flags.append("very_short_text")

    # Hashtag spam
    hashtag_count = len(re.findall(r"#\w+", text))
    if hashtag_count > 5:
        score += 0.3
        flags.append(f"hashtag_spam ({hashtag_count} hashtags)")
    elif hashtag_count > 3:
        score += 0.1
        flags.append(f"high_hashtag_count ({hashtag_count})")

    # URL-only posts (just a link with minimal text)
    urls = re.findall(r"https?://\S+", text)
    text_without_urls = re.sub(r"https?://\S+", "", text).strip()
    if urls and len(text_without_urls.split()) < 3:
        score += 0.2
        flags.append("url_only_post")

    # Emoji spam
    emoji_pattern = re.compile(
        "[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U0001F900-\U0001F9FF"
        "\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002600-\U000026FF]+",
    )
    emoji_count = sum(len(m) for m in emoji_pattern.findall(text))
    if emoji_count > 5:
        score += 0.2
        flags.append(f"emoji_spam ({emoji_count} emoji)")

    return min(1.0, score), flags


def _detect_coordination(
    observations: list[dict[str, Any]],
    time_window_seconds: int = 60,
    text_match_threshold: float = 0.9,
) -> dict[int, tuple[float, list[str]]]:
    """Detect coordinated posting: near-identical text from different sources
    posted within seconds of each other.

    Returns dict of observation index -> (penalty, flags).
    """
    penalties: dict[int, tuple[float, list[str]]] = {}
    n = len(observations)

    for i in range(n):
        claim_i = observations[i].get("claim", "").lower().strip()
        time_i = observations[i].get("captured_at", datetime.now(UTC))
        source_i = observations[i].get("source", "")

        if isinstance(time_i, str):
            try:
                time_i = datetime.fromisoformat(time_i)
            except (ValueError, TypeError):
                continue

        for j in range(i + 1, n):
            claim_j = observations[j].get("claim", "").lower().strip()
            time_j = observations[j].get("captured_at", datetime.now(UTC))
            source_j = observations[j].get("source", "")

            if isinstance(time_j, str):
                try:
                    time_j = datetime.fromisoformat(time_j)
                except (ValueError, TypeError):
                    continue

            # Different sources posting same text within seconds
            if source_i == source_j:
                continue

            time_diff = abs((time_i - time_j).total_seconds())
            if time_diff > time_window_seconds:
                continue

            # Check text similarity (simple exact or near-exact)
            if claim_i == claim_j or (
                len(claim_i) > 20
                and len(claim_j) > 20
                and claim_i[:50] == claim_j[:50]
            ):
                flag = f"coordinated_posting: identical text from {source_i} and {source_j} within {time_diff:.0f}s"
                for idx in (i, j):
                    existing = penalties.get(idx, (0.0, []))
                    penalties[idx] = (
                        min(1.0, existing[0] + 0.4),
                        existing[1] + [flag],
                    )

    return penalties


# ── Main scoring function ──────────────────────────────────────────────

_FACTOR_WEIGHTS: dict[str, float] = {
    "engagement_bait": 0.25,
    "ragebait": 0.20,
    "propaganda": 0.25,
    "source_credibility": 0.15,
    "factual_density": 0.10,
    "bot_patterns": 0.05,
}


def score_observation(
    obs: dict[str, Any],
    coordination_penalty: float = 0.0,
    coordination_flags: list[str] | None = None,
) -> SlopScore:
    """Score a single observation for slop/propaganda/bait."""
    text = obs.get("claim", "")
    source = obs.get("source", "")
    url = obs.get("url", "")
    obs_id = obs.get("id", obs.get("observation_id", 0))

    all_flags: list[str] = []
    factor_scores: dict[str, float] = {}

    # Run each detector
    bait_score, bait_flags = _score_engagement_bait(text)
    factor_scores["engagement_bait"] = bait_score
    all_flags.extend(bait_flags)

    rage_score, rage_flags = _score_ragebait(text)
    factor_scores["ragebait"] = rage_score
    all_flags.extend(rage_flags)

    prop_score, prop_flags = _score_propaganda(text)
    factor_scores["propaganda"] = prop_score
    all_flags.extend(prop_flags)

    src_score, src_flags = _score_source_credibility(source, url)
    factor_scores["source_credibility"] = src_score
    all_flags.extend(src_flags)

    factual_score, factual_flags = _score_factual_density(text)
    factor_scores["factual_density"] = factual_score
    all_flags.extend(factual_flags)

    bot_score, bot_flags = _score_bot_patterns(text, source)
    factor_scores["bot_patterns"] = bot_score
    all_flags.extend(bot_flags)

    # Coordination penalty (computed externally across the batch)
    if coordination_penalty > 0:
        factor_scores["coordination"] = coordination_penalty
        all_flags.extend(coordination_flags or [])

    # Weighted total
    slop_total = sum(
        factor_scores.get(k, 0.0) * w
        for k, w in _FACTOR_WEIGHTS.items()
    )
    # Add coordination penalty directly (not weighted)
    slop_total += factor_scores.get("coordination", 0.0) * 0.15
    slop_total = min(1.0, slop_total)

    # Verdict
    if prop_score >= 0.4:
        verdict = "propaganda"
    elif slop_total >= 0.5:
        verdict = "slop"
    elif slop_total >= 0.25:
        verdict = "suspicious"
    else:
        verdict = "credible"

    # Action recommendation
    if slop_total >= 0.6:
        action = "drop"
    elif slop_total >= 0.4:
        action = "flag"
    elif slop_total >= 0.2:
        action = "downweight"
    else:
        action = "accept"

    return SlopScore(
        observation_id=obs_id,
        source=source,
        claim_preview=text[:200],
        slop_score=round(slop_total, 3),
        credibility_score=round(1.0 - slop_total, 3),
        verdict=verdict,
        flags=all_flags,
        factor_scores=factor_scores,
        recommended_action=action,
    )


# ── Batch filtering ───────────────────────────────────────────────────

def filter_slop(
    observations: list[dict[str, Any]],
    drop_threshold: float = 0.6,
    flag_threshold: float = 0.4,
) -> SlopFilterResult:
    """Filter a batch of observations, detecting slop/propaganda/bait.

    Returns scored observations with verdicts and recommendations.
    """
    # First pass: detect coordinated posting
    coord_penalties = _detect_coordination(observations)

    scores: list[SlopScore] = []
    pattern_stats: Counter = Counter()

    for i, obs in enumerate(observations):
        coord_penalty, coord_flags = coord_penalties.get(i, (0.0, []))
        score = score_observation(obs, coord_penalty, coord_flags)
        scores.append(score)

        # Track which patterns fire most
        for flag in score.flags:
            category = flag.split(":")[0].split("(")[0].strip()
            pattern_stats[category] += 1

    credible = sum(1 for s in scores if s.verdict == "credible")
    suspicious = sum(1 for s in scores if s.verdict == "suspicious")
    slop = sum(1 for s in scores if s.verdict == "slop")
    propaganda = sum(1 for s in scores if s.verdict == "propaganda")
    dropped = sum(1 for s in scores if s.slop_score >= drop_threshold)

    return SlopFilterResult(
        total=len(scores),
        credible=credible,
        suspicious=suspicious,
        slop=slop,
        propaganda=propaganda,
        dropped_count=dropped,
        scores=scores,
        pattern_stats=dict(pattern_stats.most_common(20)),
    )
