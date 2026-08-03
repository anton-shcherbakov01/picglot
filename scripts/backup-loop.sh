#!/bin/sh
# Runs inside the `backup` service (docker-compose.production.yml, image
# postgres:17-alpine). Loops forever, dumping PostgreSQL on an interval and
# pruning old dumps. Object storage is backed up separately from the host —
# see scripts/backup.sh — because this container has no S3/mc client.
set -eu

INTERVAL="${BACKUP_INTERVAL:-21600}"          # 6 hours
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
BACKUP_ROOT="/backups"
: "${POSTGRES_HOST:=postgres}"
: "${POSTGRES_USER:?POSTGRES_USER must be set}"
: "${POSTGRES_DB:?POSTGRES_DB must be set}"

export PGPASSWORD="${POSTGRES_PASSWORD:-}"

echo "[backup-loop] host=${POSTGRES_HOST} db=${POSTGRES_DB} interval=${INTERVAL}s retention=${RETENTION_DAYS}d"

while true; do
  STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
  DEST="${BACKUP_ROOT}/${STAMP}"
  mkdir -p "${DEST}"

  if pg_dump -h "${POSTGRES_HOST}" -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
      --format=custom --compress=6 --file="${DEST}/postgres.dump"; then
    echo "[backup-loop] ${STAMP} OK ($(du -sh "${DEST}/postgres.dump" | cut -f1))"
  else
    echo "[backup-loop] ${STAMP} FAILED" >&2
    rmdir "${DEST}" 2>/dev/null || true
  fi

  # Prune backups older than the retention window.
  find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -mtime "+${RETENTION_DAYS}" \
    -exec rm -rf {} + 2>/dev/null || true

  sleep "${INTERVAL}"
done
