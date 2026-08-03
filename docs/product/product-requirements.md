# Product requirements

## What PicGlot is

A web service that translates, recognises and converts text in images and
documents while preserving the original layout — same position, same colours,
similar font. One engine drives thirteen purpose-built tools.

_Translate, extract and edit text in any image._

## Who it is for

| Audience                    | Need                                               | Entry point                                            |
| --------------------------- | -------------------------------------------------- | ------------------------------------------------------ |
| One-off visitor from search | Translate one photo, right now, without signing up | Landing page with the tool above the copy; guest quota |
| Regular user                | Keep projects, re-edit, re-export, glossaries      | Account, dashboard                                     |
| Company                     | Shared workspace, seats, central billing           | Workspace, team roles                                  |
| Developer                   | Automate it                                        | Public REST API, webhooks                              |

A guest must be able to get a finished result before being asked for anything.
Registration converts the guest session without losing work.

## Tools

`image-translator`, `translate-photo`, `screenshot-translator`,
`image-to-text`, `jpg-to-word`, `image-to-excel`, `handwriting-to-text`,
`pdf-translator`, `pdf-ocr`, `document-scanner`, `receipt-scanner`,
`invoice-ocr`, `batch`.

Each is a mode over the same pipeline, not a separate product. The set is
configuration (`ENABLED_TOOLS`), not hard-coded branching.

## The pipeline

```
upload → validate → preprocess → detect → recognise → normalise → layout
       → translate → remove original text → typeset translation → export
```

Every stage is observable in the UI and in job events. A page that fails a
stage reports which stage, not "something went wrong".

## Non-negotiables

These are the requirements that shape the architecture; breaking one is a bug,
not a trade-off.

1. **Honest output.** No claimed accuracy figures. Confidence is labelled as
   confidence. Low-confidence blocks are flagged, not silently rendered.
2. **Never invent data.** Receipt and invoice extraction returns `null` for a
   field it did not find. It never guesses a total.
3. **Text that does not fit is reported, not clipped.** Font size is
   binary-searched against real glyph metrics; when it genuinely cannot fit,
   the user is told.
4. **Works with no paid keys.** A fresh install recognises, edits and exports
   using bundled local models. Translation needs a provider key or offline
   models, and the UI says so plainly rather than returning untranslated text.
5. **Document content is untrusted input.** It never becomes an instruction to
   an LLM, never reaches logs or analytics, and never leaves the system when
   `LOCAL_ONLY_PROCESSING=true`.
6. **Credits are transactional.** Charging is idempotent, failures refund
   automatically, re-export of identical settings is free, and the wallet
   always equals the ledger sum.
7. **Deletion is real.** Deleting removes rows _and_ storage objects, and is
   logged without retaining the content.

## Commercial model

Credit-based, configurable. Free tier plus Pro and Business; one-off credit
packs. Roughly: one page of OCR is one credit, translating a page adds one,
and handwriting / table extraction / advanced inpainting carry multipliers.
A cancelled-before-start job costs nothing.

Payment provider is chosen by configuration per region (Stripe, YooKassa).

## Success criteria

- A visitor from search can go from landing page to downloaded result without
  an account.
- A returning user finds their projects, re-edits a block and re-exports
  without paying twice.
- An API client can create a job, poll or receive a webhook, and download the
  export.
- No page shows a stack trace, an empty CTA, or a link to something that does
  not exist.

## Explicitly out of scope

- Training a proprietary OCR model — the product composes local and vendor
  adapters, and that is a deliberate architectural choice, not a shortfall.
- Real-time collaborative editing. Edits are server-authoritative with
  optimistic locking; two people editing one project conflict rather than
  merge.
- Translating text that is not in an image or document (plain-text
  translation is a different product).

## Related documents

- [`system-overview.md`](../architecture/system-overview.md) — containers and sequences
- [`data-flow.md`](../architecture/data-flow.md) — where each artefact lives
- [`seo-architecture.md`](../seo/seo-architecture.md) — landing-page strategy
- [`threat-model.md`](../security/threat-model.md) — threats and controls
- [`legal-review.md`](legal-review.md) — what needs a lawyer before launch
