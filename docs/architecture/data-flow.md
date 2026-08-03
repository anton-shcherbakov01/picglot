# Data flow

How a file moves through the system, and where each artefact ends up. See
[system-overview.md](system-overview.md) for the container diagram and
[security-boundaries.md](security-boundaries.md) for where the trust changes.

## Upload → result

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant A as API
    participant S as Object storage
    participant Q as Redis (broker)
    participant W as Worker
    participant P as OCR / MT provider

    B->>A: POST /api/v1/projects/upload (multipart + options)
    A->>A: prepare_upload: size, magic bytes, malware, PDF sanitise
    A->>S: put(original) under a random key
    A->>A: create Project, Asset, Job (status=queued)
    A->>Q: dispatch(job)
    A-->>B: 202 JobOut
    B->>A: GET /api/v1/jobs/{id}/events (SSE)

    W->>Q: consume
    W->>S: get(original)
    W->>W: preprocess → detect → recognise
    opt cloud OCR configured
        W->>P: image region
        P-->>W: text + boxes
    end
    W->>W: normalise → layout
    opt translation requested
        W->>W: glossary → TM → fuzzy → cache
        W->>P: only the segments still unresolved
        P-->>W: translations
    end
    W->>W: inpaint → typeset → render
    W->>S: put(preview, result)
    W->>A: job events per stage
    A-->>B: SSE stage updates
    B->>A: GET /api/v1/projects/{id}
    A->>S: signed URL (short TTL)
    A-->>B: project + signed links
```

## What is written where

| Artefact                   | Store                      | Lifetime                                                            |
| -------------------------- | -------------------------- | ------------------------------------------------------------------- |
| Original upload            | Object storage, random key | Plan retention (`RETENTION_*_HOURS`)                                |
| Preview / thumbnail        | Object storage             | `RETENTION_INTERMEDIATE_HOURS` (6h)                                 |
| Rendered result            | Object storage             | Plan retention                                                      |
| Export (PDF/DOCX/XLSX/…)   | Object storage             | Plan retention; re-export of identical settings reuses the artefact |
| Text regions, translations | PostgreSQL                 | With the project                                                    |
| Job + stage events         | PostgreSQL                 | With the project                                                    |
| Credit ledger entries      | PostgreSQL                 | Append-only, never deleted                                          |
| Analytics events           | PostgreSQL                 | Allow-listed properties only                                        |

Intermediate masks and per-stage scratch files are deleted by the worker as
soon as the stage that produced them completes; they are never addressable
from the API.

## Editing an existing project

Region edits are server-authoritative. The browser sends a PATCH carrying the
region's `version`; the API rejects a stale version with a conflict rather
than silently overwriting, and the editor surfaces that as "someone changed
this first". Undo replays an inverse PATCH — it does not rewind local state,
so two tabs cannot diverge.

Re-rendering after an edit reuses the recognised regions and only redoes
inpaint + typeset + render, which is why an edit costs no OCR credit.

## Deletion

```mermaid
flowchart LR
    U[User deletes project] --> SD[soft delete: deleted_at set]
    SD --> T[Trash, RETENTION_TRASH_HOURS]
    T -->|user restores| P[Project active again]
    T -->|window elapses| H[Lifecycle worker hard-deletes]
    H --> DB[(Rows removed)]
    H --> OS[(Storage objects purged)]
    H --> L[Deletion logged without content]
```

Account deletion cascades the same way across every project the account owns.
Billing records are kept where tax law requires, with personal identifiers
minimised.

## Money flow

```mermaid
sequenceDiagram
    participant B as Browser
    participant A as API
    participant PSP as Stripe / YooKassa

    B->>A: POST /api/v1/billing/checkout
    A->>PSP: create checkout session
    PSP-->>B: hosted payment page
    PSP->>A: webhook (signed)
    A->>A: verify signature, reject replays
    A->>A: append credit ledger entry (idempotent by event id)
    A-->>PSP: 200
```

Credits are only ever changed by appending to the ledger; the wallet balance
is derived from it, so a double-delivered webhook cannot double-credit and a
failed job refunds exactly what it charged.
