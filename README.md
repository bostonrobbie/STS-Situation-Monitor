# STS Situation Monitor

A local-first **open-source intelligence (OSINT) situation monitor** for individuals and small teams. Ingest fast-moving public information from 18+ data sources, triage noisy claims, detect coordinated narratives, and produce evidence-backed situation reports — all running on your own hardware.

> **Important:** This project is for lawful analysis of publicly available information. It is not a surveillance, harassment, or doxxing tool.

## Key capabilities

| Layer | What it does |
|---|---|
| **Collection** | 18 connectors pull from GDELT, USGS, NASA FIRMS, ACLED, NWS, FEMA, ReliefWeb, OpenSky, ADS-B Exchange, MarineTraffic, Nitter/Twitter, Reddit, Telegram, RSS, web scraper, DuckDuckGo, Internet Archive, and public webcams. |
| **Pipeline** | Deduplication, reliability scoring, claim extraction, dispute detection, and slop (AI-generated content) filtering. |
| **Enrichment** | Entity extraction, story clustering, corroboration analysis, entity graph construction, narrative timeline, anomaly detection, and discovery summary — all in one pass. |
| **Surge detection** | Alpha signal extraction from social media, account credibility scoring, coordination / bot detection. |
| **Deep Truth** | Roemmele's 8-step forensic protocol: authority weighting, provenance entropy, multi-track hypothesis testing, silence gap detection, manufactured consensus analysis. |
| **Alerting** | Rule-based alert evaluation with cooldowns and severity levels (critical / high / medium / low). |
| **Reporting** | Structured Markdown/JSON situation reports with claim-to-evidence lineage and confidence scores. |
| **Topic promotion** | High-scoring discoveries are automatically promoted into new investigation topics. |
| **Data retention** | Time-based cleanup that respects open investigations. |
| **Privacy** | Configurable PII stripping with a privacy layer that can redact before storage. |
| **API** | Full FastAPI REST API with investigation lifecycle, jobs/scheduling, search, admin/auth, and dashboard. |

## Architecture

```
CLI / API
    │
    ▼
┌──────────┐     ┌──────────┐     ┌────────────┐     ┌─────────┐
│Collection│ ──▶ │ Pipeline │ ──▶ │ Enrichment │ ──▶ │ Report  │
│18 sources│     │filter/dup│     │8 processors│     │generator│
└──────────┘     └──────────┘     └────────────┘     └─────────┘
                                        │
                              ┌─────────┼──────────┐
                              ▼         ▼          ▼
                          Alerts    Surge Det.  Deep Truth
```
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

### 1) Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2) Configure

```bash
cp .env.example .env
python scripts/generate_api_key.py   # paste into STS_AUTH_API_KEY in .env
```

### 3) Run

**CLI — run a full intelligence cycle:**
### 4) Run API
```

### 3) Bootstrap checks (recommended)

```bash
./scripts/bootstrap.sh
```

### 3.5) Apply DB migrations

```bash
python -m sts_monitor cycle "earthquake in Turkey"
python -m sts_monitor cycle "earthquake in Turkey" --json --report-file report.md
```

**CLI — other commands:**

```bash
python -m sts_monitor deep-truth "lab leak theory" --claim "virus was engineered"
python -m sts_monitor surge "Ukraine counteroffensive" --categories conflict,osint
python -m sts_monitor retention --max-age 90 --dry-run
python -m sts_monitor serve --port 8080 --reload
```

**API server:**
### 5) Optional local infra
### 4) Optional local infra

```bash
uvicorn sts_monitor.main:app --reload --port 8080
```

### 4) Optional infrastructure
## API endpoints (starter)

- `GET /health` → service health.
- `GET /system/preflight` → verify DB, local LLM connectivity/model availability, and workspace wiring.
- `POST /investigations` → create an investigation topic.
- `GET /investigations` → list investigations.
- `POST /investigations/{id}/ingest/rss` → ingest observations from one or more RSS feeds.
- `POST /investigations/{id}/ingest/simulated` → inject synthetic data to break-test the pipeline.
- `GET /investigations/{id}/ingestion-runs` → inspect ingestion audit trail and connector failures.
- `GET /investigations/{id}/observations` → list persisted observations for an investigation.
- `POST /investigations/{id}/run` → run pipeline against persisted observations (optional local LLM summarization).
- `POST /investigations/{id}/feedback` → store analyst feedback for iterative memory.
- `GET /investigations/{id}/memory` → view accumulated feedback memory.
- `GET /reports/{investigation_id}` → fetch latest report snapshot.
- `GET /dashboard/summary` → aggregate counters and latest report summaries for UI widgets.
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
docker compose up -d          # Postgres + Redis + Qdrant + API
alembic upgrade head          # apply DB migrations (Postgres mode)
```

## Data source connectors

All connectors implement a common `Connector` interface with a `collect(topic)` method.

| Connector | Source | Auth required |
|---|---|---|
| GDELT | Global news events | No |
| USGS | Earthquakes | No |
| NASA FIRMS | Fire/hotspot satellite data | Yes (free MAP_KEY) |
| ACLED | Armed conflict & protests | Yes (free registration) |
| NWS | US weather alerts | No |
| FEMA | US disaster declarations | No |
| ReliefWeb | UN OCHA humanitarian reports | No |
| OpenSky | Live aircraft tracking | No |
| ADS-B Exchange | Military/civilian aircraft | Yes (API key) |
| MarineTraffic | Ship AIS tracking | Yes (API key) |
| Nitter | Twitter/X via Nitter proxies | No |
| Reddit | Public subreddit posts | No |
| Telegram | Public channels | No |
| RSS | Any RSS/Atom feed | No |
| Web Scraper | General web pages | No |
| DuckDuckGo | Search engine | No |
| Internet Archive | Wayback Machine snapshots | No |
| Webcams | Public webcams (Windy, DOT, YouTube) | Optional (Windy API key) |

See `.env.example` for all configuration variables per connector.

## CLI reference

```
python -m sts_monitor <command> [options]

Commands:
  cycle        Run one full intelligence cycle (collect → enrich → alert → report)
  deep-truth   Run Deep Truth forensic analysis on a topic/claim
  surge        Analyze social media surge for a topic
  retention    Clean up old data (respects open investigations)
  serve        Start the FastAPI API server
```

Run `python -m sts_monitor <command> --help` for full option details.
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

See [`docs/architecture.md`](docs/architecture.md) for end-to-end design and security model, [`docs/blockers-and-privacy.md`](docs/blockers-and-privacy.md) for practical offline-LLM/internet integration risks, and [`docs/capability-failure-modes.md`](docs/capability-failure-modes.md) for capability-by-capability failure analysis. Use [`docs/offline-buildout-checklist.md`](docs/offline-buildout-checklist.md) for PC wiring and break-test execution, and [`docs/functional-vision-and-gaps.md`](docs/functional-vision-and-gaps.md) for per-function improvement roadmap.
See [`docs/architecture.md`](docs/architecture.md) for end-to-end design and security model, [`docs/blockers-and-privacy.md`](docs/blockers-and-privacy.md) for practical offline-LLM/internet integration risks, and [`docs/capability-failure-modes.md`](docs/capability-failure-modes.md) for capability-by-capability failure analysis. Use [`docs/offline-buildout-checklist.md`](docs/offline-buildout-checklist.md) for PC wiring and break-test execution.
See [`docs/architecture.md`](docs/architecture.md) for end-to-end design and security model, [`docs/blockers-and-privacy.md`](docs/blockers-and-privacy.md) for practical offline-LLM/internet integration risks, and [`docs/capability-failure-modes.md`](docs/capability-failure-modes.md) for capability-by-capability failure analysis. Use [`docs/offline-buildout-checklist.md`](docs/offline-buildout-checklist.md) for PC wiring and break-test execution, and [`docs/functional-vision-and-gaps.md`](docs/functional-vision-and-gaps.md) for per-function improvement roadmap. For evidence-first local model operation, adopt [`docs/local-llm-analyst-policy.md`](docs/local-llm-analyst-policy.md) with structured output from [`docs/local-llm-output-schema.json`](docs/local-llm-output-schema.json).


## API endpoints

The API provides 40+ endpoints organized into these groups:

- **Health & system** — `/health`, `/system/preflight`, `/system/online-tools`
- **Investigations** — CRUD lifecycle with priority, ownership, status, SLA
- **Ingestion** — RSS, Reddit, simulated, local-JSON, trending topic ingest
- **Pipeline** — Run analysis pipeline with optional LLM summarization
- **Reports** — Fetch reports, validate claim-to-evidence lineage, RSS feed
- **Claims** — List extracted claims and their linked evidence
- **Search** — Cross-investigation ranked search, profiles, suggestions
- **Discovery** — Emergent term extraction and source concentration analysis
- **Research** — Trending topics, curated source profiles
- **Jobs** — Priority-aware background job queue with retry and dead-letter handling
- **Schedules** — Recurring job templates with tick-based enqueuing
- **Alerts** — Rule configuration, evaluation, event history
- **Admin** — API key management (create, list, revoke)
- **Audit** — Immutable operation audit trail
- **Dashboard** — Aggregate counters and summaries for UI widgets
- **Feedback** — Analyst feedback loop with iterative memory

Full endpoint list: start the server and visit `/docs` (Swagger UI).

## Testing

```bash
# Full test suite with coverage
pytest --cov=sts_monitor --cov-fail-under=75

# By marker
pytest -m unit
pytest -m integration
pytest -m simulation

# QA gates
./scripts/qa_local.sh        # full local QA
./scripts/qa_ci.sh           # CI-friendly split gate
./scripts/qa_security.sh     # security checks (pip-audit, authz surface)
```

Coverage is enforced at 75% minimum. Current coverage: ~85%.

## Docker

```bash
# Development (SQLite, single container)
docker build -t sts-monitor .
docker run -p 8080:8080 -e STS_AUTH_API_KEY=change-me sts-monitor

# Full stack (Postgres + Redis + Qdrant + API)
docker compose up -d

# Production with HTTPS (Caddy reverse proxy)
docker compose -f docker-compose.public.yml up -d
```

The Dockerfile includes health checks. The entrypoint runs Alembic migrations automatically before starting the server.

## Authentication

- All endpoints except `/health` and `/system/preflight` require `X-API-Key`.
- Root key via `STS_AUTH_API_KEY` env var (bootstrap/admin access).
- Scoped DB-backed keys with roles: `admin`, `analyst`, `viewer`.
- Mutating endpoints require at least `analyst` role.

```bash
curl -H "X-API-Key: your-key" http://localhost:8080/investigations
```

## Privacy

- Local SQLite by default — no data leaves your machine.
- Configurable PII redaction layer strips names, emails, and phone numbers before storage.
- Local LLM endpoint (Ollama) keeps AI processing on-premises.
- Privacy status inspectable via API.

## Backups

```bash
# SQLite database backup/restore
python scripts/db_backup.py
python scripts/db_restore.py backups/<backup-file>.db

# Full Git repository backup (bundle with all branches/tags/history)
python scripts/repo_backup.py              # create bundle
python scripts/repo_backup.py --verify     # create + verify integrity
python scripts/repo_backup.py --mirror URL # also push mirror to another remote

# Restore from bundle
git clone backups/sts-monitor_<timestamp>.bundle sts-monitor-restored
```

## Documentation

| Document | Description |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | End-to-end design and security model |
| [`docs/blockers-and-privacy.md`](docs/blockers-and-privacy.md) | Offline-LLM and internet integration risks |
| [`docs/capability-failure-modes.md`](docs/capability-failure-modes.md) | Per-capability failure analysis |
| [`docs/functional-vision-and-gaps.md`](docs/functional-vision-and-gaps.md) | Per-function improvement roadmap |
| [`docs/internet-deployment.md`](docs/internet-deployment.md) | Public internet deployment guide |
| [`docs/local-llm-analyst-policy.md`](docs/local-llm-analyst-policy.md) | Evidence-first local model policy |
| [`docs/offline-buildout-checklist.md`](docs/offline-buildout-checklist.md) | PC wiring and break-test execution |
| [`docs/osint-data-sources-reference.md`](docs/osint-data-sources-reference.md) | Data source reference |

## License

See repository for license details.
