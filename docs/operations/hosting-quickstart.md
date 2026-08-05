# Hosting quickstart — single server, shared with other projects

The condensed, verified path from a bare VPS to a running instance. For the
managed-infrastructure shape and the zero-downtime upgrade procedure see
[deployment.md](deployment.md); for what to do when it breaks, see
[incident-response.md](incident-response.md).

Assumes: Ubuntu 22.04/24.04, Docker with the compose plugin, and that **this
server already runs other projects** — so every step below is written to
avoid trampling them.

---

## 0. Before anything: check what's already there

```bash
bash scripts/server-diagnostics.sh > diag.txt 2>&1
```

Read-only survey of ports, containers, reverse proxies, certificates and
disk. The three things that decide the whole plan:

1. **Is something already on :80/:443?** If yes, this stack's bundled nginx
   must not publish those ports — see "Sharing the server" below.
2. **Is there already a Postgres/Redis on the host?** You can reuse them, but
   the bundled containers are isolated and simpler; the only real cost is RAM.
3. **Free RAM and disk.** The full stack wants ~8–16 GB RAM to be comfortable
   (Postgres 4g + worker-cpu 8g limits are set in the production compose) and
   ~40 GB disk before uploads.

---

## 1. Requirements

|      | Minimum   | Comfortable |
| ---- | --------- | ----------- |
| vCPU | 4         | 8           |
| RAM  | 8 GB      | 16 GB       |
| Disk | 40 GB SSD | 100 GB SSD  |

Nothing here needs a GPU. A fresh install performs OCR, editing and every
export with **no API keys at all** (bundled RapidOCR); translation needs
either a provider key or the offline Argos models.

---

## 2. Clone and configure

```bash
sudo mkdir -p /opt/picglot && sudo chown "$USER" /opt/picglot
git clone <your-repo> /opt/picglot && cd /opt/picglot
cp .env.example .env
```

Edit `.env`. The values that actually matter, and the traps:

```bash
# --- runtime ---
ENVIRONMENT=production
DEBUG=false
LOG_FORMAT=json
SECRET_KEY=<python3 -c "import secrets;print(secrets.token_urlsafe(64))">
SEED_ENABLED=false
SESSION_COOKIE_SECURE=true

# --- domain (all five must agree, or OAuth/CORS/canonical URLs break) ---
BRAND_DOMAIN=example.com
PUBLIC_WEB_URL=https://example.com
PUBLIC_API_URL=https://example.com
NEXT_PUBLIC_API_URL=https://example.com
NEXT_PUBLIC_SITE_URL=https://example.com

# --- database ---
# TRAP: the app reads DATABASE_URL; the postgres container reads POSTGRES_*.
# They are separate variables that must describe the SAME credentials, and in
# production the host is the compose service name `postgres`, NOT localhost.
DATABASE_URL=postgresql+psycopg://picglot:<strong-pw>@postgres:5432/picglot
POSTGRES_USER=picglot
POSTGRES_PASSWORD=<strong-pw>
POSTGRES_DB=picglot

# --- redis ---
# TRAP: production compose starts redis with --requirepass, so the password
# must ALSO be embedded in REDIS_URL or nothing can connect.
REDIS_PASSWORD=<strong-pw>
REDIS_URL=redis://:<strong-pw>@redis:6379/0

# --- object storage (MinIO reuses these under MINIO_ROOT_USER/PASSWORD) ---
S3_ACCESS_KEY_ID=<strong>
S3_SECRET_ACCESS_KEY=<strong>
S3_ENDPOINT_URL=http://minio:9000

# --- object storage as the browser sees it ---
# TRAP: the two below are a pair, and BOTH are needed for pictures to appear.
# S3_PUBLIC_ENDPOINT_URL is the address presigned URLs are signed against; leave
# it empty and they point at `minio:9000`, which only the cluster can resolve.
# NEXT_PUBLIC_S3_URL puts that same origin in the Content-Security-Policy;
# leave it empty and the browser blocks every image before a request is made —
# the store stays healthy, the object is there, the logs stay clean, and the
# editor shows an empty canvas. Give the store its own hostname and certificate.
S3_PUBLIC_ENDPOINT_URL=https://s3.example.com
NEXT_PUBLIC_S3_URL=https://s3.example.com

# --- backups ---
BACKUP_INTERVAL=21600
BACKUP_RETENTION_DAYS=14
```

Optional, each independently skippable: `DEEPL_API_KEY` (or another
translation provider), `STRIPE_*`/`YOOKASSA_*` for payments (otherwise set
`BILLING_ENABLED=false`), `RESEND_API_KEY`/`SMTP_*` for email, `SENTRY_DSN`.

The config validator refuses to start on a development `SECRET_KEY`,
`DEBUG=true`, insecure cookies, local storage backend, missing payment
webhook secret, or `SEED_ENABLED=true` — and names exactly which one. Let it
do its job rather than working around it.

---

## 3. DNS

`A` record for `example.com` → server IP. Verify before requesting a
certificate, or the ACME challenge fails:

```bash
dig +short example.com
```

---

## 4. TLS certificate — order matters

**This is the step most likely to go wrong.** The bundled nginx config's
`:443` block references
`/etc/letsencrypt/live/${BRAND_DOMAIN}/fullchain.pem`, so **nginx cannot
start until the certificate already exists** — and `certbot --webroot` needs
something already serving port 80. Chicken and egg.

Break it with a one-off `--standalone` issuance, where certbot binds :80
itself (nothing else is running yet):

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml \
  run --rm --service-ports --entrypoint certbot certbot \
  certonly --standalone \
  -d example.com \
  --agree-tos -m ops@example.com --non-interactive
```

If port 80 is already taken by another project's web server, use that
server's webroot instead:

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml \
  run --rm -v /var/www/html:/var/www/certbot --entrypoint certbot certbot \
  certonly --webroot -w /var/www/certbot -d example.com \
  --agree-tos -m ops@example.com --non-interactive
```

After the first certificate exists, the long-running `certbot` service
renews it every 12h via `--webroot`, which works because nginx is up by then
and serves `/.well-known/acme-challenge/`.

---

## 5. Start

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d --build
```

The `migrate` service runs Alembic once; `api` waits for it to complete.
First build pulls and compiles a lot — expect 5–15 minutes.

---

## 6. Seed and create an admin

```bash
# Plans, feature flags, SEO content — NO demo accounts.
docker compose exec api python -m picglot.cli seed --baseline-only

docker compose exec api python -m picglot.cli create-admin \
  --email ops@example.com --password '<strong>' --role superadmin
```

Never run a plain `seed` in production — it creates the demo accounts whose
passwords are in `.env.example`, i.e. public.

---

## 7. Verify

```bash
docker compose exec api python -m picglot.cli health
curl -fsS https://example.com/health/ready | python3 -m json.tool
docker compose ps
```

Then do the one test that actually matters: open `https://example.com`,
upload a real photo with text on it, and confirm you get a translated image
back.

---

## 8. Payments webhook (only if billing is on)

Stripe Dashboard → Developers → Webhooks → endpoint
`https://example.com/api/v1/billing/webhooks/stripe`, events:
`checkout.session.completed`, `invoice.paid`, `invoice.payment_failed`,
`customer.subscription.updated`, `customer.subscription.deleted`,
`charge.refunded`. Put the signing secret in `STRIPE_WEBHOOK_SECRET` and
restart the API. The config validator refuses to start with billing enabled
and no webhook secret, so you cannot forget this one silently.

---

## Sharing the server with other projects

If something already owns :80/:443, do **not** publish this stack's nginx on
them. Create a `docker-compose.override.yml` (git-ignored, local to this
server):

```yaml
services:
  nginx:
    ports: !override
      - "127.0.0.1:8080:80"
```

Then point the host's existing reverse proxy at `127.0.0.1:8080`. A minimal
nginx server block on the host:

```nginx
server {
    listen 443 ssl;
    server_name example.com;
    ssl_certificate     /etc/letsencrypt/live/example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;

    client_max_body_size 210m;          # uploads are large

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Job progress is Server-Sent Events — buffering must be off or the
    # progress bar freezes until the job finishes.
    location ~ ^/api/v1/jobs/[^/]+/events$ {
        proxy_pass http://127.0.0.1:8080;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_set_header Connection '';
        proxy_http_version 1.1;
    }
}
```

Two other collision points on a shared box:

- **Ports** — the production compose already unpublishes Postgres, Redis and
  MinIO (`ports: []`), so they only exist on the internal compose network.
  Nothing to do.
- **Compose project name** — this stack sets `name: picglot`, so its
  containers, networks and volumes are namespaced and won't collide with
  another project's `postgres`/`redis` containers.

---

## Costs, roughly

| Shape                                                         | Monthly  |
| ------------------------------------------------------------- | -------- |
| Single VPS 8 vCPU / 16 GB (Hetzner, Timeweb, DO)              | $25–50   |
| Managed starter (container service + managed PG + Redis + S3) | $80–150  |
| Managed under real load (2 API + 4 workers + CDN)             | $200–500 |

Plus domain (~$10/yr), payment processor fees, and per-provider translation
costs if you enable a paid one. `LLM_DAILY_COST_LIMIT_USD` and the
per-provider caps are enforced in code — set them before enabling any paid
provider, not after the first surprise invoice.

---

## Production checklist

- [ ] `ENVIRONMENT=production`, `DEBUG=false`, `LOG_FORMAT=json`
- [ ] Unique `SECRET_KEY`
- [ ] `SESSION_COOKIE_SECURE=true`, HTTPS enforced
- [ ] `SEED_ENABLED=false`
- [ ] `DATABASE_URL` host is `postgres`, credentials match `POSTGRES_*`
- [ ] `REDIS_URL` embeds `REDIS_PASSWORD`
- [ ] Payment webhook secret set and endpoint registered
- [ ] `scripts/backup.sh` runs, and a restore has actually been tested
- [ ] Prometheus scraping `/metrics`, alerts routed somewhere human
- [ ] Object storage public access blocked
- [ ] Legal pages reviewed for your jurisdiction
- [ ] `python -m picglot.cli health` green
- [ ] One real file processed end to end through the public URL
