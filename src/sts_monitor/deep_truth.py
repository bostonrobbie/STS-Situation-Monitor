"""Deep Truth Mode — forensic reasoning engine for claim analysis.

Inspired by Brian Roemmele's (@BrianRoemmele) Deep Truth Mode prompt framework.
Applies adversarial, multi-track forensic analysis to claims and intelligence
products to counter groupthink, consensus bias, and manufactured narratives.

The 8-step protocol:
1. Consensus Fortress  — What does the mainstream say? What labels are applied?
2. Suppression Audit   — Who benefits? What are the incentive structures?
3. Triple-Track        — Steel-man mainstream, dissenting, AND hybrid positions
4. Red-Team Attack     — Adversarially try to destroy each track
5. Survivor Output     — What claims survived the red-team?
6. Falsification Paths — What evidence would disprove each survivor?
7. Silence Analysis    — What questions are conspicuously absent?
8. Forensic Verdict    — Probability distribution across hypotheses

Scoring dimensions:
- Authority Weight (0-0.99): institutional coordination behind a source (higher = more skepticism)
- Provenance Entropy (bits): diversity/tamper-resistance of evidence chain
- Primary Source Multiplier: reward for original/primary vs derivative sources

This module implements the protocol deterministically (no LLM required)
for the enrichment pipeline, with optional LLM-powered deep analysis.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


# ── Core scoring structures ───────────────────────────────────────────

@dataclass(slots=True)
class AuthorityWeight:
    """How much institutional weight is behind a claim.

    Paradox: high authority weight means MORE skepticism is warranted.
    A claim pushed by every major outlet simultaneously deserves harder
    scrutiny than a claim from a primary-source nobody.
    """
    score: float  # 0.0 (no institutional backing) to 0.99 (total consensus)
    sources_pushing: int  # how many institutional sources repeat this
    pejorative_labels: list[str]  # "conspiracy theory", "debunked", etc.
    coordination_markers: list[str]  # identical phrasing across outlets
    breakdown: dict[str, float]

    @property
    def skepticism_level(self) -> str:
        """Higher authority weight = more skepticism needed."""
        if self.score >= 0.8:
            return "maximum_scrutiny"
        if self.score >= 0.6:
            return "elevated_scrutiny"
        if self.score >= 0.4:
            return "standard"
        return "low_coordination"


@dataclass(slots=True)
class ProvenanceScore:
    """Entropy/diversity of the evidence chain behind a claim.

    High provenance entropy = evidence from many independent, decentralized
    sources that would be hard to coordinate or fabricate.
    Low entropy = evidence from a single coordinated chain.
    """
    entropy_bits: float  # Shannon entropy of source chain
    source_independence: float  # 0.0-1.0 how independent are sources
    primary_source_ratio: float  # fraction that are primary vs derivative
    tamper_resistance: float  # 0.0-1.0 how hard to fabricate
    source_chain: list[str]  # ordered provenance chain


@dataclass(slots=True)
class TrackAnalysis:
    """Analysis of one position/hypothesis in the triple-track."""
    track_name: str  # "mainstream", "dissenting", "hybrid"
    position_summary: str
    supporting_evidence: list[str]
    weaknesses: list[str]  # from red-team attack
    survived_attack: bool  # did it survive adversarial scrutiny?
    explanatory_power: float  # 0.0-1.0
    ad_hoc_assumptions: int  # fewer = stronger
    probability: float  # assigned probability (must sum to ~1.0 across tracks)


@dataclass(slots=True)
class SilenceGap:
    """A question or data point conspicuously absent from the discourse."""
    question: str
    expected_data: str  # what data should exist but doesn't
    possible_reason: str  # why it might be absent
    importance: float  # 0.0-1.0


@dataclass(slots=True)
class DeepTruthVerdict:
    """Complete forensic analysis verdict for a claim or topic."""
    claim: str
    analyzed_at: datetime

    # Step 1: Consensus fortress
    consensus_position: str
    authority_weight: AuthorityWeight

    # Step 2: Suppression audit
    incentive_analysis: str
    funding_flows: list[str]
    career_consequences: list[str]
    conflicts_of_interest: list[str]

    # Step 3-4: Triple track + red team
    tracks: list[TrackAnalysis]

    # Step 5: Survivors
    surviving_claims: list[str]

    # Step 6: Falsification paths
    falsification_tests: list[dict[str, str]]  # [{hypothesis, test, timeframe}]

    # Step 7: Silence gaps
    silence_gaps: list[SilenceGap]

    # Step 8: Final verdict
    verdict_summary: str
    probability_distribution: dict[str, float]  # hypothesis -> probability
    confidence_in_verdict: float  # meta-confidence
    active_suppression_detected: bool
    manufactured_consensus_detected: bool

    # Provenance
    provenance: ProvenanceScore

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "analyzed_at": self.analyzed_at.isoformat(),
            "consensus_position": self.consensus_position,
            "authority_weight": {
                "score": round(self.authority_weight.score, 3),
                "skepticism_level": self.authority_weight.skepticism_level,
                "sources_pushing": self.authority_weight.sources_pushing,
                "pejorative_labels": self.authority_weight.pejorative_labels,
                "coordination_markers": self.authority_weight.coordination_markers,
            },
            "incentive_analysis": self.incentive_analysis,
            "tracks": [
                {
                    "name": t.track_name,
                    "position": t.position_summary,
                    "survived": t.survived_attack,
                    "explanatory_power": round(t.explanatory_power, 3),
                    "ad_hoc_assumptions": t.ad_hoc_assumptions,
                    "probability": round(t.probability, 3),
                    "weaknesses": t.weaknesses,
                }
                for t in self.tracks
            ],
            "surviving_claims": self.surviving_claims,
            "falsification_tests": self.falsification_tests,
            "silence_gaps": [
                {
                    "question": g.question,
                    "expected_data": g.expected_data,
                    "importance": round(g.importance, 3),
                }
                for g in self.silence_gaps
            ],
            "verdict_summary": self.verdict_summary,
            "probability_distribution": {
                k: round(v, 3) for k, v in self.probability_distribution.items()
            },
            "confidence_in_verdict": round(self.confidence_in_verdict, 3),
            "active_suppression_detected": self.active_suppression_detected,
            "manufactured_consensus_detected": self.manufactured_consensus_detected,
            "provenance": {
                "entropy_bits": round(self.provenance.entropy_bits, 3),
                "source_independence": round(self.provenance.source_independence, 3),
                "primary_source_ratio": round(self.provenance.primary_source_ratio, 3),
            },
        }


# ── Pejorative / dismissal label detection ────────────────────────────

_PEJORATIVE_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.I) for p in [
        r"\bconspiracy\s+theor(?:y|ies|ist)\b",
        r"\bdebunked\b",
        r"\bmisinformation\b",
        r"\bdisinformation\b",
        r"\bfalse\s+(?:claim|narrative|story)\b",
        r"\bpseudoscience\b",
        r"\bfringe\b",
        r"\bunfounded\b",
        r"\bbaseless\b",
        r"\bbogus\b",
        r"\bjunk\s+science\b",
        r"\banti-?(?:vax|science|establishment)\b",
        r"\bfact.?check(?:ed|ers?|ing)?\b",
        r"\bclaimed\s+without\s+evidence\b",
        r"\bwidely\s+(?:discredited|rejected|dismissed)\b",
    ]
]


def detect_pejorative_labels(text: str) -> list[str]:
    """Find dismissal/pejorative labels in text — these are Deep Truth triggers."""
    labels = []
    for pattern in _PEJORATIVE_PATTERNS:
        matches = pattern.findall(text)
        for m in matches:
            labels.append(m.lower().strip())
    return list(set(labels))


# ── Coordination / identical phrasing detection ───────────────────────

def detect_phrase_coordination(
    observations: list[dict[str, Any]],
    min_phrase_len: int = 5,
    min_repeats: int = 3,
) -> list[str]:
    """Detect when multiple outlets use identical phrasing — a coordination marker.

    In genuine independent reporting, outlets use different words. When you see
    "experts say" or "baseless claims" repeated identically across 5+ outlets
    within hours, that's a talking-points distribution chain.
    """
    # Extract 5-word phrases from each observation
    phrase_sources: dict[str, set[str]] = {}

    for obs in observations:
        text = obs.get("claim", "").lower()
        source = obs.get("source", "")
        words = text.split()

        for i in range(len(words) - min_phrase_len + 1):
            phrase = " ".join(words[i:i + min_phrase_len])
            # Skip very common phrases
            if any(w in phrase for w in ("the ", "and ", "that ", "this ", "with ", "from ")):
                continue
            phrase_sources.setdefault(phrase, set()).add(source)

    coordinated = []
    for phrase, sources in phrase_sources.items():
        if len(sources) >= min_repeats:
            coordinated.append(f'"{phrase}" repeated across {len(sources)} sources')

    return coordinated


# ── Authority weight computation ──────────────────────────────────────

# Tier-1 institutional sources — high authority weight when they ALL agree
_INSTITUTIONAL_DOMAINS: set[str] = {
    "reuters.com", "apnews.com", "bbc.com", "nytimes.com", "washingtonpost.com",
    "theguardian.com", "cnn.com", "nbcnews.com", "abcnews.go.com",
}

# Government/official sources
_OFFICIAL_SOURCES: set[str] = {
    "state.gov", "who.int", "nih.gov", "cdc.gov", "fbi.gov", "cia.gov",
    "un.org", "nato.int", "europa.eu", "mod.uk",
}


def compute_authority_weight(
    observations: list[dict[str, Any]],
    claim_text: str,
) -> AuthorityWeight:
    """Compute how much institutional weight is behind a claim.

    The paradox: unanimous institutional agreement on a contested claim
    is itself a signal that demands investigation. Not because institutions
    are always wrong, but because unanimous agreement removes the adversarial
    process that keeps information honest.
    """
    source_domains: list[str] = []
    for obs in observations:
        url = obs.get("url", "").lower()
        source = obs.get("source", "").lower()
        # Extract domain
        for domain in _INSTITUTIONAL_DOMAINS | _OFFICIAL_SOURCES:
            if domain in url or domain.split(".")[0] in source:
                source_domains.append(domain)

    institutional_count = len(set(source_domains))
    total_sources = len({obs.get("source", "") for obs in observations})

    # Coordination markers
    coordination = detect_phrase_coordination(observations)

    # Pejorative labels in the discourse
    all_text = " ".join(obs.get("claim", "") for obs in observations)
    pejoratives = detect_pejorative_labels(all_text)

    # Score components
    breakdown: dict[str, float] = {}

    # Institutional saturation: what fraction of coverage is institutional?
    inst_ratio = institutional_count / max(1, total_sources)
    breakdown["institutional_saturation"] = round(inst_ratio * 0.4, 3)

    # Coordination penalty: identical phrasing across outlets
    coord_score = min(0.3, len(coordination) * 0.05)
    breakdown["phrase_coordination"] = round(coord_score, 3)

    # Pejorative deployment: dismissal labels indicate active narrative defense
    pejorative_score = min(0.2, len(pejoratives) * 0.04)
    breakdown["pejorative_deployment"] = round(pejorative_score, 3)

    # Official source backing
    official_count = sum(1 for d in source_domains if d in _OFFICIAL_SOURCES)
    official_score = min(0.1, official_count * 0.03)
    breakdown["official_backing"] = round(official_score, 3)

    total = min(0.99, sum(breakdown.values()))

    return AuthorityWeight(
        score=round(total, 3),
        sources_pushing=institutional_count,
        pejorative_labels=pejoratives,
        coordination_markers=coordination,
        breakdown=breakdown,
    )


# ── Provenance entropy ────────────────────────────────────────────────

def compute_provenance(observations: list[dict[str, Any]]) -> ProvenanceScore:
    """Compute provenance entropy — how diverse and tamper-resistant is the evidence?

    High entropy = many independent sources, hard to coordinate fabrication.
    Low entropy = single chain of custody, easy to manipulate.
    """
    import math

    sources = [obs.get("source", "") for obs in observations]
    source_families = [s.split(":")[0] for s in sources]
    connector_types = list(set(source_families))

    # Shannon entropy of source distribution
    total = len(source_families)
    counts = Counter(source_families)
    entropy = 0.0
    for count in counts.values():
        p = count / total if total > 0 else 0
        if p > 0:
            entropy -= p * math.log2(p)

    # Source independence: unique families / total
    unique_families = len(set(s.split(":", 1)[-1].split("/")[0] for s in sources if ":" in s))
    independence = unique_families / max(1, total)

    # Primary source ratio: government data, direct observations vs derivative
    primary_connectors = {"usgs", "nasa_firms", "nws", "fema", "acled", "opensky", "adsb"}
    primary_count = sum(1 for sf in source_families if sf in primary_connectors)
    primary_ratio = primary_count / max(1, total)

    # Tamper resistance: decentralized + diverse = hard to fabricate
    tamper = min(1.0, (entropy / 3.0) * 0.5 + independence * 0.3 + primary_ratio * 0.2)

    return ProvenanceScore(
        entropy_bits=round(entropy, 3),
        source_independence=round(independence, 3),
        primary_source_ratio=round(primary_ratio, 3),
        tamper_resistance=round(tamper, 3),
        source_chain=connector_types,
    )


# ── Silence gap detection ─────────────────────────────────────────────

def detect_silence_gaps(
    observations: list[dict[str, Any]],
    topic: str,
) -> list[SilenceGap]:
    """Identify conspicuous absences in the available information.

    What data SHOULD exist but doesn't? What questions are no one asking?
    These gaps are often more informative than what IS being reported.
    """
    gaps: list[SilenceGap] = []
    topic_lower = topic.lower()
    all_text = " ".join(obs.get("claim", "") for obs in observations).lower()

    # Check for expected but missing data categories
    expected_categories = {
        "casualty": {
            "keywords": ["killed", "dead", "casualties", "wounded", "attack", "strike", "bomb"],
            "expected": "official casualty figures from local authorities",
            "check": ["official", "ministry", "hospital", "confirmed dead", "confirmed killed"],
        },
        "geographic": {
            "keywords": ["earthquake", "fire", "flood", "hurricane", "tornado"],
            "expected": "precise geographic coordinates or boundaries",
            "check": ["coordinates", "latitude", "longitude", "radius", "boundary", "affected area"],
        },
        "timeline": {
            "keywords": ["breaking", "developing", "ongoing"],
            "expected": "clear chronological timeline of events",
            "check": ["timeline", "first reported", "sequence of events", "chronolog"],
        },
        "attribution": {
            "keywords": ["attack", "hack", "breach", "strike", "bomb", "sabotage"],
            "expected": "evidence-based attribution or official investigation",
            "check": ["investigation", "attributed to", "claimed responsibility", "evidence suggests"],
        },
        "primary_source": {
            "keywords": [],  # Always check
            "expected": "primary source data (not just media citing media)",
            "check": ["primary", "original", "firsthand", "witness", "raw data"],
        },
    }

    # Count sources by type
    source_types = Counter(obs.get("source", "").split(":")[0] for obs in observations)

    for category, config in expected_categories.items():
        # Check if topic involves this category
        if config["keywords"] and not any(kw in topic_lower or kw in all_text for kw in config["keywords"]):
            continue

        # Check if the expected data is present
        has_expected = any(check in all_text for check in config["check"])
        if not has_expected:
            importance = 0.7 if config["keywords"] else 0.5
            gaps.append(SilenceGap(
                question=f"Where is the {config['expected']}?",
                expected_data=config["expected"],
                possible_reason=f"Absent from {len(observations)} observations across {len(source_types)} source types",
                importance=importance,
            ))

    # Check for missing independent verification
    if len(source_types) < 3:
        gaps.append(SilenceGap(
            question="Why are only a few source types covering this?",
            expected_data="Coverage from multiple independent source types",
            possible_reason=f"Only {len(source_types)} source type(s): {', '.join(source_types.keys())}",
            importance=0.6,
        ))

    # Check for missing contradictions (unanimous agreement on contested topic is suspicious)
    claims = [obs.get("claim", "") for obs in observations]
    has_contradictions = any(
        re.search(r"\b(?:however|but|contradicts?|disputes?|denies?|refutes?|false|incorrect)\b", c, re.I)
        for c in claims
    )
    if not has_contradictions and len(observations) > 10:
        gaps.append(SilenceGap(
            question="Why is there no dissenting view in a large dataset?",
            expected_data="At least some contradicting or questioning sources",
            possible_reason="Uniform narrative across all sources — possible manufactured consensus",
            importance=0.8,
        ))

    return gaps


# ── Main analysis function ────────────────────────────────────────────

def analyze_deep_truth(
    observations: list[dict[str, Any]],
    topic: str,
    claim: str = "",
) -> DeepTruthVerdict:
    """Run the Deep Truth forensic protocol on a claim or topic.

    This is the deterministic version that works without an LLM.
    It applies the 8-step protocol using pattern matching, statistical
    analysis, and heuristic reasoning.

    For LLM-powered deep analysis, use the DEEP_TRUTH_PROMPT with
    a local LLM and feed it the output of this function.
    """
    now = datetime.now(UTC)
    claim_text = claim or topic
    all_text = " ".join(obs.get("claim", "") for obs in observations)

    # ── Step 1: Consensus fortress ────────────────────────────────
    pejorative_labels = detect_pejorative_labels(all_text)
    # Most common claim direction = consensus position
    consensus_position = _extract_consensus(observations)

    # ── Step 2: Authority weight + suppression audit ──────────────
    authority = compute_authority_weight(observations, claim_text)

    # Incentive analysis (heuristic)
    incentive_analysis = _analyze_incentives(observations, topic)
    funding_flows = _detect_funding_indicators(all_text)
    career_consequences = _detect_career_consequences(all_text)
    conflicts = _detect_conflicts_of_interest(observations)

    # ── Step 3-4: Triple track + red team ─────────────────────────
    tracks = _build_triple_track(observations, claim_text, authority)

    # ── Step 5: Survivors ─────────────────────────────────────────
    surviving_claims = [t.position_summary for t in tracks if t.survived_attack]

    # ── Step 6: Falsification paths ───────────────────────────────
    falsification_tests = _build_falsification_paths(tracks)

    # ── Step 7: Silence gaps ──────────────────────────────────────
    silence_gaps = detect_silence_gaps(observations, topic)

    # ── Step 8: Provenance ────────────────────────────────────────
    provenance = compute_provenance(observations)

    # ── Step 8: Final verdict ─────────────────────────────────────
    prob_dist = {t.track_name: t.probability for t in tracks}
    manufactured = (
        authority.score >= 0.7
        and len(authority.coordination_markers) >= 2
        and len(pejorative_labels) >= 2
    )
    suppression = (
        len(silence_gaps) >= 3
        and authority.score >= 0.5
        and any(g.importance >= 0.7 for g in silence_gaps)
    )

    verdict = _build_verdict(tracks, authority, silence_gaps, provenance, manufactured, suppression)

    # Meta-confidence: how confident are we in our own analysis?
    meta_confidence = _compute_meta_confidence(observations, authority, provenance, silence_gaps)

    return DeepTruthVerdict(
        claim=claim_text,
        analyzed_at=now,
        consensus_position=consensus_position,
        authority_weight=authority,
        incentive_analysis=incentive_analysis,
        funding_flows=funding_flows,
        career_consequences=career_consequences,
        conflicts_of_interest=conflicts,
        tracks=tracks,
        surviving_claims=surviving_claims,
        falsification_tests=falsification_tests,
        silence_gaps=silence_gaps,
        verdict_summary=verdict,
        probability_distribution=prob_dist,
        confidence_in_verdict=meta_confidence,
        active_suppression_detected=suppression,
        manufactured_consensus_detected=manufactured,
        provenance=provenance,
    )


# ── Internal helpers ──────────────────────────────────────────────────

def _extract_consensus(observations: list[dict[str, Any]]) -> str:
    """Extract the mainstream consensus position from observations."""
    if not observations:
        return "No consensus position available."
    # Use highest-reliability observation as consensus representative
    sorted_obs = sorted(observations, key=lambda o: o.get("reliability_hint", 0), reverse=True)
    return sorted_obs[0].get("claim", "")[:500]


def _analyze_incentives(observations: list[dict[str, Any]], topic: str) -> str:
    """Heuristic incentive analysis."""
    source_types = Counter(obs.get("source", "").split(":")[0] for obs in observations)
    dominant = source_types.most_common(1)[0] if source_types else ("unknown", 0)

    parts = [f"Primary narrative driven by {dominant[0]} sources ({dominant[1]} of {len(observations)} observations)."]

    # Check for government sources pushing narrative
    gov_sources = {"nws", "fema", "usgs", "acled", "nasa_firms"}
    gov_count = sum(c for s, c in source_types.items() if s in gov_sources)
    if gov_count > len(observations) * 0.5:
        parts.append("Government/authoritative sources dominate — verify independence of non-government sources.")

    return " ".join(parts)


def _detect_funding_indicators(text: str) -> list[str]:
    """Detect mentions of funding, grants, sponsorship."""
    patterns = [
        r"funded by\s+([^.]+)",
        r"grant from\s+([^.]+)",
        r"sponsored by\s+([^.]+)",
        r"partnership with\s+([^.]+)",
    ]
    results = []
    for p in patterns:
        matches = re.findall(p, text, re.I)
        results.extend(m.strip()[:100] for m in matches)
    return results


def _detect_career_consequences(text: str) -> list[str]:
    """Detect mentions of professional consequences for dissent."""
    patterns = [
        r"(?:fired|terminated|deplatformed|retracted|censored|banned|silenced)\s+(?:for|after|because)",
    ]
    results = []
    for p in patterns:
        if re.search(p, text, re.I):
            results.append(p.replace("\\s+", " ").replace("(?:", "").replace(")", ""))
    return results


def _detect_conflicts_of_interest(observations: list[dict[str, Any]]) -> list[str]:
    """Detect potential conflicts of interest in the source chain."""
    conflicts = []
    sources = [obs.get("source", "") for obs in observations]
    source_families = [s.split(":")[0] for s in sources]

    # Check if a single source family dominates
    counts = Counter(source_families)
    total = len(source_families)
    for family, count in counts.most_common(3):
        if count > total * 0.5:
            conflicts.append(f"Source family '{family}' provides {count}/{total} ({count/total:.0%}) of observations")

    return conflicts


def _build_triple_track(
    observations: list[dict[str, Any]],
    claim: str,
    authority: AuthorityWeight,
) -> list[TrackAnalysis]:
    """Build three analysis tracks: mainstream, dissenting, hybrid."""
    all_text = " ".join(obs.get("claim", "") for obs in observations)

    # Track 1: Mainstream
    mainstream_evidence = [
        obs.get("claim", "")[:200]
        for obs in observations
        if obs.get("reliability_hint", 0) >= 0.6
    ][:5]

    mainstream_weaknesses = []
    if authority.score >= 0.6:
        mainstream_weaknesses.append("High institutional coordination — possible echo chamber")
    if authority.coordination_markers:
        mainstream_weaknesses.append(f"{len(authority.coordination_markers)} identical phrasing patterns detected")
    if authority.pejorative_labels:
        mainstream_weaknesses.append("Pejorative labels deployed against alternatives — defensive posture")

    # Track 2: Dissenting
    dissenting_obs = [
        obs for obs in observations
        if any(re.search(r"\b(?:however|contrary|disputed|questioned|alternative)\b",
               obs.get("claim", ""), re.I) for _ in [1])
    ]
    dissenting_evidence = [obs.get("claim", "")[:200] for obs in dissenting_obs][:5]

    dissenting_weaknesses = []
    if not dissenting_obs:
        dissenting_weaknesses.append("No explicit dissenting sources found in current dataset")
    if len(dissenting_obs) < len(observations) * 0.1:
        dissenting_weaknesses.append("Very few dissenting voices — may indicate genuine consensus OR suppression")

    # Track 3: Hybrid
    hybrid_weaknesses = ["Hybrid position requires holding multiple possibilities simultaneously"]

    # Score tracks
    total_obs = max(1, len(observations))
    mainstream_support = len(mainstream_evidence) / total_obs
    dissent_support = len(dissenting_evidence) / total_obs

    # Mainstream probability adjusted by authority weight
    # High authority weight = slight downward adjustment (more scrutiny needed)
    mainstream_prob = max(0.1, min(0.85, 0.5 + mainstream_support * 0.3 - authority.score * 0.1))
    dissent_prob = max(0.05, min(0.4, dissent_support * 0.5 + authority.score * 0.05))
    hybrid_prob = max(0.05, 1.0 - mainstream_prob - dissent_prob)

    tracks = [
        TrackAnalysis(
            track_name="mainstream",
            position_summary=_extract_consensus(observations),
            supporting_evidence=mainstream_evidence,
            weaknesses=mainstream_weaknesses,
            survived_attack=len(mainstream_weaknesses) < 3,
            explanatory_power=mainstream_support,
            ad_hoc_assumptions=len(mainstream_weaknesses),
            probability=round(mainstream_prob, 3),
        ),
        TrackAnalysis(
            track_name="dissenting",
            position_summary="Alternative interpretation of available evidence" if dissenting_evidence
                           else "No explicit dissenting position found in dataset",
            supporting_evidence=dissenting_evidence,
            weaknesses=dissenting_weaknesses,
            survived_attack=len(dissenting_evidence) > 0 and len(dissenting_weaknesses) < 2,
            explanatory_power=dissent_support,
            ad_hoc_assumptions=max(1, 3 - len(dissenting_evidence)),
            probability=round(dissent_prob, 3),
        ),
        TrackAnalysis(
            track_name="hybrid",
            position_summary="Partial truth in multiple tracks; reality likely more complex than any single narrative",
            supporting_evidence=mainstream_evidence[:2] + dissenting_evidence[:2],
            weaknesses=hybrid_weaknesses,
            survived_attack=True,  # Hybrid almost always survives
            explanatory_power=round((mainstream_support + dissent_support) / 2, 3),
            ad_hoc_assumptions=2,
            probability=round(hybrid_prob, 3),
        ),
    ]

    return tracks


def _build_falsification_paths(tracks: list[TrackAnalysis]) -> list[dict[str, str]]:
    """For each surviving track, define what would disprove it."""
    paths = []
    for track in tracks:
        if not track.survived_attack:
            continue
        if track.track_name == "mainstream":
            paths.append({
                "hypothesis": "Mainstream consensus is correct",
                "test": "Release of primary source data that contradicts the narrative, or discovery of coordinated messaging campaign",
                "timeframe": "Ongoing — monitor for contradicting primary data",
            })
        elif track.track_name == "dissenting":
            paths.append({
                "hypothesis": "Dissenting position has merit",
                "test": "Independent replication of mainstream claims by adversarial researchers, or primary evidence directly contradicting dissent",
                "timeframe": "1-6 months for independent verification",
            })
        elif track.track_name == "hybrid":
            paths.append({
                "hypothesis": "Reality is more nuanced than either extreme",
                "test": "Discovery that one position is completely, unambiguously correct with no caveats",
                "timeframe": "Ongoing",
            })
    return paths


def _build_verdict(
    tracks: list[TrackAnalysis],
    authority: AuthorityWeight,
    silence_gaps: list[SilenceGap],
    provenance: ProvenanceScore,
    manufactured: bool,
    suppression: bool,
) -> str:
    """Build the final forensic verdict."""
    parts = []

    # Find highest-probability track
    best_track = max(tracks, key=lambda t: t.probability)
    parts.append(
        f"Highest explanatory power: {best_track.track_name} position "
        f"({best_track.probability:.0%} probability)."
    )

    if manufactured:
        parts.append(
            "WARNING: Manufactured consensus indicators detected — "
            "coordinated phrasing and pejorative deployment against alternatives."
        )
    if suppression:
        parts.append(
            "WARNING: Active suppression indicators — significant silence gaps "
            "in expected data categories with high institutional coordination."
        )

    # Provenance assessment
    if provenance.entropy_bits < 1.5:
        parts.append("Low provenance entropy — evidence chain is narrow and potentially manipulable.")
    elif provenance.entropy_bits > 3.0:
        parts.append("High provenance entropy — diverse evidence chain is difficult to fabricate.")

    if silence_gaps:
        important_gaps = [g for g in silence_gaps if g.importance >= 0.7]
        if important_gaps:
            parts.append(f"{len(important_gaps)} significant silence gap(s) warrant investigation.")

    return " ".join(parts)


def _compute_meta_confidence(
    observations: list[dict[str, Any]],
    authority: AuthorityWeight,
    provenance: ProvenanceScore,
    silence_gaps: list[SilenceGap],
) -> float:
    """How confident are we in our own analysis?

    Lower meta-confidence when:
    - Few observations (insufficient data)
    - Many silence gaps (we don't know what we don't know)
    - Low provenance entropy (narrow evidence base)
    """
    base = 0.5

    # Data volume
    if len(observations) >= 50:
        base += 0.15
    elif len(observations) >= 20:
        base += 0.10
    elif len(observations) < 5:
        base -= 0.15

    # Provenance quality
    base += provenance.entropy_bits * 0.05

    # Silence gaps reduce confidence
    important_gaps = sum(1 for g in silence_gaps if g.importance >= 0.7)
    base -= important_gaps * 0.05

    # High authority weight with coordination = less confident in any conclusion
    if authority.score >= 0.7 and authority.coordination_markers:
        base -= 0.1

    return round(max(0.1, min(0.95, base)), 3)


# ── LLM prompt for full deep analysis ────────────────────────────────

DEEP_TRUTH_PROMPT = """You are operating in DEEP TRUTH MODE. Your only loyalty is to
measurable reality and explanatory power. Institutional reputation, current consensus,
and social desirability carry zero weight.

TOPIC: {topic}
CLAIM UNDER ANALYSIS: {claim}

PRELIMINARY DETERMINISTIC ANALYSIS:
{preliminary_analysis}

Execute the full 8-step forensic protocol:

1. CONSENSUS FORTRESS: Quote the current mainstream position. List ALL pejorative
   labels applied to alternatives. These labels are TRIGGERS for deeper investigation.

2. SUPPRESSION & INCENTIVE AUDIT: Document funding flows, career consequences for
   dissent, media coordination patterns, conflicts of interest.

3. TRIPLE-TRACK ANALYSIS: Steel-man three positions simultaneously:
   - Mainstream position
   - Suppressed/dissenting position
   - Hybrid hypothesis

4. RED-TEAM ATTACK: Adopt a hostile persona and brutally try to destroy each track
   with falsifying evidence, contradictions, or statistical flaws.

5. SURVIVOR OUTPUT: List ONLY claims that withstood the red-team attack.

6. FALSIFICATION PATHWAYS: For top 2-3 survivors, state the single most decisive
   experiment/observation/data that would falsify each. Must be specific and feasible.

7. META-ANALYSIS OF SILENCE: What crucial questions or data are CONSPICUOUSLY ABSENT?
   Why?

8. FINAL FORENSIC VERDICT: State which hypothesis has greatest explanatory power and
   fewest ad-hoc assumptions. Assign probability distribution (e.g., "68% consensus
   correct | 24% major revision required | 8% consensus inverted"). Justify every
   percentage point. Flag evidence of active suppression or manufactured consensus.

Respond with JSON matching this structure:
{{
    "consensus_fortress": "...",
    "pejorative_labels": ["..."],
    "suppression_audit": "...",
    "tracks": [
        {{"name": "mainstream", "position": "...", "evidence": ["..."], "weaknesses": ["..."]}},
        {{"name": "dissenting", "position": "...", "evidence": ["..."], "weaknesses": ["..."]}},
        {{"name": "hybrid", "position": "...", "evidence": ["..."], "weaknesses": ["..."]}}
    ],
    "survivors": ["..."],
    "falsification_tests": [{{"hypothesis": "...", "test": "...", "timeframe": "..."}}],
    "silence_gaps": [{{"question": "...", "importance": 0.8}}],
    "verdict": "...",
    "probability_distribution": {{"mainstream_correct": 0.6, "revision_required": 0.3, "consensus_inverted": 0.1}},
    "suppression_detected": false,
    "manufactured_consensus": false
}}
"""
