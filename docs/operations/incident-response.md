# Incident response

## Severity levels

| Level | Definition | Response |
|---|---|---|
| SEV-1 | Service fully down, or data integrity at risk | Immediate |
| SEV-2 | Degraded — a tool or provider chain failing, users blocked from a workflow | < 30 min |
| SEV-3 | Minor — single provider down but the fallback chain is covering it | Next business day |

## Procedure

1. **Detect** — a Prometheus alert fires, or a user/support report arrives.
2. **Assess** —
   ```bash
   curl -fsS https://<domain>/health/ready | python3 -m json.tool
   curl -fsS https://<domain>/health/providers | python3 -m json.tool
   curl -fsS https://<domain>/api/v1/status | python3 -m json.tool
   ```
   Check Grafana for queue depth, error rate and provider circuit state.
3. **Mitigate.** Options in rough order of severity:
   - Roll back to the previous image tag (see "Rollback" below).
   - Flip `MAINTENANCE_MODE=true` and restart the `api`/`web` services to show
     the maintenance banner while you work — this does not stop workers
     draining the existing queue.
   - For a single failing provider, see
     [provider-outage.md](provider-outage.md) instead of a full incident.
4. **Communicate** — post a status-page incident so users aren't guessing:
   ```bash
   curl -X POST https://<domain>/api/v1/admin/status/incidents \
     -H 'Cookie: <admin session>' \
     -d '{"title": "...", "severity": "...", "components": ["api"]}'
   ```
5. **Resolve** — fix the root cause, then:
   ```bash
   curl -X POST https://<domain>/api/v1/admin/status/incidents/<id>/resolve \
     -H 'Cookie: <admin session>'
   ```
6. **Post-mortem** within 48 hours: timeline, root cause, what caught it (or
   didn't), and one or two concrete follow-ups — not a blameless-in-name-only
   document nobody reads.

## Rollback

```bash
# Redeploy the previous image tag (adjust to your actual CI/CD trigger).
docker compose -f docker-compose.yml -f docker-compose.production.yml \
  pull api web worker-cpu worker-export worker-beat   # if pinned by tag, adjust tag first
docker compose -f docker-compose.yml -f docker-compose.production.yml \
  up -d --no-deps api web worker-cpu worker-export worker-beat
```

If a migration must be undone (rare — migrations are meant to be
backward-compatible, see [deployment.md](deployment.md#zero-downtime-upgrades)):

```bash
docker compose exec api alembic -c apps/api/alembic.ini downgrade -1
```

## After a SEV-1 involving data

If the incident touched the database or object storage in a way you're not
fully sure of, stop and restore from the last known-good backup
([backups.md](backups.md)) rather than trying to hand-patch rows — a restore
you've drilled is safer than a guess under pressure.
