import pytest
import sqlalchemy
from unittest.mock import MagicMock, patch
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sts_monitor.database import Base, engine, SessionLocal
from sts_monitor.models import *


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch):
    import sts_monitor.rate_limit as rl
    monkeypatch.setattr(rl, "RATE_LIMIT_ENABLED", False)
    # Ensure tables exist
    Base.metadata.create_all(bind=engine, checkfirst=True)
    # Truncate all tables (faster and avoids drop/create race conditions)
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text("PRAGMA foreign_keys = OFF"))
        tables = conn.execute(sqlalchemy.text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )).scalars().all()
        for t in tables:
            conn.execute(sqlalchemy.text(f'DELETE FROM "{t}"'))
        conn.execute(sqlalchemy.text("PRAGMA foreign_keys = ON"))
        conn.commit()


# ═══════════════════════════════════════════════════════════════════════
# 1. surge_detector.py
# ═══════════════════════════════════════════════════════════════════════

class TestSurgeDetector:

    def test_account_profile_accuracy_rate(self):
        """Lines 51-52: accuracy_rate property."""
        from sts_monitor.surge_detector import AccountProfile
        now = datetime.now(UTC)
        p = AccountProfile(
            handle="test", display_name="Test", credibility_score=0.5,
            total_observations=10, correct_calls=7, incorrect_calls=3,
            first_seen=now, last_seen=now, tags=[], specialties=[], flags=[], decay_applied=0.0,
        )
        assert p.accuracy_rate == pytest.approx(0.7)
        # Zero total => 0.5
        p2 = AccountProfile(
            handle="t2", display_name="T2", credibility_score=0.5,
            total_observations=0, correct_calls=0, incorrect_calls=0,
            first_seen=now, last_seen=now, tags=[], specialties=[], flags=[], decay_applied=0.0,
        )
        assert p2.accuracy_rate == 0.5

    def test_is_trusted(self):
        """Line 56: is_trusted property."""
        from sts_monitor.surge_detector import AccountProfile
        now = datetime.now(UTC)
        p = AccountProfile(
            handle="t", display_name="T", credibility_score=0.65,
            total_observations=0, correct_calls=0, incorrect_calls=0,
            first_seen=now, last_seen=now, tags=[], specialties=[], flags=[], decay_applied=0.0,
        )
        assert p.is_trusted is True
        p.credibility_score = 0.64
        assert p.is_trusted is False

    def test_get_account_store(self):
        """Line 210: get_account_store returns global store."""
        from sts_monitor.surge_detector import get_account_store, AccountCredibilityStore
        store = get_account_store()
        assert isinstance(store, AccountCredibilityStore)

    def test_detect_ai_elevated_markers(self):
        """Lines 259-260: elevated ai markers branch."""
        from sts_monitor.surge_detector import detect_ai_content
        # Create text with ~3% AI marker density (elevated but not high)
        words = ["hello"] * 30 + ["delve"]
        text = " ".join(words)
        score, flags = detect_ai_content(text)
        assert any("elevated_ai_markers" in f for f in flags)

    def test_detect_ai_clean_long_text(self):
        """Lines 270-271: suspiciously clean long text."""
        from sts_monitor.surge_detector import detect_ai_content
        # Over 100 words, no special chars
        text = " ".join(["word"] * 110)
        score, flags = detect_ai_content(text)
        assert any("suspiciously_clean_long_text" in f for f in flags)

    def test_detect_coordination_time_skip(self):
        """Line 380: skip when time diff > window; line 384: empty fingerprint."""
        from sts_monitor.surge_detector import SurgeTweet, detect_coordination
        now = datetime.now(UTC)
        t1 = SurgeTweet(tweet_id="1", author="a", text="hello world foo bar", url="", posted_at=now, source_tag="")
        t2 = SurgeTweet(tweet_id="2", author="b", text="hello world foo bar", url="", posted_at=now + timedelta(seconds=60), source_tag="")
        # time_diff=60 > default window=30, so should be skipped
        result = detect_coordination([t1, t2], time_window_seconds=30)
        assert len(result) == 0

    def test_detect_coordination_empty_fingerprint(self):
        """Line 384: empty fingerprint branch."""
        from sts_monitor.surge_detector import SurgeTweet, detect_coordination
        now = datetime.now(UTC)
        t1 = SurgeTweet(tweet_id="1", author="a", text="a b", url="", posted_at=now, source_tag="")
        t2 = SurgeTweet(tweet_id="2", author="b", text="x y", url="", posted_at=now, source_tag="")
        result = detect_coordination([t1, t2])
        assert len(result) == 0

    def test_novelty_score_empty_seen_fp(self):
        """Line 351: novelty_score with empty seen fingerprints in list."""
        from sts_monitor.surge_detector import novelty_score
        score = novelty_score("some interesting text here", [set()])
        # Empty seen_fp => continue, so max_overlap stays 0 => novelty=1.0
        assert score == 1.0

    def test_analyze_surge_captured_at_string_conversion(self):
        """Lines 497-500: captured_at as string, and invalid string."""
        from sts_monitor.surge_detector import analyze_surge, AccountCredibilityStore
        store = AccountCredibilityStore()
        obs = [
            {"source": "nitter:@user1", "claim": "I saw explosion downtown", "url": "http://x.com/1",
             "captured_at": datetime.now(UTC).isoformat()},
            {"source": "nitter:@user2", "claim": "Big fire reported now", "url": "http://x.com/2",
             "captured_at": "not-a-date"},
        ]
        result = analyze_surge(obs, topic="test", account_store=store)
        assert result.total_processed == 2

    def test_analyze_surge_with_surge_event(self):
        """Lines 608-623: Build surge events when surge detected."""
        from sts_monitor.surge_detector import analyze_surge, AccountCredibilityStore
        store = AccountCredibilityStore()
        now = datetime.now(UTC)
        obs = []
        for i in range(15):
            obs.append({
                "source": f"nitter:@user{i}",
                "claim": f"I witnessed the event firsthand at scene number {i} unique text here alpha",
                "url": f"http://x.com/{i}",
                "captured_at": now + timedelta(seconds=i * 5),
            })
        result = analyze_surge(obs, topic="test", account_store=store, alpha_threshold=0.3, novelty_threshold=0.1)
        assert result.total_processed == 15
        if result.surge_detected:
            assert len(result.surges) > 0
            assert result.surges[0].summary

    def test_extract_author_from_source_with_at(self):
        """Line 660: extract author when source has '@'."""
        from sts_monitor.surge_detector import _extract_author
        author = _extract_author("nitter:@johndoe", "some text")
        assert author == "johndoe"

    def test_extract_author_from_text_mention(self):
        """Line 671: extract author from @mention in text."""
        from sts_monitor.surge_detector import _extract_author
        author = _extract_author("some_source", "I saw @janedoe posting about this")
        assert author == "janedoe"

    def test_build_surge_summary(self):
        """Lines 683-694: build surge summary with alpha and noise tweets."""
        from sts_monitor.surge_detector import _build_surge_summary, SurgeTweet
        now = datetime.now(UTC)
        alpha = [SurgeTweet(tweet_id="1", author="a", text="Alpha tweet", url="", posted_at=now, source_tag="", alpha_score=0.9)]
        noise = [SurgeTweet(tweet_id="2", author="b", text="Noise tweet", url="", posted_at=now, source_tag="")]
        window = {"count": 50, "velocity": 5.0, "unique_authors": 10}
        summary = _build_surge_summary("test_topic", window, alpha, noise)
        assert "test_topic" in summary
        assert "Alpha tweet" in summary
        assert "Filtered" in summary


# ═══════════════════════════════════════════════════════════════════════
# 2. knowledge_graph.py
# ═══════════════════════════════════════════════════════════════════════

class TestKnowledgeGraph:

    def _make_investigation(self, session, inv_id="inv-1", topic="test topic"):
        inv = InvestigationORM(id=inv_id, topic=topic, status="open", priority=50)
        session.add(inv)
        session.flush()
        return inv

    def _make_observation(self, session, inv_id, claim="test claim", source="rss:test"):
        obs = ObservationORM(
            investigation_id=inv_id, source=source, claim=claim,
            url="http://example.com", reliability_hint=0.5, connector_type="rss",
        )
        session.add(obs)
        session.flush()
        return obs

    def test_add_edge_dedup(self):
        """Line 149: duplicate edge is skipped."""
        from sts_monitor.knowledge_graph import build_knowledge_graph
        session = SessionLocal()
        try:
            self._make_investigation(session)
            session.commit()
            kg = build_knowledge_graph(session, investigation_ids=["inv-1"])
            assert kg.node_count >= 1
        finally:
            session.close()

    def test_entity_co_occurrence(self):
        """Lines 250, 253-256: entity co-occurrence edges."""
        from sts_monitor.knowledge_graph import build_knowledge_graph
        # Ensure entity_mentions table exists
        Base.metadata.create_all(bind=engine, checkfirst=True)
        session = SessionLocal()
        try:
            inv = self._make_investigation(session)
            obs1 = self._make_observation(session, "inv-1", "Entity Alpha and Entity Beta")
            obs2 = self._make_observation(session, "inv-1", "Entity Alpha and Entity Beta again")
            for obs in [obs1, obs2]:
                session.add(EntityMentionORM(
                    observation_id=obs.id, investigation_id="inv-1",
                    entity_text="alpha", entity_type="PERSON", normalized="alpha",
                    confidence=0.9,
                ))
                session.add(EntityMentionORM(
                    observation_id=obs.id, investigation_id="inv-1",
                    entity_text="beta", entity_type="PERSON", normalized="beta",
                    confidence=0.9,
                ))
            session.commit()
            kg = build_knowledge_graph(session, investigation_ids=["inv-1"], min_entity_mentions=2)
            edge_types = [e.edge_type for e in kg.edges]
            assert "co_occurs" in edge_types
        finally:
            session.close()

    def test_story_entities_json_parsing(self):
        """Lines 290-291, 297, 300: story entity JSON parsing (invalid JSON, no-colon entity)."""
        from sts_monitor.knowledge_graph import build_knowledge_graph
        session = SessionLocal()
        try:
            inv = self._make_investigation(session)
            obs = self._make_observation(session, "inv-1")
            for i in range(3):
                session.add(EntityMentionORM(
                    observation_id=obs.id, investigation_id="inv-1",
                    entity_text="alpha", entity_type="PERSON", normalized="alpha", confidence=0.9,
                ))
            story1 = StoryORM(
                investigation_id="inv-1", headline="Story 1",
                entities_json="not valid json",
                source_count=2, observation_count=5, avg_reliability=0.7,
                trending_score=1.0,
                first_seen=datetime.now(UTC), last_seen=datetime.now(UTC),
            )
            story2 = StoryORM(
                investigation_id="inv-1", headline="Story 2",
                entities_json='["alpha", "PERSON:alpha"]',
                source_count=2, observation_count=3, avg_reliability=0.6,
                trending_score=0.5,
                first_seen=datetime.now(UTC), last_seen=datetime.now(UTC),
            )
            session.add_all([story1, story2])
            session.commit()
            kg = build_knowledge_graph(session, investigation_ids=["inv-1"], min_entity_mentions=1)
            assert kg.node_count >= 1
        finally:
            session.close()

    def test_claims_and_zones(self):
        """Lines 312-325, 337-359: claim and convergence zone nodes."""
        from sts_monitor.knowledge_graph import build_knowledge_graph
        session = SessionLocal()
        try:
            inv = self._make_investigation(session)
            report = ReportORM(
                investigation_id="inv-1", summary="test", confidence=0.8,
                accepted_json="[]", dropped_json="[]",
            )
            session.add(report)
            session.flush()
            claim = ClaimORM(
                investigation_id="inv-1", report_id=report.id,
                claim_text="Test claim text", stance="confirmed", confidence=0.9,
            )
            session.add(claim)
            zone = ConvergenceZoneORM(
                center_lat=40.0, center_lon=-74.0, radius_km=50.0,
                signal_count=5, signal_types_json='["earthquake", "fire"]',
                severity="high",
                first_detected_at=datetime.now(UTC),
                last_updated_at=datetime.now(UTC),
                investigation_id="inv-1",
            )
            session.add(zone)
            session.commit()
            kg = build_knowledge_graph(session, investigation_ids=["inv-1"])
            node_types = [n.node_type for n in kg.nodes]
            assert "claim" in node_types
            assert "convergence_zone" in node_types
        finally:
            session.close()

    def test_zone_bad_signal_types_json(self):
        """Lines 337-341: zone with bad signal_types_json."""
        from sts_monitor.knowledge_graph import build_knowledge_graph
        session = SessionLocal()
        try:
            inv = self._make_investigation(session)
            zone = ConvergenceZoneORM(
                center_lat=10.0, center_lon=20.0, radius_km=30.0,
                signal_count=2, signal_types_json="bad json!",
                severity="low",
                first_detected_at=datetime.now(UTC),
                last_updated_at=datetime.now(UTC),
                investigation_id="inv-1",
            )
            session.add(zone)
            session.commit()
            kg = build_knowledge_graph(session, investigation_ids=["inv-1"])
            zone_nodes = [n for n in kg.nodes if n.node_type == "convergence_zone"]
            assert len(zone_nodes) == 1
            assert zone_nodes[0].properties["signal_types"] == []
        finally:
            session.close()


# ═══════════════════════════════════════════════════════════════════════
# 3. comparative.py
# ═══════════════════════════════════════════════════════════════════════

class TestComparative:

    def test_contradiction_to_dict(self):
        """Line 44: Contradiction.to_dict."""
        from sts_monitor.comparative import Contradiction
        now = datetime.now(UTC)
        c = Contradiction(
            claim_a="Claim A", source_a="src_a",
            claim_b="Claim B", source_b="src_b",
            contradiction_type="direct", confidence=0.8,
            description="test", captured_at_a=now, captured_at_b=now,
        )
        d = c.to_dict()
        assert d["type"] == "direct"
        assert d["time_a"] is not None

    def test_silence_event_to_dict(self):
        """Line 89: SilenceEvent.to_dict."""
        from sts_monitor.comparative import SilenceEvent
        now = datetime.now(UTC)
        s = SilenceEvent(source="src", last_seen=now, hours_silent=24.5,
                         was_reporting_topic="topic", avg_prior_frequency_per_day=3.0)
        d = s.to_dict()
        assert d["hours_silent"] == 24.5

    def test_claim_similarity_empty(self):
        """Line 153: empty claim similarity."""
        from sts_monitor.comparative import _claim_similarity
        assert _claim_similarity("", "hello world") == 0.0
        assert _claim_similarity("the a an", "is are was") == 0.0

    def test_source_family_prefix_strip(self):
        """Lines 175, 179: _source_family strips prefixes and paths."""
        from sts_monitor.comparative import _source_family
        assert _source_family("rss:https://www.example.com/feed") == "example.com"
        assert _source_family("nitter:@handle") == "@handle"

    def test_detect_contradictions_direct(self):
        """Lines 192, 201-203, 218, 236: contradiction detection."""
        from sts_monitor.comparative import detect_contradictions
        now = datetime.now(UTC)
        obs = [
            {"source": "rss:source_a", "claim": "The explosion was confirmed by authorities at location downtown verified",
             "captured_at": now, "id": 1, "reliability_hint": 0.8, "url": ""},
            {"source": "rss:source_b", "claim": "The explosion was denied not confirmed false authorities downtown location",
             "captured_at": now, "id": 2, "reliability_hint": 0.7, "url": ""},
        ]
        result = detect_contradictions(obs)
        assert len(result) >= 1

    def test_detect_contradictions_ts_none(self):
        """Line 201-203: captured_at is None."""
        from sts_monitor.comparative import detect_contradictions
        obs = [
            {"source": "a", "claim": "Big event happened today confirmed verified", "captured_at": None},
            {"source": "b", "claim": "Big event happened today denied not true false", "captured_at": None},
        ]
        result = detect_contradictions(obs)
        # Should not crash

    def test_detect_contradictions_framing(self):
        """Line 236: framing contradiction (similarity > 0.4, one negation)."""
        from sts_monitor.comparative import detect_contradictions
        now = datetime.now(UTC)
        obs = [
            {"source": "src_a", "claim": "military convoy spotted heading north carrying weapons soldiers troops",
             "captured_at": now, "id": 1, "url": ""},
            {"source": "src_b", "claim": "military convoy not spotted heading north carrying weapons soldiers troops denied",
             "captured_at": now, "id": 2, "url": ""},
        ]
        result = detect_contradictions(obs)
        assert len(result) >= 1

    def test_detect_agreements(self):
        """Lines 256, 277-278: agreements with datetime handling."""
        from sts_monitor.comparative import detect_agreements
        now = datetime.now(UTC)
        obs = [
            {"source": "rss:src_a", "claim": "earthquake magnitude seven struck region today",
             "captured_at": now.isoformat(), "reliability_hint": 0.8},
            {"source": "rss:src_b", "claim": "earthquake magnitude seven struck region today",
             "captured_at": now, "reliability_hint": 0.9},
        ]
        result = detect_agreements(obs, min_sources=2)
        assert len(result) >= 1

    def test_detect_agreements_short_claim(self):
        """Line 256: short claim skipped."""
        from sts_monitor.comparative import detect_agreements
        obs = [
            {"source": "src_a", "claim": "hi"},
            {"source": "src_b", "claim": "hi"},
        ]
        result = detect_agreements(obs, min_sources=2)
        assert len(result) == 0

    def test_detect_silences(self):
        """Lines 313-315, 335, 339: silence detection."""
        from sts_monitor.comparative import detect_silences
        now = datetime.now(UTC)
        # Source was active over several days, last seen 48+ hours ago
        # Need: time_span >= 1 day, freq >= min_prior_frequency, hours_since >= threshold
        obs = []
        # 10 observations spread over 3 days, ending 48 hours ago
        last_seen = now - timedelta(hours=48)
        for i in range(10):
            obs.append({
                "source": "rss:active_src",
                "claim": "Report about topic today important news details",
                "captured_at": last_seen - timedelta(days=3) + timedelta(hours=i * 7),
            })
        silences = detect_silences(obs, silence_threshold_hours=12, min_prior_frequency=0.5)
        assert len(silences) >= 1

    def test_detect_silences_none_ts(self):
        """Line 313: Non-datetime captured_at (integer) => skipped."""
        from sts_monitor.comparative import detect_silences
        obs = [{"source": "src", "claim": "something important here today", "captured_at": 12345}]
        result = detect_silences(obs)
        assert result == []

    def test_run_comparative_analysis(self):
        """Full run_comparative_analysis."""
        from sts_monitor.comparative import run_comparative_analysis
        now = datetime.now(UTC)
        obs = [
            {"source": "rss:source_a", "claim": "Large earthquake confirmed by authorities region struck today",
             "captured_at": now},
            {"source": "reddit:source_b", "claim": "Large earthquake denied false by authorities region struck today",
             "captured_at": now},
        ]
        report = run_comparative_analysis(obs, silence_threshold_hours=1)
        assert report.total_observations == 2
        assert report.total_sources >= 1


# ═══════════════════════════════════════════════════════════════════════
# 4. rabbit_trail.py
# ═══════════════════════════════════════════════════════════════════════

class TestRabbitTrail:

    def test_run_rabbit_trail_contradictions(self):
        """Lines 245-252: contradiction detection in rabbit trail."""
        from sts_monitor.rabbit_trail import run_rabbit_trail
        obs = []
        claim_text = "the same claim key words first"
        for i in range(3):
            obs.append({
                "source": f"source_{i}",
                "claim": claim_text if i == 0 else f"not false denied {claim_text}",
            })
        result = run_rabbit_trail("inv-1", "topic", obs, ["entity1"])
        assert result.status == "completed"
        assert result.total_contradictions >= 1

    def test_run_rabbit_trail_llm_success(self):
        """Lines 316-332: LLM analysis path."""
        from sts_monitor.rabbit_trail import run_rabbit_trail
        llm = MagicMock()
        llm.summarize.return_value = (
            "# Analysis\n"
            "This is a key finding line that is long enough to be captured by the filter.\n"
            "Another key finding that gives insight into the investigation topic.\n"
        )
        obs = [{"source": "src", "claim": "allegedly leaked documents revealed cover up"}]
        result = run_rabbit_trail("inv-1", "topic", obs, ["entity1"], llm_client=llm)
        assert len(result.key_findings) >= 1

    def test_run_rabbit_trail_llm_failure(self):
        """Lines 333-343 (via except): LLM fails."""
        from sts_monitor.rabbit_trail import run_rabbit_trail
        llm = MagicMock()
        llm.summarize.side_effect = RuntimeError("LLM down")
        obs = [{"source": "src", "claim": "allegedly something"}]
        result = run_rabbit_trail("inv-1", "topic", obs, ["entity1"], llm_client=llm)
        assert any("unavailable" in s.description.lower() for s in result.steps)

    def test_run_rabbit_trail_heuristic_findings(self):
        """Lines 347: heuristic key findings when no LLM and contradiction count > 3."""
        from sts_monitor.rabbit_trail import run_rabbit_trail
        obs = []
        for i in range(10):
            claim_key = "same claim key words first"
            if i % 2 == 0:
                obs.append({"source": f"source_{i}", "claim": claim_key})
            else:
                obs.append({"source": f"source_{i}", "claim": f"not false denied {claim_key}"})
        result = run_rabbit_trail("inv-1", "topic", obs, ["entity1", "entity2"])
        assert len(result.key_findings) >= 1

    def test_store_trail_session_overflow(self):
        """Lines 376-378: store_trail_session trims when > 100."""
        from sts_monitor.rabbit_trail import (
            store_trail_session, _trail_sessions, TrailSession,
        )
        _trail_sessions.clear()
        for i in range(105):
            s = TrailSession(
                session_id=f"trail-{i:04d}", investigation_id="inv",
                topic="t", started_at=datetime.now(UTC).isoformat(),
            )
            store_trail_session(s)
        assert len(_trail_sessions) <= 55
        _trail_sessions.clear()


# ═══════════════════════════════════════════════════════════════════════
# 5. source_network.py
# ═══════════════════════════════════════════════════════════════════════

class TestSourceNetwork:

    def test_source_family_prefix(self):
        """Lines 111, 115: _source_family strips prefix and path."""
        from sts_monitor.source_network import _source_family
        assert _source_family("rss:example.com/feed") == "example.com"
        assert _source_family("telegram:channel") == "channel"

    def test_analyze_source_network_with_co_reports(self):
        """Lines 128-134, 184, 209-212: co-reporting and edge building."""
        from sts_monitor.source_network import analyze_source_network
        now = datetime.now(UTC)
        obs = []
        for i in range(4):
            obs.append({"source": "rss:source_alpha", "claim": f"important event detail number {i} happened today here",
                        "captured_at": now + timedelta(hours=i), "reliability_hint": 0.8})
            obs.append({"source": "rss:source_beta", "claim": f"important event detail number {i} happened today here",
                        "captured_at": now + timedelta(hours=i, minutes=30), "reliability_hint": 0.7})

        report = analyze_source_network(obs, co_report_window_hours=6, min_co_reports=2)
        assert report.total_sources == 2
        assert len(report.edges) >= 1

    def test_coordinated_narratives(self):
        """Line 238: coordinated narrative detection with 3+ sources."""
        from sts_monitor.source_network import analyze_source_network
        now = datetime.now(UTC)
        obs = []
        claim = "breaking major event happened downtown according sources today report"
        for i in range(4):
            obs.append({
                "source": f"rss:source_{i}",
                "claim": claim,
                "captured_at": now + timedelta(minutes=i),
                "reliability_hint": 0.5,
            })
        report = analyze_source_network(obs, coordination_window_minutes=30)
        assert report.total_sources == 4
        assert len(report.coordinated_narratives) >= 1

    def test_leader_vs_co_reporter(self):
        """Lines 209-212: leader/co-reporter relationship type."""
        from sts_monitor.source_network import analyze_source_network
        now = datetime.now(UTC)
        obs = []
        for i in range(5):
            claim = f"unique claim number {i} about important event happening today"
            obs.append({"source": "rss:leader_src", "claim": claim, "captured_at": now + timedelta(hours=i)})
            obs.append({"source": "rss:follower_src", "claim": claim, "captured_at": now + timedelta(hours=i, minutes=60)})
        report = analyze_source_network(obs, co_report_window_hours=6, min_co_reports=2)
        if report.edges:
            rel_types = [e.relationship_type for e in report.edges]
            assert any(rt in ("leader", "co-reporter", "synchronized") for rt in rel_types)


# ═══════════════════════════════════════════════════════════════════════
# 6. source_scoring.py
# ═══════════════════════════════════════════════════════════════════════

class TestSourceScoring:

    def test_accuracy_rate_zero(self):
        """Lines 36: accuracy_rate with zero evaluated."""
        from sts_monitor.source_scoring import SourceScore
        s = SourceScore(source="test")
        assert s.accuracy_rate == 0.5

    def test_reliability_grades(self):
        """Lines 44, 48, 54: reliability_grade property for various scores."""
        from sts_monitor.source_scoring import SourceScore
        s = SourceScore(source="test")
        s.raw_score = 0.9
        assert s.reliability_grade == "A"
        s.raw_score = 0.75
        assert s.reliability_grade == "B"
        s.raw_score = 0.55
        assert s.reliability_grade == "C"
        s.raw_score = 0.35
        assert s.reliability_grade == "D"
        s.raw_score = 0.2
        assert s.reliability_grade == "F"

    def test_days_active(self):
        """Lines 79, 85: days_active property."""
        from sts_monitor.source_scoring import SourceScore
        now = datetime.now(UTC)
        s = SourceScore(source="test", first_seen=now - timedelta(days=10), last_seen=now)
        assert s.days_active == 10
        s2 = SourceScore(source="test2")
        assert s2.days_active == 0

    def test_compute_source_scores_timestamps(self):
        """Lines 115, 120-121, 130: timestamp parsing branches."""
        from sts_monitor.source_scoring import compute_source_scores
        now = datetime.now(UTC)
        obs = []
        for i in range(5):
            obs.append({
                "source": "rss:source_a",
                "claim": f"unique claim {i} about the event today",
                "captured_at": now.isoformat(),
                "reliability_hint": 0.8,
            })
        for i in range(5):
            obs.append({
                "source": "rss:source_a",
                "claim": f"another claim {i} about the event today",
                "captured_at": now,
                "reliability_hint": 0.7,
            })
        obs.append({"source": "rss:source_a", "claim": "more claim info", "captured_at": None})
        obs.append({"source": "rss:source_b", "claim": "another source", "captured_at": "invalid"})
        results = compute_source_scores(obs, min_observations=3)
        assert len(results) >= 1

    def test_compute_source_scores_disputed(self):
        """Lines 142-144: disputed claim counting."""
        from sts_monitor.source_scoring import compute_source_scores
        now = datetime.now(UTC)
        obs = [
            {"source": "rss:src", "claim": "false debunked claim retracted", "captured_at": now},
            {"source": "rss:src", "claim": "hoax misleading information", "captured_at": now},
            {"source": "rss:src", "claim": "normal claim number three here", "captured_at": now},
            {"source": "rss:src2", "claim": "false debunked claim retracted", "captured_at": now},
        ]
        results = compute_source_scores(obs, min_observations=1)
        src_score = next(r for r in results if r.source == "src")
        assert src_score.disputed_count >= 1

    def test_source_leaderboard(self):
        """Line 175: get_source_leaderboard runs full pipeline."""
        from sts_monitor.source_scoring import get_source_leaderboard
        now = datetime.now(UTC)
        obs = [
            {"source": "rss:s", "claim": f"claim {i} about events", "captured_at": now}
            for i in range(5)
        ]
        lb = get_source_leaderboard(obs)
        assert lb["total_sources"] >= 1
        assert "grade_distribution" in lb


# ═══════════════════════════════════════════════════════════════════════
# 7. pattern_matching.py
# ═══════════════════════════════════════════════════════════════════════

class TestPatternMatching:

    def test_compute_current_signature_no_valid_ts(self):
        """Lines 184: no valid timestamps."""
        from sts_monitor.pattern_matching import compute_current_signature
        obs = [{"claim": "something happened", "captured_at": None}]
        sig = compute_current_signature(obs)
        assert sig.name == "current"

    def test_compute_current_signature_empty_window(self):
        """Line 209: empty window => sentiment 0.0."""
        from sts_monitor.pattern_matching import compute_current_signature
        now = datetime.now(UTC)
        obs = [
            {"claim": "crisis attack", "captured_at": now, "source": "a"},
            {"claim": "peace resolved", "captured_at": now + timedelta(hours=5), "source": "b"},
        ]
        sig = compute_current_signature(obs)
        assert len(sig.observation_velocity) == 5

    def test_compute_current_signature_entity_concentration_empty(self):
        """Line 224: no words => entity_concentration=0."""
        from sts_monitor.pattern_matching import compute_current_signature
        obs = [{"claim": "", "captured_at": datetime.now(UTC), "source": "a"}]
        sig = compute_current_signature(obs)
        assert sig.entity_concentration == 0.0

    def test_escalation_score_zero_first(self):
        """Lines 234-235: velocity[0]==0 escalation path."""
        from sts_monitor.pattern_matching import compute_current_signature
        now = datetime.now(UTC)
        obs = [{"claim": "event happened now", "captured_at": now + timedelta(hours=4), "source": "a"} for _ in range(5)]
        obs.append({"claim": "start marker text", "captured_at": now, "source": "b"})
        sig = compute_current_signature(obs)
        assert sig.escalation_score >= 0.0

    def test_escalation_score_single_window(self):
        """Lines 237: single window => escalation=0."""
        from sts_monitor.pattern_matching import compute_current_signature
        sig = compute_current_signature([], window_count=1)
        assert sig.escalation_score == 0.0

    def test_cosine_similarity_different_lengths(self):
        """Lines 254-255: cosine similarity with different length vectors."""
        from sts_monitor.pattern_matching import _cosine_similarity
        a = [1.0, 2.0, 3.0]
        b = [1.0, 2.0]
        sim = _cosine_similarity(a, b)
        assert 0 <= sim <= 1.0

    def test_match_weak_similarity(self):
        """Lines 162-164: match below threshold skipped."""
        from sts_monitor.pattern_matching import match_against_known_patterns, PatternSignature
        current = PatternSignature(
            name="current", description="test",
            observation_velocity=[0, 0, 0, 0, 0],
            source_diversity=0.0, sentiment_trend=[0, 0, 0, 0, 0],
            entity_concentration=0.0, contradiction_rate=0.0, escalation_score=0.0,
        )
        matches = match_against_known_patterns(current, threshold=0.99)
        assert len(matches) == 0

    def test_analyze_patterns_full(self):
        """Full analyze_patterns run."""
        from sts_monitor.pattern_matching import analyze_patterns
        now = datetime.now(UTC)
        obs = [
            {"claim": "crisis attack killed emergency danger war conflict", "captured_at": now + timedelta(hours=i), "source": f"src{i}"}
            for i in range(10)
        ]
        result = analyze_patterns(obs, threshold=0.3)
        assert "current_signature" in result
        assert "matches" in result


# ═══════════════════════════════════════════════════════════════════════
# 8. cycle.py
# ═══════════════════════════════════════════════════════════════════════

class TestCycle:

    def _make_enrichment(self, anomaly_list=None, slop_total=0, slop_count=0, propaganda=0,
                         corr_total=0, corr_rate=0.5, convergence_zones=None):
        """Helper to build a valid EnrichmentResult."""
        from sts_monitor.enrichment import (
            EnrichmentResult, AnomalyReport, CorroborationResult, SlopFilterResult,
        )
        from sts_monitor.anomaly_detector import Anomaly

        anomalies_list = anomaly_list or []
        by_sev = {}
        for a in anomalies_list:
            by_sev[a.severity] = by_sev.get(a.severity, 0) + 1

        anomalies = AnomalyReport(
            detected_at=datetime.now(UTC),
            total_anomalies=len(anomalies_list),
            by_type={},
            by_severity=by_sev,
            anomalies=anomalies_list,
            baseline_window_hours=24,
            detection_window_hours=1,
        )
        corroboration = CorroborationResult(
            total_claims=corr_total,
            well_corroborated=0,
            partially_corroborated=0,
            single_source=corr_total,
            contested=0,
            scores=[],
            overall_corroboration_rate=corr_rate,
        )
        slop = SlopFilterResult(
            total=slop_total,
            credible=slop_total - slop_count - propaganda,
            suspicious=0,
            slop=slop_count,
            propaganda=propaganda,
            dropped_count=0,
            scores=[],
            pattern_stats={},
        )
        return EnrichmentResult(
            slop_filter=slop,
            entities=[],
            stories=[],
            corroboration=corroboration,
            convergence_zones=convergence_zones or [],
            anomalies=anomalies,
            entity_graph=MagicMock(),
            narrative=MagicMock(),
            discovered_topics=[],
            credible_observations=[],
            enrichment_started_at=datetime.now(UTC),
            enrichment_duration_ms=0.0,
        )

    def test_evaluate_alerts_anomaly_critical(self):
        """Lines 64, 67-69: critical anomaly alert."""
        from sts_monitor.cycle import evaluate_alerts
        from sts_monitor.anomaly_detector import Anomaly

        anomalies = [
            Anomaly(anomaly_type="volume", severity="critical", title="test critical",
                    description="d", metric_name="m", current_value=10.0,
                    baseline_mean=1.0, baseline_std=0.5, z_score=18.0,
                    detected_at=datetime.now(UTC), window_hours=1,
                    affected_entity="x", evidence=[], recommended_action="investigate"),
            Anomaly(anomaly_type="volume", severity="high", title="test high",
                    description="d2", metric_name="m2", current_value=5.0,
                    baseline_mean=1.0, baseline_std=0.5, z_score=8.0,
                    detected_at=datetime.now(UTC), window_hours=1,
                    affected_entity="y", evidence=[], recommended_action="monitor"),
            Anomaly(anomaly_type="volume", severity="low", title="test low",
                    description="d3", metric_name="m3", current_value=2.0,
                    baseline_mean=1.0, baseline_std=0.5, z_score=2.0,
                    detected_at=datetime.now(UTC), window_hours=1,
                    affected_entity="z", evidence=[], recommended_action="note"),
        ]
        enrichment = self._make_enrichment(anomaly_list=anomalies)
        pipeline_result = MagicMock()
        pipeline_result.accepted = []
        pipeline_result.disputed_claims = []
        alerts = evaluate_alerts(pipeline_result, enrichment)
        assert any(a.rule_name == "anomaly_critical" for a in alerts)

    def test_evaluate_alerts_anomaly_high(self):
        """Line 72: high anomaly (no critical) alert."""
        from sts_monitor.cycle import evaluate_alerts
        from sts_monitor.anomaly_detector import Anomaly

        anomalies = [
            Anomaly(anomaly_type="volume", severity="high", title=f"h{i}",
                    description="d", metric_name="m", current_value=5.0,
                    baseline_mean=1.0, baseline_std=0.5, z_score=8.0,
                    detected_at=datetime.now(UTC), window_hours=1,
                    affected_entity="x", evidence=[], recommended_action="monitor")
            for i in range(3)
        ]
        enrichment = self._make_enrichment(anomaly_list=anomalies)
        pipeline_result = MagicMock()
        pipeline_result.accepted = []
        pipeline_result.disputed_claims = []
        alerts = evaluate_alerts(pipeline_result, enrichment)
        assert any(a.rule_name == "anomaly_high" for a in alerts)

    def test_evaluate_alerts_user_rules_cooldown(self):
        """Lines 152-153, 164, 172-173: user-defined alert rules with cooldown."""
        from sts_monitor.cycle import evaluate_alerts
        enrichment = self._make_enrichment()
        pipeline_result = MagicMock()
        pipeline_result.accepted = list(range(25))
        pipeline_result.disputed_claims = list(range(3))

        rules = [
            {"name": "recent", "active": True, "min_observations": 1, "min_disputed_claims": 1,
             "cooldown_seconds": 9999, "last_triggered_at": datetime.now(UTC).isoformat()},
            {"name": "ready_to_fire", "active": True, "min_observations": 1, "min_disputed_claims": 1,
             "cooldown_seconds": 1, "last_triggered_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat()},
        ]
        alerts = evaluate_alerts(pipeline_result, enrichment, alert_rules=rules)
        names = [a.rule_name for a in alerts]
        assert "ready_to_fire" in names
        assert "recent" not in names

    def test_cycle_summary_surge_and_deep_truth(self):
        """Lines 295, 302, 304, 306: CycleResult.summary with surge and deep truth."""
        from sts_monitor.cycle import CycleResult, PromotedTopic, AlertFired
        from sts_monitor.surge_detector import SurgeAnalysisResult
        from sts_monitor.deep_truth import DeepTruthVerdict, AuthorityWeight, ProvenanceScore

        now = datetime.now(UTC)
        surge = SurgeAnalysisResult(
            surge_detected=True, surges=[MagicMock()], account_profiles={},
            total_processed=50, alpha_count=5, noise_count=10, disinfo_count=3,
            credibility_updates=[],
        )
        deep_truth = MagicMock(spec=DeepTruthVerdict)
        deep_truth.authority_weight = MagicMock()
        deep_truth.authority_weight.score = 0.7
        deep_truth.authority_weight.skepticism_level = "moderate"
        deep_truth.provenance = MagicMock()
        deep_truth.provenance.entropy_bits = 3.5
        deep_truth.manufactured_consensus_detected = True
        deep_truth.active_suppression_detected = True

        pipeline_result = MagicMock()
        pipeline_result.accepted = []
        pipeline_result.dropped = []
        pipeline_result.disputed_claims = []
        pipeline_result.confidence = 0.9
        enrichment = MagicMock()
        enrichment.slop_filter.credible = 10
        enrichment.slop_filter.slop = 2
        enrichment.slop_filter.propaganda = 1
        enrichment.entities = []
        enrichment.stories = []
        enrichment.corroboration.well_corroborated = 5
        enrichment.corroboration.total_claims = 10
        enrichment.anomalies.total_anomalies = 0
        enrichment.convergence_zones = []

        report_mock = MagicMock()
        report_mock.generation_method = "deterministic"
        report_mock.generation_time_ms = 100.0

        cycle_result = CycleResult(
            cycle_id="abc", topic="test", investigation_id="inv",
            started_at=now, completed_at=now, duration_ms=500,
            connector_results=[], total_observations_collected=50,
            pipeline_result=pipeline_result, enrichment=enrichment,
            alerts_fired=[AlertFired(rule_name="test", severity="high", message="test alert")],
            report=report_mock,
            surge_analysis=surge,
            deep_truth_verdict=deep_truth,
            promoted_topics=[PromotedTopic(title="Topic", seed_query="q", score=0.8, source="burst", reason="test")],
        )
        summary = cycle_result.summary()
        assert "SURGE DETECTED" in summary
        assert "MANUFACTURED CONSENSUS" in summary
        assert "ACTIVE SUPPRESSION" in summary
        assert "ALERT" in summary
        assert "PROMOTE" in summary
        assert "deterministic" in summary

    def test_evaluate_alerts_high_slop_and_dispute(self):
        """Line 366: high slop ratio, low corroboration, high dispute alerts."""
        from sts_monitor.cycle import evaluate_alerts
        enrichment = self._make_enrichment(
            slop_total=10, slop_count=5, propaganda=2,
            corr_total=10, corr_rate=0.0,
        )
        pipeline_result = MagicMock()
        pipeline_result.accepted = []
        pipeline_result.disputed_claims = list(range(5))
        alerts = evaluate_alerts(pipeline_result, enrichment)
        alert_names = [a.rule_name for a in alerts]
        assert "high_slop_ratio" in alert_names
        assert "low_corroboration" in alert_names
        assert "high_dispute_rate" in alert_names


# ═══════════════════════════════════════════════════════════════════════
# 9. research_agent.py
# ═══════════════════════════════════════════════════════════════════════

class TestResearchAgent:

    @patch("sts_monitor.research_agent.SearchConnector")
    @patch("sts_monitor.research_agent.NitterConnector")
    @patch("sts_monitor.research_agent.get_accounts_for_categories")
    @patch("sts_monitor.research_agent.RSSConnector")
    @patch("sts_monitor.research_agent.get_curated_feeds")
    def test_collect_initial_rss_branch(self, mock_feeds, mock_rss, mock_get_accounts, mock_nitter, mock_search):
        """Lines 226-227, 240-241: _collect_initial RSS and Telegram branches."""
        from sts_monitor.research_agent import ResearchAgent
        from sts_monitor.pipeline import Observation

        mock_search_inst = MagicMock()
        mock_search_inst.collect.return_value = MagicMock(observations=[
            Observation(source="search:test", claim="result", url="http://test.com",
                       captured_at=datetime.now(UTC), reliability_hint=0.5),
        ])
        mock_search.return_value = mock_search_inst

        mock_get_accounts.return_value = ["@user1"]
        mock_nitter_inst = MagicMock()
        mock_nitter_inst.collect.return_value = MagicMock(observations=[])
        mock_nitter.return_value = mock_nitter_inst

        mock_feeds.return_value = [{"url": "http://feed.com/rss"}]
        mock_rss_inst = MagicMock()
        mock_rss_inst.collect.return_value = MagicMock(observations=[])
        mock_rss.return_value = mock_rss_inst

        llm = MagicMock()
        llm.summarize.return_value = '{"key_findings": [], "should_continue": false}'
        agent = ResearchAgent(llm_client=llm, max_iterations=1, inter_iteration_delay_s=0.0)

        with patch("sts_monitor.connectors.telegram.TelegramConnector") as mock_telegram:
            mock_tg_inst = MagicMock()
            mock_tg_inst.collect.return_value = MagicMock(observations=[])
            mock_telegram.return_value = mock_tg_inst
            obs, connectors = agent._collect_initial("test", "test query")

        assert "search" in connectors

    @patch("sts_monitor.research_agent.SearchConnector")
    @patch("sts_monitor.research_agent.NitterConnector")
    @patch("sts_monitor.research_agent.WebScraperConnector")
    def test_collect_directed(self, mock_scraper, mock_nitter, mock_search):
        """Lines 262-263, 280-281, 297-298, 300-301: _collect_directed branches."""
        from sts_monitor.research_agent import ResearchAgent

        mock_search_inst = MagicMock()
        mock_search_inst.collect.return_value = MagicMock(observations=[])
        mock_search.return_value = mock_search_inst

        mock_scraper_inst = MagicMock()
        mock_scraper_inst.collect.return_value = MagicMock(observations=[])
        mock_scraper.return_value = mock_scraper_inst

        mock_nitter_inst = MagicMock()
        mock_nitter_inst.collect.return_value = MagicMock(observations=[])
        mock_nitter.return_value = mock_nitter_inst

        llm = MagicMock()
        agent = ResearchAgent(llm_client=llm, max_iterations=1, inter_iteration_delay_s=0.0)

        decisions = {
            "new_search_queries": ["query1"],
            "urls_to_scrape": ["http://example.com"],
            "twitter_accounts_to_follow": ["@user1"],
            "twitter_search_queries": [],  # empty so it falls to topic_query branch
        }
        obs, connectors = agent._collect_directed(decisions, "topic_q")
        assert "search" in connectors
        assert "web_scraper" in connectors
        assert "nitter" in connectors

    @patch("sts_monitor.research_agent.SearchConnector")
    @patch("sts_monitor.research_agent.NitterConnector")
    @patch("sts_monitor.research_agent.WebScraperConnector")
    def test_collect_directed_with_twitter_queries(self, mock_scraper, mock_nitter, mock_search):
        """Lines 262-263: _collect_directed with twitter_search_queries."""
        from sts_monitor.research_agent import ResearchAgent

        mock_search_inst = MagicMock()
        mock_search_inst.collect.return_value = MagicMock(observations=[])
        mock_search.return_value = mock_search_inst

        mock_nitter_inst = MagicMock()
        mock_nitter_inst.collect.return_value = MagicMock(observations=[])
        mock_nitter.return_value = mock_nitter_inst

        llm = MagicMock()
        agent = ResearchAgent(llm_client=llm, max_iterations=1, inter_iteration_delay_s=0.0)

        decisions = {
            "new_search_queries": [],
            "urls_to_scrape": [],
            "twitter_accounts_to_follow": ["@x"],
            "twitter_search_queries": ["twitter query"],
        }
        obs, connectors = agent._collect_directed(decisions, "topic_q")
        assert "nitter" in connectors

    @patch("sts_monitor.research_agent.SearchConnector")
    @patch("sts_monitor.research_agent.NitterConnector")
    @patch("sts_monitor.research_agent.get_accounts_for_categories")
    @patch("sts_monitor.research_agent.RSSConnector")
    @patch("sts_monitor.research_agent.get_curated_feeds")
    def test_run_session_completes(self, mock_feeds, mock_rss, mock_get_accounts, mock_nitter, mock_search):
        """Lines 452-456, 504-507: full run with slop filter."""
        from sts_monitor.research_agent import ResearchAgent
        from sts_monitor.pipeline import Observation

        mock_search_inst = MagicMock()
        obs_list = [
            Observation(source="search:t", claim=f"claim {i}", url=f"http://t.com/{i}",
                       captured_at=datetime.now(UTC), reliability_hint=0.5)
            for i in range(5)
        ]
        mock_search_inst.collect.return_value = MagicMock(observations=obs_list)
        mock_search.return_value = mock_search_inst

        mock_get_accounts.return_value = []
        mock_nitter_inst = MagicMock()
        mock_nitter_inst.collect.return_value = MagicMock(observations=[])
        mock_nitter.return_value = mock_nitter_inst
        mock_feeds.return_value = []

        llm = MagicMock()
        llm.summarize.return_value = '{"key_findings": ["test"], "should_continue": false}'

        agent = ResearchAgent(llm_client=llm, max_iterations=1, inter_iteration_delay_s=0.0)

        with patch("sts_monitor.connectors.telegram.TelegramConnector", side_effect=ImportError("no telegram")):
            session = agent.run("sess-1", "test topic")
        assert session.status in ("completed", "running", "stopped")
        assert session.finished_at is not None

    @patch("sts_monitor.research_agent.SearchConnector")
    @patch("sts_monitor.research_agent.NitterConnector")
    @patch("sts_monitor.research_agent.get_accounts_for_categories")
    @patch("sts_monitor.research_agent.get_curated_feeds")
    def test_run_session_error_path(self, mock_feeds, mock_get_accounts, mock_nitter, mock_search):
        """Lines 504-507: run() catches exception and sets error status."""
        from sts_monitor.research_agent import ResearchAgent

        # Make all connectors fail
        mock_search_inst = MagicMock()
        mock_search_inst.collect.side_effect = RuntimeError("boom")
        mock_search.return_value = mock_search_inst

        mock_get_accounts.return_value = []
        mock_nitter_inst = MagicMock()
        mock_nitter_inst.collect.side_effect = RuntimeError("boom2")
        mock_nitter.return_value = mock_nitter_inst
        mock_feeds.return_value = []

        llm = MagicMock()
        # Make _generate_brief crash after the iteration
        llm.summarize.side_effect = RuntimeError("llm crash")

        agent = ResearchAgent(llm_client=llm, max_iterations=1, inter_iteration_delay_s=0.0)

        with patch("sts_monitor.connectors.telegram.TelegramConnector", side_effect=ImportError):
            session = agent.run("sess-err", "test topic")
        assert session.finished_at is not None
