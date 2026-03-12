"""Story clustering engine.

Groups related observations into coherent "stories" based on:
- Temporal proximity (events close in time)
- Textual similarity (shared key terms)
- Entity overlap (same people, places, organizations)
- Source diversity (multiple sources covering same event)

Inspired by Google News clustering and GDELT's event grouping.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta


@dataclass(slots=True)
class ObservationRef:
    """Lightweight reference to an observation for clustering."""
    id: int
    source: str
    claim: str
    url: str
    captured_at: datetime
    reliability_hint: float = 0.5
    connector_type: str = ""
    investigation_id: str = ""


@dataclass(slots=True)
class Story:
    """A cluster of related observations forming a coherent story."""
    id: str
    headline: str
    observations: list[ObservationRef]
    key_terms: list[str]
    sources: list[str]
    entities: list[str]
    first_seen: datetime
    last_seen: datetime
    observation_count: int = 0
    source_count: int = 0
    avg_reliability: float = 0.5
    trending_score: float = 0.0  # Higher = more recent activity


# ── Text processing ─────────────────────────────────────────────────────

_STOP_WORDS: set[str] = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "need", "dare",
    "it", "its", "this", "that", "these", "those", "he", "she", "they",
    "we", "you", "i", "me", "him", "her", "us", "them", "my", "your",
    "his", "our", "their", "what", "which", "who", "whom", "where",
    "when", "why", "how", "not", "no", "nor", "as", "if", "then", "than",
    "too", "very", "just", "about", "above", "after", "again", "all",
    "also", "any", "because", "before", "between", "both", "during",
    "each", "few", "more", "most", "other", "over", "same", "so", "some",
    "such", "through", "under", "until", "up", "while", "into", "out",
    "new", "said", "says", "according", "reports", "reported", "sources",
    "officials", "government", "state", "people", "country", "year",
    "last", "first", "two", "one", "three", "many", "much",
}

_WORD_RE = re.compile(r"[a-zA-Z]{3,}")


def _extract_terms(text: str) -> list[str]:
    """Extract meaningful terms from text, excluding stop words."""
    words = _WORD_RE.findall(text.lower())
    return [w for w in words if w not in _STOP_WORDS]


def _term_overlap(terms_a: list[str], terms_b: list[str]) -> float:
    """Jaccard-like similarity between two term lists."""
    if not terms_a or not terms_b:
        return 0.0
    set_a = set(terms_a)
    set_b = set(terms_b)
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


def _source_family(source: str) -> str:
    """Extract source family from source string (e.g., 'rss:bbc.com' -> 'bbc.com')."""
    return source.split(":", 1)[-1].split("/")[0] if ":" in source else source


# ── Clustering algorithm ────────────────────────────────────────────────


def cluster_observations(
    observations: list[ObservationRef],
    *,
    time_window_hours: int = 48,
    min_term_overlap: float = 0.15,
    min_cluster_size: int = 2,
    max_clusters: int = 50,
) -> list[Story]:
    """Cluster observations into stories based on temporal and textual similarity.

    Uses a greedy single-pass approach:
    1. Sort by captured_at (newest first)
    2. For each observation, try to merge into existing cluster
    3. If no match, create a new cluster
    4. Score and rank clusters
    """
    if len(observations) < min_cluster_size:
        return []

    cutoff = datetime.now(UTC) - timedelta(hours=time_window_hours)
    recent = [o for o in observations if o.captured_at >= cutoff]

    if len(recent) < min_cluster_size:
        return []

    # Sort newest first
    recent.sort(key=lambda o: o.captured_at, reverse=True)

    # Pre-compute terms for each observation
    obs_terms: list[list[str]] = [_extract_terms(o.claim) for o in recent]

    clusters: list[list[int]] = []  # Each cluster is a list of indices into `recent`
    cluster_terms: list[Counter] = []  # Aggregated term counts per cluster

    for i, obs in enumerate(recent):
        best_cluster = -1
        best_score = min_term_overlap

        terms_i = obs_terms[i]
        if not terms_i:
            continue

        for c_idx, c_term_counter in enumerate(cluster_terms):
            # Check temporal proximity: observation must be within window of cluster
            cluster_times = [recent[j].captured_at for j in clusters[c_idx]]
            earliest = min(cluster_times)
            latest = max(cluster_times)

            # Observation should be within reasonable time range of cluster
            if abs((obs.captured_at - latest).total_seconds()) > time_window_hours * 3600:
                continue

            # Term overlap with cluster's aggregated terms
            c_terms = [t for t, _ in c_term_counter.most_common(30)]
            score = _term_overlap(terms_i, c_terms)

            if score > best_score:
                best_score = score
                best_cluster = c_idx

        if best_cluster >= 0:
            clusters[best_cluster].append(i)
            cluster_terms[best_cluster].update(terms_i)
        else:
            clusters.append([i])
            cluster_terms.append(Counter(terms_i))

    # Build Story objects from clusters
    stories: list[Story] = []
    now = datetime.now(UTC)

    for c_idx, indices in enumerate(clusters):
        if len(indices) < min_cluster_size:
            continue

        obs_list = [recent[i] for i in indices]
        times = [o.captured_at for o in obs_list]
        sources = list({_source_family(o.source) for o in obs_list})
        reliabilities = [o.reliability_hint for o in obs_list]

        # Key terms: top terms from cluster, excluding very common ones
        top_terms = [t for t, _ in cluster_terms[c_idx].most_common(10)]

        # Headline: use the claim from the highest-reliability observation
        best_obs = max(obs_list, key=lambda o: o.reliability_hint)
        headline = best_obs.claim[:200]

        # Trending score: more recent activity = higher score
        hours_since_latest = max(0.1, (now - max(times)).total_seconds() / 3600)
        trending_score = len(obs_list) / hours_since_latest

        story = Story(
            id=f"story-{c_idx}-{int(min(times).timestamp())}",
            headline=headline,
            observations=obs_list,
            key_terms=top_terms,
            sources=sources,
            entities=[],  # Populated externally via entity extraction
            first_seen=min(times),
            last_seen=max(times),
            observation_count=len(obs_list),
            source_count=len(sources),
            avg_reliability=sum(reliabilities) / len(reliabilities),
            trending_score=round(trending_score, 3),
        )
        stories.append(story)

    # Sort by trending score (most active first), then by observation count
    stories.sort(key=lambda s: (s.trending_score, s.observation_count), reverse=True)
    return stories[:max_clusters]


def enrich_stories_with_entities(
    stories: list[Story],
    entity_extractor: callable,
) -> None:
    """Add extracted entities to each story's entity list."""
    for story in stories:
        all_entities: set[str] = set()
        for obs in story.observations:
            entities = entity_extractor(obs.claim)
            for ent in entities:
                all_entities.add(f"{ent.entity_type}:{ent.text}")
        story.entities = sorted(all_entities)
