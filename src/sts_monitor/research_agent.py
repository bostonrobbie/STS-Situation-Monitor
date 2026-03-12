"""Autonomous research agent powered by local LLM.

Implements a fully autonomous collect → analyze → decide → collect loop:

1. Gather observations from all configured connectors (RSS, Nitter, web scraper,
   search, GDELT, etc.)
2. Feed observations to the local LLM for analysis
3. LLM decides: what's important, what needs deeper investigation, what to
   search for next
4. Agent executes the LLM's decisions: new searches, new URLs to crawl,
   new Twitter accounts to monitor
5. Repeat until max iterations reached or LLM signals completion

The agent maintains a research log and produces a final research brief.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sts_monitor.connectors.base import ConnectorResult
from sts_monitor.connectors.nitter import NitterConnector, get_accounts_for_categories
from sts_monitor.connectors.search import SearchConnector
from sts_monitor.connectors.web_scraper import WebScraperConnector
from sts_monitor.connectors.rss import RSSConnector
from sts_monitor.collection_plan import get_curated_feeds
from sts_monitor.corroboration import analyze_corroboration
from sts_monitor.slop_detector import filter_slop
from sts_monitor.llm import LocalLLMClient
from sts_monitor.pipeline import Observation, SignalPipeline

logger = logging.getLogger(__name__)


# ── LLM Prompts ─────────────────────────────────────────────────────────

ANALYSIS_PROMPT = """You are an autonomous OSINT research agent. Analyze these observations
and decide what to do next.

CURRENT RESEARCH TOPIC: {topic}
ITERATION: {iteration}/{max_iterations}
OBSERVATIONS COLLECTED SO FAR: {total_observations}

NEW OBSERVATIONS THIS ROUND:
{observations}

PREVIOUS FINDINGS:
{previous_findings}

Based on the above, respond with a JSON object containing:
{{
  "key_findings": ["finding1", "finding2", ...],
  "confidence": 0.0-1.0,
  "new_search_queries": ["query1", "query2"],
  "urls_to_scrape": ["url1", "url2"],
  "twitter_accounts_to_follow": ["handle1", "handle2"],
  "twitter_search_queries": ["query1"],
  "assessment": "Brief assessment of the situation (2-3 sentences)",
  "should_continue": true/false,
  "reasoning": "Why continue or stop"
}}

Rules:
- Suggest 0-3 new search queries max (focused, specific)
- Suggest 0-5 URLs to scrape (from observations or your knowledge)
- Only suggest continuing if there are clear leads to follow
- Be concise in findings
- Return ONLY valid JSON, no markdown fences"""

FINAL_BRIEF_PROMPT = """You are an OSINT analyst. Write a final intelligence brief based on
this research session.

TOPIC: {topic}
ITERATIONS COMPLETED: {iterations}
TOTAL OBSERVATIONS: {total_observations}

ALL FINDINGS:
{all_findings}

KEY SOURCES:
{key_sources}

Write a structured intelligence brief with:
1. EXECUTIVE SUMMARY (2-3 sentences)
2. KEY FINDINGS (bullet points)
3. SOURCE ASSESSMENT (reliability of sources)
4. GAPS & LIMITATIONS
5. RECOMMENDED ACTIONS

Be concise and analytical. Return plain text, not JSON."""


# ── Data structures ─────────────────────────────────────────────────────

@dataclass(slots=True)
class ResearchIteration:
    """Record of one research cycle."""
    iteration: int
    started_at: datetime
    observations_collected: int
    connectors_used: list[str]
    llm_response: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    duration_s: float = 0.0


@dataclass(slots=True)
class ResearchSession:
    """Full autonomous research session."""
    session_id: str
    topic: str
    status: str  # "running", "completed", "stopped", "error"
    started_at: datetime
    finished_at: datetime | None = None
    iterations: list[ResearchIteration] = field(default_factory=list)
    all_observations: list[Observation] = field(default_factory=list)
    all_findings: list[str] = field(default_factory=list)
    final_brief: str = ""
    error: str | None = None


# ── Agent ───────────────────────────────────────────────────────────────

class ResearchAgent:
    """Fully autonomous research agent using local LLM."""

    def __init__(
        self,
        *,
        llm_client: LocalLLMClient,
        max_iterations: int = 5,
        max_observations: int = 500,
        nitter_instances: list[str] | None = None,
        search_max_results: int = 15,
        scraper_max_depth: int = 1,
        scraper_max_pages: int = 20,
        scraper_delay_s: float = 1.5,
        rss_categories: list[str] | None = None,
        nitter_categories: list[str] | None = None,
        inter_iteration_delay_s: float = 5.0,
    ) -> None:
        self.llm = llm_client
        self.max_iterations = max(1, min(20, max_iterations))
        self.max_observations = max_observations
        self.nitter_instances = nitter_instances
        self.search_max_results = search_max_results
        self.scraper_max_depth = scraper_max_depth
        self.scraper_max_pages = scraper_max_pages
        self.scraper_delay_s = scraper_delay_s
        self.rss_categories = rss_categories
        self.nitter_categories = nitter_categories
        self.inter_iteration_delay_s = inter_iteration_delay_s
        self.pipeline = SignalPipeline(min_reliability=0.3)

        # Active sessions
        self._sessions: dict[str, ResearchSession] = {}
        self._stop_flags: dict[str, bool] = {}
        self._lock = threading.Lock()

    def get_session(self, session_id: str) -> ResearchSession | None:
        return self._sessions.get(session_id)

    def list_sessions(self) -> list[dict[str, Any]]:
        return [
            {
                "session_id": s.session_id,
                "topic": s.topic,
                "status": s.status,
                "started_at": s.started_at.isoformat(),
                "iterations_completed": len(s.iterations),
                "total_observations": len(s.all_observations),
            }
            for s in self._sessions.values()
        ]

    def stop_session(self, session_id: str) -> bool:
        if session_id in self._stop_flags:
            self._stop_flags[session_id] = True
            return True
        return False

    def _collect_initial(self, topic: str, query: str) -> tuple[list[Observation], list[str]]:
        """Initial broad collection across multiple sources."""
        observations: list[Observation] = []
        connectors_used: list[str] = []

        # 1) Search engine
        try:
            search = SearchConnector(max_results=self.search_max_results)
            result = search.collect(query=query)
            observations.extend(result.observations)
            connectors_used.append("search")
        except Exception as exc:
            logger.warning("Search failed: %s", exc)

        # 2) Nitter/Twitter
        try:
            accounts = get_accounts_for_categories(self.nitter_categories)
            nitter = NitterConnector(
                instances=self.nitter_instances,
                accounts=accounts[:10],  # Limit initial accounts
                per_account_limit=10,
            )
            result = nitter.collect(query=query)
            observations.extend(result.observations)
            connectors_used.append("nitter")
        except Exception as exc:
            logger.warning("Nitter failed: %s", exc)

        # 3) RSS feeds
        try:
            feeds = get_curated_feeds(self.rss_categories)
            if feeds:
                feed_urls = [f["url"] for f in feeds[:10]]  # Limit feeds
                rss = RSSConnector(feed_urls=feed_urls, per_feed_limit=5)
                result = rss.collect(query=query)
                observations.extend(result.observations)
                connectors_used.append("rss")
        except Exception as exc:
            logger.warning("RSS failed: %s", exc)

        # 4) Telegram public channels
        try:
            from sts_monitor.connectors.telegram import TelegramConnector
            telegram = TelegramConnector(
                categories=self.nitter_categories,  # Reuse OSINT categories
                per_channel_limit=10,
            )
            result = telegram.collect(query=query)
            observations.extend(result.observations)
            if result.observations:
                connectors_used.append("telegram")
        except Exception as exc:
            logger.warning("Telegram failed: %s", exc)

        return observations, connectors_used

    def _collect_directed(
        self,
        llm_decisions: dict[str, Any],
        topic_query: str,
    ) -> tuple[list[Observation], list[str]]:
        """Directed collection based on LLM decisions."""
        observations: list[Observation] = []
        connectors_used: list[str] = []

        # 1) New search queries
        new_queries = llm_decisions.get("new_search_queries", [])
        if new_queries:
            search = SearchConnector(max_results=self.search_max_results)
            for q in new_queries[:3]:
                try:
                    result = search.collect(query=q)
                    observations.extend(result.observations)
                except Exception as exc:
                    logger.warning("Search '%s' failed: %s", q, exc)
            if new_queries:
                connectors_used.append("search")

        # 2) URLs to scrape
        urls_to_scrape = llm_decisions.get("urls_to_scrape", [])
        if urls_to_scrape:
            try:
                scraper = WebScraperConnector(
                    seed_urls=urls_to_scrape[:5],
                    max_depth=self.scraper_max_depth,
                    max_pages=self.scraper_max_pages,
                    delay_between_requests_s=self.scraper_delay_s,
                )
                result = scraper.collect(query=None)  # Don't filter scraped content
                observations.extend(result.observations)
                connectors_used.append("web_scraper")
            except Exception as exc:
                logger.warning("Scraper failed: %s", exc)

        # 3) Twitter accounts/searches
        twitter_accounts = llm_decisions.get("twitter_accounts_to_follow", [])
        twitter_queries = llm_decisions.get("twitter_search_queries", [])
        if twitter_accounts or twitter_queries:
            try:
                nitter = NitterConnector(
                    instances=self.nitter_instances,
                    accounts=twitter_accounts[:5],
                    per_account_limit=10,
                )
                for tq in twitter_queries[:2]:
                    result = nitter.collect(query=tq)
                    observations.extend(result.observations)
                if not twitter_queries:
                    result = nitter.collect(query=topic_query)
                    observations.extend(result.observations)
                connectors_used.append("nitter")
            except Exception as exc:
                logger.warning("Nitter directed failed: %s", exc)

        return observations, connectors_used

    def _ask_llm(
        self,
        topic: str,
        iteration: int,
        new_observations: list[Observation],
        previous_findings: list[str],
        total_obs: int,
    ) -> dict[str, Any]:
        """Send observations to LLM and get decisions."""
        # Format observations for prompt
        obs_text = "\n".join(
            f"- [{o.source}] {o.claim[:200]} ({o.url})"
            for o in new_observations[:30]  # Limit to fit context
        )

        findings_text = "\n".join(f"- {f}" for f in previous_findings[-10:]) if previous_findings else "None yet."

        prompt = ANALYSIS_PROMPT.format(
            topic=topic,
            iteration=iteration,
            max_iterations=self.max_iterations,
            total_observations=total_obs,
            observations=obs_text or "No new observations.",
            previous_findings=findings_text,
        )

        try:
            raw = self.llm.summarize(prompt)
            # Try to parse JSON from the response
            # Strip markdown fences if present
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
            cleaned = cleaned.strip()

            return json.loads(cleaned)
        except json.JSONDecodeError:
            # If LLM returns invalid JSON, extract what we can
            return {
                "key_findings": [],
                "confidence": 0.3,
                "new_search_queries": [],
                "urls_to_scrape": [],
                "twitter_accounts_to_follow": [],
                "twitter_search_queries": [],
                "assessment": raw[:500] if raw else "LLM response could not be parsed.",
                "should_continue": False,
                "reasoning": "JSON parse failure — stopping to avoid wasted cycles.",
            }
        except Exception as exc:
            return {
                "key_findings": [],
                "confidence": 0.0,
                "new_search_queries": [],
                "urls_to_scrape": [],
                "assessment": f"LLM error: {exc}",
                "should_continue": False,
                "reasoning": str(exc),
            }

    def _generate_brief(self, session: ResearchSession) -> str:
        """Generate final intelligence brief via LLM."""
        findings_text = "\n".join(f"- {f}" for f in session.all_findings) or "No findings."

        # Collect unique sources
        sources = set()
        for obs in session.all_observations[:100]:
            sources.add(f"{obs.source} ({obs.url})")
        sources_text = "\n".join(f"- {s}" for s in list(sources)[:30])

        prompt = FINAL_BRIEF_PROMPT.format(
            topic=session.topic,
            iterations=len(session.iterations),
            total_observations=len(session.all_observations),
            all_findings=findings_text,
            key_sources=sources_text or "No sources recorded.",
        )

        try:
            return self.llm.summarize(prompt)
        except Exception as exc:
            return f"[Brief generation failed: {exc}]\n\nFindings:\n{findings_text}"

    def run(self, session_id: str, topic: str, seed_query: str | None = None) -> ResearchSession:
        """Execute a full autonomous research session (blocking)."""
        query = seed_query or topic
        session = ResearchSession(
            session_id=session_id,
            topic=topic,
            status="running",
            started_at=datetime.now(UTC),
        )
        self._sessions[session_id] = session
        self._stop_flags[session_id] = False

        try:
            for iteration in range(1, self.max_iterations + 1):
                # Check stop flag
                if self._stop_flags.get(session_id, False):
                    session.status = "stopped"
                    break

                iter_start = time.perf_counter()
                iter_record = ResearchIteration(
                    iteration=iteration,
                    started_at=datetime.now(UTC),
                    observations_collected=0,
                    connectors_used=[],
                )

                # Collect
                if iteration == 1:
                    new_obs, connectors = self._collect_initial(topic, query)
                else:
                    # Use LLM decisions from previous iteration
                    prev_decisions = {}
                    if session.iterations:
                        prev_llm = session.iterations[-1].llm_response
                        if prev_llm:
                            prev_decisions = prev_llm
                    new_obs, connectors = self._collect_directed(prev_decisions, query)

                iter_record.connectors_used = connectors
                iter_record.observations_collected = len(new_obs)

                # Run through pipeline for dedup/filtering
                pipeline_result = self.pipeline.run(new_obs, topic=topic)
                accepted = pipeline_result.accepted

                # Auto-filter slop/propaganda/engagement bait
                if accepted:
                    obs_dicts = [
                        {"id": i, "source": o.source, "claim": o.claim,
                         "url": o.url, "captured_at": o.captured_at,
                         "reliability_hint": o.reliability_hint}
                        for i, o in enumerate(accepted)
                    ]
                    slop_result = filter_slop(obs_dicts, drop_threshold=0.6)
                    # Remove observations flagged as slop/propaganda
                    drop_ids = {
                        s.observation_id for s in slop_result.scores
                        if s.recommended_action == "drop"
                    }
                    if drop_ids:
                        accepted = [
                            o for i, o in enumerate(accepted)
                            if i not in drop_ids
                        ]
                        logger.info(
                            "Slop filter dropped %d/%d observations",
                            len(drop_ids), len(obs_dicts),
                        )

                session.all_observations.extend(accepted)

                # Cap total observations
                if len(session.all_observations) > self.max_observations:
                    session.all_observations = session.all_observations[-self.max_observations:]

                # Ask LLM what to do next
                llm_response = self._ask_llm(
                    topic=topic,
                    iteration=iteration,
                    new_observations=accepted,
                    previous_findings=session.all_findings,
                    total_obs=len(session.all_observations),
                )
                iter_record.llm_response = llm_response

                # Record findings
                new_findings = llm_response.get("key_findings", [])
                session.all_findings.extend(new_findings)

                iter_record.duration_s = round(time.perf_counter() - iter_start, 2)
                session.iterations.append(iter_record)

                logger.info(
                    "Iteration %d/%d: %d observations, %d findings, continue=%s",
                    iteration, self.max_iterations,
                    len(accepted), len(new_findings),
                    llm_response.get("should_continue", False),
                )

                # Check if LLM says we're done
                if not llm_response.get("should_continue", True):
                    break

                # Delay between iterations
                if iteration < self.max_iterations:
                    time.sleep(self.inter_iteration_delay_s)

            # Generate final brief
            session.final_brief = self._generate_brief(session)
            if session.status == "running":
                session.status = "completed"

        except Exception as exc:
            session.status = "error"
            session.error = str(exc)
            logger.error("Research agent error: %s", exc)

        session.finished_at = datetime.now(UTC)
        self._stop_flags.pop(session_id, None)
        return session

    def run_async(self, session_id: str, topic: str, seed_query: str | None = None) -> str:
        """Launch research session in a background thread. Returns session_id."""
        thread = threading.Thread(
            target=self.run,
            args=(session_id, topic, seed_query),
            daemon=True,
            name=f"research-agent-{session_id}",
        )
        # Pre-create session so it's immediately visible
        session = ResearchSession(
            session_id=session_id,
            topic=topic,
            status="running",
            started_at=datetime.now(UTC),
        )
        self._sessions[session_id] = session
        self._stop_flags[session_id] = False
        thread.start()
        return session_id
