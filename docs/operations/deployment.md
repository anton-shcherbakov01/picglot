# Deployment

Two supported shapes. Both use the same images; only the surrounding
infrastructure differs.

---

## Option 1 — single server (Docker Compose)

Suitable up to roughly 20 000 pages/day on 8 vCPU / 16 GB.

### 1. Prepare the host

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-plugin
sudo usermod -aG docker "$USER"
```

Point an A record at the host, open 80 and 443, and leave everything else closed.

### 2. Configure

```bash
git clone <repo> /opt/lingoimage && cd /opt/lingoimage
cp .env.example .env
```

Set at minimum:

```bash
ENVIRONMENT=production
DEBUG=false
LOG_FORMAT=json
SECRET_KEY=<python -c "import secrets;print(secrets.token_urlsafe(64))">
BRAND_DOMAIN=example.com
PUBLIC_WEB_URL=https://example.com
PUBLIC_API_URL=https://example.com
NEXT_PUBLIC_API_URL=https://example.com
NEXT_PUBLIC_SITE_URL=https://example.com
SESSION_COOKIE_SECURE=true
POSTGRES_USER=lingo
POSTGRES_PASSWORD=<strong>
POSTGRES_DB=lingoimage
REDIS_PASSWORD=<strong>
S3_ACCESS_KEY_ID=<strong>
S3_SECRET_ACCESS_KEY=<strong>
SEED_ENABLED=false
```

The configuration validator refuses to start if any of these is left at a
development value, and prints exactly which one. That is the intended way to
find mistakes — do not bypass it.

### 3. Certificates

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml run --rm certbot \
  certonly --webroot -w /var/www/certbot -d example.com --agree-tos -m ops@example.com
```

### 4. Start

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d
```

The `migrate` service runs Alembic once and the API waits for it to finish.

### 5. Verify

```bash
docker compose exec api python -m lingoimage.cli health
curl -fsS https://example.com/health/ready | jq
```

Then create the first administrator:

```bash
docker compose exec api python -m lingoimage.cli create-admin \
  --email ops@example.com --password '<strong>' --role superadmin
```

### 6. Load the baseline content

```bash
docker compose exec api python -m lingoimage.cli seed --baseline-only
```

`--baseline-only` loads plans, feature flags and SEO content but **not** demo
accounts. Never run a plain `seed` in production.

---

## Option 2 — managed infrastructure

Nothing here is cloud-specific; the same layout works on AWS, GCP, Azure,
Hetzner or Yandex Cloud.

| Component | Managed service | Notes |
|---|---|---|
| `web` | Any Node host, or Vercel | `output: standalone` is already configured |
| `api` | Container service behind a load balancer | 2+ instances, `/health/ready` as the probe |
| `worker-cpu` | Container service, scale on queue depth | Needs more memory than the API |
| `worker-beat` | Exactly **one** instance | Duplicates would double-schedule maintenance |
| Postgres | RDS / Cloud SQL / managed PG | Enable PITR |
| Redis | ElastiCache / Memorystore | Not a durable store; loss is survivable |
| Object storage | S3 / GCS / R2 | Block public access; lifecycle rules as a backstop |
| Secrets | Secrets Manager / Vault | Inject as environment variables |

Health probes:

* liveness → `GET /health/live` (no dependencies, fast)
* readiness → `GET /health/ready` (returns 503 when the database or storage is down)

Scale the API on CPU and request latency; scale `worker-cpu` on
`lingoimage_queue_depth`. See [scaling.md](scaling.md).

### CDN

Put a CDN in front of `/_next/static/*` (immutable, one-year cache). Never cache
`/api/*`, `/{locale}/app/*` or `/share/*` — those carry private results and
already send `Cache-Control: private, no-store`.

---

## Zero-downtime upgrades

1. Build and push the new images.
2. Run migrations first: `docker compose run --rm migrate`.
   Migrations must be backward compatible with the running version — add columns
   before writing to them, drop them a release later.
3. Roll the API, then the workers, then the web tier.
4. Watch the error rate and queue depth for ten minutes.

Rollback is the previous image tag plus, if a migration must be undone,
`alembic downgrade -1`.

---

## Production checklist

- [ ] `ENVIRONMENT=production`, `DEBUG=false`, `LOG_FORMAT=json`
- [ ] Unique `SECRET_KEY`, stored in a secrets manager
- [ ] `SESSION_COOKIE_SECURE=true`, HTTPS enforced, HSTS on
- [ ] `SEED_ENABLED=false`
- [ ] Database password and storage credentials rotated off the defaults
- [ ] Payment webhook secret set and the endpoint registered with the provider
- [ ] Backups running and a restore actually tested ([backups.md](backups.md))
- [ ] Prometheus scraping `/metrics`, alerts wired to a real channel
- [ ] Sentry DSN set
- [ ] Object storage: public access blocked, lifecycle rules applied
- [ ] Legal pages reviewed by counsel for your jurisdiction
- [ ] `python -m lingoimage.cli health` green
- [ ] One real file processed end to end through the public URL
