# Offline Buildout Checklist (PC Deployment)

## Required local components

- Python 3.11+
- STS API service (`uvicorn sts_monitor.main:app`)
- Local DB (default SQLite file `sts_monitor.db`, optional Postgres)
- Optional local vector DB (Qdrant)
- Optional local cache/queue (Redis)
- Local LLM runtime (for example, Ollama)

## Connection wiring

1. Set `.env` values:
   - `STS_DATABASE_URL`
   - `STS_LOCAL_LLM_URL`
   - `STS_LOCAL_LLM_MODEL`
   - `STS_WORKSPACE_ROOT`
   - `STS_AUTH_API_KEY`
2. Generate an API key: `python scripts/generate_api_key.py` and set `STS_AUTH_API_KEY`.
3. Run `./scripts/bootstrap.sh`.
4. Start STS API.
5. Call `GET /system/preflight`.
6. Confirm:
   - `database.ok == true`
   - `llm.reachable == true`
   - `llm.model_available == true`
   - `filesystem.workspace_root_exists == true`

## Break-test sequence

1. Create investigation.
2. Inject simulated data (`/ingest/simulated`) with noise.
3. Run pipeline with and without LLM.
4. Force LLM failure (stop local LLM) and verify fallback summary behavior.
5. Submit feedback and inspect memory.
6. Verify dashboard summary counters and latest report snapshots.

## Minimum hardening before production-like use

- Add API auth and user roles.
- Add migration tooling (Alembic) for schema evolution.
- Add connector retry/backoff and dead-letter capture.
- Add structured logging and metrics.
- Add backup policy for DB and evidence artifacts.


## One-command simulation

- `python scripts/simulate_full_workflow.py`
- Verifies: preflight, investigation creation, simulated ingest, RSS ingest failure handling, run (with/without LLM), feedback, memory, reports, and dashboard.
