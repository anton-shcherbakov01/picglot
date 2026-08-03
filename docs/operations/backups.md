# Backups

## What is backed up

| Component | Method | Where |
|---|---|---|
| PostgreSQL | `pg_dump --format=custom` | `scripts/backup-loop.sh` (production container, automatic) or `scripts/backup.sh` (on demand) |
| Object storage | `mc mirror` from the S3-compatible bucket | `scripts/backup.sh` only — the automatic container has no S3 client |
| Configuration | `.env` key names only, values redacted | Both scripts, for recovering the *shape* of the config, never secrets |

## Automatic backups (production)

The `backup` service in `docker-compose.production.yml` runs
`scripts/backup-loop.sh` in a loop, dumping Postgres and pruning old dumps.
It is controlled by two variables in `.env`:

```bash
BACKUP_INTERVAL=21600          # seconds between dumps (default 6h)
BACKUP_RETENTION_DAYS=14
```

Dumps land in the `backup-data` volume, under `/backups/<UTC timestamp>/`.
This container only has a `postgres` client (its image is `postgres:17-alpine`)
so it cannot mirror object storage — run `scripts/backup.sh` separately
(cron, or a CI scheduled job) for that half.

## On-demand backup

```bash
./scripts/backup.sh ./backups
```

Dumps Postgres via `docker compose exec postgres pg_dump` and mirrors the
object storage bucket via the `minio-init` service's `mc` client. Requires
`POSTGRES_USER`/`POSTGRES_DB` and the `S3_*` variables from `.env` (the
script sources `.env` itself).

## Restore

```bash
./scripts/restore.sh ./backups/20260803T120000Z/postgres_lingoimage.dump
# or point it at a directory and it picks the newest *.dump:
./scripts/restore.sh ./backups
```

The script asks for confirmation before running `pg_restore --clean`, which
overwrites the target database.

## Restore drill

Backups nobody has restored are a hope, not a backup. Schedule a quarterly
drill:

1. Spin up a disposable host or VM with Docker.
2. `git clone` the repo, `cp .env.example .env` (fill in real values).
3. `docker compose up -d postgres`.
4. `./scripts/restore.sh <a real dump copied over>`.
5. `python -m lingoimage.cli health` — must be green.
6. Open the app, confirm a known project loads with its expected content.
7. Record how long the whole drill took; that number is your real RTO.

## Object storage

Enable bucket versioning or cross-region replication on whatever S3-compatible
service is in production — `scripts/backup.sh`'s mirror is a point-in-time
snapshot, not continuous protection. The lifecycle worker already deletes
expired artefacts on its own schedule (see [privacy-model.md](../security/privacy-model.md#retention));
backups should cover originals and paid exports, not every intermediate file.
