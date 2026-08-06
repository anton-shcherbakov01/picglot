# syntax=docker/dockerfile:1.7
# API image — FastAPI + uvicorn. Shares the picglot package with the workers
# but installs only the light extras (no OCR models) to keep the image small.
FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libjpeg62-turbo \
        libpng16-16 \
        libtiff6 \
        libwebp7 \
        libopenjp2-7 \
        libheif1 \
        libgl1 \
        libglib2.0-0 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# The API renders too — `POST /projects/{id}/rerender` redraws pages in-process
# — so it needs the same font coverage as the workers, not just the workers.
# Beyond script coverage, the extra families give the typeface matcher
# something to choose between: condensed and humanist faces, so lettering that
# is not a plain grotesque can be redrawn as something close to it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-dejavu-core fonts-dejavu-extra fonts-noto-core fonts-noto-cjk \
        fonts-noto-color-emoji fonts-liberation \
        fonts-crosextra-carlito fonts-crosextra-caladea \
    && rm -rf /var/lib/apt/lists/*

FROM base AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY apps/api/pyproject.toml apps/api/README.md ./
COPY apps/api/picglot ./picglot
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip wheel \
    && /opt/venv/bin/pip install .

FROM base AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    TMPDIR=/tmp/picglot
COPY --from=builder /opt/venv /opt/venv

RUN groupadd --system --gid 1001 picglot \
    && useradd --system --uid 1001 --gid picglot --create-home picglot \
    && mkdir -p /tmp/picglot /app \
    && chown -R picglot:picglot /tmp/picglot /app

WORKDIR /app
COPY --chown=picglot:picglot apps/api/alembic.ini ./alembic.ini
# alembic.ini sets script_location = %(here)s/migrations, so the revisions have
# to be in the image or `alembic upgrade head` exits before touching the
# database. This image is what the `migrate` service runs.
COPY --chown=picglot:picglot apps/api/migrations ./migrations
COPY --chown=picglot:picglot apps/api/picglot ./picglot
COPY --chown=picglot:picglot assets ./assets
COPY --chown=picglot:picglot infra/docker/api-entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER picglot
EXPOSE 8000
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/health/live || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "picglot.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-graceful-shutdown", "25"]
