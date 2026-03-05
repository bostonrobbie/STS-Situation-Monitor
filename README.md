# STS Situation Monitor

A local-first **open-source intelligence (OSINT) situation monitor** designed to help individuals and small teams ingest fast-moving public information, triage noisy claims, and build evidence-backed situation reports.

> **Important:** This project is for lawful analysis of publicly available information. It is not a surveillance, harassment, or doxxing tool.

## What this starter includes

- A reference architecture for ingestion, enrichment, verification, and reporting.
- A lightweight FastAPI service with investigation, RSS/simulated ingestion, persistent storage, offline-local-LLM preflight checks, feedback memory, and report scaffolding.
- A lightweight FastAPI service with investigation and report scaffolding.
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

### 4) Run API
```

### 3) Run API

```bash
uvicorn sts_monitor.main:app --reload --port 8080
```

### 5) Optional local infra
### 4) Optional local infra

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
- `GET /investigations/{id}/ingestion-runs` → inspect ingestion audit trail and connector failures.
- `GET /investigations/{id}/observations` → list persisted observations for an investigation.
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
- `POST /investigations/{id}/run` → run pipeline against persisted observations (optional local LLM summarization).
- `POST /investigations/{id}/feedback` → store analyst feedback for iterative memory.
- `GET /investigations/{id}/memory` → view accumulated feedback memory.
- `GET /reports/{investigation_id}` → fetch latest report snapshot.
- `GET /dashboard/summary` → aggregate counters and latest report summaries for UI widgets.
- `POST /investigations` → create an investigation topic.
- `GET /investigations` → list investigations.
- `POST /investigations/{id}/run` → run a mock pipeline pass.
- `GET /reports/{investigation_id}` → fetch latest report snapshot.

## Roadmap (high level)

- [ ] Connector framework for RSS, Reddit, X/Twitter, YouTube, web pages.
- [ ] Evidence graph and entity linking.
- [ ] Source credibility model + dynamic trust decay.
- [ ] Contradiction and corroboration engine.
- [ ] Dashboard UI (map/timeline/claim graph/live confidence meter).
- [ ] Alerting and watchlists.

## Documentation

See [`docs/architecture.md`](docs/architecture.md) for end-to-end design and security model, [`docs/blockers-and-privacy.md`](docs/blockers-and-privacy.md) for practical offline-LLM/internet integration risks, and [`docs/capability-failure-modes.md`](docs/capability-failure-modes.md) for capability-by-capability failure analysis. Use [`docs/offline-buildout-checklist.md`](docs/offline-buildout-checklist.md) for PC wiring and break-test execution, and [`docs/functional-vision-and-gaps.md`](docs/functional-vision-and-gaps.md) for per-function improvement roadmap.


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
