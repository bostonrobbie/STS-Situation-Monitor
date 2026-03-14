"""LLM-powered claim verification pipeline.

Feeds conflicting claims to the local LLM to assess:
- Which version is more likely true
- What evidence would resolve the conflict
- Confidence assessment with reasoning

Falls back to heuristic scoring when LLM is unavailable.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    """Result of verifying a single claim."""
    claim_text: str
    verdict: str  # "likely_true", "likely_false", "unverified", "disputed", "needs_evidence"
    confidence: float  # 0-1
    reasoning: str
    supporting_sources: list[str]
    contradicting_sources: list[str]
    evidence_needed: list[str]
    method: str  # "llm" or "heuristic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim_text[:500],
            "verdict": self.verdict,
            "confidence": round(self.confidence, 3),
            "reasoning": self.reasoning,
            "supporting_sources": self.supporting_sources,
            "contradicting_sources": self.contradicting_sources,
            "evidence_needed": self.evidence_needed,
            "method": self.method,
        }


@dataclass
class ClaimCluster:
    """Group of observations about the same claim from different sources."""
    representative_claim: str
    observations: list[dict[str, Any]]
    sources: list[str]
    has_contradiction: bool
    positive_count: int
    negative_count: int
    neutral_count: int


@dataclass
class VerificationReport:
    """Full verification report for an investigation."""
    investigation_id: str
    topic: str
    total_claims_analyzed: int
    verified_true: int
    verified_false: int
    disputed: int
    unverified: int
    results: list[VerificationResult]
    method: str
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "investigation_id": self.investigation_id,
            "topic": self.topic,
            "total_claims_analyzed": self.total_claims_analyzed,
            "verified_true": self.verified_true,
            "verified_false": self.verified_false,
            "disputed": self.disputed,
            "unverified": self.unverified,
            "results": [r.to_dict() for r in self.results[:100]],
            "method": self.method,
            "generated_at": self.generated_at,
        }


# ── Negation / confirmation signals ──────────────────────────────────
_NEGATIVE_SIGNALS = {"false", "denied", "debunked", "retracted", "incorrect", "fake",
                      "hoax", "misleading", "wrong", "refuted", "untrue", "disproven",
                      "not true", "no evidence"}
_POSITIVE_SIGNALS = {"confirmed", "verified", "proven", "authenticated", "corroborated",
                      "validated", "established", "substantiated", "documented"}


def _claim_sentiment(claim: str) -> str:
    """Classify claim as positive, negative, or neutral."""
    lower = claim.lower()
    neg = sum(1 for w in _NEGATIVE_SIGNALS if w in lower)
    pos = sum(1 for w in _POSITIVE_SIGNALS if w in lower)
    if neg > pos:
        return "negative"
    if pos > neg:
        return "positive"
    return "neutral"


def _normalize_for_grouping(claim: str) -> str:
    """Create a rough grouping key from a claim."""
    import re
    c = claim.lower().strip()
    c = re.sub(r'[^\w\s]', ' ', c)
    words = [w for w in c.split() if len(w) > 3][:6]
    return " ".join(sorted(words))


def _group_claims(observations: list[dict[str, Any]]) -> list[ClaimCluster]:
    """Group observations by similar claims."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for obs in observations:
        claim = obs.get("claim") or ""
        if len(claim) < 15:
            continue
        key = _normalize_for_grouping(claim)
        if key:
            groups[key].append(obs)

    clusters = []
    for _, obs_list in groups.items():
        if len(obs_list) < 1:
            continue
        sources = list({(o.get("source") or "unknown") for o in obs_list})
        sentiments = [_claim_sentiment(o.get("claim", "")) for o in obs_list]
        pos = sentiments.count("positive")
        neg = sentiments.count("negative")
        neu = sentiments.count("neutral")
        clusters.append(ClaimCluster(
            representative_claim=obs_list[0].get("claim", "")[:500],
            observations=obs_list,
            sources=sources,
            has_contradiction=(pos > 0 and neg > 0),
            positive_count=pos,
            negative_count=neg,
            neutral_count=neu,
        ))

    clusters.sort(key=lambda c: c.has_contradiction, reverse=True)
    return clusters


def _verify_heuristic(cluster: ClaimCluster) -> VerificationResult:
    """Verify a claim cluster using heuristic rules."""
    claim = cluster.representative_claim
    total = len(cluster.observations)
    source_count = len(cluster.sources)

    # Calculate reliability-weighted score
    reliabilities = [o.get("reliability_hint", 0.5) for o in cluster.observations]
    avg_reliability = sum(reliabilities) / len(reliabilities) if reliabilities else 0.5

    pos_sources = [o.get("source", "?") for o in cluster.observations if _claim_sentiment(o.get("claim", "")) == "positive"]
    neg_sources = [o.get("source", "?") for o in cluster.observations if _claim_sentiment(o.get("claim", "")) == "negative"]

    if cluster.has_contradiction:
        verdict = "disputed"
        confidence = 0.3
        reasoning = (
            f"Conflicting reports from {source_count} sources. "
            f"{cluster.positive_count} confirm, {cluster.negative_count} deny. "
            f"Average source reliability: {avg_reliability:.0%}"
        )
        evidence_needed = [
            "Independent verification from additional sources",
            "Official documentation or primary source material",
            "Timeline analysis to determine which report came first",
        ]
    elif cluster.negative_count > cluster.positive_count:
        verdict = "likely_false"
        confidence = min(0.8, avg_reliability * cluster.negative_count / total)
        reasoning = f"Majority of {total} observations suggest claim is false. Avg reliability: {avg_reliability:.0%}"
        evidence_needed = ["Confirmation from authoritative source"]
    elif cluster.positive_count > 0 and source_count >= 3:
        verdict = "likely_true"
        confidence = min(0.9, avg_reliability * (source_count / 5.0))
        reasoning = f"Corroborated by {source_count} sources. Avg reliability: {avg_reliability:.0%}"
        evidence_needed = []
    elif source_count >= 2:
        verdict = "likely_true"
        confidence = min(0.7, avg_reliability)
        reasoning = f"Reported by {source_count} sources. Avg reliability: {avg_reliability:.0%}"
        evidence_needed = ["Additional independent confirmation"]
    else:
        verdict = "unverified"
        confidence = avg_reliability * 0.5
        reasoning = f"Single source report. Reliability: {avg_reliability:.0%}"
        evidence_needed = ["Corroboration from at least one independent source"]

    return VerificationResult(
        claim_text=claim,
        verdict=verdict,
        confidence=confidence,
        reasoning=reasoning,
        supporting_sources=list(set(pos_sources))[:10],
        contradicting_sources=list(set(neg_sources))[:10],
        evidence_needed=evidence_needed,
        method="heuristic",
    )


def _verify_with_llm(cluster: ClaimCluster, llm_client) -> VerificationResult:
    """Verify a claim cluster using LLM analysis."""
    claim = cluster.representative_claim
    obs_summaries = "\n".join(
        f"- [{o.get('source', '?')}] (reliability: {o.get('reliability_hint', 0.5):.0%}) {(o.get('claim') or '')[:200]}"
        for o in cluster.observations[:10]
    )

    prompt = (
        f"You are a fact-checking intelligence analyst. Analyze these reports about the same event/claim "
        f"and determine the most likely truth.\n\n"
        f"REPORTS:\n{obs_summaries}\n\n"
        f"Analyze:\n"
        f"1. VERDICT: Is this likely_true, likely_false, disputed, or unverified?\n"
        f"2. CONFIDENCE: 0.0-1.0\n"
        f"3. REASONING: Brief explanation (2-3 sentences)\n"
        f"4. EVIDENCE NEEDED: What would confirm or deny this?\n\n"
        f"Be direct and specific."
    )

    try:
        response = llm_client.summarize(prompt)
        # Parse LLM response (best effort)
        verdict = "unverified"
        confidence = 0.5
        for v in ("likely_true", "likely_false", "disputed", "unverified"):
            if v in response.lower():
                verdict = v
                break
        for line in response.split("\n"):
            if "confidence" in line.lower():
                import re
                numbers = re.findall(r'0\.\d+', line)
                if numbers:
                    confidence = float(numbers[0])
                    break

        pos_sources = [o.get("source", "?") for o in cluster.observations if _claim_sentiment(o.get("claim", "")) == "positive"]
        neg_sources = [o.get("source", "?") for o in cluster.observations if _claim_sentiment(o.get("claim", "")) == "negative"]

        return VerificationResult(
            claim_text=claim,
            verdict=verdict,
            confidence=confidence,
            reasoning=response[:1000],
            supporting_sources=list(set(pos_sources))[:10],
            contradicting_sources=list(set(neg_sources))[:10],
            evidence_needed=["See LLM analysis above"],
            method="llm",
        )
    except Exception as e:
        log.warning("LLM verification failed, falling back to heuristic: %s", e)
        return _verify_heuristic(cluster)


def verify_investigation_claims(
    investigation_id: str,
    topic: str,
    observations: list[dict[str, Any]],
    llm_client=None,
    max_claims: int = 50,
) -> VerificationReport:
    """Run claim verification on all observations in an investigation."""
    clusters = _group_claims(observations)[:max_claims]

    results = []
    for cluster in clusters:
        if llm_client and cluster.has_contradiction:
            result = _verify_with_llm(cluster, llm_client)
        else:
            result = _verify_heuristic(cluster)
        results.append(result)

    verdicts = [r.verdict for r in results]

    return VerificationReport(
        investigation_id=investigation_id,
        topic=topic,
        total_claims_analyzed=len(results),
        verified_true=verdicts.count("likely_true"),
        verified_false=verdicts.count("likely_false"),
        disputed=verdicts.count("disputed"),
        unverified=verdicts.count("unverified") + verdicts.count("needs_evidence"),
        results=results,
        method="llm+heuristic" if llm_client else "heuristic",
        generated_at=datetime.now(UTC).isoformat(),
    )
