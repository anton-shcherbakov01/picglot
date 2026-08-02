# 2. Local-first OCR with a cloud fallback chain

**Status:** accepted · 2026-08-02

## Context

OCR quality varies enormously by input. A cloud API is usually the most accurate
choice, but it means: a key before the product does anything, per-page cost from
the first request, and every uploaded document leaving the machine.

We evaluated:

| Option | Offline | Setup | Languages | Photo quality |
|---|---|---|---|---|
| Tesseract | yes | system binary + language packs | 100+ | weak on photos |
| PaddleOCR | yes | heavy Python stack | good | strong |
| RapidOCR (ONNX) | yes | `pip install`, models bundled | good | strong |
| EasyOCR | yes | pulls in Torch (~2 GB) | good | strong |
| Cloud vision APIs | no | key + billing | excellent | excellent |

## Decision

Default chain: **RapidOCR → Tesseract**, then any configured cloud provider.

RapidOCR is first because it is the only strong-on-photographs engine that
installs from PyPI with its models included — a fresh checkout recognises text
with no key, no download step and no system package. Tesseract is second for its
language breadth on clean scans. Cloud providers sit behind explicit
`*_ENABLED` flags *and* credentials, so a stray key in the environment cannot
start sending documents off-machine.

Everything goes through one `OcrProvider` interface with a shared chain runner
that applies circuit breaking, budget caps, fallback and metrics uniformly.
`purpose` selects the chain: text, handwriting or table.

## Consequences

* The product is genuinely usable on a laptop with no accounts.
* `LOCAL_ONLY_PROCESSING=true` is a real guarantee, not a promise — the chain
  runner refuses every non-local adapter.
* We ship ONNX runtime in the worker image (~80 MB). Acceptable.
* Language coverage in the default configuration is bounded by what RapidOCR and
  the installed Tesseract packs support. Documented rather than glossed over.
