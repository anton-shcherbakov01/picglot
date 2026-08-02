# syntax=docker/dockerfile:1.7
# API image — FastAPI + uvicorn. Shares the lingoimage package with the workers
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

FROM base AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY apps/api/pyproject.toml apps/api/README.md ./
COPY apps/api/lingoimage ./lingoimage
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip wheel \
    && /opt/venv/bin/pip install .

FROM base AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    TMPDIR=/tmp/lingoimage
COPY --from=builder /opt/venv /opt/venv

RUN groupadd --system --gid 1001 lingo \
    && useradd --system --uid 1001 --gid lingo --create-home lingo \
    && mkdir -p /tmp/lingoimage /app \
    && chown -R lingo:lingo /tmp/lingoimage /app

WORKDIR /app
COPY --chown=lingo:lingo apps/api/alembic.ini ./alembic.ini
COPY --chown=lingo:lingo apps/api/lingoimage ./lingoimage
COPY --chown=lingo:lingo assets ./assets
COPY --chown=lingo:lingo infra/docker/api-entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER lingo
EXPOSE 8000
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/health/live || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "lingoimage.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-graceful-shutdown", "25"]
