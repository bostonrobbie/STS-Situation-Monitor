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

```bash
uvicorn sts_monitor.main:app --reload --port 8080
```

### 4) Optional infrastructure

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
