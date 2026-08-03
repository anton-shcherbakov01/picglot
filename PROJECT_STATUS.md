# PROJECT_STATUS

**PicGlot** — 2026-08-03

25 900 lines of Python across 81 files, 12 700 lines of TypeScript across 71
files, 51 database tables, 131 API endpoints, 13 tools, 26 languages,
10 fully translated locales.

Everything below marked ✅ was executed on this machine and its output checked,
not just written. Everything marked ⚠️ or ❌ says plainly what is missing.

---

## 1. Verification results

| Gate             | Command                        | Result                                             |
| ---------------- | ------------------------------ | -------------------------------------------------- |
| Python lint      | `ruff check apps/api`          | ✅ All checks passed                               |
| Python format    | `ruff format --check apps/api` | ✅ 88 files clean                                  |
| Python tests     | `pytest apps/api/tests`        | ✅ **106 passed, 1 skipped** (57 s)                |
| Python typecheck | `mypy apps/api/picglot`        | ❌ **72 pre-existing errors** in 23 files — see §3 |
| TS typecheck     | `tsc --noEmit`                 | ✅ clean (strict, `noUncheckedIndexedAccess`)      |
| Web lint         | `next lint --max-warnings 0`   | ✅ No warnings or errors                           |
| Web build        | `next build`                   | ✅ **236 static pages** generated                  |
| API boot         | `import picglot.main`          | ✅ 131 routes, OpenAPI generates                   |

The one skip is `test_concurrent_charges_never_oversell`: the guarantee comes
from `SELECT … FOR UPDATE`, which SQLite does not have. It is marked
`@pytest.mark.integration` and skips with a message rather than passing for the
wrong reason.

### What the tests actually prove

Not mocks — the suite runs the real pipeline against SQLite, local storage and
the inline queue, which execute the same application code as production.

- A guest uploads a PNG, RapidOCR recognises it, and the recognised text
  contains the words that were drawn into the image.
- Translation is applied and a rendered image is produced.
- Layout analysis identifies the heading and returns blocks in reading order.
- Eight export formats are produced and their magic bytes checked.
- **The searchable PDF is re-opened with PyMuPDF and its invisible text layer
  read back** — the feature is verified from the artefact, not from the code path.
- A ruled table is detected, its cells typed as numbers, and XLSX exported.
- Charging the same job three times debits once; refunds are capped; the wallet
  always equals the ledger sum.
- Another account gets 404 (not 403) on someone else's project.
- Fake MIME types, disguised executables and traversal filenames are refused;
  PDF JavaScript is stripped.

---

## 2. What is implemented

### Processing engine ✅

| Stage       | Implementation                                                                                                                                                                      |
| ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Preprocess  | EXIF orientation, metadata stripping, Hough-line deskew, perspective correction, shadow flattening, CLAHE, denoise, sharpen, adaptive threshold, orientation detection, bomb guards |
| Recognise   | Provider chain with fallback: RapidOCR (bundled ONNX, offline), Tesseract, Google Vision, Azure AI Vision, AWS Textract, Yandex Vision, multimodal LLM                              |
| Normalise   | Artefact removal, hyphenation repair, context-guarded O↔0 / l↔1 repair, URL/email protection, per-correction revert, raw OCR preserved separately                                   |
| Layout      | Line→paragraph grouping, column detection via vertical gutters, length-weighted heading detection, ink/paper colour sampling, reading order, region typing                          |
| Translate   | DeepL, Google, Yandex, Azure, LLM, offline Argos, test echo. Glossary → TM → fuzzy → cache → provider, with placeholder masking                                                     |
| Remove text | Auto strategy from _surrounding_ texture: solid fill, Telea, Navier-Stokes, honest translucent plate; brush/eraser hooks; quality score                                             |
| Typeset     | Binary-search fit on real glyph metrics, CJK line-break rules, RTL shaping + reordering, vertical CJK, outline/shadow, overflow reported not clipped                                |
| Tables      | Ruled (grid recovery) and unruled (position clustering), cell typing incl. EU/US number formats, ambiguity flagged                                                                  |
| Quality     | Seven weighted signals, every factor listed with the blocks that caused it; explicitly labelled confidence, not measured accuracy                                                   |

Fonts: 236 files discovered on this machine, **all 12 advertised scripts covered**
(Latin, Cyrillic, Arabic, Hebrew, Han ×2, Kana, Hangul, Devanagari, Thai,
Georgian, Armenian) — verified by test, with cmap coverage checked per string.

### Exports ✅

PNG, JPG, WEBP, PDF, searchable PDF, bilingual PDF, DOCX, XLSX, TXT, Markdown,
CSV, JSON, ZIP. Re-exporting identical settings reuses the artefact and is free.

### Platform ✅

Auth (password, magic link, Google OAuth, TOTP 2FA + backup codes, sessions,
lockout), guest sessions that convert to accounts without losing work, projects
with soft delete and trash, jobs with staged progress over SSE, an append-only
credit ledger, Stripe and YooKassa adapters, signed outbound webhooks with
retry/replay, share links with password/expiry/view caps, glossaries,
translation memory, admin API with mandatory reasons and an audit trail,
first-party analytics with a server-side allow-list, and a lifecycle worker that
enforces retention.

### Web app ✅

236 prerendered pages: home, 13 tool pages, format and language-pair landing
pages, pricing, API docs, supported languages, status, blog, contact, five legal
pages, security, auth flows, dashboard, account area, project editor, admin
panel, share view — across 10 locales, with sitemap, robots, manifest,
structured data, hreflang, dark mode and a keyboard-navigable editor whose block
list doubles as the accessible alternative to the image overlay.

**All ten locales are fully translated** (`en ru es de fr pt tr id pl uk`);
`MISSING_TRANSLATIONS` in `apps/web/src/lib/messages.ts` is now empty, and the
`Messages` type makes a dropped or misspelled key a compile error.

### Admin UI ✅

`/{locale}/admin` — dashboard (volume, success/error rates, job duration
percentiles, revenue, provider cost, storage), users (search, detail, credit
adjustment, suspend/reactivate), jobs (filter, timeline, provider calls, retry,
refund), providers (config, 24h usage, live health), feature flags (toggle,
rollout, kill switch), support tickets, status incidents (open/resolve) and the
audit log. Client-rendered and `noindex`, since every response depends on the
caller's admin role. Every mutating action prompts for the reason the API
requires and records it in the audit trail.

### Editor undo/redo ✅

100-step history. Because block edits are server-authoritative with optimistic
version locking, undo replays an inverse `PATCH` rather than rewinding local
state; a new edit discards the redo branch, and a stale version surfaces the
same conflict message as any other concurrent edit.

### Account area ✅

`/{locale}/app/account` — usage and the full credit ledger, billing (plans,
subscription, cancel, invoices, provider-hosted checkout), API keys, webhooks
(create, rotate secret, delivery log, replay), glossaries, translation memory,
profile, security (password, TOTP 2FA with backup codes, active sessions,
sign out everywhere) and data (JSON export, retention, account deletion).
Client-rendered and `noindex`. One-time secrets — API keys, webhook secrets,
backup codes — stay on screen until dismissed, because re-fetching never
shows them again.

### Batch ✅

`POST /api/v1/batch` accepts many files, validates **all** of them before
creating anything, then fans out to one child job per file under a parent batch
job. The UI at `/{locale}/batch` takes a drop of files or a folder, shows
per-file status from the children rather than local bookkeeping, and offers the
assembled ZIP (with `report.csv` inside) plus retry-failed-only when some files
did not make it.

### Localised SEO slugs ✅

`ru` publishes five tools under Russian paths (`/ru/perevod-po-foto`,
`/tekst-s-kartinki`, `/foto-v-word`, `/foto-v-excel`, `/perevod-pdf`). The map
lives in `LOCALIZED_SLUGS` and is served through `/api/v1/config`, so the
backend stays the single source of truth. It is a **replacement**, not an
addition: the English path redirects, hreflang follows each locale's own slug,
and startup assertions plus `test_seo.py` guard against slug collisions.

### Team / workspace ✅

`/api/v1/workspaces` — create, rename, soft-delete, members with roles
(owner/admin/editor/viewer) and per-member monthly credit limits, invitations
by email with a single-use expiring token, revoke, and ownership transfer that
leaves exactly one owner and demotes the previous one to admin rather than
locking them out. Non-membership returns **404, not 403**, so a stranger cannot
confirm a workspace exists. 14 HTTP-level tests cover the authorisation rules,
including that an invitation is addressed to a person rather than to whoever
holds the link. The Team tab in the account area drives all of it.

### Canvas editor ✅

Konva stage for pointer work: wheel and pinch zoom about the cursor, pan,
fit-to-screen, select/move/resize/rotate a block through a transformer, drag
polygon vertices, and a brush/eraser that paints an inpainting mask. The mask
is flattened to a PNG and sent with the re-render, where it reaches the
`user_mask` argument the inpainting code already accepted but no endpoint could
supply. Compare view is a real before/after wipe driven by a range input, so it
works from the keyboard.

Geometry edits go through the same `PATCH` path as text, so optimistic version
locking, the 100-step history and conflict reporting apply to dragging a box
exactly as they do to retyping it. The block list beside the canvas remains the
keyboard and screen-reader path — a bitmap cannot carry an accessibility tree,
so the list is the accessible equivalent rather than an afterthought, and the
stage itself is `aria-hidden`.

### PWA ✅

Service worker caches the app shell and immutable `/_next/static/` assets, and
explicitly never caches `/api/`, `/app/`, `/share/` or `/admin` — results and
share links stay off disk.

---

## 3. Gaps — stated plainly

### ⚠️ Docker Compose was not executed

Docker Desktop's engine was not running on this machine, so the compose stack,
the Dockerfiles and the nginx config are **written but not executed**. Everything
they orchestrate was verified natively instead (API, workers via the inline
queue, migrations, seed, full pipeline, exports). Expect the usual first-run
friction: image build times and any base-image drift.

### ⚠️ Workspace billing is per-user, not per-seat

Workspaces, roles, invitations and per-member credit limits all work, and jobs
and wallets are workspace-aware. What is _not_ built is seat-based pricing: a
workspace inherits its owner's plan, and there is no separate subscription
object for a team. Charging a company per seat needs a pricing decision first,
not more code.

### ⚠️ Playwright E2E is smoke-level

`apps/web/e2e/smoke.spec.ts` covers page rendering, the locale redirect, the
theme toggle and the auth forms. The full upload → translate → export journey
is still covered only at the HTTP level in `apps/api/tests/test_pipeline.py`,
which needs no browser or file fixtures.

### ⚠️ Backend mypy is not clean

`ruff check`, `ruff format --check`, `tsc --noEmit`, `next lint` and `pytest`
all pass. `mypy apps/api/picglot` reports **72 pre-existing errors across 23
files** — mostly missing third-party stubs (celery, sentry_sdk, opentelemetry)
plus a handful of real `str | None` argument mismatches in `pipeline.py`,
`batch.py`, `jobs.py` and `account.py`. `make typecheck` therefore fails today.

### ❌ Golden fixture corpus

`tests/golden/` holds only a README explaining why it is empty. The suite
generates its fixtures programmatically (synthetic signs and ruled tables) —
deliberate, since committing third-party documents would be a licensing
problem, but a curated corpus of real-world photographs is what would catch
regressions in the preprocessing heuristics.

### ❌ Advanced inpainting model

The hook (`vision/advanced_inpaint.py`) and configuration exist; no model is
bundled. The classical strategies are what runs.

### ❌ Docker Compose still not executed

Unchanged from the note above: the compose stack has never been run on this
machine. The first-run instructions in
`docs/operations/hosting-quickstart.md` were derived by reading the compose
files, Dockerfiles and nginx config — including a certificate-ordering bug
they fix — but they have not been executed end to end.

---

## 4. Running it

```bash
cp .env.example .env
make setup && make up && make migrate && make seed && make health
```

Without Docker:

```bash
python -m venv .venv
.venv/Scripts/pip install -e "apps/api[dev,ocr]"
.venv/Scripts/python -m picglot.cli health
```

Set `QUEUE_BACKEND=inline` and `STORAGE_BACKEND=local` to run with neither Redis
nor MinIO.

| URL                                | What                     |
| ---------------------------------- | ------------------------ |
| http://localhost:3000              | Web app                  |
| http://localhost:8000/docs         | Swagger UI               |
| http://localhost:8000/health/ready | Readiness                |
| http://localhost:8025              | Mailpit (captured email) |
| http://localhost:9001              | MinIO console            |

### Demo credentials

Created by `make seed`, **only** when `SEED_ENABLED=true`, and refused outright
in production by the config validator:

| Role            | Email                   | Password          |
| --------------- | ----------------------- | ----------------- |
| Superadmin      | `admin@picglot.example` | `Sup3r!Seed-2026` |
| Demo user (Pro) | `demo@picglot.example`  | `Tr1al!Seed-2026` |

Seeding was executed and verified: 4 plans, 8 feature flags, **61 SEO pages**
(tool, format and language-pair landing pages in `en` and `ru`), 3 blog posts and
both accounts, in 0.25 s.

Both come from `.env`. Change them, or use
`python -m picglot.cli create-admin`.

---

## 5. Credentials needed for production

**None to run it.** A fresh install recognises text, edits and exports using the
bundled RapidOCR models with no accounts at all.

| Capability                 | Needs                                                                              | Without it                                                                                |
| -------------------------- | ---------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Translation                | A provider key (DeepL / Google / Yandex / Azure / LLM) **or** offline Argos models | OCR, editing and exports work; the UI states plainly that translation needs configuration |
| Payments                   | Stripe or YooKassa keys + webhook secret                                           | `BILLING_ENABLED=false`; everything else works                                            |
| Email                      | SMTP or Resend                                                                     | Mailpit captures mail locally                                                             |
| Handwriting (best quality) | An LLM key                                                                         | Falls back to Tesseract, with lower confidence honestly reported                          |
| Cloud OCR                  | Per-provider key **and** its `*_ENABLED` flag                                      | Local engines are used                                                                    |
| Error tracking             | Sentry DSN                                                                         | Structured logs only                                                                      |

---

## 6. Documentation

| Path                                   | Contents                                                                         |
| -------------------------------------- | -------------------------------------------------------------------------------- |
| `README.md`                            | Setup, commands, configuration, testing                                          |
| `apps/api/README.md`                   | Backend package layout                                                           |
| `docs/architecture/system-overview.md` | Context, containers, sequence and deletion diagrams (Mermaid), failure behaviour |
| `docs/security/threat-model.md`        | 16 threats with the control and the test that covers each                        |
| `docs/operations/deployment.md`        | Single-server and managed paths, production checklist                            |
| `docs/architecture-decisions/`         | Four ADRs: package layout, OCR strategy, credit ledger, migrations               |
| `http://localhost:8000/docs`           | Live OpenAPI 3 / Swagger UI                                                      |

---

## 7. Objective external steps remaining

These need accounts, money or a lawyer — they cannot be done in code:

1. **Domain and TLS** — register the domain, point DNS, run the bundled certbot.
2. **Payment account** — a real Stripe or YooKassa account, price objects
   created, webhook endpoint registered with its signing secret.
3. **Provider keys** — whichever translation and OCR vendors you choose, with
   billing enabled and spend caps set (`LLM_DAILY_COST_LIMIT_USD` and the
   per-provider limits are already enforced in code).
4. **Email deliverability** — SPF, DKIM and DMARC for the sending domain.
5. **Legal review** — the Terms, Privacy, Cookie, Refund and Acceptable Use
   pages are marked in the UI as templates and describe real behaviour, but need
   review for your jurisdiction.
6. **Penetration test** — before handling regulated data.
7. **Backup drill** — the scripts and runbook exist; restore has not been
   rehearsed on real infrastructure.

---

## 8. Production checklist

- [ ] `ENVIRONMENT=production`, `DEBUG=false`, `LOG_FORMAT=json`
- [ ] Unique `SECRET_KEY` from a secrets manager
- [ ] `SESSION_COOKIE_SECURE=true`, HTTPS enforced
- [ ] `SEED_ENABLED=false`
- [ ] Database, Redis and storage credentials off their defaults
- [ ] Payment webhook secret set and registered
- [ ] Backups running and a restore tested
- [ ] Prometheus scraping `/metrics`, alerts routed somewhere a human reads
- [ ] Object storage public access blocked
- [ ] Legal pages reviewed
- [ ] `python -m picglot.cli health` green
- [ ] One real file processed end to end through the production URL

The configuration validator enforces the first five and refuses to start
otherwise, naming exactly what is wrong.
