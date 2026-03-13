"""Twitter/X surge intelligence engine.

When a story breaks on Twitter/X, hundreds of accounts flood the zone.
Most are noise — reposts, engagement farming, AI-generated commentary,
people who are simply wrong, or bad actors seeding disinfo.

This module finds the alpha: the rare first-hand accounts, domain experts,
and independent corroborations that actually tell you something new.

Architecture:
1. Surge detection    — identify when a topic is blowing up (volume spike)
2. Account scoring    — build/maintain credibility profiles per author
3. Alpha extraction   — surface high-value signal from the noise
4. Disinfo tagging    — flag coordinated campaigns, AI-generated content, known bad actors
5. Source decay       — accounts that are repeatedly wrong lose credibility over time

Design principle: humans naturally do this when scrolling Twitter during a
breaking event. They learn who to trust, who to ignore, and which tweets
actually contain new information. We automate that judgment loop.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


# ── Account credibility profiles ─────────────────────────────────────

@dataclass(slots=True)
class AccountProfile:
    """Credibility profile for a Twitter/X account."""
    handle: str
    display_name: str
    credibility_score: float  # 0.0-1.0; starts at 0.5
    total_observations: int
    correct_calls: int  # claims later corroborated
    incorrect_calls: int  # claims later debunked
    first_seen: datetime
    last_seen: datetime
    tags: list[str]  # domain_expert, journalist, official, bot_suspected, ai_suspected, disinfo_source
    specialties: list[str]  # topics they're accurate about
    flags: list[str]  # reasons for credibility adjustments
    decay_applied: float  # cumulative credibility decay

    @property
    def accuracy_rate(self) -> float:
        total = self.correct_calls + self.incorrect_calls
        return self.correct_calls / total if total > 0 else 0.5

    @property
    def is_trusted(self) -> bool:
        return self.credibility_score >= 0.65

    @property
    def is_discredited(self) -> bool:
        return self.credibility_score < 0.25


@dataclass(slots=True)
class SurgeTweet:
    """A single tweet/post within a surge event."""
    tweet_id: str
    author: str
    text: str
    url: str
    posted_at: datetime
    source_tag: str  # nitter:@handle or similar

    # Scored after analysis
    alpha_score: float = 0.0  # 0.0-1.0; how much new/valuable info
    credibility: float = 0.5  # author's credibility at time of scoring
    is_first_hand: bool = False
    is_expert: bool = False
    is_ai_generated: bool = False
    is_coordinated: bool = False
    novelty_score: float = 0.0  # how different from already-seen claims
    flags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SurgeEvent:
    """A detected surge on a topic — a breaking story flooding the zone."""
    topic: str
    detected_at: datetime
    tweet_count: int
    unique_authors: int
    velocity: float  # tweets per minute
    duration_minutes: float
    peak_minute: datetime
    alpha_tweets: list[SurgeTweet]  # top valuable tweets
    noise_tweets: list[SurgeTweet]  # flagged garbage
    discredited_authors: list[str]  # authors we don't trust
    timeline: list[dict[str, Any]]  # minute-by-minute breakdown
    summary: str


@dataclass(slots=True)
class SurgeAnalysisResult:
    """Full result of surge analysis on a batch of social media observations."""
    surge_detected: bool
    surges: list[SurgeEvent]
    account_profiles: dict[str, AccountProfile]
    total_processed: int
    alpha_count: int  # high-value signal tweets
    noise_count: int  # dropped/downweighted
    disinfo_count: int  # flagged coordinated/AI content
    credibility_updates: list[dict[str, Any]]  # changes to account profiles


# ── Account credibility store (in-memory, persistent across cycles) ────

class AccountCredibilityStore:
    """Maintains credibility profiles across cycles.

    This is the memory that makes the system smarter over time.
    Accounts that are repeatedly wrong decay. Accounts that provide
    accurate first-hand information build credibility.
    """

    def __init__(self) -> None:
        self._profiles: dict[str, AccountProfile] = {}

    def get_or_create(self, handle: str, display_name: str = "") -> AccountProfile:
        """Get existing profile or create a new one with neutral credibility."""
        handle_lower = handle.lower().lstrip("@")
        if handle_lower not in self._profiles:
            now = datetime.now(UTC)
            self._profiles[handle_lower] = AccountProfile(
                handle=handle_lower,
                display_name=display_name or handle_lower,
                credibility_score=0.5,  # neutral start
                total_observations=0,
                correct_calls=0,
                incorrect_calls=0,
                first_seen=now,
                last_seen=now,
                tags=[],
                specialties=[],
                flags=[],
                decay_applied=0.0,
            )
        return self._profiles[handle_lower]

    def boost(self, handle: str, amount: float = 0.05, reason: str = "") -> None:
        """Increase account credibility (corroborated claim, expert content)."""
        profile = self.get_or_create(handle)
        old = profile.credibility_score
        profile.credibility_score = min(1.0, profile.credibility_score + amount)
        profile.correct_calls += 1
        if reason:
            profile.flags.append(f"+{amount:.2f}: {reason}")

    def penalize(self, handle: str, amount: float = 0.1, reason: str = "") -> None:
        """Decrease account credibility (debunked claim, bot behavior, etc)."""
        profile = self.get_or_create(handle)
        old = profile.credibility_score
        profile.credibility_score = max(0.0, profile.credibility_score - amount)
        profile.incorrect_calls += 1
        if reason:
            profile.flags.append(f"-{amount:.2f}: {reason}")

    def tag(self, handle: str, tag: str) -> None:
        """Add a tag to an account profile."""
        profile = self.get_or_create(handle)
        if tag not in profile.tags:
            profile.tags.append(tag)

    def apply_decay(self, inactive_days: int = 30, decay_rate: float = 0.02) -> int:
        """Apply credibility decay to inactive accounts.

        Accounts that haven't been seen recently slowly drift back toward 0.5.
        This prevents stale profiles from having outsized influence.
        """
        now = datetime.now(UTC)
        decayed = 0
        for profile in self._profiles.values():
            days_inactive = (now - profile.last_seen).total_seconds() / 86400
            if days_inactive >= inactive_days:
                # Drift toward 0.5
                current = profile.credibility_score
                direction = 0.5 - current
                adjustment = min(abs(direction), decay_rate) * (1 if direction > 0 else -1)
                profile.credibility_score += adjustment
                profile.decay_applied += abs(adjustment)
                decayed += 1
        return decayed

    @property
    def profiles(self) -> dict[str, AccountProfile]:
        return dict(self._profiles)

    def get_discredited(self, threshold: float = 0.25) -> list[str]:
        """Return handles of discredited accounts."""
        return [h for h, p in self._profiles.items() if p.credibility_score < threshold]

    def get_trusted(self, threshold: float = 0.65) -> list[str]:
        """Return handles of trusted accounts."""
        return [h for h, p in self._profiles.items() if p.credibility_score >= threshold]


# Global store — persists across cycles within a process
_account_store = AccountCredibilityStore()

def get_account_store() -> AccountCredibilityStore:
    """Get the global account credibility store."""
    return _account_store


# ── AI content detection ──────────────────────────────────────────────

_AI_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.I) for p in [
        r"\bas an ai\b",
        r"\bi'm an ai\b",
        r"(?:delve|tapestry|nuanced|multifaceted|landscape)\s",
        r"(?:it'?s\s+(?:important|worth)\s+(?:to\s+)?not(?:e|ing))",
        r"(?:in\s+(?:today'?s|this)\s+(?:digital|modern|complex)\s+(?:age|landscape|world|era))",
        r"(?:let'?s\s+(?:dive|unpack|explore|break\s+(?:this|it)\s+down))",
        r"(?:here'?s?\s+(?:the\s+)?(?:thing|deal|bottom\s+line|takeaway))",
        r"(?:(?:firstly|secondly|thirdly|lastly),?\s)",
    ]
]

_AI_FLUENCY_MARKERS: set[str] = {
    "delve", "tapestry", "multifaceted", "nuanced", "landscape",
    "paramount", "leveraging", "pivotal", "fostering", "holistic",
    "synergy", "paradigm", "ecosystem", "stakeholders", "granular",
    "robust", "scalable", "streamline", "comprehensive", "innovative",
}


def detect_ai_content(text: str) -> tuple[float, list[str]]:
    """Score likelihood that text is AI-generated. Returns (score, flags).

    Score 0.0 = definitely human, 1.0 = almost certainly AI.
    """
    flags: list[str] = []
    score = 0.0

    # Pattern matches
    for pattern in _AI_PATTERNS:
        if pattern.search(text):
            score += 0.2
            flags.append(f"ai_pattern: {pattern.pattern[:40]}")

    # AI fluency marker density
    words = text.lower().split()
    if words:
        ai_word_count = sum(1 for w in words if w.strip(".,!?;:") in _AI_FLUENCY_MARKERS)
        ai_density = ai_word_count / len(words)
        if ai_density > 0.05:
            score += 0.3
            flags.append(f"high_ai_marker_density ({ai_density:.1%})")
        elif ai_density > 0.02:
            score += 0.15
            flags.append(f"elevated_ai_markers ({ai_density:.1%})")

    # Suspiciously balanced/hedged language (AI loves both-sides-ing)
    hedging = len(re.findall(r"\b(?:however|on the other hand|that said|conversely|arguably|nevertheless)\b", text, re.I))
    if hedging >= 3:
        score += 0.15
        flags.append(f"excessive_hedging ({hedging} markers)")

    # Perfect grammar + long text from social media = suspicious
    if len(words) > 100 and not re.search(r"[^\w\s.,!?;:@#\-'\"()]", text):
        score += 0.1
        flags.append("suspiciously_clean_long_text")

    return min(1.0, score), flags


# ── First-hand / expert detection ─────────────────────────────────────

_FIRSTHAND_MARKERS: list[re.Pattern] = [
    re.compile(p, re.I) for p in [
        r"\bi (?:saw|heard|witnessed|was\s+(?:there|at|near|present))\b",
        r"\bmy (?:friend|family|neighbor|colleague|source)\b",
        r"\bjust (?:happened|saw|heard|witnessed)\b",
        r"\bhere (?:right now|on the ground|at the scene)\b",
        r"\bcan confirm\b",
        r"\bphoto[s]?\s+(?:i|we)\s+took\b",
        r"\bvideo\s+(?:i|we)\s+(?:shot|recorded|captured)\b",
        r"\b(?:i'?m|we'?re)\s+(?:on|at)\s+the\s+(?:scene|ground|location)\b",
    ]
]

_EXPERT_MARKERS: list[re.Pattern] = [
    re.compile(p, re.I) for p in [
        r"\bas a (?:doctor|nurse|medic|paramedic|firefighter|officer|engineer|scientist|researcher|analyst|journalist|reporter)\b",
        r"\bmy (?:analysis|assessment|reading) (?:of|is)\b",
        r"\bbased on (?:my|our) (?:experience|data|models|analysis)\b",
        r"\bseismic data (?:shows?|indicates?|suggests?)\b",
        r"\bsatellite (?:imagery|images?|photos?) (?:show|reveal|confirm)\b",
        r"\bopen.?source (?:data|intelligence|analysis|investigation)\b",
        r"\bgeolocation (?:confirms?|shows?|places?)\b",
    ]
]


def detect_firsthand(text: str) -> tuple[bool, list[str]]:
    """Detect if tweet appears to be first-hand account."""
    flags = []
    for pattern in _FIRSTHAND_MARKERS:
        if pattern.search(text):
            flags.append(f"firsthand: {pattern.pattern[:40]}")
    return len(flags) > 0, flags


def detect_expert(text: str) -> tuple[bool, list[str]]:
    """Detect if tweet appears to come from domain expert."""
    flags = []
    for pattern in _EXPERT_MARKERS:
        if pattern.search(text):
            flags.append(f"expert: {pattern.pattern[:40]}")
    return len(flags) > 0, flags


# ── Novelty scoring ──────────────────────────────────────────────────

def _text_fingerprint(text: str) -> set[str]:
    """Extract term fingerprint for novelty comparison."""
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    stop = {"the", "and", "for", "are", "but", "not", "you", "all", "can",
            "had", "her", "was", "one", "our", "out", "has", "his", "how",
            "its", "may", "new", "now", "old", "see", "way", "who", "did",
            "got", "let", "say", "she", "too", "use", "this", "that", "with",
            "have", "from", "been", "just", "more", "about", "they", "will",
            "what", "when", "where", "which", "their", "there", "would"}
    return {w for w in words if w not in stop}


def novelty_score(text: str, seen_fingerprints: list[set[str]]) -> float:
    """Score how novel a tweet is compared to already-seen content.

    Returns 0.0 (exact duplicate) to 1.0 (completely novel).
    """
    if not seen_fingerprints:
        return 1.0

    fp = _text_fingerprint(text)
    if not fp:
        return 0.0

    max_overlap = 0.0
    for seen_fp in seen_fingerprints:
        if not seen_fp:
            continue
        overlap = len(fp & seen_fp) / len(fp | seen_fp)
        max_overlap = max(max_overlap, overlap)

    return round(1.0 - max_overlap, 3)


# ── Coordination detection ────────────────────────────────────────────

def detect_coordination(
    tweets: list[SurgeTweet],
    time_window_seconds: int = 30,
    text_similarity_threshold: float = 0.8,
) -> dict[str, list[str]]:
    """Detect coordinated posting: near-identical text from different accounts
    posted within seconds.

    Returns: {tweet_id: [flag1, flag2, ...]}
    """
    flagged: dict[str, list[str]] = defaultdict(list)

    for i, a in enumerate(tweets):
        fp_a = _text_fingerprint(a.text)
        for j in range(i + 1, len(tweets)):
            b = tweets[j]
            if a.author == b.author:
                continue
            time_diff = abs((a.posted_at - b.posted_at).total_seconds())
            if time_diff > time_window_seconds:
                continue

            fp_b = _text_fingerprint(b.text)
            if not fp_a or not fp_b:
                continue

            similarity = len(fp_a & fp_b) / len(fp_a | fp_b)
            if similarity >= text_similarity_threshold:
                flag = f"coordinated: {similarity:.0%} similar to @{b.author} within {time_diff:.0f}s"
                flagged[a.tweet_id].append(flag)
                flagged[b.tweet_id].append(f"coordinated: {similarity:.0%} similar to @{a.author} within {time_diff:.0f}s")

    return dict(flagged)


# ── Surge detection ──────────────────────────────────────────────────

def detect_surge(
    tweets: list[SurgeTweet],
    window_minutes: int = 15,
    min_tweets: int = 10,
    min_velocity: float = 2.0,  # tweets per minute
) -> list[dict[str, Any]]:
    """Detect volume surges in tweet flow.

    Returns list of surge windows with stats.
    """
    if not tweets:
        return []

    sorted_tweets = sorted(tweets, key=lambda t: t.posted_at)
    surges: list[dict[str, Any]] = []

    # Sliding window
    window = timedelta(minutes=window_minutes)
    i = 0
    for j, tweet in enumerate(sorted_tweets):
        while sorted_tweets[i].posted_at < tweet.posted_at - window:
            i += 1

        window_tweets = sorted_tweets[i:j + 1]
        if len(window_tweets) < min_tweets:
            continue

        span = (window_tweets[-1].posted_at - window_tweets[0].posted_at).total_seconds() / 60
        velocity = len(window_tweets) / max(1, span)

        if velocity >= min_velocity:
            unique_authors = len({t.author for t in window_tweets})
            surges.append({
                "start": window_tweets[0].posted_at,
                "end": window_tweets[-1].posted_at,
                "count": len(window_tweets),
                "velocity": round(velocity, 2),
                "unique_authors": unique_authors,
                "peak_text": max(window_tweets, key=lambda t: t.alpha_score).text[:200] if window_tweets else "",
            })

    # Merge overlapping surges
    merged: list[dict[str, Any]] = []
    for surge in surges:
        if merged and surge["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], surge["end"])
            merged[-1]["count"] = max(merged[-1]["count"], surge["count"])
            merged[-1]["velocity"] = max(merged[-1]["velocity"], surge["velocity"])
        else:
            merged.append(surge)

    return merged


# ── Main analysis entry point ─────────────────────────────────────────

def analyze_surge(
    observations: list[dict[str, Any]],
    topic: str = "",
    *,
    account_store: AccountCredibilityStore | None = None,
    alpha_threshold: float = 0.6,
    ai_threshold: float = 0.4,
    novelty_threshold: float = 0.3,
) -> SurgeAnalysisResult:
    """Analyze a batch of social media observations for surge intelligence.

    This is the main entry point. Feed it observations from NitterConnector
    or any social media source, and it will:
    1. Convert to SurgeTweets
    2. Score each for alpha/noise/disinfo
    3. Build/update account credibility profiles
    4. Detect surges and coordination
    5. Return ranked results

    Parameters
    ----------
    observations : list[dict]
        Observations with: source, claim, url, captured_at, reliability_hint
    topic : str
        Topic being monitored.
    account_store : AccountCredibilityStore | None
        Credibility store. Defaults to global singleton.
    alpha_threshold : float
        Minimum alpha score to be considered high-value signal.
    ai_threshold : float
        AI content score above which tweet is flagged.
    novelty_threshold : float
        Minimum novelty to count as new information.
    """
    store = account_store or _account_store

    # ── Convert observations to SurgeTweets ───────────────────────
    tweets: list[SurgeTweet] = []
    for obs in observations:
        source = obs.get("source", "")
        claim = obs.get("claim", "")
        url = obs.get("url", "")
        captured = obs.get("captured_at", datetime.now(UTC))
        if isinstance(captured, str):
            try:
                captured = datetime.fromisoformat(captured)
            except (ValueError, TypeError):
                captured = datetime.now(UTC)

        # Extract author from source tag or claim
        author = _extract_author(source, claim)
        tweet_id = hashlib.md5(f"{author}:{claim[:100]}:{captured}".encode()).hexdigest()[:12]

        tweets.append(SurgeTweet(
            tweet_id=tweet_id,
            author=author,
            text=claim,
            url=url,
            posted_at=captured,
            source_tag=source,
        ))

    if not tweets:
        return SurgeAnalysisResult(
            surge_detected=False, surges=[], account_profiles={},
            total_processed=0, alpha_count=0, noise_count=0, disinfo_count=0,
            credibility_updates=[],
        )

    # ── Score each tweet ──────────────────────────────────────────
    seen_fingerprints: list[set[str]] = []
    credibility_updates: list[dict[str, Any]] = []

    # Detect coordination first (needs full batch)
    coord_flags = detect_coordination(tweets)

    for tweet in tweets:
        all_flags: list[str] = []

        # Account credibility
        profile = store.get_or_create(tweet.author)
        profile.total_observations += 1
        profile.last_seen = tweet.posted_at
        tweet.credibility = profile.credibility_score

        # AI detection
        ai_score, ai_flags = detect_ai_content(tweet.text)
        if ai_score >= ai_threshold:
            tweet.is_ai_generated = True
            all_flags.extend(ai_flags)
            store.penalize(tweet.author, 0.05, "AI-generated content detected")

        # First-hand detection
        is_firsthand, fh_flags = detect_firsthand(tweet.text)
        tweet.is_first_hand = is_firsthand
        if is_firsthand:
            all_flags.extend(fh_flags)

        # Expert detection
        is_expert, expert_flags = detect_expert(tweet.text)
        tweet.is_expert = is_expert
        if is_expert:
            all_flags.extend(expert_flags)

        # Coordination
        if tweet.tweet_id in coord_flags:
            tweet.is_coordinated = True
            all_flags.extend(coord_flags[tweet.tweet_id])
            store.penalize(tweet.author, 0.1, "Coordinated posting detected")
            store.tag(tweet.author, "coordinated")

        # Novelty
        tweet.novelty_score = novelty_score(tweet.text, seen_fingerprints)
        seen_fingerprints.append(_text_fingerprint(tweet.text))

        # ── Compute alpha score ───────────────────────────────────
        # Alpha = how valuable is this tweet for intelligence purposes?
        alpha = 0.0

        # Novelty is king — new information is the most valuable
        alpha += tweet.novelty_score * 0.35

        # Account credibility matters
        alpha += tweet.credibility * 0.25

        # First-hand accounts are gold
        if tweet.is_first_hand:
            alpha += 0.20
        if tweet.is_expert:
            alpha += 0.15

        # Penalties
        if tweet.is_ai_generated:
            alpha *= 0.3  # Heavy penalty but don't zero out
        if tweet.is_coordinated:
            alpha *= 0.2
        if profile.is_discredited:
            alpha *= 0.1

        # Factual density bonus (specific details = higher value)
        specific_count = len(re.findall(r"\b\d+(?:[.,]\d+)?\b", tweet.text))
        proper_nouns = len(re.findall(r"(?<=\s)[A-Z][a-z]{2,}", tweet.text))
        if specific_count >= 3 or proper_nouns >= 3:
            alpha += 0.05

        tweet.alpha_score = round(min(1.0, alpha), 3)
        tweet.flags = all_flags

    # ── Detect surges ─────────────────────────────────────────────
    surge_windows = detect_surge(tweets)
    surge_detected = len(surge_windows) > 0

    # ── Build surge events ────────────────────────────────────────
    surges: list[SurgeEvent] = []
    if surge_detected:
        for sw in surge_windows:
            window_tweets = [
                t for t in tweets
                if sw["start"] <= t.posted_at <= sw["end"]
            ]
            alpha_tweets = sorted(
                [t for t in window_tweets if t.alpha_score >= alpha_threshold],
                key=lambda t: t.alpha_score, reverse=True,
            )
            noise_tweets = [
                t for t in window_tweets
                if t.is_ai_generated or t.is_coordinated or t.novelty_score < novelty_threshold
            ]

            duration = (sw["end"] - sw["start"]).total_seconds() / 60
            surges.append(SurgeEvent(
                topic=topic,
                detected_at=sw["start"],
                tweet_count=sw["count"],
                unique_authors=sw["unique_authors"],
                velocity=sw["velocity"],
                duration_minutes=round(duration, 1),
                peak_minute=sw["start"],  # simplified
                alpha_tweets=alpha_tweets[:20],
                noise_tweets=noise_tweets[:20],
                discredited_authors=store.get_discredited(),
                timeline=[sw],
                summary=_build_surge_summary(topic, sw, alpha_tweets, noise_tweets),
            ))

    # ── Categorize all tweets ─────────────────────────────────────
    alpha_count = sum(1 for t in tweets if t.alpha_score >= alpha_threshold)
    noise_count = sum(1 for t in tweets if t.novelty_score < novelty_threshold or t.is_ai_generated)
    disinfo_count = sum(1 for t in tweets if t.is_coordinated or t.is_ai_generated)

    return SurgeAnalysisResult(
        surge_detected=surge_detected,
        surges=surges,
        account_profiles=store.profiles,
        total_processed=len(tweets),
        alpha_count=alpha_count,
        noise_count=noise_count,
        disinfo_count=disinfo_count,
        credibility_updates=credibility_updates,
    )


def _extract_author(source: str, text: str) -> str:
    """Extract author handle from source tag or tweet text."""
    # From source: "nitter:@handle" or "nitter:search:query"
    if "@" in source:
        parts = source.split("@")
        if len(parts) >= 2:
            return parts[-1].split("/")[0].strip()

    # From text: "@handle: ..."
    match = re.match(r"@(\w+):\s", text)
    if match:
        return match.group(1)

    # From text: any @mention
    match = re.search(r"@(\w+)", text)
    if match:
        return match.group(1)

    return "unknown"


def _build_surge_summary(
    topic: str,
    window: dict[str, Any],
    alpha_tweets: list[SurgeTweet],
    noise_tweets: list[SurgeTweet],
) -> str:
    """Build human-readable surge summary."""
    parts = [
        f"Surge detected on '{topic}': {window['count']} tweets at {window['velocity']:.1f}/min "
        f"from {window['unique_authors']} unique authors.",
    ]
    if alpha_tweets:
        parts.append(f"Found {len(alpha_tweets)} high-value alpha signals.")
        # Best tweet
        best = alpha_tweets[0]
        parts.append(f"Top signal (score {best.alpha_score:.2f}): @{best.author}: {best.text[:200]}")
    if noise_tweets:
        parts.append(f"Filtered {len(noise_tweets)} noise/disinfo tweets.")
    return " ".join(parts)
