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
4. Run `alembic upgrade head`.
5. Start STS API.
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


## Queue processing

- Enqueue jobs via API (`/jobs/enqueue/...`) for simulated ingest or pipeline runs.
- Process queued jobs with `python scripts/job_worker.py` (or `POST /jobs/process-next` for manual dev testing).


## Recurring schedules

- Create schedule templates with `POST /schedules`.
- Tick scheduler with `python scripts/scheduler_tick.py` (or `POST /schedules/tick`).
- Worker (`python scripts/job_worker.py`) then processes enqueued jobs by priority.


## QA operating model

- Local gate: `./scripts/qa_local.sh`
- CI gate: `./scripts/qa_ci.sh`
- Infra gate: `./scripts/qa_infra.sh`
- Security gate: `./scripts/qa_security.sh`
- Coverage target: enforced by pytest (`--cov-fail-under=75`).
- Test tiers: `unit`, `integration`, `simulation`, `slow`, `network`.

Recommended cadence:
1. Run local gate before every PR push.
2. Keep simulation in CI as a separate stage to isolate slow failures.
3. Track coverage trend and fail PRs that drop below threshold.


## Backup and restore drill

- Create backup: `python scripts/db_backup.py`
- Restore backup: `python scripts/db_restore.py backups/<backup-file>.db`
- Verify by rerunning `GET /system/preflight` and one investigation flow.


## Discovery and alerting drill

1. Create investigation and ingest simulated/trending observations.
2. Call `POST /investigations/{id}/discovery` and verify top terms/source breakdown.
3. Create a rule with `POST /alerts/rules` (low thresholds for test).
4. Run `POST /alerts/evaluate/{id}` and confirm events are returned.
5. Verify events via `GET /alerts/events/{id}` and dashboard alert counters.


## Internet exposure drill

1. Set `STS_PUBLIC_BASE_URL`, `STS_TRUSTED_HOSTS`, and `STS_CORS_ORIGINS` in `.env`.
2. Launch public stack: `docker compose -f docker-compose.public.yml up -d --build`.
3. Validate external HTTPS response: `curl -i https://<domain>/health`.
4. Validate config visibility: `GET /system/online-tools`.
5. (Optional) Set `STS_ALERT_WEBHOOK_URL`, trigger `/alerts/evaluate/{id}`, and verify webhook delivery.


## Infra QA drill

1. Run `./scripts/qa_infra.sh`.
2. Confirm migration graph check reports exactly one root and one head.
3. Confirm config surface check reports no missing env keys for `config.py`.
4. Confirm migration upgrade check passes on disposable DB.
5. Confirm truth harness check passes (`scripts/evaluate_truth_harness.py`).


## Security QA drill

1. Run `./scripts/qa_security.sh`.
2. Confirm `pip-audit` reports no known vulnerable package set for the installed environment.
3. Confirm secret-pattern scan returns no matches.
4. Confirm authz surface check passes through infra gate (`scripts/check_authz_surface.py`).
5. Confirm deployment surface check passes (`scripts/check_deployment_surface.py`).
6. Confirm README endpoint sync check passes (`scripts/check_readme_endpoint_sync.py`).


## Report lineage hard-gate drill

1. Set `STS_ENFORCE_REPORT_LINEAGE_GATE=true` and choose threshold with `STS_REPORT_MIN_LINEAGE_COVERAGE` (for example `0.8`).
2. Run a pipeline for an investigation with sparse evidence.
3. Confirm `/investigations/{id}/run` returns HTTP 409 when coverage is below threshold.
4. Lower threshold or add corroborating evidence and rerun to confirm successful publish.
