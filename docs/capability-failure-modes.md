# Capability Map, Failure Modes, and Edge Cases

This document breaks the system into concrete capabilities, what each must do, and how/why each can fail.

## 0) End-to-end objective

For each investigation, the system should:
1. Collect public information from diverse sources.
2. Normalize and deduplicate evidence.
3. Extract claims and entities.
4. Score credibility and detect contradictions.
5. Produce transparent reports with provenance.
6. Learn from analyst feedback and improve future runs.

---

## 1) Capability: Source discovery and ingestion

### Required tasks
- Maintain source registry (RSS, Reddit, official APIs, selected web sources).
- Schedule collection (steady polling + burst mode).
- Handle auth, rate limits, retries, and temporary outages.
- Capture raw payload + metadata (source, author, timestamp, URL, hash).

### High-risk failures
- API limits/plan restrictions (especially social platforms).
- Connector drift due to markup/API changes.
- Silent data gaps (collector appears healthy but misses data).
- Duplicate ingestion storms from repost loops.

### Edge cases
- Same content across multiple URLs.
- Edited/deleted posts after initial capture.
- Timezone mismatch and clock skew.
- Source sends malformed timestamps or missing fields.

### Mitigations
- Per-connector health metrics and freshness SLAs.
- Idempotency keys and dedup windows.
- Dead-letter queue for malformed payloads.
- Backfill jobs for outage windows.

---

## 2) Capability: Normalization and canonicalization

### Required tasks
- Convert each source record into a canonical `Observation`.
- Normalize URLs, timestamps, language, and text fields.
- Split media metadata from text metadata.

### High-risk failures
- Inconsistent schema per source causes dropped fields.
- Incorrect URL canonicalization merges unrelated items.
- Character encoding issues (Unicode, emojis, RTL scripts).

### Edge cases
- Threaded conversations where context is in parent posts.
- Quote tweets/reposts where original and commentary mix.
- Multi-language posts with auto-translation artifacts.

### Mitigations
- Strict schema validation with versioned payload adapters.
- Keep both raw and normalized data.
- Store source-native IDs and canonical IDs separately.

---

## 3) Capability: Deduplication and clustering

### Required tasks
- Exact dedup by source ID/hash.
- Near-duplicate clustering by text similarity.
- Event-level grouping by entities/time/location.

### High-risk failures
- Over-aggressive dedup suppresses independent corroboration.
- Under-aggressive dedup floods pipeline with noise.

### Edge cases
- Same event reported with opposite framing/language.
- Minor updates to breaking stories (new casualty counts, etc.).

### Mitigations
- Keep raw duplicates but mark cluster relationships.
- Confidence-weighted dedup (don’t drop, just collapse views).

---

## 4) Capability: Claim extraction and entity linking

### Required tasks
- Break content into atomic claims.
- Extract entities (people/orgs/locations/dates).
- Link claims to supporting observations.

### High-risk failures
- LLM hallucinated claims not present in source text.
- Entity ambiguity (same name, different person/place).

### Edge cases
- Sarcasm/irony/memes that invert literal meaning.
- Screenshots of text without OCR extraction.

### Mitigations
- Rule: no claim may exist without quoted evidence span.
- Human review queue for high-impact low-confidence claims.
- Entity disambiguation via context windows + knowledge base.

---

## 5) Capability: Credibility scoring and contradiction analysis

### Required tasks
- Apply source priors (historical quality, verification, domain profile).
- Measure cross-source corroboration independence.
- Flag contradictions and temporal inconsistencies.

### High-risk failures
- Feedback loops that overweight popular but wrong sources.
- Biased source priors that suppress contrarian truth.

### Edge cases
- Early reports are wrong, later reports correct.
- Coordinated inauthentic behavior creating fake corroboration.

### Mitigations
- Time-decayed confidence and revision tracking.
- Independence weighting (same network != independent source).
- Keep “disputed” and “unknown” sections explicit in report output.

---

## 6) Capability: Local LLM reasoning and report generation

### Required tasks
- Summarize findings with uncertainty labels.
- Generate report sections: likely true, disputed, unknown, next-watch.
- Cite evidence IDs and links for every assertion.

### High-risk failures
- Hallucinations and fabricated references.
- Overconfident language despite sparse evidence.
- Prompt injection from source content.

### Edge cases
- Conflicting but equally credible sources.
- Long-context truncation drops key evidence.

### Mitigations
- Retrieval-augmented generation from curated evidence only.
- Structured output schema validation.
- Prompt isolation and content sanitization.

---

## 7) Capability: Memory, learning, and iteration

### Required tasks
- Persist investigations, observations, claims, reports, and feedback.
- Track model/pipeline version per report.
- Learn from analyst corrections.

### High-risk failures
- Catastrophic forgetting (new tuning harms prior performance).
- Feedback poisoning (bad labels degrade system quality).

### Edge cases
- One analyst’s preference differs from another’s standards.
- Regime changes: source trust profile shifts abruptly.

### Mitigations
- Versioned policy + scoring configs.
- Holdout evaluation set and regression checks before rollout.
- Signed audit records for manual overrides.

---

## 8) Capability: Dashboard and analyst workflow

### Required tasks
- Show live ingestion health and backlog.
- Show claim matrix (support/contradict/unknown).
- Provide evidence drill-down and timeline.
- Provide report diff across iterations.

### High-risk failures
- UI hides uncertainty; users over-trust single confidence number.
- Performance collapse on large investigations.

### Edge cases
- Partial outages where some widgets are stale.
- Concurrent analyst edits causing race conditions.

### Mitigations
- Surface freshness timestamps and data quality warnings.
- Pagination, virtualized tables, and async background jobs.
- Explicit conflict resolution for concurrent edits.

---

## 9) Capability: Privacy, security, and compliance

### Required tasks
- Keep data local by default.
- Enforce role-based access and audit trails.
- Minimize personal data retention.

### High-risk failures
- Exposed API on public interfaces.
- Secrets leaked through logs/config commits.
- Unauthorized filesystem access from dashboard tools.

### Edge cases
- User imports sensitive local files by accident.
- Model prompts include sensitive content copied from workspace.

### Mitigations
- Default localhost binding and authenticated access.
- Secrets scanning and redacted logs.
- Filesystem allowlist root + read-only defaults.

---

## 10) Capability: Reliability and operations

### Required tasks
- Observe ingestion latency, error rates, queue depth, report latency.
- Backup/restore DB and evidence artifacts.
- Graceful degradation when connectors fail.

### High-risk failures
- Single-point failures (DB locked, worker crash).
- Unbounded storage growth.

### Edge cases
- Burst events create extreme load spikes.
- Duplicate jobs due to worker restarts.

### Mitigations
- Job idempotency and dedupe tokens.
- Retention + archival policies.
- Circuit breakers and per-source rate governance.

---

## 11) Minimal task graph for your local AI

1. Intake investigation topic and constraints.
2. Pull source data from allowed connectors.
3. Normalize to canonical observations.
4. Deduplicate and cluster by event.
5. Extract claims/entities and link evidence.
6. Score credibility and detect contradictions.
7. Generate report draft with citations.
8. Run policy validator (reject unsupported claims).
9. Publish report and notify dashboard.
10. Capture analyst feedback and schedule re-run.

## 12) What fails first in real deployments (priority)

1. Source access limits and connector drift.
2. Poor provenance discipline in generated reports.
3. Contradiction handling under breaking-news volatility.
4. Dashboard trust design (confidence over-simplification).
5. Lack of evaluation harness for iterative improvements.
