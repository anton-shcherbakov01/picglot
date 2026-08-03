# Scaling guide

## When to scale

| Signal                             | Threshold                                | Action                                       |
| ---------------------------------- | ---------------------------------------- | -------------------------------------------- |
| `picglot_queue_depth` (Prometheus) | Sustained > 200 for 10 min               | Add `worker-cpu` replicas                    |
| API p95 latency                    | > 3 s                                    | Add `api` replicas                           |
| Worker memory                      | > 80% of container limit                 | Increase memory or add replicas              |
| Postgres connections               | > 80% of `DATABASE_POOL_SIZE × replicas` | Add PgBouncer, or raise `DATABASE_POOL_SIZE` |
| `picglot_provider_circuit_open`    | Any provider open for > 5 min            | See [provider-outage.md](provider-outage.md) |

Numbers stay in Prometheus (`infra/monitoring/prometheus.yml`); wire alerts
to a channel a human actually reads, per the production checklist in
[deployment.md](deployment.md).

## Horizontal scaling

- **`api`** is stateless — sessions live in Postgres/Redis, not memory. Put
  2+ instances behind a load balancer with `/health/ready` as the probe.
- **`worker-cpu`/`worker-export`** scale on queue depth; each Celery worker
  process pulls from its assigned queues (`cpu`, `export`).
- **`worker-beat`** must stay at exactly one instance — it schedules
  periodic maintenance (lifecycle, retries), and two instances double-run
  everything it triggers.
- **`worker-gpu`** (behind the `gpu` compose profile) only matters once
  `ADVANCED_INPAINT_ENABLED=true` and a GPU-backed inpainting model is
  actually bundled; the classical strategies (Telea/NS/solid/overlay) run on
  CPU.

## Database

- Use managed Postgres with point-in-time recovery in production, not the
  bundled container.
- Put PgBouncer in transaction-pooling mode in front of it once API replicas
  multiply connection counts.
- Route admin-dashboard and reporting queries to a read replica if the
  primary starts showing contention from them.

## Object storage

S3-compatible storage scales without any action from this app. Enable
lifecycle rules as a backstop to the retention worker (see
[privacy-model.md](../security/privacy-model.md#retention)), and put a CDN in
front of `/_next/static/*` — never in front of `/api/*`, `/{locale}/app/*` or
`/share/*`, which already send `Cache-Control: private, no-store`.

## Redis

Not a durable store here — cache, rate limiting and the Celery broker only.
Losing it degrades rate limiting to fail-open and clears the cache; it does
not lose jobs already committed to Postgres. A managed Redis (ElastiCache/
Memorystore) is fine without HA if you can tolerate a brief cache-cold period
on failover.
