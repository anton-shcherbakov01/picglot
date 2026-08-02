#!/bin/sh
# API container entrypoint: optionally run migrations, then exec the CMD.
set -eu

if [ "${RUN_MIGRATIONS_ON_START:-false}" = "true" ]; then
  echo "[entrypoint] waiting for the database..."
  python -m lingoimage.cli wait-for-db --timeout "${DB_WAIT_TIMEOUT:-90}"
  echo "[entrypoint] applying migrations..."
  alembic -c /app/alembic.ini upgrade head
fi

if [ "${SEED_ON_START:-false}" = "true" ]; then
  echo "[entrypoint] seeding baseline data..."
  python -m lingoimage.cli seed --baseline-only
fi

exec "$@"
