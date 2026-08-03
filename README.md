# LingoImage AI

Translate text on photos, screenshots and in documents, keeping the original
layout, colours and position.

One engine drives thirteen tools: image translation, photo and screenshot
translation, OCR to text, photo → Word, table photo → Excel, handwriting
recognition, PDF translation, PDF OCR (searchable PDF), a document scanner,
receipt and invoice extraction, and batch processing.

---

## What actually runs

The pipeline is real, not a wrapper around one API call:

```
upload → validate → preprocess → detect → recognise → normalise → layout
       → translate → remove original text → typeset translation → export
```

* **Preprocessing** — EXIF orientation, deskew via Hough line analysis,
  perspective correction, shadow flattening, CLAHE contrast, orientation
  detection (0/90/180/270).
* **Recognition** — provider chain with automatic fallback: RapidOCR (bundled
  ONNX models, works offline), Tesseract, Google Vision, Azure AI Vision, AWS
  Textract, Yandex Vision, or a multimodal LLM for handwriting and hard pages.
* **Layout reconstruction** — lines are regrouped into paragraphs, columns are
  found from vertical gutters, headings are detected against a *length-weighted*
  body size, hyphenated line breaks are repaired, ink and paper colours are
  sampled from the image.
* **Translation** — DeepL, Google, Yandex, Azure, an LLM, or offline Argos
  models. Glossary → translation memory → fuzzy match → cache → provider, with
  URLs, emails, product codes and template variables masked before they leave.
* **Removing the original text** — the strategy is chosen from how textured the
  background *around* each block is: flat fill, Telea inpainting, Navier-Stokes
  inpainting, or an honest translucent plate on photographic backgrounds.
* **Typesetting** — font size is binary-searched against real glyph metrics
  (never an average character width), with script-aware wrapping, CJK line-break
  rules, RTL shaping and reordering, and a warning when text genuinely does not fit.
* **Exports** — PNG, JPG, WEBP, PDF, searchable PDF (verified invisible text
  layer), bilingual PDF, DOCX, XLSX with typed cells, TXT, Markdown, CSV, JSON, ZIP.

## Requirements

| | Minimum | Notes |
|---|---|---|
| Python | 3.11 | 3.12 in the Docker images |
| Node | 20.11 | 22 in the Docker images |
| PostgreSQL | 15 | 17 in `docker-compose.yml` |
| Redis | 7 | optional in development — degrades to an in-process fallback |
| Object storage | any S3 API | MinIO locally |

Docker Desktop (or Docker Engine + compose v2) is enough to run everything.

## Quick start

```bash
cp .env.example .env
make setup        # virtualenv + npm install
make up           # postgres, redis, minio, mailpit, api, workers, web
make migrate
make seed
make health
```

Then open <http://localhost:3000>. The API is on <http://localhost:8000>, with
interactive docs at <http://localhost:8000/docs>.

**Windows without GNU make** — the same targets exist as npm scripts and a
PowerShell shim:

```bash
npm run setup
```

```bash
./scripts/make.ps1 up
```

### Running without Docker

```bash
python -m venv .venv
.venv/Scripts/pip install -e "apps/api[dev,ocr]"
.venv/Scripts/python -m lingoimage.cli health
.venv/Scripts/uvicorn lingoimage.main:app --reload
```

With `QUEUE_BACKEND=inline` and `STORAGE_BACKEND=local` you need neither Redis
nor MinIO; jobs execute in an in-process thread pool through exactly the same
code path as the Celery workers.

## Commands

| Command | What it does |
|---|---|
| `make setup` | Create the virtualenv, install Python and Node dependencies, create `.env` |
| `make dev` | Infrastructure in Docker, app processes locally with hot reload |
| `make up` / `make down` | Start / stop the full Docker stack |
| `make migrate` | Apply Alembic migrations |
| `make seed` | Plans, feature flags, SEO content, blog posts, demo accounts |
| `make health` | Check database, Redis, storage, OCR, translation, email, fonts |
| `make lint` / `make typecheck` | Ruff + ESLint, mypy + tsc |
| `make test` / `make test-e2e` | pytest and Playwright |
| `make build` | Production build |
| `make backup` / `make restore` | Postgres and object storage |

`python -m lingoimage.cli --help` lists the operational commands
(`health`, `seed`, `create-admin`, `openapi`, `lifecycle`,
`install-language-pack`, `config`).

## Configuration

Everything is environment-driven and validated at startup —
`apps/api/lingoimage/core/config.py` is the single source of truth, and
`.env.example` documents every variable. In production the validator refuses to
start on a development `SECRET_KEY`, `DEBUG=true`, insecure cookies, a local
storage backend, a missing payment webhook secret, or `SEED_ENABLED=true`, and
prints exactly what is wrong.

The product name, domain, locales, enabled tools, limits, retention, provider
priorities, billing provider and quotas are all configuration — none of them are
hard-coded in business logic.

### Working without paid API keys

A fresh install performs recognition, layout analysis, editing and every export
with no keys at all, using the bundled RapidOCR models. Translation needs either
a provider key or the offline Argos models
(`python -m lingoimage.cli install-language-pack en ru`); until one is present
the interface says so plainly instead of returning untranslated text.

Set `LOCAL_ONLY_PROCESSING=true` to forbid every external provider outright.

## Repository layout

```
apps/api/        FastAPI service, vision pipeline, workers  (package: lingoimage)
apps/web/        Next.js 15 App Router front end
infra/           Dockerfiles, nginx, Prometheus/Grafana
docs/            architecture, ADRs, API, operations, security, SEO, testing
scripts/         setup, backup, restore, hooks
tests/           shared fixtures, golden files, load tests
```

Backend detail: [`apps/api/README.md`](apps/api/README.md).
Architecture: [`docs/architecture/system-overview.md`](docs/architecture/system-overview.md).

## Testing

```bash
make test
```

87 tests cover the credit ledger (pricing, idempotency, refunds, balance
integrity), the vision primitives (geometry, layout grouping, column detection,
OCR normalisation, font fitting, wrapping, RTL, table typing), the full HTTP
pipeline (upload → OCR → translate → render → nine export formats, with the
searchable PDF's text layer read back out of the produced file), and security
behaviour (IDOR, magic-byte validation, filename sanitisation, PDF JavaScript
stripping, password and token hashing, webhook signing and replay, SSRF).

They run against SQLite, local storage and the inline queue — the same
application code as production, with no mocked internals.

## Security and privacy

* Uploads are identified by magic bytes, not extension; executables and
  disguised files are refused, PDFs are stripped of JavaScript and auto-actions.
* Storage keys are random, buckets are private, downloads go through short-lived
  signed URLs.
* Passwords use Argon2id; sessions, API keys and share tokens are stored only as
  keyed hashes.
* Document text never reaches logs or analytics — the log processor drops those
  keys and the analytics endpoint enforces a property allow-list.
* Document content is untrusted input to any LLM: it is delimited, the system
  prompt forbids following instructions found inside it, no tools are exposed
  and the reply must satisfy a strict schema.

Details: [`docs/security/threat-model.md`](docs/security/threat-model.md) and
[`docs/security/privacy-model.md`](docs/security/privacy-model.md).

## Production

Start with [`docs/operations/hosting-quickstart.md`](docs/operations/hosting-quickstart.md)
for the condensed single-server path (including sharing a server with other
projects). See [`docs/operations/deployment.md`](docs/operations/deployment.md)
for the managed-infrastructure path and upgrades, plus
[`backups.md`](docs/operations/backups.md), [`scaling.md`](docs/operations/scaling.md),
[`incident-response.md`](docs/operations/incident-response.md) and
[`provider-outage.md`](docs/operations/provider-outage.md).

`PROJECT_STATUS.md` records what is implemented, what was verified and how, and
what still requires external accounts or credentials.

## Licence

Proprietary — see [`LICENSE`](LICENSE). Bundled third-party components keep
their own licences.
