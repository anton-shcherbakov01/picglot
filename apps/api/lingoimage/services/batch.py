"""Batch processing: one parent job fans out to child jobs, then collects results."""

from __future__ import annotations

import io
import zipfile
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lingoimage.core.errors import AppError, ErrorCode
from lingoimage.core.logging import get_logger
from lingoimage.db.session import session_scope
from lingoimage.domain.enums import AssetKind, ExportFormat, JobStatus, JobType
from lingoimage.services import exports as export_service
from lingoimage.services import jobs as job_service
from lingoimage.services import projects as project_service
from lingoimage.services import storage

log = get_logger(__name__)


def run(parent_job_id: str) -> dict[str, Any]:
    """Wait for children, then assemble a ZIP plus a per-file CSV report."""
    from lingoimage.db.models import Job

    with session_scope() as session:
        parent = job_service.get_job(session, parent_job_id)
        children = list(
            session.execute(select(Job).where(Job.parent_job_id == parent.id)).scalars()
        )
        if not children:
            raise AppError(code=ErrorCode.VALIDATION_FAILED, internal="batch job has no children")

        finished = [child for child in children if JobStatus(child.status).is_terminal]
        progress = len(finished) / max(len(children), 1)
        job_service.update_progress(
            session,
            parent,
            stage=JobStatus.EXPORTING if progress >= 1 else JobStatus.RECOGNIZING,
            progress=min(0.95, 0.05 + progress * 0.9),
            data={"completed": len(finished), "total": len(children)},
        )
        session.commit()

        if progress < 1:
            # Not everything is done yet; the last child to finish re-triggers us.
            return {"pending": len(children) - len(finished)}

        return _assemble(session, parent, children)


def _assemble(session: Session, parent: Any, children: list[Any]) -> dict[str, Any]:
    project = project_service.get_project(session, parent.project_id, with_pages=True)
    formats = parent.input.get("export_formats") or [ExportFormat.PNG]
    options = export_service.ExportOptions.from_dict(parent.input.get("export_options"))

    rows: list[dict[str, Any]] = []
    buffer = io.BytesIO()
    succeeded = 0

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for child in children:
            child_project = project_service.get_project(session, child.project_id, with_pages=True)
            label = child.input.get("filename") or child_project.name
            if child.status not in {
                str(JobStatus.COMPLETED),
                str(JobStatus.PARTIALLY_COMPLETED),
            }:
                rows.append(
                    {
                        "file": label,
                        "status": child.status,
                        "pages": child.pages_total,
                        "error_code": child.error_code or "",
                        "project_id": child.project_id,
                        "download": "",
                    }
                )
                continue

            succeeded += 1
            for fmt in formats[:3]:
                try:
                    export = export_service.build(
                        session, child_project, ExportFormat(str(fmt)), options
                    )
                    asset = project_service.get_asset(session, export.asset_id)
                    data = storage.get_storage().get(asset.storage_key)
                    stem = label.rsplit(".", 1)[0][:60]
                    archive.writestr(f"{stem}/{stem}.{ExportFormat(str(fmt)).extension}", data)
                except AppError as exc:
                    log.warning(
                        "batch.export_failed",
                        child=child.id,
                        format=str(fmt),
                        code=str(exc.code),
                    )
            rows.append(
                {
                    "file": label,
                    "status": child.status,
                    "pages": child.pages_completed,
                    "error_code": child.error_code or "",
                    "project_id": child.project_id,
                    "download": f"/app/projects/{child.project_id}",
                }
            )

        archive.writestr("report.csv", export_service.batch_report_csv(rows))

    asset = project_service.store_asset(
        session,
        project,
        data=buffer.getvalue(),
        kind=AssetKind.EXPORT,
        mime_type="application/zip",
        extension="zip",
        original_filename=f"{project.name}-batch.zip",
        metadata={"files": len(children), "succeeded": succeeded},
    )
    from lingoimage.db.models import Export

    export = Export(
        project_id=project.id,
        job_id=parent.id,
        format=str(ExportFormat.ZIP),
        asset_id=asset.id,
        byte_size=asset.byte_size,
        settings=options.as_dict(),
        expires_at=project.expires_at,
    )
    session.add(export)
    session.flush()

    output = {
        "files_total": len(children),
        "files_succeeded": succeeded,
        "files_failed": len(children) - succeeded,
        "archive_export_id": export.id,
        "report": rows,
    }
    parent.pages_completed = succeeded
    parent.pages_failed = len(children) - succeeded
    if succeeded == 0:
        job_service.mark_failed(
            session,
            parent,
            AppError(code=ErrorCode.INTERNAL_ERROR, internal="every file in the batch failed"),
        )
    elif succeeded < len(children):
        job_service.mark_partial(
            session, parent, output=output, pages_failed=len(children) - succeeded
        )
    else:
        job_service.mark_completed(session, parent, output=output)
    session.commit()

    from lingoimage.domain.enums import WebhookEvent
    from lingoimage.services import notifications

    notifications.on_job_finished(session, parent, WebhookEvent.BATCH_COMPLETED)
    return output


def on_child_finished(session: Session, child: Any) -> None:
    """Called when a child job reaches a terminal state."""
    if not child.parent_job_id:
        return
    if settings_inline():
        run(child.parent_job_id)
        return
    from lingoimage.workers.celery_app import celery_app

    celery_app.send_task("lingoimage.execute_batch", args=[child.parent_job_id])


def settings_inline() -> bool:
    from lingoimage.core.config import settings

    return settings.queue_backend == "inline"


def retry_failed_children(session: Session, parent_job_id: str) -> int:
    from lingoimage.db.models import Job

    children = list(
        session.execute(
            select(Job).where(
                Job.parent_job_id == parent_job_id, Job.status == str(JobStatus.FAILED)
            )
        ).scalars()
    )
    for child in children:
        job_service.retry(session, child, forced=True)
    return len(children)


def cancel_batch(session: Session, parent_job_id: str) -> int:
    from lingoimage.db.models import Job

    children = list(
        session.execute(select(Job).where(Job.parent_job_id == parent_job_id)).scalars()
    )
    cancelled = 0
    for child in children:
        if not JobStatus(child.status).is_terminal:
            job_service.request_cancel(session, child)
            cancelled += 1
    parent = job_service.get_job(session, parent_job_id)
    if not JobStatus(parent.status).is_terminal:
        job_service.request_cancel(session, parent)
    return cancelled


def create_child(
    session: Session,
    *,
    parent: Any,
    project: Any,
    payload: dict[str, Any],
    pages: int,
    user: Any | None,
) -> Any:
    child = job_service.create_job(
        session,
        project=project,
        job_type=JobType.FULL_PIPELINE,
        user=user,
        workspace_id=parent.workspace_id,
        guest_session_id=parent.guest_session_id,
        payload=payload,
        pages_total=pages,
    )
    child.parent_job_id = parent.id
    session.flush()
    return child
