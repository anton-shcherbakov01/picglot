# Privacy model

Scope: what personal and document data LingoImage AI collects, where it goes,
and how long it lives. See [threat-model.md](threat-model.md) for the security
controls and [llm-safety.md](llm-safety.md) for how document content is
handled around the LLM provider.

## What we collect

| Data | Source | Purpose |
|---|---|---|
| Account email, hashed password | Sign-up | Auth |
| Uploaded files and their recognised/translated text | Processing pipeline | The product itself |
| Job metadata (status, timings, provider used) | Pipeline | Debugging, billing |
| Credit ledger entries | Billing | Charging correctly, refunds |
| IP address | Every request | Rate limiting, abuse detection — **stored only as a keyed hash** (`hash_ip`), never in plaintext |
| Analytics events | `ANALYTICS_ENABLED` | Product usage (page views, conversions) |

## What we do not collect

* Document text is never sent to the analytics pipeline. The ingest endpoint
  enforces a server-side property allow-list — properties outside it are
  dropped, not stored and rejected.
* Document content and recognised/translated text are excluded from
  application logs; the log processor drops those keys before a line is
  written.
* Passwords are never stored or logged in any form; only an Argon2id hash.
* Uploaded documents are never used to train models, ours or a provider's.

## Where data goes

* **Object storage** (S3-compatible): the files themselves, under random,
  unguessable keys. Buckets are private; the only path out is a short-lived
  signed URL (`SIGNED_URL_TTL_SECONDS`).
* **Postgres**: everything relational — accounts, jobs, credits, glossaries,
  translation memory, audit log.
* **Translation/OCR providers**: only when a non-local provider is selected
  and `LOCAL_ONLY_PROCESSING=false`. The request is the minimum needed (the
  text or image region), not the whole document, and URLs/emails/codes are
  masked before translation providers see them.
* **The LLM provider** (handwriting, hard OCR, structured extraction):
  document content is passed as delimited, untrusted *data* — the system
  prompt forbids following instructions found inside it, no tools are
  exposed to the model, and the reply must satisfy a strict schema. See
  [llm-safety.md](llm-safety.md).
* **Analytics providers** (`ANALYTICS_PROVIDERS`, e.g. PostHog/GA4/Yandex
  Metrica): only event names and the allow-listed properties, never document
  content, and only after consent is granted (see below).

## Consent

The web app shows a consent banner before any non-essential analytics
provider is loaded. `setConsent`/`consentState` in
`apps/web/src/lib/analytics.ts` gate outbound analytics calls; declining
keeps first-party server-side event capture (used for abuse detection and
aggregate product metrics) but skips third-party providers.

## Retention

Enforced by the lifecycle worker, per plan (`RETENTION_*_HOURS` in
`.env.example`):

| Tier | Files kept for |
|---|---|
| Guest | 24 hours |
| Free | 7 days |
| Pro | 90 days |
| Business | 365 days |
| Intermediate artefacts (previews, thumbnails) | 6 hours regardless of plan |
| Trash (soft-deleted projects) | 30 days, then hard delete |

A user can delete a project — and everything in it — at any time; deletion is
immediate for the database row and queues an async purge of the underlying
storage objects.

## Data subject requests

* **Export**: everything in a project is downloadable through the normal
  export flow; account-level export is a straightforward query across the
  tables in `docs/architecture/system-overview.md`.
* **Deletion**: deleting the account cascades to projects, jobs, glossaries
  and translation memory belonging to it; billing records are retained as
  required by tax law, with personal identifiers minimised.
* Requests arriving outside the product (email, support ticket) are logged
  in the admin audit trail with the reason and the operator who actioned them.

## Sub-processors

Whichever OCR/translation/LLM/payment/email providers are enabled in
`.env` for a given deployment are that deployment's sub-processors. Because
this is entirely configuration-driven, the authoritative list for a
production instance is `python -m lingoimage.cli config` run against its own
`.env`, not this document.
