"""FastAPI application: middleware, error handling, router wiring, lifespan."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from picglot import __version__
from picglot.api.routers import account, admin, auth, billing, jobs, projects, public, system
from picglot.core.config import settings
from picglot.core.errors import AppError, ErrorCode
from picglot.core.ids import request_id as new_request_id
from picglot.core.logging import bind_request, clear_context, configure_logging, get_logger
from picglot.core.metrics import http_request_duration, http_requests_total

log = get_logger(__name__)

DESCRIPTION = """
Recognise, translate, edit and convert text in images and documents.

**Authentication** — session cookie for the web app, `Authorization: Bearer lik_live_…`
for the public API.

**Idempotency** — send an `Idempotency-Key` header on any POST that creates a job or
a payment; repeating the request returns the original result instead of doing the work
twice.

**Errors** — every failure returns `{"error": {"code", "message", "retryable"}}` with a
stable machine-readable `code`.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    log.info(
        "app.starting",
        version=__version__,
        environment=settings.environment,
        queue=settings.queue_backend,
        storage=settings.storage_backend,
    )
    _init_optional_observability()

    if settings.storage_backend == "s3":
        try:
            from picglot.services.storage import get_storage

            get_storage().ensure_bucket()
        except Exception as exc:  # pragma: no cover - depends on environment
            log.warning("app.storage_unavailable", error=type(exc).__name__)

    # Warm the font index so the first render is not slow.
    try:
        from picglot.vision.fonts import registry

        registry.load()
    except Exception:  # pragma: no cover
        log.warning("app.font_index_failed")

    yield

    log.info("app.stopping")
    try:
        from picglot.workers.dispatch import shutdown

        # Production drains in-flight inline jobs. Under test the app is started
        # and stopped around jobs the test itself is driving, so joining the pool
        # here would deadlock against the caller.
        shutdown(wait=not settings.is_test)
    except Exception:  # pragma: no cover
        pass
    from picglot.db.session import dispose_engine

    dispose_engine()


def _init_optional_observability() -> None:
    if settings.sentry_dsn:
        try:
            import sentry_sdk

            sentry_sdk.init(
                dsn=settings.sentry_dsn,
                environment=settings.environment,
                release=__version__,
                traces_sample_rate=settings.sentry_traces_sample_rate,
                send_default_pii=False,  # never ship user content to an error tracker
            )
            log.info("app.sentry_enabled")
        except ImportError:
            log.warning("app.sentry_sdk_missing")

    if settings.otel_enabled:
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider(
                resource=Resource.create({"service.name": settings.otel_service_name})
            )
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
            )
            trace.set_tracer_provider(provider)
            log.info("app.otel_enabled")
        except ImportError:
            log.warning("app.otel_packages_missing")


def create_app() -> FastAPI:
    app = FastAPI(
        title=f"{settings.brand_name} API",
        description=DESCRIPTION,
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        contact={"name": settings.brand_name, "email": settings.brand_support_email},
        license_info={"name": "Proprietary"},
        servers=[{"url": settings.public_api_url, "description": settings.environment}],
    )

    _install_middleware(app)
    _install_error_handlers(app)

    app.include_router(system.router)
    app.include_router(auth.router)
    app.include_router(projects.router)
    app.include_router(jobs.router)
    app.include_router(account.router)
    app.include_router(billing.router)
    app.include_router(public.router)
    app.include_router(admin.router)

    return app


def _install_middleware(app: FastAPI) -> None:
    allowed_origins = [settings.public_web_url]
    if not settings.is_production:
        allowed_origins += [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
        ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(set(allowed_origins)),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=[
            "X-Request-ID",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
            "Retry-After",
        ],
        max_age=600,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    if settings.is_production:
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=[
                settings.brand_domain,
                f"*.{settings.brand_domain}",
                "api",
                "localhost",
            ],
        )

    @app.middleware("http")
    async def context_and_metrics(request: Request, call_next: Any) -> Any:
        request_id = request.headers.get("x-request-id") or new_request_id()
        request.state.request_id = request_id
        bind_request(request_id=request_id)
        started = time.perf_counter()

        try:
            response = await call_next(request)
        finally:
            clear_context()

        elapsed = time.perf_counter() - started
        route = request.scope.get("route")
        template = getattr(route, "path", request.url.path)

        response.headers["X-Request-ID"] = request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(self), microphone=(), geolocation=()"
        )
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload"
            )
        # The API returns JSON only; a restrictive CSP costs nothing here.
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
            if not request.url.path.startswith(("/docs", "/redoc"))
            else "default-src 'self'; img-src 'self' data: https:; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "frame-ancestors 'none'",
        )

        try:
            http_requests_total.labels(
                method=request.method, route=template, status=str(response.status_code)
            ).inc()
            http_request_duration.labels(method=request.method, route=template).observe(elapsed)
        except Exception:  # pragma: no cover
            pass
        return response

    @app.middleware("http")
    async def maintenance_gate(request: Request, call_next: Any) -> Any:
        if settings.maintenance_mode and not _maintenance_exempt(request.url.path):
            return JSONResponse(
                status_code=503,
                content=AppError(code=ErrorCode.MAINTENANCE).to_payload(
                    getattr(request.state, "request_id", None)
                ),
                headers={"Retry-After": "300"},
            )
        return await call_next(request)


def _maintenance_exempt(path: str) -> bool:
    return path.startswith(("/health", "/metrics", "/api/v1/status", "/api/v1/config", "/docs"))


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        if exc.internal:
            log.warning(
                "request.app_error",
                code=str(exc.code),
                status=exc.status_code,
                path=request.url.path,
                internal=exc.internal[:400],
            )
        headers = {}
        if exc.code is ErrorCode.RATE_LIMIT_EXCEEDED:
            retry_after = exc.details.get("retry_after_seconds")
            if retry_after:
                headers["Retry-After"] = str(retry_after)
        return JSONResponse(
            status_code=exc.status_code or 400,
            content=exc.to_payload(request_id),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {
                "field": ".".join(str(part) for part in error["loc"][1:]) or "body",
                "reason": error["type"],
            }
            for error in exc.errors()[:20]
        ]
        error = AppError(code=ErrorCode.VALIDATION_FAILED, details={"fields": fields})
        return JSONResponse(
            status_code=422,
            content=error.to_payload(getattr(request.state, "request_id", None)),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        # Full detail to the logs; the client only ever sees a reference.
        log.exception("request.unhandled", path=request.url.path, request_id=request_id)
        error = AppError(code=ErrorCode.INTERNAL_ERROR)
        return JSONResponse(status_code=500, content=error.to_payload(request_id))


app = create_app()
