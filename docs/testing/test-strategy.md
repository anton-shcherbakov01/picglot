# Test strategy

## Principle

The suite runs the **real** application code — the same pipeline, routers and
services that production runs — against SQLite, local storage and the inline
queue. Internals are not mocked. Only the outside world is: external OCR and
translation providers are replaced by local/deterministic adapters that the
product genuinely ships (RapidOCR, Argos, `echo`), not by stubs invented for
the tests.

The consequence is worth stating plainly: a green suite means the pipeline
actually recognised text, actually produced the export, and the bytes were
read back — not that a mock returned what a mock was told to return.

## Layers

| Layer              | Location                                       | Runs against                                                                                          |
| ------------------ | ---------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Unit               | `apps/api/tests/test_vision.py`, `test_seo.py` | Pure functions: geometry, layout grouping, font fitting, wrapping, RTL, table typing, slug invariants |
| Ledger             | `apps/api/tests/test_credits.py`               | Pricing, idempotency, refunds, balance integrity                                                      |
| HTTP / integration | `apps/api/tests/test_pipeline.py`              | Upload → OCR → translate → render → export, through the real endpoints                                |
| Security           | `apps/api/tests/test_security.py`              | IDOR, magic bytes, filename sanitisation, PDF JavaScript, hashing, webhook signing and replay, SSRF   |
| Browser smoke      | `apps/web/e2e/smoke.spec.ts`                   | Pages render, auth forms present, locale redirect, theme toggle                                       |
| Load               | `tests/load/upload.js` (k6)                    | Health, config and language endpoints under concurrency                                               |

## What the HTTP tests prove

- A guest uploads a PNG, RapidOCR recognises it, and the recognised text
  contains the words drawn into the image.
- Translation is applied and a rendered image is produced.
- Layout analysis identifies the heading and returns blocks in reading order.
- Eight export formats are produced and their magic bytes checked.
- The searchable PDF is reopened with PyMuPDF and its invisible text layer
  read back — verified from the artefact, not the code path.
- A ruled table is detected, cells typed as numbers, XLSX exported.
- Charging the same job three times debits once; refunds are capped; the
  wallet always equals the ledger sum.
- Another account gets 404 (not 403) on someone else's project.
- Fake MIME types, disguised executables and traversal filenames are refused;
  PDF JavaScript is stripped.

## Running

```bash
make test          # pytest + web unit tests
make test-e2e      # Playwright (needs the dev server or BASE_URL)
make load-test     # k6
```

Windows without GNU make: `.\scripts\make.ps1 test`.

## Known gaps, stated rather than hidden

- **`test_concurrent_charges_never_oversell` skips on SQLite.** The guarantee
  comes from `SELECT … FOR UPDATE`, which SQLite does not have. It is marked
  `@pytest.mark.integration` and skips with a message rather than passing for
  the wrong reason. Run it against PostgreSQL to exercise it.
- **`tests/golden/` is empty by design.** Fixtures are generated
  programmatically because committing third-party documents would be a
  licensing problem. See `tests/golden/README.md` for what a real corpus
  would add and the rules for adding one.
- **E2E covers rendering, not the full journey.** The upload → translate →
  export path needs a live backend and fixture files; it is covered at the
  HTTP layer instead, against the same endpoints the browser calls.
- **`mypy apps/api` is not clean.** It reports pre-existing errors that
  predate the current work; `PROJECT_STATUS.md` records the count rather than
  leaving a gate that looks green because nobody runs it.

## Adding tests

New behaviour needs a test at the lowest layer that can observe it. Prefer a
unit test over an HTTP test where the logic is pure; prefer an HTTP test over
a browser test where the question is "does the API do the right thing".

Fixtures must be synthetic or licensed. Never add a real customer document,
even anonymised.
