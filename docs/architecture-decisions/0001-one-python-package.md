# 1. One Python package for the API and the workers

**Status:** accepted · 2026-08-02

## Context

The requested layout suggested separate packages under `apps/api`,
`workers/vision` and `workers/export`. The API and the workers share the ORM
models, the provider adapters, the error taxonomy and the credit rules.

Three separate distributions sharing that much means either a fourth "common"
package with its own version, or duplicated code. Both invite the failure mode
where the API validates against one version of a model and a worker writes
another.

## Decision

One installable package, `lingoimage`, living in `apps/api`, imported by both
tiers. `workers/vision` and `workers/export` remain as deployment units — their
own Dockerfiles, queues and scaling — but not as separate Python distributions.

The images differ by extras, not by code: the worker image installs `[ocr]` plus
OpenCV, Tesseract language packs and fonts; the API image does not.

## Consequences

* Models, adapters and pipeline logic cannot drift between tiers.
* One dependency set, one lint and type configuration, one test suite.
* The API image carries a little more code than it strictly executes. Measured
  against the class of bug this prevents, that is a good trade.
* Splitting later is mechanical if the vision engine ever needs its own release
  cadence: the boundary already exists at `lingoimage.vision`.
