# Functional Vision, Current Gaps, and Top-Tier Upgrade Path

This document captures what each major system function does today, what it lacks, and how to evolve it toward a high-quality intelligence workstation.

## 1) System preflight

### Current
- Checks DB query path, local LLM reachability/model availability, workspace root presence, and auth mode.

### Lacking
- No active network diagnostics per connector.
- No hardware profile checks (CPU/GPU/RAM) for local LLM suitability.

### Upgrade vision
- Add a "readiness score" that includes connector health, queue health, and model latency budget.

## 2) Investigation lifecycle

### Current
- Create/list investigations and persist records.

### Lacking
- No prioritization, ownership, or SLA fields.

### Upgrade vision
- Add priorities, assignees, incident status transitions, and escalation policy.

## 3) Ingestion (RSS + simulated)

### Current
- RSS ingest with retry/backoff and failure metadata.
- Simulated ingest for stress tests.
- Ingestion audit trail persisted.

### Lacking
- No Reddit/X/web connectors in code.
- No scheduler/worker queue for continuous collection.

### Upgrade vision
- Multi-connector orchestration with task queues, source-specific parsers, and quality gates.

## 4) Observation store and retrieval

### Current
- Persist and list observations by investigation.

### Lacking
- No advanced filtering (time range, source, reliability threshold).
- No media artifacts/OCR pipeline.

### Upgrade vision
- Add indexed filters + full-text + semantic retrieval over normalized evidence chunks.

## 5) Signal pipeline

### Current
- Deduplication, reliability clamping, low-signal filtering, basic disputed-claim detection.

### Lacking
- Contradiction engine is marker-based (not semantic or event-aware).
- No entity graph, no claim-evidence lineage table.

### Upgrade vision
- Claim extraction + graph-based corroboration + temporal consistency scoring.

## 6) Report generation

### Current
- Deterministic summary with optional local LLM synthesis.
- Fallback path when LLM unavailable.

### Lacking
- No structured report schema sections (likely true/disputed/unknown/next-watch) in DB.
- No citation renderer tied to evidence IDs.

### Upgrade vision
- Template-driven report contracts with hard citation validation before publish.

## 7) Analyst feedback memory

### Current
- Feedback labels/notes persisted and summarized by label.

### Lacking
- No automated learning loop from feedback.
- No regression tests tied to previous analyst corrections.

### Upgrade vision
- Feedback-driven policy tuning with rollbackable scoring config versions.

## 8) Dashboard and ops intelligence

### Current
- Summary counts + latest reports endpoint.

### Lacking
- No live stream widgets, no backlog/error monitors, no confidence trend chart.

### Upgrade vision
- Real-time operations board: ingestion lag, connector failures, contradiction spikes, and analyst workload.

## 9) Security and governance

### Current
- API key enforcement by default for non-public endpoints.

### Lacking
- Single shared key only (no RBAC, no audit actor identity, no key rotation endpoint).

### Upgrade vision
- Role-based auth (admin/analyst/viewer), per-action audit logging, and key lifecycle management.

## 10) Recommended build order

1. Add background job queue and scheduler.
1. Continue hardening the implemented queue+schedule foundation with retry policies, dead-letter lanes, and priority quotas.
2. Add normalized claim/evidence tables.
3. Add connector expansion (Reddit + web crawler + optional X API where legal).
4. Add structured reporting contract with citation enforcement.
5. Add dashboard UI with operational metrics and analyst workflows.
