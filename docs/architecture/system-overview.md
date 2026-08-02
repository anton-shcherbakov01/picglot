# System overview

## Context

```mermaid
graph TB
    User([Visitor / customer])
    Admin([Staff])
    ApiClient([API client])

    subgraph LingoImage["LingoImage AI"]
        Web["Web app<br/>Next.js 15"]
        Api["API<br/>FastAPI"]
        Worker["Workers<br/>Celery"]
        DB[("PostgreSQL")]
        Cache[("Redis")]
        Storage[("Object storage<br/>S3 / MinIO")]
    end

    OCR["OCR providers<br/>RapidOCR · Tesseract · Vision APIs"]
    MT["Translation providers<br/>DeepL · Google · Yandex · Azure · LLM · Argos"]
    Pay["Payments<br/>Stripe · YooKassa"]
    Mail["Email<br/>SMTP · Resend"]

    User --> Web --> Api
    ApiClient --> Api
    Admin --> Web
    Api --> DB
    Api --> Cache
    Api --> Storage
    Api -. enqueue .-> Cache
    Cache -. dequeue .-> Worker
    Worker --> DB
    Worker --> Storage
    Worker --> OCR
    Worker --> MT
    Api --> Pay
    Worker --> Mail
```

The web tier never talks to a provider and never opens an image. Everything
heavy happens in workers, which is why a slow vendor cannot make the site slow.

## Containers

| Component | Responsibility | Scales on |
|---|---|---|
| `web` | SSR marketing and app pages, editor UI | requests |
| `api` | HTTP, auth, quotas, job creation, SSE progress | requests |
| `worker-cpu` | Preprocess, OCR, translate, inpaint, render | queue depth |
| `worker-export` | Export generation, webhooks, email | export volume |
| `worker-beat` | Retention sweep, stuck jobs, webhook retries, monthly credits | fixed (1) |
| `worker-gpu` | Optional advanced inpainting | GPU availability |

The API and the workers install the **same** Python package (`lingoimage`), so
the models, provider adapters and pipeline cannot drift between tiers. Only the
extras differ: workers carry OpenCV, Tesseract language packs and the ONNX
models; the API image does not.

## Upload and processing flow

```mermaid
sequenceDiagram
    participant B as Browser
    participant A as API
    participant S as Storage
    participant Q as Redis
    participant W as Worker

    B->>A: POST /api/v1/process (file + options)
    A->>A: magic-byte check, bomb guards, AV scan, PDF sanitise
    A->>A: quota / credit check, cost estimate
    A->>S: store original (random key, private)
    A->>A: create project + job, debit credits (idempotent)
    A->>Q: enqueue job
    A-->>B: 202 { job_id, cost_estimate }

    B->>A: GET /api/v1/jobs/{id}/events (SSE)
    Q->>W: dequeue
    loop each page
        W->>S: read page
        W->>W: preprocess → detect → recognise → normalise → layout
        W->>W: translate (glossary → TM → cache → provider)
        W->>W: remove original text → typeset translation
        W->>S: write cleaned + rendered + preview
        W->>Q: publish progress event
        Q-->>A: event
        A-->>B: progress
    end
    W->>W: quality score, exports
    W-->>B: done
```

A page that fails does not fail the job: the other pages are saved, the job ends
`partially_completed`, and the credits for the failed pages are refunded.

## Deletion flow

```mermaid
graph LR
    A[User deletes project] --> B[Storage objects removed]
    B --> C[Rows removed]
    C --> D[Share links revoked]
    D --> E[DeletionRecord written<br/>counts only, no content]

    F[Lifecycle worker<br/>every 15 min] --> G{expires_at passed?}
    G -->|yes| B
    G -->|no| H[skip]
```

Retention is a column, not a rule buried in code: anything holding user content
carries `expires_at`, so one sweep handles guests, free accounts, intermediate
artefacts, exports and the trash.

## Payment flow

```mermaid
sequenceDiagram
    participant B as Browser
    participant A as API
    participant P as Stripe / YooKassa

    B->>A: POST /billing/checkout
    A->>P: create session (metadata: user, plan)
    A-->>B: checkout_url
    B->>P: pays
    P->>A: webhook
    A->>A: verify signature + timestamp window
    A->>A: look up payment by (provider, external id)
    alt already processed
        A-->>P: 200 (no-op)
    else new
        A->>A: record payment, grant credits (idempotent), set plan
        A-->>P: 200
    end
```

Replaying a webhook is inert by construction: the credit grant is keyed on
`payment:{id}:credits`, so the second delivery finds the existing ledger row.

## Data model

Around 50 tables. The parts worth knowing:

* `projects` → `document_pages` → `text_regions` → `region_translations`.
  A region keeps the raw OCR string *and* the normalised one, so nothing is lost.
* `assets` holds every stored object with its `kind` and `expires_at`.
* `jobs` + `job_events` give the timeline, progress and error reference.
* `credit_wallets` + `credit_ledger_entries` — the ledger is append-only and
  every entry carries a unique idempotency key; the wallet is its running sum.
* `audit_logs` is append-only and records the reason for every staff action.

Full schema: `apps/api/lingoimage/db/models.py`.

## Failure behaviour

| Failure | Result |
|---|---|
| One OCR provider down | Chain falls through to the next; circuit breaker opens after 5 failures |
| Every provider down | Job fails with `provider_unavailable`, credits refunded |
| Redis down | Rate limiting, cache and events fall back in-process; jobs still run |
| Worker killed mid-job | Heartbeat goes stale, beat re-queues it, idempotent charge prevents double billing |
| Storage down | Job fails with `storage_failed`, credits refunded |
| Provider over budget | Provider is skipped before it is called |
