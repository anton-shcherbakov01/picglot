# Public API

The authoritative reference is the live OpenAPI document — this page is an
orientation, not a duplicate.

- **Interactive docs**: `<PUBLIC_API_URL>/docs` (Swagger UI), generated
  directly from the FastAPI app, always in sync with the deployed code.
- **Raw schema**:
  ```bash
  python -m picglot.cli openapi --output openapi.json
  ```
  or fetch it live from a running instance at `<PUBLIC_API_URL>/openapi.json`.
- **Checked-in snapshot**: [`openapi.json`](openapi.json) in this directory,
  generated the same way — a point-in-time copy for browsing offline or
  importing without a running instance. It drifts from any deployed code the
  moment either changes; regenerate it with the command above rather than
  trusting it as current.

## Postman collection

[`picglot.postman_collection.json`](picglot.postman_collection.json) — 111
requests in 8 folders, with `{{baseUrl}}` and `{{apiKey}}` as collection
variables and bearer auth pre-wired.

It is **generated**, not hand-maintained, so it cannot drift from the API:

```bash
python -m picglot.cli openapi --output docs/api/openapi.json
npm run api:postman
```

Regenerate it in the same commit as any route change. Insomnia and most other
clients import the OpenAPI document directly if you prefer
(**Import → File → `openapi.json`**).

## Authentication

Public API access (`FEATURE_PUBLIC_API`) uses an API key issued from the
dashboard (`Account → API keys`), sent as:

```
Authorization: Bearer <api_key>
```

Session-cookie auth (used by the web app itself) also works for the same
endpoints when called from a browser context; CSRF protection applies to
cookie-authenticated state-changing requests, not to bearer-token calls.

## Rate limits

Per-key limits are enforced (`RATE_LIMIT_API_PER_MINUTE` and friends);
responses include the usual `X-RateLimit-*` headers plus `Retry-After` on a 429.

## Stability

Endpoint shapes are additive — new optional fields may appear on a response
without notice; fields are not removed or repurposed without a version bump
in the path (`/api/v1/...`). Treat unknown response fields as forward
compatibility, not a bug.

## Webhooks

Outbound webhooks (job completion, billing events) are signed — see the
signature verification section in `/docs` for the exact header and HMAC
scheme — and retried with backoff on a non-2xx response, with replay
protection on the receiving side already implemented for the Stripe/YooKassa
inbound webhooks as a reference.
