#!/bin/bash
set -e

# Run Alembic migrations before starting the app
echo "Running database migrations..."
python -m alembic upgrade head 2>/dev/null || echo "Migrations skipped (tables may already exist via create_all)"

# Start the application
exec "$@"
