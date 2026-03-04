# STS Situation Monitor

A local-first **open-source intelligence (OSINT) situation monitor** designed to help individuals and small teams ingest fast-moving public information, triage noisy claims, and build evidence-backed situation reports.

> **Important:** This project is for lawful analysis of publicly available information. It is not a surveillance, harassment, or doxxing tool.

## What this starter includes

- A reference architecture for ingestion, enrichment, verification, and reporting.
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

See [`docs/architecture.md`](docs/architecture.md) for end-to-end design and security model.
