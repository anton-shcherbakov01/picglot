# Security Policy

## Reporting a vulnerability

Email the address in `BRAND_SUPPORT_EMAIL` (see `.env.example`) with
`[security]` in the subject. Do not open a public issue — the threat model
and current controls are public (`docs/security/threat-model.md`), but an
unpatched exploit against a live deployment is not something to broadcast.

We aim to acknowledge within 2 business days and to give a fix timeline
within 5.

## Scope

- The web application, API, workers, and the data they store.
- Out of scope: self-XSS, social engineering, and anything requiring
  physical access to infrastructure.

## What's already covered

See `docs/security/threat-model.md` for the full list of 16 threats and
their controls. Highlights: Argon2id password hashing, sessions/API
keys/share tokens stored only as keyed hashes, private storage buckets with
short-lived signed URLs, magic-byte upload validation with PDF JavaScript
stripping, signed and replay-protected webhooks, SSRF guards on any
server-side fetch, and an append-only credit ledger with idempotency to
prevent double-charging or over-refunding.

## Disclosure

We'll credit reporters who want credit, once a fix has shipped, in the
release notes or changelog for the affected version.
