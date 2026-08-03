#!/usr/bin/env bash
# Restore PostgreSQL from a backup produced by scripts/backup.sh or the
# production backup-loop container. Usage:
#   ./scripts/restore.sh [backup_dir_or_dump_file]
set -euo pipefail

TARGET="${1:-./backups}"

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' .env)
  set +a
fi

DB_NAME="${POSTGRES_DB:-picglot}"
DB_USER="${POSTGRES_USER:-picglot}"

if [ -f "${TARGET}" ]; then
  DUMP="${TARGET}"
else
  DUMP="$(find "${TARGET}" -name '*.dump' -type f | sort | tail -1)"
fi

if [ -z "${DUMP:-}" ] || [ ! -f "${DUMP}" ]; then
  echo "[restore] No dump found under ${TARGET}" >&2
  exit 1
fi

echo "[restore] Source: ${DUMP}"
echo "[restore] Target database: ${DB_NAME} (user ${DB_USER})"
read -rp "This OVERWRITES the current database. Continue? [y/N] " confirm
[[ "${confirm}" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

COMPOSE_FILES=(-f docker-compose.yml)
[ -f docker-compose.production.yml ] && COMPOSE_FILES+=(-f docker-compose.production.yml)

docker compose "${COMPOSE_FILES[@]}" exec -T postgres pg_restore \
  -U "${DB_USER}" -d "${DB_NAME}" \
  --clean --if-exists --no-owner \
  < "${DUMP}"

echo "[restore] Done. Run 'python -m picglot.cli health' to verify."
