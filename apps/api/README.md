# `lingoimage` — API and processing engine

Single installable Python package shared by the FastAPI service and the Celery
workers. Keeping one package (rather than three that import each other) means
the models, provider adapters and vision pipeline cannot drift between the web
tier and the workers.

```
lingoimage/
  core/       configuration, errors, logging, security, metrics, rate limiting
  domain/     languages, tools, enums, credit rules, plans
  db/         declarative models, session handling, seed data
  schemas/    Pydantic request/response contracts (source of the OpenAPI spec)
  services/   storage, auth, projects, jobs, credits, exports, sharing, …
  providers/  OCR, translation, LLM, payments, email, analytics adapters
  vision/     preprocessing, detection, OCR normalisation, inpainting, rendering
  api/        FastAPI routers (public v1, internal, admin, auth, webhooks)
  workers/    Celery application, task definitions, scheduled maintenance
```

## Local use

```bash
python -m venv .venv
.venv/bin/pip install -e "apps/api[dev,ocr]"
.venv/bin/uvicorn lingoimage.main:app --reload
```

Run `python -m lingoimage.cli health` to check that the database, Redis, object
storage and the configured providers are reachable.
