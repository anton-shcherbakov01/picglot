# Provider outage playbook

Applies to any external OCR, translation or LLM provider — DeepL, Google,
Azure, AWS Textract, Yandex, or the LLM provider.

## Detection

- `picglot_provider_circuit_open{provider="..."}` = 1 in Prometheus.
- `GET /health/providers` reports the provider as `unavailable`.
- Job failure rate rising for jobs that route through that provider.

## Automatic behaviour — nothing to do, by design

1. The circuit breaker opens after repeated consecutive failures for that
   provider.
2. The provider chain runner falls through to the next provider configured
   in `OCR_PROVIDER_PRIORITY` / `TRANSLATION_PROVIDER_PRIORITY` (etc).
3. If every provider for a purpose is down, the job fails with
   `provider_unavailable` and the credit charge is refunded automatically —
   the ledger never charges for work that wasn't delivered.
4. RapidOCR (bundled, offline) and Argos (bundled, offline) keep working
   regardless of any cloud outage, so OCR and translation degrade rather
   than stop, as long as they're in the priority list.

## Manual actions, in order

1. Check the provider's own status page to gauge expected duration.
2. If it'll be a while, lower that provider's position in the priority list
   (or remove it) via `.env` + restart, or via the admin providers endpoint
   if you don't want a restart.
3. If **all** OCR is down and you need to keep serving requests: set
   `LOCAL_ONLY_PROCESSING=true` — this forces RapidOCR/Argos-only and refuses
   external providers outright rather than silently queueing failures.
4. Watch `picglot_jobs_total{status="failed"}` and the refund rate; a
   spike confirms the fallback chain is (or isn't) covering the gap.
5. When the provider recovers, restore its priority/position and confirm
   `/health/providers` reports it healthy before removing any temporary
   `LOCAL_ONLY_PROCESSING` override.

## Cost note

A prolonged outage of a cheap default provider can silently shift load to a
more expensive fallback further down the chain (e.g. onto the LLM provider).
`LLM_DAILY_COST_LIMIT_USD` and the per-provider limits already cap the
blast radius — check them, don't just raise them, when everything is
suddenly routing to the LLM.
