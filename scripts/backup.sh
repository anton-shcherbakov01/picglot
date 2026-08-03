#!/usr/bin/env bash
# On-demand backup: PostgreSQL dump + object storage mirror.
# Run from the repo root, next to docker-compose.yml. Usage:
#   ./scripts/backup.sh [backup_dir]
set -euo pipefail

BACKUP_DIR="${1:-./backups}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${BACKUP_DIR}/${TIMESTAMP}"
mkdir -p "${DEST}"

COMPOSE_FILES=(-f docker-compose.yml)
[ -f docker-compose.production.yml ] && COMPOSE_FILES+=(-f docker-compose.production.yml)
COMPOSE=(docker compose "${COMPOSE_FILES[@]}")

# Load .env for the values this script itself needs (POSTGRES_*, S3_*).
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' .env)
  set +a
fi

DB_NAME="${POSTGRES_DB:-lingoimage}"
DB_USER="${POSTGRES_USER:-lingo}"

echo "[backup] Dumping PostgreSQL (${DB_NAME})..."
"${COMPOSE[@]}" exec -T postgres pg_dump \
  -U "${DB_USER}" -d "${DB_NAME}" \
  --format=custom --compress=6 \
  > "${DEST}/postgres_${DB_NAME}.dump"
echo "[backup] PostgreSQL dump saved -> ${DEST}/postgres_${DB_NAME}.dump"

echo "[backup] Mirroring object storage bucket '${S3_BUCKET:-lingoimage}'..."
mkdir -p "${DEST}/minio"
"${COMPOSE[@]}" run --rm --no-deps \
  -v "$(pwd)/${DEST}/minio:/backup" \
  --entrypoint sh \
  minio-init -c "
    mc alias set backup-src '${S3_ENDPOINT_URL:-http://minio:9000}' '${S3_ACCESS_KEY_ID:-lingoimage}' '${S3_SECRET_ACCESS_KEY:-lingoimage-dev-secret}' >/dev/null &&
    mc mirror --overwrite backup-src/${S3_BUCKET:-lingoimage} /backup
  " || echo "[backup] Object storage mirror failed or minio-init unavailable — check manually."

# .env keys without values, so the shape of the config is recoverable without leaking secrets.
[ -f .env ] && sed -E 's/=.*/=<redacted>/' .env > "${DEST}/env-keys.txt"

echo "[backup] Done -> ${DEST}"
du -sh "${DEST}"
