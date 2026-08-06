# syntax=docker/dockerfile:1.7
# Worker image — carries the heavy vision stack: OpenCV, Tesseract language
# packs, RapidOCR ONNX models, PDF tooling and the bundled font set.
FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OPENCV_IO_ENABLE_OPENEXR=0

# Tesseract language data: the scripts we advertise as supported. The font set
# goes beyond script coverage: the extra families give the typeface matcher
# something to choose between — condensed and humanist faces, so lettering that
# is not a plain grotesque can be redrawn as something close to it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 libjpeg62-turbo libpng16-16 libtiff6 libwebp7 libopenjp2-7 \
        libheif1 libgl1 libglib2.0-0 libgomp1 \
        ghostscript qpdf unpaper pngquant \
        tesseract-ocr \
        tesseract-ocr-eng tesseract-ocr-rus tesseract-ocr-ukr tesseract-ocr-bel \
        tesseract-ocr-deu tesseract-ocr-fra tesseract-ocr-spa tesseract-ocr-por \
        tesseract-ocr-ita tesseract-ocr-pol tesseract-ocr-ces tesseract-ocr-tur \
        tesseract-ocr-ara tesseract-ocr-heb tesseract-ocr-chi-sim tesseract-ocr-chi-tra \
        tesseract-ocr-jpn tesseract-ocr-jpn-vert tesseract-ocr-kor tesseract-ocr-hin \
        tesseract-ocr-ind tesseract-ocr-vie tesseract-ocr-tha tesseract-ocr-kaz \
        tesseract-ocr-uzb tesseract-ocr-kat tesseract-ocr-hye tesseract-ocr-osd \
        fonts-dejavu-core fonts-dejavu-extra fonts-noto-core fonts-noto-cjk \
        fonts-noto-color-emoji fonts-liberation \
        fonts-crosextra-carlito fonts-crosextra-caladea \
        curl \
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
    && /opt/venv/bin/pip install ".[ocr]"

FROM base AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    TMPDIR=/tmp/picglot \
    FONT_DIR=/app/assets/fonts \
    OMP_THREAD_LIMIT=1
COPY --from=builder /opt/venv /opt/venv

RUN groupadd --system --gid 1001 picglot \
    && useradd --system --uid 1001 --gid picglot --create-home picglot \
    && mkdir -p /tmp/picglot /app /home/picglot/.cache \
    && chown -R picglot:picglot /tmp/picglot /app /home/picglot

WORKDIR /app
COPY --chown=picglot:picglot apps/api/alembic.ini ./alembic.ini
# Workers do not migrate, but they ship alembic.ini and the revisions travel
# with it so a shell in a worker can inspect or repair schema state.
COPY --chown=picglot:picglot apps/api/migrations ./migrations
COPY --chown=picglot:picglot apps/api/picglot ./picglot
COPY --chown=picglot:picglot assets ./assets

USER picglot
STOPSIGNAL SIGTERM

CMD ["celery", "-A", "picglot.workers.celery_app", "worker", "-Q", "cpu", "-l", "info"]
