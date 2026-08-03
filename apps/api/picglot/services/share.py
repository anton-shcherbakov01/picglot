"""Share links: tokenised, optionally password protected, revocable, rate limited."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from picglot.core.errors import AppError, ErrorCode
from picglot.core.ids import token as random_token
from picglot.core.logging import get_logger
from picglot.core.ratelimit import share_views
from picglot.core.security import hash_ip, hash_password, hash_token, verify_password
from picglot.db.models import Project, ShareLink, ShareView
from picglot.domain.enums import SharePermission

log = get_logger(__name__)


def create(
    session: Session,
    project: Project,
    *,
    created_by_id: str | None,
    permission: SharePermission | str = SharePermission.VIEW,
    password: str | None = None,
    expires_in_hours: int | None = 168,
    max_views: int | None = None,
    show_owner: bool = False,
    watermark: bool = False,
    export_id: str | None = None,
) -> tuple[ShareLink, str]:
    raw = random_token(24)
    link = ShareLink(
        project_id=project.id,
        export_id=export_id,
        created_by_id=created_by_id,
        token_hash=hash_token(raw),
        permission=str(permission),
        password_hash=hash_password(password) if password else None,
        show_owner=show_owner,
        watermark=watermark,
        expires_at=(
            datetime.now(UTC) + timedelta(hours=expires_in_hours) if expires_in_hours else None
        ),
        max_views=max_views,
    )
    session.add(link)
    session.flush()
    return link, raw


def resolve(
    session: Session, raw_token: str, *, password: str | None = None, ip: str | None = None
) -> tuple[ShareLink, Project]:
    """Validate a share token. Every failure looks the same from outside."""
    token_hash = hash_token(raw_token)
    share_views(token_hash[:24]).raise_if_blocked()

    link = session.execute(
        select(ShareLink).where(ShareLink.token_hash == token_hash)
    ).scalar_one_or_none()
    if link is None or link.revoked_at is not None:
        raise AppError(code=ErrorCode.NOT_FOUND)
    if link.expires_at and link.expires_at <= datetime.now(UTC):
        raise AppError(code=ErrorCode.TOKEN_EXPIRED)
    if link.max_views is not None and int(link.view_count) >= int(link.max_views):
        raise AppError(code=ErrorCode.FORBIDDEN, details={"reason": "view_limit_reached"})
    if link.password_hash:
        if not password:
            raise AppError(code=ErrorCode.UNAUTHENTICATED, details={"reason": "password_required"})
        if not verify_password(password, link.password_hash):
            raise AppError(code=ErrorCode.INVALID_CREDENTIALS)

    project = session.get(Project, link.project_id)
    if project is None or project.deleted_at is not None:
        raise AppError(code=ErrorCode.NOT_FOUND)

    link.view_count = int(link.view_count) + 1
    session.add(ShareView(share_link_id=link.id, ip_hash=hash_ip(ip), action="view"))
    return link, project


def can_download(link: ShareLink) -> bool:
    return link.permission == str(SharePermission.VIEW_DOWNLOAD)


def record_download(session: Session, link: ShareLink, ip: str | None) -> None:
    if not can_download(link):
        raise AppError(code=ErrorCode.FORBIDDEN, details={"reason": "download_not_allowed"})
    session.add(ShareView(share_link_id=link.id, ip_hash=hash_ip(ip), action="download"))


def revoke(session: Session, link: ShareLink) -> None:
    link.revoked_at = datetime.now(UTC)


def list_for_project(session: Session, project_id: str) -> list[ShareLink]:
    return list(
        session.execute(
            select(ShareLink)
            .where(ShareLink.project_id == project_id, ShareLink.revoked_at.is_(None))
            .order_by(ShareLink.created_at.desc())
        ).scalars()
    )


def public_view(session: Session, link: ShareLink, project: Project) -> dict[str, Any]:
    """The payload a share page may see — deliberately narrow."""
    from picglot.services import projects as project_service
    from picglot.services import storage

    backend = storage.get_storage()
    pages: list[dict[str, Any]] = []
    for page in sorted(project.pages, key=lambda item: item.page_number):
        asset_id = page.rendered_asset_id or page.preview_asset_id
        url = None
        if asset_id:
            try:
                asset = project_service.get_asset(session, asset_id)
                url = backend.signed_download_url(asset.storage_key, expires_in=600)
            except AppError:
                url = None
        pages.append(
            {
                "page_number": page.page_number,
                "width": page.width,
                "height": page.height,
                "image_url": url,
            }
        )

    owner_name = None
    if link.show_owner and project.owner_user_id:
        from picglot.db.models import User

        owner = session.get(User, project.owner_user_id)
        owner_name = owner.name if owner else None

    return {
        "project": {
            "name": project.name,
            "tool": project.tool_type,
            "source_language": project.source_language,
            "target_language": project.target_language,
            "page_count": project.page_count,
            "created_at": project.created_at.isoformat(),
        },
        "owner": owner_name,
        "permission": link.permission,
        "can_download": can_download(link),
        "watermark": link.watermark,
        "pages": pages,
        "expires_at": link.expires_at.isoformat() if link.expires_at else None,
    }
