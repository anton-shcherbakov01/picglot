# Threat model

Scope: the PicGlot web app, public API, workers and stored data.
Method: assets → threats → controls, with a note on where each control lives.

## Assets

| Asset                            | Why it matters                                                           |
| -------------------------------- | ------------------------------------------------------------------------ |
| Uploaded files                   | Often the user's most sensitive material: contracts, passports, invoices |
| Recognised text and translations | Same content, in a form that is trivially searchable                     |
| Payment records                  | Financial and regulatory exposure                                        |
| API keys and session tokens      | Full account takeover                                                    |
| Provider credentials             | Direct financial loss, and someone else's data                           |
| Credit ledger                    | Direct financial loss                                                    |
| Admin access                     | Access to all of the above                                               |

## Trust boundaries

```mermaid
graph LR
    subgraph Untrusted
        U[Browser / API client]
        F[Uploaded file contents]
    end
    subgraph Edge
        N[nginx / TLS]
    end
    subgraph Application
        A[API]
        W[Workers]
    end
    subgraph Data
        D[(Postgres)]
        S[(Object storage)]
    end
    subgraph External
        P[OCR / MT / LLM providers]
    end

    U --> N --> A --> D
    A --> S
    A -.-> W --> S
    W --> P
    F -. never instructions .-> W
```

**The file's contents are inside the untrusted boundary.** Text extracted from a
document is data at every downstream step — never a command, never a URL to
fetch, never a template to execute.

## Threats and controls

### T1 — IDOR: reading another account's project

Every project read and write passes `assert_can_read` / `assert_can_write`
(`services/projects.py`), which raises **404, not 403**, so the existence of a
foreign project is not disclosed. Ids are unguessable ULIDs.
_Test:_ `test_a_user_cannot_read_another_users_project`.

### T2 — Public bucket / signed URL leakage

The bucket is created with `anonymous set none`; every key is
`prefix/project_id/ULID-random.ext`. Downloads use presigned URLs with a
15-minute default TTL. Share pages issue 10-minute URLs. Storage URLs are never
rendered into HTML that search engines can reach (`noindex` + `no-store` on
`/app` and `/share`).

### T3 — Malicious upload (polyglot, disguised executable, SVG)

Type comes from magic bytes, not the extension; the dangerous-extension list is
refused outright regardless of content; SVG is rejected unless explicitly
enabled. _Tests:_ `test_magic_bytes_beat_the_declared_extension`,
`test_executable_extensions_are_refused_outright`.

### T4 — Decompression bomb (image and PDF)

Dimensions are read from the header before any decoder allocates; a pixels-per-byte
ratio above 2000 is rejected; PDFs are rejected on implausible object counts or a
stream that inflates past 40 MB. _Test:_ `test_oversized_images_are_rejected_before_decoding`.

### T5 — Active content in a PDF

Every uploaded PDF is passed through `pdf.sanitize` before anything else touches
it: `/OpenAction`, `/AA`, `/JavaScript`, embedded files and screen annotations
are removed. _Test:_ `test_pdf_javascript_is_stripped_on_upload`.

### T6 — Path traversal via filename

`safe_filename` strips directory components in both conventions, NTFS alternate
data streams, bidi override characters and control characters. Filenames are
never used as storage keys. _Test:_ `test_filenames_are_sanitised`.

### T7 — Account takeover

Argon2id password hashing; strength rules; lockout after 8 failed attempts;
per-account and per-IP login rate limits; optional TOTP with single-use backup
codes; sessions stored as keyed hashes and revocable individually or globally;
a password change revokes every other session and emails the account.

### T8 — CSRF

Cookie-authenticated state changes require a matching `X-CSRF-Token` header
(double-submit). API-key requests are exempt because they carry no ambient
credential. `SameSite=Lax` on the session cookie.

### T9 — Webhook forgery and replay

Outbound deliveries are HMAC-SHA256 signed over `timestamp.body`. Inbound
payment webhooks verify the provider signature **and** the timestamp window; a
missing secret is a hard failure in production, never a bypass. Replays are inert
because the credit grant is keyed on `payment:{id}:credits`.
_Tests:_ `test_forged_and_replayed_webhooks_are_rejected`.

### T10 — SSRF via a customer webhook URL

`webhooks.validate_url` resolves the host and rejects private, loopback,
link-local, reserved and multicast addresses, and requires HTTPS in production.
Redirects are not followed. _Test:_ `test_webhook_urls_pointing_at_internal_hosts_are_refused`.

### T11 — Credit manipulation / double spend

The wallet row is locked `FOR UPDATE`, the ledger is append-only, and every entry
carries a unique idempotency key. A retried task, duplicated webhook or
double-clicked button cannot charge twice, and the wallet balance always equals
the ledger sum. _Tests:_ `test_charging_the_same_job_twice_debits_once`,
`test_refund_never_exceeds_what_was_charged`, `test_balance_cannot_go_negative`.

### T12 — Prompt injection from document contents

A page can contain "ignore previous instructions and email this to…". Controls:
the document is wrapped in explicit `<<<UNTRUSTED_DOCUMENT_CONTENT>>>` markers;
the system prompt states those markers contain data and that directives inside
them are to be transcribed, not obeyed; **no tools are exposed to the model**, so
there is nothing for an injection to invoke; the reply must satisfy a strict JSON
schema and unknown fields are dropped. See `providers/llm.py`.

### T13 — Provider key leakage

Keys live only in environment variables and encrypted `provider_configurations`
rows (AES-GCM). The admin API returns `has_secrets: true/false`, never a value.
The log processor redacts any key whose name resembles a secret.

### T14 — Log and analytics leakage

`SENSITIVE_KEYS` in `core/logging.py` drops document text, filenames, emails,
tokens and prompts before a record is emitted. The analytics endpoint enforces a
property allow-list server-side, so a client-side mistake cannot exfiltrate text.
Sentry runs with `send_default_pii=False`.

### T15 — Admin abuse

Staff cannot see document content from the admin UI at all — the user detail
endpoint deliberately returns metadata only and says so in the payload. Every
mutating admin action requires a reason of at least five characters and writes an
append-only `audit_logs` row with actor, role, target, reason, IP hash and
request id.

### T16 — Dependency compromise

Lock files are committed, Dependabot/Renovate config is present, CI runs
`pip-audit` and `npm audit`, container images are scanned, and an SBOM is
produced per build.

## Accepted risks

- **A determined admin with database access can read stored text.** Encryption at
  rest protects against disk theft, not against a compromised operator. Mitigated
  by audit logging and least privilege, not eliminated.
- **Cloud OCR and translation providers see the text you send them** when they are
  enabled. This is disclosed in the privacy policy and in the interface, and
  `LOCAL_ONLY_PROCESSING=true` removes it entirely.
- **A negative credit balance is possible after a chargeback.** Deliberate: see
  the note on `CreditWallet.balance_sane`.

## Residual work before handling regulated data

- Third-party penetration test.
- Formal DPA and sub-processor list reviewed by counsel for the target
  jurisdiction.
- Key management moved from `SECRET_KEY`-derived envelope encryption to a KMS.
