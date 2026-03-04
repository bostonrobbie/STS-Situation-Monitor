# STS Situation Monitor — End-to-End Architecture

## 1) Product goals

Build a **local-first intelligence workflow** that ingests open/public data, evaluates source quality, reconciles conflicting claims, and emits transparent situation reports.

Core properties:

- **Evidence over opinion**: Every claim references source URLs and capture timestamps.
- **Uncertainty-aware outputs**: Reports include confidence and known unknowns.
- **Local control**: Run on your own hardware with optional local LLM backend.
- **Safety and legality**: Public information only, no unauthorized access workflows.

## 2) System layers

1. **Ingestion layer**
   - Pull connectors: RSS/news APIs, Reddit, website crawls, curated watchlists.
   - Push connectors: Webhooks/manual dropbox for links and files.
   - Capture metadata: URL, author handle, time, platform, language, media hash.

2. **Normalization layer**
   - Convert source artifacts into canonical `Observation` records.
   - Deduplicate near-identical content (URL canonicalization + embedding similarity).
   - Detect language and classify content type (news/report/video/thread).

3. **Enrichment layer**
   - Entity extraction (people, orgs, locations, events, dates).
   - Geotagging if coordinates or explicit place names are available.
   - Claim extraction: break posts/articles into atomic verifiable claims.

4. **Reliability layer**
   - Source priors: historical reliability, verification badges, editorial standards.
   - Corroboration graph: count independent confirmations.
   - Contradiction detector: flag direct conflicts and timing discrepancies.
   - Decay model: stale reports lower confidence over time.

5. **Reasoning layer (local LLM + rules)**
   - LLM performs summarization and hypothesis framing.
   - Rule engine enforces provenance requirements and red-line policies.
   - Output must cite evidence IDs; unsupported statements are rejected.

6. **Storage layer**
   - Postgres for structured entities, cases, claims, and audit logs.
   - Qdrant for vector search across source text and extracted claims.
   - Object store (optional MinIO) for snapshots/media.

7. **Presentation layer**
   - Dashboard: timeline, claim graph, source map, confidence trend.
   - Analyst workbench: compare claims, inspect provenance, triage queue.
   - Report exporter: markdown/pdf/json outputs.

## 3) Suggested data model (initial)

- `investigations`: topic, owner, status, priority, tags.
- `sources`: platform, account/domain identity, trust profile, history.
- `observations`: raw ingested units linked to sources.
- `claims`: extracted assertions from observations.
- `evidence_links`: claim ↔ observation connections with rationale.
- `reports`: generated report snapshots with confidence metrics.

## 4) Processing pipeline design

### Stage A — Acquire

- Poll feeds on schedule.
- Burst mode for breaking events (short interval polling + trend triggers).

### Stage B — Triage

- Remove spam, repost storms, obvious low-quality noise.
- Score source credibility and novelty.

### Stage C — Verify

- Group claims by event/entity/time.
- Seek corroboration and detect contradictions.
- Trigger manual review where confidence is low but impact is high.

### Stage D — Publish

- Produce concise situation report with:
  - What is likely true.
  - What is disputed.
  - What is unknown.
  - Which sources to monitor next.

## 5) Security and trust controls

- Local auth for dashboard/API, strong secrets in `.env` or secret manager.
- Data-at-rest encryption on workstation/host volumes.
- Role separation for admin vs analyst user flows.
- Immutable audit log for report generation and manual edits.
- Safety policies for prohibited targets, personal data minimization, and legal compliance.

## 6) Dashboard blueprint

Main panels:

- **Incident feed**: newest relevant observations with confidence chips.
- **Claim matrix**: rows = claims, columns = corroborating/contradicting evidence.
- **Geo + timeline**: map and time slider for event development.
- **Watchlist monitor**: high-value accounts/domains and drift alerts.
- **Report pane**: live draft with citations and confidence breakdown.

## 7) Near-term implementation plan

1. Implement connectors for RSS + Reddit first (API stable and easy to validate).
2. Add source reliability profiles and first-pass scoring.
3. Store evidence and claim relations in Postgres.
4. Add vector semantic search for cross-source matching.
5. Build React dashboard with timeline + claim matrix MVP.
6. Add local LLM abstraction (e.g., Ollama endpoint) for summaries.
7. Add policy gate and report validator before final output.

## 8) Non-goals (for early versions)

- Real-time global monitoring of all platforms.
- Autonomous decision-making without human review.
- Any collection from private/non-consensual channels.
