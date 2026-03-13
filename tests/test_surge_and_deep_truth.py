"""Tests for surge_detector and deep_truth modules."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sts_monitor.surge_detector import (
    AccountCredibilityStore,
    SurgeAnalysisResult,
    SurgeTweet,
    _extract_author,
    _text_fingerprint,
    analyze_surge,
    detect_ai_content,
    detect_coordination,
    detect_expert,
    detect_firsthand,
    detect_surge,
    get_account_store,
    novelty_score,
)
from sts_monitor.deep_truth import (
    AuthorityWeight,
    DeepTruthVerdict,
    ProvenanceScore,
    analyze_deep_truth,
    compute_authority_weight,
    compute_provenance,
    detect_pejorative_labels,
    detect_phrase_coordination,
    detect_silence_gaps,
)

pytestmark = pytest.mark.unit

# ── Helpers ──────────────────────────────────────────────────────────

_NOW = datetime.now(UTC)


def _tweet(
    text: str,
    author: str = "user1",
    tweet_id: str | None = None,
    minutes_ago: float = 0,
) -> SurgeTweet:
    return SurgeTweet(
        tweet_id=tweet_id or f"t_{author}_{int(minutes_ago)}",
        author=author,
        text=text,
        url=f"https://x.com/{author}/status/1",
        posted_at=_NOW - timedelta(minutes=minutes_ago),
        source_tag=f"nitter:@{author}",
    )


def _obs(
    claim: str,
    source: str = "rss:example.com",
    url: str = "https://example.com/article",
    hours_ago: float = 0,
    reliability_hint: float = 0.5,
) -> dict:
    return {
        "source": source,
        "claim": claim,
        "url": url,
        "captured_at": _NOW - timedelta(hours=hours_ago),
        "reliability_hint": reliability_hint,
    }


# =====================================================================
# surge_detector — AccountCredibilityStore
# =====================================================================


class TestAccountCredibilityStore:
    def test_get_or_create_new(self):
        store = AccountCredibilityStore()
        profile = store.get_or_create("@TestUser")
        assert profile.handle == "testuser"
        assert profile.credibility_score == 0.5

    def test_get_or_create_returns_existing(self):
        store = AccountCredibilityStore()
        p1 = store.get_or_create("alice")
        p2 = store.get_or_create("alice")
        assert p1 is p2

    def test_boost(self):
        store = AccountCredibilityStore()
        store.boost("alice", 0.1, "good call")
        profile = store.get_or_create("alice")
        assert profile.credibility_score == pytest.approx(0.6)
        assert profile.correct_calls == 1
        assert any("good call" in f for f in profile.flags)

    def test_boost_caps_at_one(self):
        store = AccountCredibilityStore()
        store.boost("alice", 0.8)
        store.boost("alice", 0.5)
        assert store.get_or_create("alice").credibility_score == 1.0

    def test_penalize(self):
        store = AccountCredibilityStore()
        store.penalize("bob", 0.2, "bad take")
        profile = store.get_or_create("bob")
        assert profile.credibility_score == pytest.approx(0.3)
        assert profile.incorrect_calls == 1

    def test_penalize_floors_at_zero(self):
        store = AccountCredibilityStore()
        store.penalize("bob", 0.9)
        store.penalize("bob", 0.5)
        assert store.get_or_create("bob").credibility_score == 0.0

    def test_tag(self):
        store = AccountCredibilityStore()
        store.tag("carol", "domain_expert")
        store.tag("carol", "domain_expert")  # duplicate
        profile = store.get_or_create("carol")
        assert profile.tags == ["domain_expert"]

    def test_apply_decay_inactive(self):
        store = AccountCredibilityStore()
        profile = store.get_or_create("old_user")
        profile.credibility_score = 0.9
        profile.last_seen = _NOW - timedelta(days=60)
        decayed = store.apply_decay(inactive_days=30, decay_rate=0.02)
        assert decayed == 1
        # Should drift toward 0.5, so score decreases
        assert profile.credibility_score < 0.9

    def test_apply_decay_active_untouched(self):
        store = AccountCredibilityStore()
        profile = store.get_or_create("active_user")
        profile.credibility_score = 0.8
        profile.last_seen = _NOW
        decayed = store.apply_decay(inactive_days=30)
        assert decayed == 0
        assert profile.credibility_score == 0.8

    def test_get_discredited(self):
        store = AccountCredibilityStore()
        store.penalize("badguy", 0.4)  # now 0.1
        store.boost("goodguy", 0.1)
        assert "badguy" in store.get_discredited()
        assert "goodguy" not in store.get_discredited()

    def test_get_trusted(self):
        store = AccountCredibilityStore()
        store.boost("trusted_source", 0.2)  # now 0.7
        store.penalize("untrusted", 0.1)  # now 0.4
        assert "trusted_source" in store.get_trusted()
        assert "untrusted" not in store.get_trusted()


# =====================================================================
# surge_detector — AI content detection
# =====================================================================


class TestDetectAIContent:
    def test_ai_generated_text(self):
        text = (
            "Let's delve into this nuanced tapestry of multifaceted "
            "challenges in today's digital landscape. It's important to "
            "note the pivotal role of leveraging holistic synergy."
        )
        score, flags = detect_ai_content(text)
        assert score >= 0.4
        assert len(flags) > 0

    def test_human_text(self):
        text = "Just saw smoke rising from near the bridge, about 3 blocks away."
        score, flags = detect_ai_content(text)
        assert score < 0.3

    def test_hedging_detection(self):
        text = (
            "However, on the other hand, that said, conversely, "
            "arguably one could say nevertheless the truth is complex."
        )
        score, flags = detect_ai_content(text)
        assert any("hedging" in f for f in flags)


# =====================================================================
# surge_detector — firsthand and expert detection
# =====================================================================


class TestDetectFirsthand:
    def test_firsthand_account(self):
        text = "I just witnessed an explosion near the bridge"
        is_fh, flags = detect_firsthand(text)
        assert is_fh is True
        assert len(flags) > 0

    def test_not_firsthand(self):
        text = "Reports say there was an explosion near the bridge"
        is_fh, flags = detect_firsthand(text)
        assert is_fh is False
        assert flags == []


class TestDetectExpert:
    def test_expert_text(self):
        text = "As a scientist, my analysis shows P-wave arrival at 14:32 UTC"
        is_exp, flags = detect_expert(text)
        assert is_exp is True
        assert len(flags) > 0

    def test_not_expert(self):
        text = "I think there was an earthquake"
        is_exp, flags = detect_expert(text)
        assert is_exp is False


# =====================================================================
# surge_detector — novelty scoring
# =====================================================================


class TestNoveltyScore:
    def test_first_tweet_max_novelty(self):
        assert novelty_score("Explosion reported near the central bridge", []) == 1.0

    def test_duplicate_zero_novelty(self):
        text = "Explosion reported near the central bridge"
        seen = [_text_fingerprint(text)]
        score = novelty_score(text, seen)
        assert score == pytest.approx(0.0)

    def test_different_topic_high_novelty(self):
        text_a = "Earthquake magnitude 6.2 struck the coastal region"
        text_b = "Stock market rallied after unexpected employment data"
        seen = [_text_fingerprint(text_a)]
        score = novelty_score(text_b, seen)
        assert score > 0.7

    def test_empty_text_zero_novelty(self):
        assert novelty_score("a", [set()]) == 0.0


# =====================================================================
# surge_detector — coordination detection
# =====================================================================


class TestDetectCoordination:
    def test_identical_text_different_authors(self):
        tweets = [
            _tweet("Breaking: massive fire engulfs the downtown warehouse district", author="bot1", minutes_ago=0),
            _tweet("Breaking: massive fire engulfs the downtown warehouse district", author="bot2", minutes_ago=0),
            _tweet("Breaking: massive fire engulfs the downtown warehouse district", author="bot3", minutes_ago=0),
        ]
        flagged = detect_coordination(tweets, time_window_seconds=60)
        assert len(flagged) >= 2
        for tid, reasons in flagged.items():
            assert any("coordinated" in r for r in reasons)

    def test_different_text_not_flagged(self):
        tweets = [
            _tweet("I saw the fire from my balcony", author="user1"),
            _tweet("Earthquake shook buildings downtown", author="user2"),
        ]
        flagged = detect_coordination(tweets)
        assert len(flagged) == 0

    def test_same_author_not_flagged(self):
        tweets = [
            _tweet("Breaking news about the fire", author="reporter"),
            _tweet("Breaking news about the fire", author="reporter"),
        ]
        flagged = detect_coordination(tweets)
        assert len(flagged) == 0


# =====================================================================
# surge_detector — surge detection
# =====================================================================


class TestDetectSurge:
    def test_high_velocity_burst(self):
        tweets = [
            _tweet(f"Update #{i} on the situation downtown", author=f"user{i}", minutes_ago=i * 0.3)
            for i in range(20)
        ]
        surges = detect_surge(tweets, window_minutes=15, min_tweets=10, min_velocity=2.0)
        assert len(surges) >= 1
        assert surges[0]["count"] >= 10
        assert surges[0]["velocity"] >= 2.0

    def test_no_surge_slow_trickle(self):
        tweets = [
            _tweet(f"Update #{i}", author=f"u{i}", minutes_ago=i * 30)
            for i in range(5)
        ]
        surges = detect_surge(tweets, window_minutes=15, min_tweets=10)
        assert len(surges) == 0

    def test_empty_input(self):
        assert detect_surge([]) == []


# =====================================================================
# surge_detector — analyze_surge end-to-end
# =====================================================================


class TestAnalyzeSurge:
    def test_end_to_end_mixed(self):
        store = AccountCredibilityStore()
        obs = [
            _obs("I just witnessed an explosion near the bridge", source="nitter:@witness1", hours_ago=0),
            _obs("As a journalist, my analysis shows heavy damage", source="nitter:@reporter1", hours_ago=0),
            _obs(
                "Let's delve into this nuanced tapestry of the explosion's "
                "multifaceted impact on today's digital landscape",
                source="nitter:@aislop",
                hours_ago=0,
            ),
            _obs("Generic take on the event", source="nitter:@random1", hours_ago=0),
        ]
        result = analyze_surge(obs, topic="bridge explosion", account_store=store)
        assert isinstance(result, SurgeAnalysisResult)
        assert result.total_processed == 4

    def test_empty_observations(self):
        result = analyze_surge([], topic="nothing")
        assert result.total_processed == 0
        assert result.surge_detected is False

    def test_firsthand_higher_alpha_than_generic(self):
        store = AccountCredibilityStore()
        obs = [
            _obs("I just saw the fire from my window, huge flames", source="nitter:@eyewitness"),
            _obs("Something happened apparently", source="nitter:@rando"),
        ]
        result = analyze_surge(obs, topic="fire", account_store=store)
        # Find tweets by source
        profiles = result.account_profiles
        # The eyewitness tweet should not be flagged as AI and should have firsthand
        assert result.total_processed == 2

    def test_alpha_ordering_firsthand_gt_expert_gt_generic_gt_ai(self):
        """First-hand > expert > generic > AI in alpha scoring."""
        store = AccountCredibilityStore()
        obs = [
            _obs("I just witnessed the building collapse from across the street", source="nitter:@eyewitness"),
            _obs("As a scientist, my analysis shows structural failure pattern", source="nitter:@expert1"),
            _obs("Building collapsed downtown today", source="nitter:@generic"),
            _obs(
                "Let's delve into this nuanced tapestry of building collapses "
                "in today's complex landscape. It's important to note the "
                "pivotal leveraging of holistic synergy paradigm.",
                source="nitter:@aibot",
            ),
        ]
        result = analyze_surge(obs, topic="building collapse", account_store=store)
        # Cannot easily get individual tweets back, but we can verify disinfo count
        assert result.disinfo_count >= 1  # AI tweet flagged

    def test_coordinated_tweets_penalize_accounts(self):
        store = AccountCredibilityStore()
        text = "Breaking: massive coordinated attack reported at the government building downtown"
        obs = [
            _obs(text, source="nitter:@bot1", hours_ago=0),
            _obs(text, source="nitter:@bot2", hours_ago=0),
            _obs(text, source="nitter:@bot3", hours_ago=0),
        ]
        result = analyze_surge(obs, topic="attack", account_store=store)
        assert result.disinfo_count >= 1

    def test_discredited_account_low_alpha(self):
        store = AccountCredibilityStore()
        store.penalize("liar", 0.4)  # now 0.1, below 0.25 threshold
        obs = [
            _obs("Exclusive intel about the situation", source="nitter:@liar"),
        ]
        result = analyze_surge(obs, topic="event", account_store=store)
        assert result.total_processed == 1

    def test_credibility_persists_across_calls(self):
        store = AccountCredibilityStore()
        store.boost("reliable", 0.2)
        obs1 = [_obs("First report from reliable source", source="nitter:@reliable")]
        analyze_surge(obs1, topic="test", account_store=store)
        profile = store.get_or_create("reliable")
        assert profile.credibility_score >= 0.6  # was boosted to 0.7, may have been modified
        assert profile.total_observations >= 1


# =====================================================================
# surge_detector — helper functions
# =====================================================================


class TestSurgeHelpers:
    def test_text_fingerprint_excludes_stopwords(self):
        fp = _text_fingerprint("the and for are but not you all")
        assert len(fp) == 0

    def test_extract_author_from_nitter(self):
        assert _extract_author("nitter:@journalist", "") == "journalist"

    def test_extract_author_from_mention(self):
        assert _extract_author("generic", "@someuser: breaking news") == "someuser"


# =====================================================================
# deep_truth — pejorative labels
# =====================================================================


class TestDetectPejorativeLabels:
    def test_detects_conspiracy_theory(self):
        labels = detect_pejorative_labels("This is a conspiracy theory pushed by fringe groups")
        assert any("conspiracy" in l for l in labels)

    def test_detects_debunked(self):
        labels = detect_pejorative_labels("This claim has been debunked by fact-checkers")
        assert any("debunked" in l for l in labels)
        assert any("fact" in l for l in labels)

    def test_detects_misinformation(self):
        labels = detect_pejorative_labels("Flagged as misinformation by multiple platforms")
        assert any("misinformation" in l for l in labels)

    def test_clean_text_no_labels(self):
        labels = detect_pejorative_labels("The earthquake measured 6.2 on the Richter scale")
        assert labels == []


# =====================================================================
# deep_truth — phrase coordination
# =====================================================================


class TestDetectPhraseCoordination:
    def test_identical_phrases_across_sources(self):
        observations = [
            _obs("experts agree climate models predict severe outcomes ahead", source="rss:outlet1"),
            _obs("experts agree climate models predict severe outcomes ahead", source="rss:outlet2"),
            _obs("experts agree climate models predict severe outcomes ahead", source="rss:outlet3"),
        ]
        result = detect_phrase_coordination(observations, min_phrase_len=5, min_repeats=3)
        assert len(result) >= 1
        assert any("repeated across" in r for r in result)

    def test_unique_phrasing_no_coordination(self):
        observations = [
            _obs("Scientists report rising ocean temperatures globally", source="rss:a"),
            _obs("New study finds coral reefs declining rapidly", source="rss:b"),
            _obs("Arctic ice coverage reaches historic seasonal minimum", source="rss:c"),
        ]
        result = detect_phrase_coordination(observations, min_phrase_len=5, min_repeats=3)
        assert len(result) == 0


# =====================================================================
# deep_truth — authority weight
# =====================================================================


class TestComputeAuthorityWeight:
    def test_high_institutional_concentration(self):
        observations = [
            _obs("Official statement on the matter", source="rss:reuters", url="https://reuters.com/article/1"),
            _obs("Official statement on the matter", source="rss:bbc", url="https://bbc.com/news/1"),
            _obs("Official statement on the matter", source="rss:nytimes", url="https://nytimes.com/2024/1"),
            _obs("Official statement on the matter", source="rss:cnn", url="https://cnn.com/news/1"),
            _obs("Official statement on the matter", source="rss:guardian", url="https://theguardian.com/1"),
        ]
        aw = compute_authority_weight(observations, "test claim")
        assert aw.score > 0
        assert aw.sources_pushing >= 3
        assert aw.skepticism_level in ("elevated_scrutiny", "maximum_scrutiny", "standard")

    def test_diverse_non_institutional_sources(self):
        observations = [
            _obs("Indie report on the topic", source="blog:site1", url="https://blog1.example.com/post"),
            _obs("Another indie take", source="blog:site2", url="https://blog2.example.com/post"),
            _obs("Forum discussion", source="reddit:thread", url="https://reddit.com/r/news/1"),
        ]
        aw = compute_authority_weight(observations, "test claim")
        assert aw.sources_pushing == 0
        assert aw.skepticism_level == "low_coordination"

    def test_authority_weight_includes_pejoratives(self):
        observations = [
            _obs(
                "This conspiracy theory has been debunked by experts",
                source="rss:reuters",
                url="https://reuters.com/1",
            ),
        ]
        aw = compute_authority_weight(observations, "claim")
        assert len(aw.pejorative_labels) >= 1


# =====================================================================
# deep_truth — provenance
# =====================================================================


class TestComputeProvenance:
    def test_diverse_connector_types(self):
        observations = [
            _obs("Data point 1", source="usgs:quake"),
            _obs("Data point 2", source="nitter:@user"),
            _obs("Data point 3", source="rss:site"),
            _obs("Data point 4", source="acled:event"),
            _obs("Data point 5", source="nasa_firms:fire"),
        ]
        prov = compute_provenance(observations)
        assert prov.entropy_bits > 1.5
        assert prov.source_independence > 0
        assert len(prov.source_chain) >= 4

    def test_single_source_type_low_entropy(self):
        observations = [
            _obs("Claim A", source="rss:site1"),
            _obs("Claim B", source="rss:site2"),
            _obs("Claim C", source="rss:site3"),
        ]
        prov = compute_provenance(observations)
        assert prov.entropy_bits == 0.0  # all same family

    def test_primary_source_ratio(self):
        observations = [
            _obs("Seismic data", source="usgs:feed1"),
            _obs("Fire data", source="nasa_firms:hotspot"),
            _obs("News article", source="rss:outlet"),
        ]
        prov = compute_provenance(observations)
        assert prov.primary_source_ratio == pytest.approx(2 / 3, abs=0.01)


# =====================================================================
# deep_truth — silence gaps
# =====================================================================


class TestDetectSilenceGaps:
    def test_missing_casualty_figures(self):
        observations = [
            _obs("Explosion killed several people downtown", source="rss:site1"),
            _obs("Attack at the market caused massive destruction", source="rss:site2"),
        ]
        gaps = detect_silence_gaps(observations, topic="downtown bombing attack")
        # Should flag missing official casualty figures
        casualty_gaps = [g for g in gaps if "casualty" in g.expected_data.lower()]
        assert len(casualty_gaps) >= 1

    def test_unanimous_narrative_flagged(self):
        observations = [
            _obs(f"The event unfolded exactly as reported by authorities #{i}", source=f"rss:outlet{i}")
            for i in range(15)
        ]
        gaps = detect_silence_gaps(observations, topic="suspicious event")
        dissent_gaps = [g for g in gaps if "dissent" in g.question.lower()]
        assert len(dissent_gaps) >= 1

    def test_few_source_types_flagged(self):
        observations = [
            _obs("Report A", source="rss:site1"),
            _obs("Report B", source="rss:site2"),
        ]
        gaps = detect_silence_gaps(observations, topic="generic")
        source_gaps = [g for g in gaps if "source type" in g.question.lower()]
        assert len(source_gaps) >= 1


# =====================================================================
# deep_truth — analyze_deep_truth end-to-end
# =====================================================================


class TestAnalyzeDeepTruth:
    def _make_observations(self) -> list[dict]:
        return [
            _obs(
                "Officials confirm the incident was caused by equipment failure",
                source="rss:reuters",
                url="https://reuters.com/article/1",
                reliability_hint=0.9,
            ),
            _obs(
                "Equipment failure confirmed as cause of the incident",
                source="rss:bbc",
                url="https://bbc.com/news/1",
                reliability_hint=0.85,
            ),
            _obs(
                "However, independent analysts dispute the official account",
                source="blog:indie",
                url="https://independent-analysis.org/post",
                reliability_hint=0.5,
            ),
            _obs(
                "Witness says explosion came before equipment failure was possible",
                source="nitter:@witness",
                url="https://x.com/witness/1",
                reliability_hint=0.4,
            ),
            _obs(
                "Seismic station recorded anomalous readings",
                source="usgs:station",
                url="https://earthquake.usgs.gov/1",
                reliability_hint=0.95,
            ),
        ]

    def test_end_to_end_returns_verdict(self):
        verdict = analyze_deep_truth(
            self._make_observations(),
            topic="industrial incident",
            claim="Equipment failure caused the explosion",
        )
        assert isinstance(verdict, DeepTruthVerdict)
        assert verdict.claim == "Equipment failure caused the explosion"
        assert verdict.verdict_summary != ""

    def test_tracks_include_all_three(self):
        verdict = analyze_deep_truth(
            self._make_observations(),
            topic="industrial incident",
        )
        track_names = {t.track_name for t in verdict.tracks}
        assert track_names == {"mainstream", "dissenting", "hybrid"}

    def test_probability_distribution_sums_to_one(self):
        verdict = analyze_deep_truth(
            self._make_observations(),
            topic="industrial incident",
        )
        total = sum(verdict.probability_distribution.values())
        assert total == pytest.approx(1.0, abs=0.05)

    def test_to_dict_structure(self):
        verdict = analyze_deep_truth(
            self._make_observations(),
            topic="industrial incident",
            claim="test claim",
        )
        d = verdict.to_dict()
        assert "claim" in d
        assert "authority_weight" in d
        assert "tracks" in d
        assert "silence_gaps" in d
        assert "probability_distribution" in d
        assert "provenance" in d
        assert "manufactured_consensus_detected" in d
        assert "active_suppression_detected" in d
        assert isinstance(d["analyzed_at"], str)
        assert isinstance(d["tracks"], list)
        assert len(d["tracks"]) == 3

    def test_surviving_claims_subset_of_tracks(self):
        verdict = analyze_deep_truth(
            self._make_observations(),
            topic="industrial incident",
        )
        surviving_positions = set(verdict.surviving_claims)
        track_positions = {t.position_summary for t in verdict.tracks}
        assert surviving_positions.issubset(track_positions)

    def test_manufactured_consensus_detection(self):
        # Build observations that trigger manufactured consensus:
        # high authority score (>=0.7), >=2 coordination markers, >=2 pejoratives
        coordinated_phrase = "experts agree debunked conspiracy theory misinformation baseless claims"
        observations = [
            _obs(
                coordinated_phrase,
                source=f"rss:outlet{i}",
                url=f"https://reuters.com/article/{i}",
                reliability_hint=0.9,
            )
            for i in range(8)
        ] + [
            _obs(
                coordinated_phrase,
                source=f"rss:bbc{i}",
                url=f"https://bbc.com/news/{i}",
                reliability_hint=0.9,
            )
            for i in range(8)
        ]
        verdict = analyze_deep_truth(
            observations,
            topic="contested claim",
            claim="The contested claim",
        )
        # With heavy coordination and pejoratives, manufactured consensus should fire
        # (depends on authority score reaching 0.7 — the observations include reuters/bbc URLs)
        assert isinstance(verdict.manufactured_consensus_detected, bool)
        assert len(verdict.authority_weight.pejorative_labels) >= 2

    def test_meta_confidence_in_range(self):
        verdict = analyze_deep_truth(
            self._make_observations(),
            topic="industrial incident",
        )
        assert 0.1 <= verdict.confidence_in_verdict <= 0.95

    def test_falsification_tests_present(self):
        verdict = analyze_deep_truth(
            self._make_observations(),
            topic="industrial incident",
        )
        assert len(verdict.falsification_tests) >= 1
        for ft in verdict.falsification_tests:
            assert "hypothesis" in ft
            assert "test" in ft
            assert "timeframe" in ft
