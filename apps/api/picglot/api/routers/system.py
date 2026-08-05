"""Health, readiness, configuration, metrics and the public status page."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Response
from sqlalchemy import select

from picglot import __version__
from picglot.api.deps import DbSession
from picglot.core.config import settings
from picglot.core.metrics import CONTENT_TYPE, db_up, render, storage_used_bytes
from picglot.core.redis import redis_healthy
from picglot.db.models import StatusIncident
from picglot.db.session import database_healthy, pool_stats
from picglot.domain import languages as language_table
from picglot.domain import plans as plan_catalog
from picglot.domain import tools as tool_catalog
from picglot.domain.credits import describe_rules
from picglot.providers import ocr as ocr_providers
from picglot.providers import translation as translation_providers
from picglot.schemas import AppConfigOut, HealthOut, StatusComponentOut, StatusOut

router = APIRouter(tags=["system"])


@router.get("/health/live", response_model=HealthOut, summary="Liveness probe")
def live() -> HealthOut:
    """Answers as soon as the process can serve traffic. No dependencies."""
    return HealthOut(status="ok", version=__version__, environment=settings.environment)


def _public_url_check() -> dict[str, Any]:
    """Report how object URLs are being handed to browsers, and why."""
    from picglot.services import storage

    if settings.storage_backend != "s3":
        return {"mode": "api", "reason": "local storage backend"}
    if not settings.storage_endpoint_reachable_by_browser:
        return {
            "mode": "api",
            "endpoint": settings.s3_browser_endpoint,
            "reason": "endpoint address is not one a browser could open",
            "fix": "set S3_PUBLIC_ENDPOINT_URL to the public https address of the store",
        }
    backend = storage.get_storage()
    answers = getattr(backend, "public_endpoint_answers", lambda: True)()
    if not answers:
        return {
            "mode": "api",
            "endpoint": settings.s3_browser_endpoint,
            "reason": "endpoint does not answer (DNS, TLS or routing)",
            "fix": "check the DNS record, the certificate for that name, and the vhost",
        }
    return {"mode": "direct", "endpoint": settings.s3_browser_endpoint}


@router.get("/health/ready", response_model=HealthOut, summary="Readiness probe")
def ready(response: Response) -> HealthOut:
    """Checks every dependency the app needs to actually do work."""
    from picglot.services import storage

    database = database_healthy()
    db_up.set(1 if database else 0)
    redis = redis_healthy()
    try:
        object_storage = storage.get_storage().healthy()
    except Exception:
        object_storage = False

    checks: dict[str, Any] = {
        "database": {"ok": database},
        "redis": {"ok": redis, "note": "" if redis else "using in-process fallback"},
        "storage": {"ok": object_storage, "backend": settings.storage_backend},
        # Whether *visitors* can fetch objects, which is a different question
        # from whether the API can. When this is false the API streams them
        # instead, so the app works — it just costs bandwidth it should not.
        "storage_public_url": _public_url_check(),
        "queue": {"backend": settings.queue_backend},
        "pool": pool_stats(),
    }
    # Redis degrades gracefully; the database and storage do not.
    critical_ok = database and object_storage
    status = "ok" if critical_ok and redis else "degraded" if critical_ok else "error"
    if not critical_ok:
        response.status_code = 503
    return HealthOut(
        status=status, version=__version__, environment=settings.environment, checks=checks
    )


@router.get("/health/providers", summary="Provider health")
def providers() -> dict[str, Any]:
    from picglot.providers import email as email_provider
    from picglot.providers.llm import get_llm

    return {
        "ocr": ocr_providers.health_report(),
        "translation": translation_providers.health_report(),
        "llm": get_llm().health().as_dict(),
        "email": email_provider.health(),
        "local_only_processing": settings.local_only_processing,
    }


@router.get("/metrics", include_in_schema=False)
def metrics(session: DbSession) -> Response:
    if not settings.metrics_enabled:
        return Response(status_code=404)
    from picglot.services import lifecycle

    try:
        storage_used_bytes.set(lifecycle.storage_usage(session)["bytes"])
    except Exception:  # pragma: no cover
        pass
    db_up.set(1 if database_healthy() else 0)
    return Response(content=render(), media_type=CONTENT_TYPE)


@router.get("/api/v1/config", response_model=AppConfigOut, summary="Client bootstrap config")
def app_config() -> AppConfigOut:
    """Everything the web app needs to render: tools, languages, plans, limits."""
    return AppConfigOut(
        brand_name=settings.brand_name,
        default_locale=settings.default_locale,
        locales=settings.enabled_locales,
        tools=[
            {
                "slug": tool.slug,
                "type": str(tool.type),
                "translates": tool.translates,
                "accepts": list(tool.accepts),
                "exports": [str(item) for item in tool.exports],
                "multipage": tool.multipage,
                "icon": tool.icon,
                "i18n_key": tool.i18n_key,
                "credit_multiplier": tool.credit_multiplier,
                "localized_slugs": tool_catalog.localized_slugs(tool),
            }
            for tool in tool_catalog.enabled_tools()
        ],
        languages=language_table.as_dicts(),
        plans=[spec.as_dict() for spec in plan_catalog.plan_specs() if spec.is_public],
        credit_rules=describe_rules(),
        limits={
            "guest_pages": settings.guest_free_pages,
            "max_upload_bytes": {
                "guest": settings.max_upload_bytes_guest,
                "free": settings.max_upload_bytes_free,
                "pro": settings.max_upload_bytes_pro,
                "business": settings.max_upload_bytes_business,
            },
            "max_pdf_pages": {
                "guest": settings.max_pdf_pages_guest,
                "free": settings.max_pdf_pages_free,
                "pro": settings.max_pdf_pages_pro,
                "business": settings.max_pdf_pages_business,
            },
            "max_image_pixels": settings.max_image_pixels,
            # Surfaced so the account area can state retention without
            # hard-coding numbers that are configuration everywhere else.
            "retention_hours": {
                "guest": settings.retention_guest_hours,
                "free": settings.retention_free_hours,
                "pro": settings.retention_pro_hours,
                "business": settings.retention_business_hours,
            },
        },
        features={
            "batch": settings.feature_batch,
            "api": settings.feature_public_api,
            "webhooks": settings.feature_webhooks,
            "sharing": settings.feature_sharing,
            "teams": settings.feature_teams,
            "pwa": settings.feature_pwa,
            "handwriting": settings.feature_handwriting,
            "tables": settings.feature_tables,
            "receipts": settings.feature_receipts,
            "billing": settings.billing_enabled,
        },
        translation_available=translation_providers.any_provider_configured(),
        local_only_processing=settings.local_only_processing,
        maintenance_mode=settings.maintenance_mode,
    )


@router.get("/api/v1/languages", summary="Supported languages")
def supported_languages() -> dict[str, Any]:
    return {"languages": language_table.as_dicts(), "ui_locales": settings.enabled_locales}


@router.get("/api/v1/status", response_model=StatusOut, summary="Service status")
def status(session: DbSession) -> StatusOut:
    """Derived from live health checks, with manual overrides from the admin panel."""
    from picglot.services import storage

    incidents = list(
        session.execute(
            select(StatusIncident)
            .where(StatusIncident.resolved_at.is_(None))
            .order_by(StatusIncident.started_at.desc())
        ).scalars()
    )
    overrides = {incident.component: incident for incident in incidents}

    def component(key: str, ok: bool, detail: str = "") -> StatusComponentOut:
        incident = overrides.get(key)
        if incident is not None and incident.manual_override:
            state = (
                "maintenance"
                if incident.is_scheduled
                else ("down" if incident.severity == "down" else "degraded")
            )
            return StatusComponentOut(key=key, state=state, detail=incident.title)
        return StatusComponentOut(key=key, state="operational" if ok else "down", detail=detail)

    database = database_healthy()
    try:
        object_storage = storage.get_storage().healthy()
    except Exception:
        object_storage = False

    ocr_ok = any(item["state"] in {"healthy", "degraded"} for item in ocr_providers.health_report())
    translation_ok = translation_providers.any_provider_configured()

    components = [
        component("web", True),
        component("api", database),
        component("processing", ocr_ok, "" if ocr_ok else "no OCR provider available"),
        component("translation", translation_ok, "" if translation_ok else "not configured"),
        component("storage", object_storage),
        component(
            "payments",
            not settings.billing_enabled
            or settings.billing_provider == "manual"
            or bool(settings.stripe_secret_key or settings.yookassa_shop_id),
        ),
    ]
    if settings.maintenance_mode:
        overall = "maintenance"
    elif any(item.state == "down" for item in components):
        overall = "down"
    elif any(item.state in {"degraded", "maintenance"} for item in components):
        overall = "degraded"
    else:
        overall = "operational"

    return StatusOut(
        overall=overall,  # type: ignore[arg-type]
        components=components,
        incidents=[
            {
                "id": incident.id,
                "component": incident.component,
                "title": incident.title,
                "severity": incident.severity,
                "started_at": incident.started_at.isoformat(),
                "scheduled": incident.is_scheduled,
            }
            for incident in incidents
        ],
        checked_at=datetime.now(UTC),
    )
