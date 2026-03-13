# STS Situation Monitor

A local-first **open-source intelligence (OSINT) situation monitor** designed to help individuals and small teams ingest fast-moving public information, triage noisy claims, and build evidence-backed situation reports.

> **Important:** This project is for lawful analysis of publicly available information. It is not a surveillance, harassment, or doxxing tool.

## What this starter includes

- A reference architecture for ingestion, enrichment, verification, and reporting.
- A lightweight FastAPI service with investigation, RSS/simulated ingestion, persistent storage, offline-local-LLM preflight checks, feedback memory, and report scaffolding.
- A lightweight FastAPI service with investigation and report scaffolding.
- A lightweight FastAPI service with investigation lifecycle, RSS/simulated/trending ingestion, persistent storage, preflight checks, feedback memory, and report scaffolding.
- A pluggable processing pipeline abstraction to support multiple sources.
- Local deployment primitives (`docker-compose.yml`) for API, Postgres, Redis, and Qdrant.

## Vision

The monitor should let you:

1. Track incidents/topics across multiple public channels (news/RSS, social, forums).
2. Preserve source-level provenance so every claim can be traced back to evidence.
3. Run local ranking and filtering to reduce low-signal content.
4. Generate transparent reports with confidence scores and source rationale.
5. Keep your data private and local while optionally plugging into local LLMs.

## Quick start

### 1) Create environment

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
pip install -e .
```

### 2) Configure

```bash
cp .env.example .env
python scripts/generate_api_key.py  # paste into STS_AUTH_API_KEY in .env
```

### 3) Bootstrap checks (recommended)

```bash
./scripts/bootstrap.sh
```

### 3.5) Apply DB migrations

```bash
alembic upgrade head
```

### 4) Run API

```bash
uvicorn sts_monitor.main:app --reload --port 8080
```

### 5) Optional local infra

```bash
docker compose up -d
```

## API endpoints (starter)

- `GET /health` → service health.
- `GET /system/preflight` → verify DB, local LLM connectivity/model availability, and workspace wiring.
- `POST /investigations` → create an investigation topic.
- `GET /investigations` → list investigations.
- `POST /investigations/{id}/ingest/rss` → ingest observations from one or more RSS feeds.
- `POST /investigations/{id}/ingest/simulated` → inject synthetic data to break-test the pipeline.
- `GET /investigations/{id}/observations` → list persisted observations for an investigation.
- `POST /investigations/{id}/run` → run pipeline against persisted observations (optional local LLM summarization).
- `POST /investigations/{id}/feedback` → store analyst feedback for iterative memory.
- `GET /investigations/{id}/memory` → view accumulated feedback memory.
- `GET /reports/{investigation_id}` → fetch latest report snapshot.
- `GET /dashboard/summary` → aggregate counters and latest report summaries for UI widgets.
- `GET /system/preflight` → verify DB, local LLM connectivity/model availability + latency, workspace disk health, queue health, connector diagnostics, and readiness score.
- `GET /system/online-tools` → inspect internet exposure config (public URL, CORS/trusted hosts, webhook setup).
- `POST /investigations` → create an investigation topic.
- `GET /investigations` → list investigations (priority-first).
- `PATCH /investigations/{id}` → update ownership, status, priority, and SLA due time.
- `POST /investigations/{id}/ingest/rss` → ingest observations from one or more RSS feeds.
- `POST /investigations/{id}/ingest/simulated` → inject synthetic data to break-test the pipeline.
- `POST /investigations/{id}/ingest/local-json` → ingest offline/local observation snapshots (no external API dependency).
- `POST /investigations/{id}/ingest/reddit` → ingest public Reddit listing data (subreddit + query filter).
- `GET /research/trending-topics` → pull currently trending topics from public web trend feeds.
- `POST /research/sources` → register a research/discovery source profile.
- `GET /research/sources` → list research/discovery source profiles.
- `POST /search/profiles` → create reusable multi-subject search profiles (terms, exclusions, synonyms).
- `GET /search/profiles` → list saved search profiles (optionally scoped by investigation).
- `POST /search/query` → run cross-investigation ranked search over observations + claims with facets, source-trust/freshness boosts, and kind toggles.
- `GET /search/suggest` → suggest related terms for expanding multi-subject research queries.
- `POST /search/related-investigations` → rank related investigations for a query using cross-investigation match aggregation.
- `POST /investigations/{id}/ingest/trending` → scan online trending topics and ingest related headlines as observations.
- `GET /investigations/{id}/ingestion-runs` → inspect ingestion audit trail and connector failures.
- `GET /investigations/{id}/observations` → list persisted observations for an investigation (supports source/reliability/time filters).
- `POST /investigations/{id}/discovery` → generate discovery summary (top terms/source breakdown, optional LLM brief).
- `POST /jobs/enqueue/ingest-simulated/{id}` → queue simulated ingestion as background job.
- `POST /jobs/enqueue/run/{id}` → queue pipeline run as background job.
- `POST /jobs/process-next` → process the next pending job (priority-aware, with retry/dead-letter handling).
- `POST /jobs/process-batch` → process jobs by priority lanes (high/normal/low quotas).
- `GET /jobs` → inspect persisted job queue state.
- `GET /jobs/dead-letters` → inspect dead-letter jobs.
- `POST /jobs/dead-letters/{id}/requeue` → requeue a dead-letter job for another attempt cycle.
- `POST /schedules` → create recurring schedule templates for jobs.
- `POST /schedules/tick` → enqueue due scheduled jobs.
- `GET /schedules` → inspect configured schedules.
- `POST /investigations/{id}/run` → run pipeline against persisted observations (optional local LLM summarization with structured-schema validation + deterministic fallback).
- `POST /investigations/{id}/feedback` → store analyst feedback for iterative memory.
- `GET /investigations/{id}/memory` → view accumulated feedback memory.
- `GET /reports/{investigation_id}` → fetch latest report snapshot (includes structured `report_sections` + lineage validation).
- `GET /reports/{investigation_id}/validation` → validate latest report claim-to-evidence coverage.
- `GET /investigations/{id}/feed.rss` → RSS feed of recent generated reports for syndication/notifications.
- `GET /investigations/{id}/claims` → list extracted claims with stance/confidence for report lineage.
- `GET /claims/{claim_id}/evidence` → list linked observation evidence for a claim.
- `POST /admin/api-keys` → create scoped API keys (admin only).
- `GET /admin/api-keys` → list provisioned API keys (admin only).
- `POST /admin/api-keys/{id}/revoke` → revoke an API key (admin only).
- `GET /audit/logs` → inspect immutable audit trail of key operations (admin only).
- `GET /dashboard/summary` → aggregate counters and latest report summaries for UI widgets (includes claim lineage/auth/audit counters).
- `POST /alerts/rules` → create alerting rules for an investigation.
- `GET /alerts/rules` → inspect configured alerting rules.
- `POST /alerts/evaluate/{id}` → evaluate rules and emit alert events.
- `GET /alerts/events/{id}` → list alert events for an investigation.


## QA and reliability gates

Run the full local QA gate:

```bash
./scripts/qa_local.sh
```

Run the CI-friendly split gate (fast tests then simulation):

```bash
./scripts/qa_ci.sh
```

Run infra/config QA checks only:

```bash
./scripts/qa_infra.sh
```

Run security QA checks only:

```bash
./scripts/qa_security.sh
```

Pytest marker tiers:
- `unit` for deterministic logic tests.
- `integration` for API/database integration tests.
- `simulation` + `slow` for end-to-end workflow simulation.

Examples:

```bash
pytest -m unit
pytest -m integration
pytest -m simulation
```

Coverage is enforced via pytest config (`--cov=sts_monitor --cov-fail-under=75`).
Migration integrity is checked in QA via `./scripts/verify_migrations.sh`.
Authorization surface check runs via `scripts/check_authz_surface.py`.
Deployment surface check runs via `scripts/check_deployment_surface.py`.
README endpoint sync check runs via `scripts/check_readme_endpoint_sync.py`.
Truth-evaluation harness runs via `scripts/evaluate_truth_harness.py`.
Dependency vulnerability scanning runs via `pip-audit` in `./scripts/qa_security.sh`.
Optional report lineage gate can be enforced via `STS_ENFORCE_REPORT_LINEAGE_GATE=true` with minimum coverage in `STS_REPORT_MIN_LINEAGE_COVERAGE`.

## Roadmap (high level)

- [ ] Connector framework for RSS, Reddit, X/Twitter, YouTube, web pages.
- [ ] Evidence graph and entity linking.
- [ ] Source credibility model + dynamic trust decay.
- [ ] Contradiction and corroboration engine.
- [ ] Dashboard UI (map/timeline/claim graph/live confidence meter).
- [ ] Alerting and watchlists.

## Documentation

See [`docs/architecture.md`](docs/architecture.md) for end-to-end design and security model, [`docs/blockers-and-privacy.md`](docs/blockers-and-privacy.md) for practical offline-LLM/internet integration risks, and [`docs/capability-failure-modes.md`](docs/capability-failure-modes.md) for capability-by-capability failure analysis. Use [`docs/offline-buildout-checklist.md`](docs/offline-buildout-checklist.md) for PC wiring and break-test execution.
See [`docs/architecture.md`](docs/architecture.md) for end-to-end design and security model, [`docs/blockers-and-privacy.md`](docs/blockers-and-privacy.md) for practical offline-LLM/internet integration risks, and [`docs/capability-failure-modes.md`](docs/capability-failure-modes.md) for capability-by-capability failure analysis. Use [`docs/offline-buildout-checklist.md`](docs/offline-buildout-checklist.md) for PC wiring and break-test execution, and [`docs/functional-vision-and-gaps.md`](docs/functional-vision-and-gaps.md) for per-function improvement roadmap. For evidence-first local model operation, adopt [`docs/local-llm-analyst-policy.md`](docs/local-llm-analyst-policy.md) with structured output from [`docs/local-llm-output-schema.json`](docs/local-llm-output-schema.json).


## Privacy defaults

- Uses local SQLite by default (`STS_DATABASE_URL=sqlite:///./sts_monitor.db`).
- Local LLM endpoint is configurable and defaults to localhost (`STS_LOCAL_LLM_URL`).
- Keep API bound to localhost when running outside containers if you do not need LAN access.


## Offline local LLM connection checklist

1. Run a local model server (example: Ollama) on your PC.
2. Set `STS_LOCAL_LLM_URL` and `STS_LOCAL_LLM_MODEL` in `.env`.
3. Call `GET /system/preflight` to verify reachability and model availability before enabling LLM summaries.
4. Use `POST /investigations/{id}/run` with `{ "use_llm": true }` to attempt local model summarization; pipeline falls back to deterministic summary if the model is offline.

## Stress and break testing

- Use `POST /investigations/{id}/ingest/simulated` to inject synthetic contradictory/noisy data.
- Re-run `/run` repeatedly after simulated ingest to test deduplication, filtering, dispute detection, and report stability.
- Submit analyst corrections through `/feedback` and inspect `/memory` to verify iterative memory capture.


## Authentication

- By default, all endpoints except `GET /health` and `GET /system/preflight` require `X-API-Key`.
- Legacy root key (`STS_AUTH_API_KEY`) remains supported for bootstrap/admin access.
- Scoped DB-backed keys can be created via admin endpoints and support roles (`admin`, `analyst`, `viewer`).
- Mutating operational endpoints require at least analyst role.
- Configure via `STS_AUTH_API_KEY` and `STS_ENFORCE_AUTH`.

Example:

```bash
curl -H "X-API-Key: change-me" http://localhost:8080/investigations
```


## End-to-end simulation

Run a full local simulation (preflight, ingest, run, feedback, report, dashboard):

```bash
python scripts/simulate_full_workflow.py
```

Run a human-readable VM demo report (PASS/FAIL + preflight + dashboard snapshot):

```bash
python scripts/demo_simulated_functioning.py
```


## Background worker

Run a simple in-house worker loop that processes queued jobs:

```bash
python scripts/job_worker.py
```


## Scheduler tick loop

Run a simple scheduler loop that enqueues due recurring jobs:

```bash
python scripts/scheduler_tick.py
```


## Queue retry controls

Configure queue retry/dead-letter behavior via:

- `STS_JOB_MAX_ATTEMPTS`
- `STS_JOB_RETRY_BACKOFF_S`
See [`docs/architecture.md`](docs/architecture.md) for end-to-end design and security model.


## Database backup and restore (SQLite)

```bash
python scripts/db_backup.py
python scripts/db_restore.py backups/<backup-file>.db
```

## Public internet deployment

- Use `scripts/run_public_api.sh` to run uvicorn on `0.0.0.0` with workers.
- Use `docker-compose.public.yml` + `ops/Caddyfile` for HTTPS reverse proxy with automatic TLS.
- Full steps are documented in [`docs/internet-deployment.md`](docs/internet-deployment.md).


## Research, discovery, and alerting build-out

- Register curated source profiles via `/research/sources` for source trust and discovery organization.
- Use `/investigations/{id}/discovery` after ingest cycles to compute emergent terms/source concentration and optional local-LLM brief.
- Configure `/alerts/rules` + run `/alerts/evaluate/{id}` on schedule for watchlist-style operations.
