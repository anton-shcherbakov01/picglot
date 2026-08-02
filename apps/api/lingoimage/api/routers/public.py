"""Public surface: share links, SEO content, blog, contact, analytics ingest."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import select

from lingoimage.api.deps import CurrentIdentity, DbSession, client_ip
from lingoimage.core.config import settings
from lingoimage.core.errors import AppError, ErrorCode
from lingoimage.core.logging import get_logger
from lingoimage.core.ratelimit import check as rate_check
from lingoimage.core.security import hash_ip
from lingoimage.db.models import AnalyticsEvent, BlogPost, ContactRequest, SeoPage, Testimonial
from lingoimage.schemas import (
    AnalyticsEventIn,
    BlogPostOut,
    ContactRequestIn,
    SeoPageOut,
)
from lingoimage.services import share as share_service

log = get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["public"])


# --------------------------------------------------------------------------- #
# Share links
# --------------------------------------------------------------------------- #
@router.get("/share/{token}")
def view_share(
    token: str, request: Request, session: DbSession, password: str | None = None
) -> dict[str, Any]:
    link, project = share_service.resolve(session, token, password=password, ip=client_ip(request))
    payload = share_service.public_view(session, link, project)
    session.commit()
    return payload


@router.get("/share/{token}/download")
def download_share(
    token: str,
    request: Request,
    session: DbSession,
    password: str | None = None,
    export_id: str | None = None,
) -> dict[str, str]:
    link, project = share_service.resolve(session, token, password=password, ip=client_ip(request))
    share_service.record_download(session, link, client_ip(request))

    from lingoimage.db.models import Export
    from lingoimage.services import exports as export_service

    target_id = export_id or link.export_id
    if target_id:
        export = session.get(Export, target_id)
    else:
        export = (
            session.execute(
                select(Export)
                .where(Export.project_id == project.id)
                .order_by(Export.created_at.desc())
            )
            .scalars()
            .first()
        )
    if export is None or export.project_id != project.id:
        raise AppError(code=ErrorCode.NOT_FOUND, internal="no export available for this link")

    url = export_service.download_url(session, export)
    session.commit()
    return {"url": url}


# --------------------------------------------------------------------------- #
# SEO content
# --------------------------------------------------------------------------- #
@router.get("/content/pages/{locale}", response_model=list[SeoPageOut])
def list_pages(locale: str, session: DbSession, kind: str | None = None) -> list[SeoPageOut]:
    statement = select(SeoPage).where(SeoPage.locale == locale, SeoPage.published.is_(True))
    if kind:
        statement = statement.where(SeoPage.kind == kind)
    return [SeoPageOut.model_validate(row) for row in session.execute(statement).scalars()]


@router.get("/content/page", response_model=SeoPageOut)
def get_page(path: str, locale: str, session: DbSession) -> SeoPageOut:
    row = session.execute(
        select(SeoPage).where(
            SeoPage.path == path, SeoPage.locale == locale, SeoPage.published.is_(True)
        )
    ).scalar_one_or_none()
    if row is None:
        raise AppError(code=ErrorCode.NOT_FOUND, details={"path": path, "locale": locale})
    return SeoPageOut.model_validate(row)


@router.get("/content/sitemap")
def sitemap_data(session: DbSession, locale: str | None = None) -> dict[str, Any]:
    """Feeds the Next.js sitemap route so URLs come from one source."""
    statement = select(SeoPage).where(SeoPage.published.is_(True), SeoPage.noindex.is_(False))
    if locale:
        statement = statement.where(SeoPage.locale == locale)
    pages = [
        {
            "path": row.path,
            "locale": row.locale,
            "kind": row.kind,
            "updated_at": row.updated_at.isoformat(),
        }
        for row in session.execute(statement).scalars()
    ]
    posts = [
        {
            "slug": row.slug,
            "locale": row.locale,
            "updated_at": (row.published_at or row.updated_at).isoformat(),
        }
        for row in session.execute(
            select(BlogPost).where(BlogPost.published_at.is_not(None))
        ).scalars()
    ]
    return {"pages": pages, "posts": posts, "locales": settings.enabled_locales}


@router.get("/content/blog/{locale}", response_model=list[BlogPostOut])
def list_posts(locale: str, session: DbSession, limit: int = 20) -> list[BlogPostOut]:
    rows = session.execute(
        select(BlogPost)
        .where(BlogPost.locale == locale, BlogPost.published_at.is_not(None))
        .order_by(BlogPost.published_at.desc())
        .limit(min(50, limit))
    ).scalars()
    return [BlogPostOut.model_validate({**row.__dict__, "body_markdown": None}) for row in rows]


@router.get("/content/blog/{locale}/{slug}", response_model=BlogPostOut)
def get_post(locale: str, slug: str, session: DbSession) -> BlogPostOut:
    row = session.execute(
        select(BlogPost).where(
            BlogPost.locale == locale,
            BlogPost.slug == slug,
            BlogPost.published_at.is_not(None),
        )
    ).scalar_one_or_none()
    if row is None:
        raise AppError(code=ErrorCode.NOT_FOUND)
    return BlogPostOut.model_validate(row)


@router.get("/content/testimonials")
def testimonials(session: DbSession, locale: str = "en") -> dict[str, Any]:
    """Returns only genuinely published testimonials — the UI hides the section when empty."""
    rows = session.execute(
        select(Testimonial).where(Testimonial.published.is_(True), Testimonial.locale == locale)
    ).scalars()
    return {
        "items": [
            {
                "author_name": row.author_name,
                "author_title": row.author_title,
                "body": row.body,
                "source_url": row.source_url,
            }
            for row in rows
        ]
    }


# --------------------------------------------------------------------------- #
# Contact
# --------------------------------------------------------------------------- #
@router.post("/contact", status_code=status.HTTP_202_ACCEPTED)
def contact(
    payload: ContactRequestIn,
    request: Request,
    session: DbSession,
    identity: CurrentIdentity,
) -> dict[str, bool]:
    if payload.website:
        # Honeypot filled: pretend success, store nothing.
        log.info("contact.honeypot_triggered")
        return {"received": True}

    ip = hash_ip(client_ip(request)) or "anon"
    rate_check("contact", ip, 5, 3600).raise_if_blocked()

    session.add(
        ContactRequest(
            user_id=identity.user_id,
            email=str(payload.email),
            name=payload.name,
            category=payload.category,
            subject=payload.subject,
            message=payload.message,
            project_id=payload.project_id,
            request_id=payload.request_id or getattr(request.state, "request_id", None),
            ip_hash=ip,
        )
    )
    session.commit()
    return {"received": True}


# --------------------------------------------------------------------------- #
# Analytics ingest
# --------------------------------------------------------------------------- #
#: Only these property keys are stored. Document content can never leak through.
ALLOWED_PROPERTIES = frozenset(
    {
        "tool",
        "format",
        "locale",
        "plan",
        "pages",
        "duration_ms",
        "source",
        "error_code",
        "step",
        "variant",
        "target_language",
        "source_language",
        "file_type",
        "file_size_bucket",
        "render_mode",
        "provider",
    }
)


@router.post("/analytics/events", status_code=status.HTTP_204_NO_CONTENT)
def track(
    payload: AnalyticsEventIn,
    request: Request,
    session: DbSession,
    identity: CurrentIdentity,
) -> Response:
    if not settings.analytics_enabled:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    ip = hash_ip(client_ip(request)) or "anon"
    if not rate_check("analytics", ip, 240, 60).allowed:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    properties = {
        key: value
        for key, value in payload.properties.items()
        if key in ALLOWED_PROPERTIES and isinstance(value, (str, int, float, bool))
    }
    session.add(
        AnalyticsEvent(
            name=payload.name[:64],
            user_id=identity.user_id,
            anonymous_id=(payload.anonymous_id or "")[:64] or None,
            locale=payload.locale,
            properties=properties,
            utm={
                key: str(value)[:120]
                for key, value in list(payload.utm.items())[:8]
                if key.startswith("utm_") or key in {"referrer", "landing_path"}
            },
        )
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Sample files
# --------------------------------------------------------------------------- #
@router.get("/samples")
def samples() -> dict[str, Any]:
    """Safe demo files a visitor can try without uploading anything of their own."""
    base = f"{settings.public_web_url}/samples"
    return {
        "items": [
            {
                "key": "poster",
                "label_key": "samples.poster",
                "url": f"{base}/poster.png",
                "tools": ["image-translator", "translate-photo"],
            },
            {
                "key": "screenshot",
                "label_key": "samples.screenshot",
                "url": f"{base}/screenshot.png",
                "tools": ["screenshot-translator"],
            },
            {
                "key": "table",
                "label_key": "samples.table",
                "url": f"{base}/table.png",
                "tools": ["image-to-excel"],
            },
            {
                "key": "receipt",
                "label_key": "samples.receipt",
                "url": f"{base}/receipt.png",
                "tools": ["receipt-scanner", "invoice-ocr"],
            },
            {
                "key": "handwriting",
                "label_key": "samples.handwriting",
                "url": f"{base}/handwriting.png",
                "tools": ["handwriting-to-text"],
            },
            {
                "key": "document",
                "label_key": "samples.document",
                "url": f"{base}/document.pdf",
                "tools": ["pdf-translator", "pdf-ocr", "jpg-to-word"],
            },
        ]
    }
