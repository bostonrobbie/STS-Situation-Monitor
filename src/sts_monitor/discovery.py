from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re

from sts_monitor.pipeline import Observation

WORD_RE = re.compile(r"[a-zA-Z]{4,}")
STOP_WORDS = {
    "this",
    "that",
    "with",
    "from",
    "have",
    "will",
    "about",
    "source",
    "topic",
    "update",
    "related",
    "there",
}


@dataclass(slots=True)
class DiscoverySummary:
    top_terms: list[dict[str, int]]
    source_breakdown: list[dict[str, int]]
    sample_claims: list[str]


def build_discovery_summary(observations: list[Observation], sample_size: int = 10) -> DiscoverySummary:
    term_counter: Counter[str] = Counter()
    source_counter: Counter[str] = Counter()

    for item in observations:
        source_counter[item.source] += 1
        for word in WORD_RE.findall(item.claim.lower()):
            if word in STOP_WORDS:
                continue
            term_counter[word] += 1

    top_terms = [{"term": term, "count": count} for term, count in term_counter.most_common(15)]
    source_breakdown = [{"source": source, "count": count} for source, count in source_counter.most_common(15)]
    sample_claims = [item.claim for item in observations[:sample_size]]

    return DiscoverySummary(top_terms=top_terms, source_breakdown=source_breakdown, sample_claims=sample_claims)
