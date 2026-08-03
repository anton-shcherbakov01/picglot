# Golden fixtures

Deliberately empty.

The pytest suite (`apps/api/tests/`) generates its fixtures programmatically
(synthetic signs, ruled tables) instead of committing real-world documents
here — committing third-party photographs, screenshots or scans would be a
licensing problem, since this repository is public/shared and the images
would need clear rights to redistribute.

What this directory is *for*, if you want to build it out: a curated corpus
of real-world photographs (varied lighting, angles, languages, fonts) that
would catch regressions in the preprocessing heuristics (deskew, shadow
flattening, CLAHE) that synthetic fixtures can't exercise realistically.

If you add fixtures here:

* Only use images you personally hold the rights to, or that are under a
  licence permitting redistribution (record which, per file, in a
  `LICENSES.md` in this directory).
* Do not add real customer or personal documents, even anonymised ones.
* Keep files small — this is a git repository, not object storage.
