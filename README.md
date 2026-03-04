# STS Situation Monitor

A local-first **open-source intelligence (OSINT) situation monitor** designed to help individuals and small teams ingest fast-moving public information, triage noisy claims, and build evidence-backed situation reports.

> **Important:** This project is for lawful analysis of publicly available information. It is not a surveillance, harassment, or doxxing tool.

## What this starter includes

- A reference architecture for ingestion, enrichment, verification, and reporting.
- A lightweight FastAPI service with investigation, RSS/simulated ingestion, persistent storage, offline-local-LLM preflight checks, feedback memory, and report scaffolding.
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
pip install -e .
```

### 2) Configure

```bash
cp .env.example .env
```

### 3) Run API

```bash
uvicorn sts_monitor.main:app --reload --port 8080
```

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
- `GET /investigations/{id}/observations` → list persisted observations for an investigation.
- `POST /investigations/{id}/run` → run pipeline against persisted observations (optional local LLM summarization).
- `POST /investigations/{id}/feedback` → store analyst feedback for iterative memory.
- `GET /investigations/{id}/memory` → view accumulated feedback memory.
- `GET /reports/{investigation_id}` → fetch latest report snapshot.
- `GET /dashboard/summary` → aggregate counters and latest report summaries for UI widgets.

## Roadmap (high level)

- [ ] Connector framework for RSS, Reddit, X/Twitter, YouTube, web pages.
- [ ] Evidence graph and entity linking.
- [ ] Source credibility model + dynamic trust decay.
- [ ] Contradiction and corroboration engine.
- [ ] Dashboard UI (map/timeline/claim graph/live confidence meter).
- [ ] Alerting and watchlists.

## Documentation

See [`docs/architecture.md`](docs/architecture.md) for end-to-end design and security model, [`docs/blockers-and-privacy.md`](docs/blockers-and-privacy.md) for practical offline-LLM/internet integration risks, and [`docs/capability-failure-modes.md`](docs/capability-failure-modes.md) for capability-by-capability failure analysis. Use [`docs/offline-buildout-checklist.md`](docs/offline-buildout-checklist.md) for PC wiring and break-test execution.


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
