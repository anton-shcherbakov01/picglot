"""Retention enforcement.

Runs every 15 minutes. Deletes expired assets from object storage *and* the
database, records proof of deletion without keeping any content, and revokes
share links pointing at removed projects.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from lingoimage.core.config import settings
from lingoimage.core.logging import get_logger
from lingoimage.db.models import (
    Asset,
    DeletionRecord,
    Export,
    GuestSession,
    IdempotencyRecord,
    Project,
    ShareLink,
    VerificationToken,
)
from lingoimage.services import storage

log = get_logger(__name__)

BATCH = 500


def sweep(session: Session) -> dict[str, Any]:
    now = datetime.now(UTC)
    stats = {
        "assets_deleted": 0,
        "bytes_freed": 0,
        "projects_purged": 0,
        "exports_removed": 0,
        "guest_sessions_removed": 0,
        "tokens_removed": 0,
        "share_links_revoked": 0,
    }

    stats.update(_expire_assets(session, now))
    stats["projects_purged"] = _purge_projects(session, now)
    stats["exports_removed"] = _remove_exports(session, now)
    stats["guest_sessions_removed"] = _remove_guests(session, now)
    stats["tokens_removed"] = _remove_tokens(session, now)
    stats["share_links_revoked"] = _revoke_share_links(session, now)

    if any(value for value in stats.values()):
        log.info("lifecycle.swept", **stats)
    return stats


def _expire_assets(session: Session, now: datetime) -> dict[str, int]:
    rows = list(
        session.execute(
            select(Asset).where(Asset.expires_at.is_not(None), Asset.expires_at <= now).limit(BATCH)
        ).scalars()
    )
    if not rows:
        return {"assets_deleted": 0, "bytes_freed": 0}

    backend = storage.get_storage()
    removed = backend.delete_many([asset.storage_key for asset in rows])
    freed = sum(int(asset.byte_size or 0) for asset in rows)

    session.execute(delete(Asset).where(Asset.id.in_([asset.id for asset in rows])))
    session.add(
        DeletionRecord(
            subject_type="asset",
            subject_id=f"batch:{now.isoformat()}",
            reason="retention_expired",
            objects_removed=removed,
            bytes_removed=freed,
            initiated_by="lifecycle_worker",
        )
    )
    return {"assets_deleted": removed, "bytes_freed": freed}


def _purge_projects(session: Session, now: datetime) -> int:
    from lingoimage.services import projects as project_service

    rows = list(
        session.execute(
            select(Project)
            .where(Project.expires_at.is_not(None), Project.expires_at <= now)
            .limit(100)
        ).scalars()
    )
    purged = 0
    for project in rows:
        objects = project_service.purge(session, project)
        session.add(
            DeletionRecord(
                subject_type="project",
                subject_id=project.id,
                reason="retention_expired" if project.deleted_at is None else "trash_expired",
                objects_removed=objects,
                initiated_by="lifecycle_worker",
            )
        )
        purged += 1
    return purged


def _remove_exports(session: Session, now: datetime) -> int:
    rows = list(
        session.execute(
            select(Export)
            .where(Export.expires_at.is_not(None), Export.expires_at <= now)
            .limit(BATCH)
        ).scalars()
    )
    for export in rows:
        session.delete(export)
    return len(rows)


def _remove_guests(session: Session, now: datetime) -> int:
    result = session.execute(
        delete(GuestSession).where(
            GuestSession.expires_at <= now, GuestSession.claimed_by_user_id.is_(None)
        )
    )
    return int(result.rowcount or 0)


def _remove_tokens(session: Session, now: datetime) -> int:
    tokens = session.execute(delete(VerificationToken).where(VerificationToken.expires_at <= now))
    idempotency = session.execute(
        delete(IdempotencyRecord).where(IdempotencyRecord.expires_at <= now)
    )
    return int(tokens.rowcount or 0) + int(idempotency.rowcount or 0)


def _revoke_share_links(session: Session, now: datetime) -> int:
    result = session.execute(
        update(ShareLink)
        .where(
            ShareLink.revoked_at.is_(None),
            ShareLink.expires_at.is_not(None),
            ShareLink.expires_at <= now,
        )
        .values(revoked_at=now)
    )
    return int(result.rowcount or 0)


def schedule_project_expiry(project: Project, plan_code: str | None) -> None:
    from datetime import timedelta

    hours = settings.retention_hours(plan_code)
    project.expires_at = datetime.now(UTC) + timedelta(hours=hours)


def delete_project_now(session: Session, project: Project) -> dict[str, Any]:
    """Immediate, user-initiated deletion — storage first, then rows."""
    from lingoimage.services import projects as project_service

    keys = [asset.storage_key for asset in project.assets]
    bytes_removed = sum(int(asset.byte_size or 0) for asset in project.assets)
    objects = project_service.purge(session, project)
    session.add(
        DeletionRecord(
            subject_type="project",
            subject_id=project.id,
            reason="user_requested",
            objects_removed=objects,
            bytes_removed=bytes_removed,
            initiated_by="user",
        )
    )
    return {"objects_removed": objects, "keys": len(keys)}


def storage_usage(session: Session) -> dict[str, int]:
    from sqlalchemy import func

    total = session.execute(select(func.coalesce(func.sum(Asset.byte_size), 0))).scalar_one()
    count = session.execute(select(func.count(Asset.id))).scalar_one()
    return {"bytes": int(total), "objects": int(count)}
