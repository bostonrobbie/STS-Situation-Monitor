"""Tests for the slop/propaganda/bot detection filter."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sts_monitor.slop_detector import (
    SlopFilterResult,
    SlopScore,
    filter_slop,
    score_observation,
    _detect_coordination,
    _extract_domain,
    _score_bot_patterns,
    _score_engagement_bait,
    _score_factual_density,
    _score_propaganda,
    _score_ragebait,
    _score_source_credibility,
)

pytestmark = pytest.mark.unit


# ── Helpers ───────────────────────────────────────────────────────────

_NOW = datetime.now(UTC)


def _obs(
    claim: str,
    source: str = "rss:example.com",
    url: str = "https://example.com/article",
    hours_ago: float = 0,
    obs_id: int = 0,
) -> dict:
    return {
        "id": obs_id,
        "source": source,
        "claim": claim,
        "url": url,
        "captured_at": _NOW - timedelta(hours=hours_ago),
    }


# ── _extract_domain ──────────────────────────────────────────────────

def test_extract_domain_basic() -> None:
    assert _extract_domain("https://www.rt.com/news/article") == "rt.com"


def test_extract_domain_no_scheme() -> None:
    assert _extract_domain("infowars.com/page") == "infowars.com"


def test_extract_domain_empty() -> None:
    assert _extract_domain("") == ""


# ── _score_engagement_bait ───────────────────────────────────────────

def test_engagement_bait_all_caps() -> None:
    score, flags = _score_engagement_bait(
        "THIS IS ABSOLUTELY INSANE AND EVERYONE SHOULD KNOW ABOUT THIS RIGHT NOW"
    )
    assert score > 0
    assert any("caps" in f for f in flags)


def test_engagement_bait_high_caps_threshold() -> None:
    """Between 25% and 50% caps should trigger high_caps."""
    # 3 out of 10 words are all-caps (30%)
    score, flags = _score_engagement_bait(
        "THIS earthquake WAS very large NEAR the coast of Chile region"
    )
    assert any("high_caps" in f or "excessive_caps" in f for f in flags)


def test_engagement_bait_excessive_exclamation() -> None:
    score, flags = _score_engagement_bait("This is huge!!!")
    assert score > 0
    assert any("exclamation" in f for f in flags)


def test_engagement_bait_excessive_questions() -> None:
    score, flags = _score_engagement_bait("What is going on??? Are they lying??? Really???")
    assert score > 0
    assert any("question" in f for f in flags)


def test_engagement_bait_clickbait_phrase_you_wont_believe() -> None:
    score, flags = _score_engagement_bait("You won't believe what happened next in Ukraine")
    assert score > 0
    assert any("clickbait" in f for f in flags)


def test_engagement_bait_clickbait_share_this() -> None:
    score, flags = _score_engagement_bait("Share this before it gets deleted!")
    assert score > 0
    assert any("clickbait" in f for f in flags)


def test_engagement_bait_clickbait_must_watch() -> None:
    score, flags = _score_engagement_bait("Must watch video reveals the truth")
    assert score > 0
    assert any("clickbait" in f for f in flags)


def test_engagement_bait_clickbait_viral() -> None:
    score, flags = _score_engagement_bait("This viral clip shows everything")
    assert score > 0
    assert any("clickbait" in f for f in flags)


def test_engagement_bait_clean_text() -> None:
    score, flags = _score_engagement_bait(
        "A 6.2 magnitude earthquake struck central Chile at 14:32 UTC on Monday."
    )
    assert score == 0.0
    assert flags == []


def test_engagement_bait_capped_at_one() -> None:
    """Score should never exceed 1.0 even with many triggers."""
    score, _ = _score_engagement_bait(
        "YOU WON'T BELIEVE THIS!!! SHOCKING BOMBSHELL!!! "
        "MUST WATCH!!! SHARE THIS!!! EXPOSED!!! LIKE AND SHARE!!!"
    )
    assert score <= 1.0


# ── _score_ragebait ──────────────────────────────────────────────────

def test_ragebait_partisan_slur() -> None:
    score, flags = _score_ragebait("This libtard is destroying the country")
    assert score > 0
    assert any("ragebait" in f for f in flags)


def test_ragebait_owned_pattern() -> None:
    score, flags = _score_ragebait("Senator destroyed liberal opponent in debate")
    assert score > 0


def test_ragebait_destroying_pattern() -> None:
    score, flags = _score_ragebait("Democrats are destroying this country")
    assert score > 0


def test_ragebait_deep_state() -> None:
    score, flags = _score_ragebait("The deep state agenda is becoming clear")
    assert score > 0


def test_ragebait_clean_text() -> None:
    score, flags = _score_ragebait(
        "The Senate voted 52-48 to approve the budget resolution on Thursday."
    )
    assert score == 0.0
    assert flags == []


def test_ragebait_capped_at_one() -> None:
    score, _ = _score_ragebait(
        "The woke mob libtard snowflake cuck sheeple destroyed conservative values "
        "this is why the left is ruining everything deep state globalist agenda"
    )
    assert score <= 1.0


# ── _score_propaganda ────────────────────────────────────────────────

def test_propaganda_special_military_operation() -> None:
    score, flags = _score_propaganda("The special military operation continues as planned")
    assert score > 0
    assert any("propaganda" in f for f in flags)


def test_propaganda_denazification() -> None:
    score, flags = _score_propaganda("Denazification of the region is necessary")
    assert score > 0


def test_propaganda_western_aggression() -> None:
    score, flags = _score_propaganda("This is a result of western aggression against our sovereignty")
    assert score > 0


def test_propaganda_internal_affairs_china() -> None:
    score, flags = _score_propaganda("This is an internal affairs of China issue")
    assert score > 0


def test_propaganda_so_called_genocide() -> None:
    score, flags = _score_propaganda("The so-called genocide never happened")
    assert score > 0


def test_propaganda_color_revolution() -> None:
    score, flags = _score_propaganda("Another color revolution backed by outside forces")
    assert score > 0


def test_propaganda_clean_text() -> None:
    score, flags = _score_propaganda(
        "The UN Security Council convened on Wednesday to discuss humanitarian aid."
    )
    assert score == 0.0
    assert flags == []


def test_propaganda_capped_at_one() -> None:
    score, _ = _score_propaganda(
        "The special military operation for denazification is a response to "
        "western aggression and NATO puppet provocation by the west. "
        "This is an internal affairs of China. So-called genocide is anti-Russia propaganda. "
        "Color revolution foreign interference hurting the feelings of the chinese people."
    )
    assert score <= 1.0


# ── _score_source_credibility ────────────────────────────────────────

def test_source_credibility_known_disinfo() -> None:
    score, flags = _score_source_credibility("RT News", "https://rt.com/news/article")
    assert score >= 0.6
    assert any("disinfo" in f for f in flags)


def test_source_credibility_known_disinfo_infowars() -> None:
    score, flags = _score_source_credibility("InfoWars", "https://infowars.com/story")
    assert score >= 0.6


def test_source_credibility_content_farm_blogspot() -> None:
    score, flags = _score_source_credibility("some blog", "https://myblog.blogspot.com/article")
    assert score > 0
    assert any("content_farm" in f for f in flags)


def test_source_credibility_content_farm_wordpress() -> None:
    score, flags = _score_source_credibility("wp blog", "https://someone.wordpress.com/2024/01/article")
    assert score > 0


def test_source_credibility_no_url() -> None:
    score, flags = _score_source_credibility("some source", "")
    assert score > 0
    assert any("no_url" in f for f in flags)


def test_source_credibility_reputable() -> None:
    score, flags = _score_source_credibility("Reuters", "https://reuters.com/article")
    assert score == 0.0
    assert flags == []


def test_source_credibility_capped_at_one() -> None:
    score, _ = _score_source_credibility("disinfo", "https://rt.com/article.blogspot.com")
    assert score <= 1.0


# ── _score_factual_density ───────────────────────────────────────────

def test_factual_density_high_factual() -> None:
    text = (
        "A 6.2 magnitude earthquake struck Santiago, Chile at 14:32 UTC on "
        "Monday January 15. The epicenter was 45 km south of the city center. "
        "President Boric issued a statement from the National Emergency Office."
    )
    score, flags = _score_factual_density(text)
    assert score == 0.0  # Factual text should have no penalty


def test_factual_density_low_vague_text() -> None:
    text = (
        "something terrible is happening and nobody is talking about it and "
        "this is really bad and everyone should be worried about what is going on and "
        "things are getting worse and worse every single day and we need to do "
        "something about it before it is too late for all of us here and everywhere"
    )
    score, flags = _score_factual_density(text)
    assert score > 0
    assert any("factual_density" in f for f in flags)


def test_factual_density_very_short_text() -> None:
    score, flags = _score_factual_density("Bad things happening")
    # Short text has low expected indicators so might not be penalized heavily
    assert score >= 0.0


# ── _score_bot_patterns ──────────────────────────────────────────────

def test_bot_very_short_text() -> None:
    score, flags = _score_bot_patterns("wow", "nitter:bot123")
    assert score > 0
    assert any("short" in f for f in flags)


def test_bot_hashtag_spam() -> None:
    score, flags = _score_bot_patterns(
        "Breaking news #war #conflict #ukraine #russia #nato #ww3", "nitter:user"
    )
    assert score > 0
    assert any("hashtag" in f for f in flags)


def test_bot_moderate_hashtags() -> None:
    """3-5 hashtags should trigger lighter flag."""
    score, flags = _score_bot_patterns(
        "News report on conflict #ukraine #russia #nato #breaking", "nitter:user"
    )
    assert any("hashtag" in f for f in flags)


def test_bot_url_only_post() -> None:
    score, flags = _score_bot_patterns(
        "https://example.com/article check", "nitter:user"
    )
    assert score > 0
    assert any("url_only" in f for f in flags)


def test_bot_emoji_spam() -> None:
    score, flags = _score_bot_patterns(
        "This \U0001F525 is \U0001F525 so \U0001F525 wild \U0001F525 right \U0001F525 now \U0001F525 wow \U0001F525",
        "nitter:user",
    )
    assert score > 0
    assert any("emoji" in f for f in flags)


def test_bot_clean_text() -> None:
    score, flags = _score_bot_patterns(
        "A magnitude 6.2 earthquake struck near Santiago, Chile today according to USGS data.",
        "rss:reuters.com",
    )
    assert score == 0.0
    assert flags == []


def test_bot_capped_at_one() -> None:
    score, _ = _score_bot_patterns(
        "wow #a #b #c #d #e #f https://t.co/abc \U0001F525\U0001F525\U0001F525\U0001F525\U0001F525\U0001F525\U0001F525\U0001F525",
        "nitter:bot",
    )
    assert score <= 1.0


# ── _detect_coordination ─────────────────────────────────────────────

def test_coordination_identical_text_different_sources() -> None:
    obs = [
        _obs("Exact same text posted by both accounts", source="nitter:user1", hours_ago=0),
        _obs("Exact same text posted by both accounts", source="nitter:user2", hours_ago=0),
    ]
    penalties = _detect_coordination(obs, time_window_seconds=60)
    assert len(penalties) == 2
    assert penalties[0][0] > 0
    assert penalties[1][0] > 0
    assert any("coordinated" in f for f in penalties[0][1])


def test_coordination_same_source_ignored() -> None:
    """Same source posting twice should not be flagged as coordination."""
    obs = [
        _obs("Same text from same source", source="nitter:user1", hours_ago=0),
        _obs("Same text from same source", source="nitter:user1", hours_ago=0),
    ]
    penalties = _detect_coordination(obs, time_window_seconds=60)
    assert len(penalties) == 0


def test_coordination_outside_time_window() -> None:
    obs = [
        _obs("Exact same text posted by both", source="nitter:user1", hours_ago=0),
        _obs("Exact same text posted by both", source="nitter:user2", hours_ago=1),
    ]
    # 1 hour = 3600 seconds, window is 60 seconds
    penalties = _detect_coordination(obs, time_window_seconds=60)
    assert len(penalties) == 0


def test_coordination_different_text_no_flag() -> None:
    obs = [
        _obs("First completely different text about something", source="nitter:user1", hours_ago=0),
        _obs("Second unrelated text about another topic", source="nitter:user2", hours_ago=0),
    ]
    penalties = _detect_coordination(obs, time_window_seconds=60)
    assert len(penalties) == 0


def test_coordination_near_identical_prefix() -> None:
    """Text sharing the first 50 characters should be detected."""
    prefix = "A" * 55
    obs = [
        _obs(prefix + " ending one", source="nitter:user1", hours_ago=0),
        _obs(prefix + " ending two", source="nitter:user2", hours_ago=0),
    ]
    penalties = _detect_coordination(obs, time_window_seconds=60)
    assert len(penalties) == 2


def test_coordination_string_timestamps() -> None:
    """ISO format string timestamps should be handled."""
    now = datetime.now(UTC)
    obs = [
        {"claim": "Same text coordinated", "source": "src1", "url": "", "captured_at": now.isoformat()},
        {"claim": "Same text coordinated", "source": "src2", "url": "", "captured_at": now.isoformat()},
    ]
    penalties = _detect_coordination(obs, time_window_seconds=60)
    assert len(penalties) == 2


# ── score_observation ────────────────────────────────────────────────

def test_score_credible_factual_text() -> None:
    obs = _obs(
        "A 6.2 magnitude earthquake struck 45 km south of Santiago, Chile "
        "at 14:32 UTC on Monday January 15 according to the USGS.",
        source="rss:reuters.com",
        url="https://reuters.com/article",
    )
    score = score_observation(obs)
    assert score.verdict == "credible"
    assert score.slop_score < 0.25
    assert score.recommended_action == "accept"


def test_score_obvious_slop() -> None:
    obs = _obs(
        "YOU WON'T BELIEVE WHAT HAPPENED!!! SHOCKING BOMBSHELL!!! "
        "SHARE THIS BEFORE THEY DELETE IT!!! MUST WATCH!!!",
        source="nitter:random_user",
        url="",
    )
    score = score_observation(obs)
    assert score.verdict in ("slop", "suspicious")
    assert score.slop_score > 0.25


def test_score_propaganda_verdict() -> None:
    obs = _obs(
        "The special military operation for denazification continues as planned. "
        "Western aggression has provoked this necessary response.",
        source="rss:rt.com",
        url="https://rt.com/news/article",
    )
    score = score_observation(obs)
    assert score.verdict == "propaganda"
    assert score.factor_scores["propaganda"] >= 0.4


def test_score_with_coordination_penalty() -> None:
    obs = _obs("Some claim text here about something", source="nitter:user")
    score = score_observation(obs, coordination_penalty=0.5, coordination_flags=["coordinated_posting: test"])
    assert score.factor_scores.get("coordination") == 0.5
    assert "coordinated_posting: test" in score.flags


def test_score_observation_id_from_id_field() -> None:
    obs = {"id": 42, "claim": "text", "source": "src", "url": ""}
    score = score_observation(obs)
    assert score.observation_id == 42


def test_score_observation_id_from_observation_id_field() -> None:
    obs = {"observation_id": 99, "claim": "text", "source": "src", "url": ""}
    score = score_observation(obs)
    assert score.observation_id == 99


def test_score_claim_preview_truncated() -> None:
    long_claim = "A" * 500
    obs = _obs(long_claim)
    score = score_observation(obs)
    assert len(score.claim_preview) == 200


def test_score_credibility_inverse_of_slop() -> None:
    obs = _obs("Some text about events", source="rss:example.com", url="https://example.com/art")
    score = score_observation(obs)
    assert score.credibility_score == pytest.approx(1.0 - score.slop_score, abs=0.001)


def test_score_all_factor_scores_present() -> None:
    obs = _obs("Some text here", source="rss:example.com", url="https://example.com/art")
    score = score_observation(obs)
    assert "engagement_bait" in score.factor_scores
    assert "ragebait" in score.factor_scores
    assert "propaganda" in score.factor_scores
    assert "source_credibility" in score.factor_scores
    assert "factual_density" in score.factor_scores
    assert "bot_patterns" in score.factor_scores


def test_score_action_thresholds() -> None:
    """Verify the action recommendation mapping."""
    # Low slop -> accept
    clean = score_observation(_obs(
        "A 6.2 magnitude earthquake struck Santiago Chile at 14:32 UTC Monday January 15 per USGS data.",
        source="rss:reuters.com",
        url="https://reuters.com/art",
    ))
    assert clean.recommended_action == "accept"


# ── Verdict classification ───────────────────────────────────────────

def test_verdict_credible() -> None:
    obs = _obs(
        "The UN Security Council voted 12-3 on Thursday to extend the peacekeeping "
        "mandate in South Sudan by 12 months, according to diplomats present.",
        source="rss:reuters.com",
        url="https://reuters.com/article",
    )
    score = score_observation(obs)
    assert score.verdict == "credible"


def test_verdict_suspicious_mixed_content() -> None:
    obs = _obs(
        "BREAKING: This shocking revelation about the earthquake was exposed!!! "
        "The truth about what really happened.",
        source="rss:example.com",
        url="https://example.com/art",
    )
    score = score_observation(obs)
    assert score.verdict in ("suspicious", "slop")


def test_verdict_propaganda_takes_priority() -> None:
    """Propaganda verdict should trigger when propaganda score >= 0.4."""
    obs = _obs(
        "The special military operation for denazification proceeds. "
        "Western aggression and NATO puppet provocation continues. "
        "This so-called invasion is anti-Russia propaganda.",
        source="rss:rt.com",
        url="https://rt.com/article",
    )
    score = score_observation(obs)
    assert score.verdict == "propaganda"


# ── filter_slop (batch processing) ──────────────────────────────────

def test_filter_slop_empty_input() -> None:
    result = filter_slop([])
    assert result.total == 0
    assert result.credible == 0
    assert result.scores == []


def test_filter_slop_single_credible() -> None:
    result = filter_slop([
        _obs(
            "A 6.2 magnitude earthquake struck Santiago Chile at 14:32 UTC on Monday January 15.",
            source="rss:reuters.com",
            url="https://reuters.com/art",
        ),
    ])
    assert result.total == 1
    assert result.credible == 1
    assert result.dropped_count == 0


def test_filter_slop_mixed_batch() -> None:
    result = filter_slop([
        _obs(
            "The UN voted 12-3 to extend the mandate in South Sudan on Thursday.",
            source="rss:reuters.com",
            url="https://reuters.com/art",
            obs_id=1,
        ),
        _obs(
            "The special military operation for denazification continues as planned "
            "against western aggression.",
            source="rss:rt.com",
            url="https://rt.com/art",
            obs_id=2,
        ),
        _obs(
            "YOU WON'T BELIEVE THIS!!! SHOCKING!!! SHARE THIS!!! MUST WATCH!!! "
            "LIKE AND SHARE BEFORE THEY DELETE!!!",
            source="nitter:bot",
            url="",
            obs_id=3,
        ),
    ])
    assert result.total == 3
    assert result.credible >= 1
    assert result.propaganda >= 1


def test_filter_slop_coordination_detection() -> None:
    """Coordinated posts should get penalized in batch processing."""
    now = _NOW
    result = filter_slop([
        {
            "id": 1,
            "source": "nitter:bot1",
            "claim": "Exact coordinated message posted simultaneously by network",
            "url": "",
            "captured_at": now,
        },
        {
            "id": 2,
            "source": "nitter:bot2",
            "claim": "Exact coordinated message posted simultaneously by network",
            "url": "",
            "captured_at": now,
        },
    ])
    assert result.total == 2
    # Both should be flagged for coordination
    for score in result.scores:
        assert any("coordinated" in f for f in score.flags)


def test_filter_slop_dropped_count() -> None:
    """High-slop items should increment dropped_count."""
    result = filter_slop([
        _obs(
            "YOU WON'T BELIEVE THIS!!! SHOCKING BOMBSHELL!!! "
            "EXPOSED!!! SHARE THIS BEFORE THEY DELETE!!! MUST WATCH!!!",
            source="nitter:bot",
            url="",
            obs_id=1,
        ),
    ])
    # With heavy engagement bait + no URL, slop_score should be high
    if result.scores[0].slop_score >= 0.6:
        assert result.dropped_count >= 1


def test_filter_slop_pattern_stats() -> None:
    result = filter_slop([
        _obs("The special military operation continues", source="rss:rt.com", url="https://rt.com/art"),
        _obs("YOU WON'T BELIEVE THIS!!!", source="nitter:user", url="https://example.com/art"),
    ])
    assert isinstance(result.pattern_stats, dict)
    # At least some patterns should have fired
    assert len(result.pattern_stats) > 0


def test_filter_slop_scores_list_matches_total() -> None:
    obs_list = [
        _obs("First claim", obs_id=1),
        _obs("Second claim", obs_id=2),
        _obs("Third claim", obs_id=3),
    ]
    result = filter_slop(obs_list)
    assert len(result.scores) == result.total == 3


def test_filter_slop_verdict_counts_add_up() -> None:
    result = filter_slop([
        _obs("Clean factual text about events on Monday January 15 at 14:32 UTC.", source="rss:reuters.com", url="https://reuters.com/art", obs_id=1),
        _obs("The special military operation for denazification continues.", source="rss:rt.com", url="https://rt.com/art", obs_id=2),
    ])
    assert result.credible + result.suspicious + result.slop + result.propaganda == result.total
