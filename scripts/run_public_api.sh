#!/usr/bin/env bash
set -euo pipefail

HOST="${STS_BIND_HOST:-0.0.0.0}"
PORT="${STS_API_PORT:-8080}"
WORKERS="${STS_UVICORN_WORKERS:-2}"

exec uvicorn sts_monitor.main:app --host "$HOST" --port "$PORT" --workers "$WORKERS"
