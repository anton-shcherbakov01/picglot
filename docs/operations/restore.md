# Restore

Companion to [backups.md](backups.md), which covers how the backups are made.
This page is what you follow when something is already broken — keep it short
enough to be usable at 3am.

## Before you start

1. **Stop writing to the target.** `docker compose stop api worker-cpu
worker-export worker-beat` — leave `postgres` up, it is the restore target.
2. Know which dump you are restoring and how old it is. Anything the users did
   after that timestamp is gone; say so before you begin, not after.

## Restore PostgreSQL

```bash
./scripts/restore.sh ./backups/20260803T120000Z/postgres_picglot.dump
```

Point it at a directory instead and it picks the newest `*.dump`:

```bash
./scripts/restore.sh ./backups
```

The script confirms before running `pg_restore --clean --if-exists
--no-owner`, which drops and recreates the objects it restores.

From the production `backup` container, the dumps live in the `backup-data`
volume as `/backups/<UTC timestamp>/postgres.dump`. Copy one out first:

```bash
docker compose cp backup:/backups/20260803T120000Z/postgres.dump ./restore.dump
./scripts/restore.sh ./restore.dump
```

## Restore object storage

Database rows reference storage keys, so a database restored to an older point
than storage will reference objects that still exist (harmless), and storage
restored older than the database will reference objects that do not (broken
downloads). Restore both to the same point where you can.

```bash
docker compose run --rm --no-deps \
  -v "$(pwd)/backups/20260803T120000Z/minio:/backup" \
  --entrypoint sh minio-init -c \
  "mc alias set restore-dst \"\$S3_ENDPOINT_URL\" \"\$S3_ACCESS_KEY_ID\" \"\$S3_SECRET_ACCESS_KEY\" && \
   mc mirror --overwrite /backup/\$S3_BUCKET restore-dst/\$S3_BUCKET"
```

## After restoring

```bash
docker compose up -d
docker compose exec api python -m picglot.cli health
```

Then check by hand, in this order:

1. `/health/ready` returns 200.
2. Sign in as a known account.
3. Open a project that existed before the incident and confirm its pages,
   regions and exports load — a green health check only proves the services
   are reachable, not that the data came back intact.
4. Run one file end to end through the pipeline.

## Expected data loss

`BACKUP_INTERVAL` defaults to 6 hours, so the worst case is 6 hours of work
plus however long the restore takes. If that is too much for your users, lower
the interval or move to managed PostgreSQL with point-in-time recovery — the
scripts here are a floor, not a target.

## If the restore itself fails

Do not retry blindly onto a half-restored database. Drop and recreate it
first, then restore again:

```bash
docker compose exec postgres psql -U "$POSTGRES_USER" -d postgres \
  -c "DROP DATABASE IF EXISTS $POSTGRES_DB;" -c "CREATE DATABASE $POSTGRES_DB;"
./scripts/restore.sh <dump>
```

If a second attempt fails the same way, the dump is likely damaged — move to
the next oldest and note the failure for the post-mortem
([incident-response.md](incident-response.md)).
