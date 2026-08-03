"""Task definitions.

Each task is written so that it can also be called as a plain function — that
is what the inline queue backend and the tests do, guaranteeing one code path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from picglot.core.errors import AppError, ErrorCode
from picglot.core.logging import get_logger
from picglot.db.session import session_scope
from picglot.domain.enums import ExportFormat, JobStatus, WebhookEvent

log = get_logger(__name__)


def _task(name: str):
    """Register with Celery when it is importable; always return the function."""

    def decorator(function):
        try:
            from picglot.workers.celery_app import celery_app

            celery_app.task(name=name, bind=False, ignore_result=True)(function)
        except Exception:  # pragma: no cover - inline mode / import cycle at boot
            pass
        return function

    return decorator


# --------------------------------------------------------------------------- #
# Main job execution
# --------------------------------------------------------------------------- #
@_task("picglot.execute_job")
def execute_job(job_id: str) -> dict[str, Any]:
    from picglot.services import jobs as job_service
    from picglot.services import pipeline

    with session_scope() as session:
        job = job_service.get_job(session, job_id)
        if JobStatus(job.status).is_terminal:
            log.info("task.skip_terminal", job_id=job_id, status=job.status)
            return {"skipped": True}
        if job_service.cancel_requested(session, job):
            job_service.mark_cancelled(session, job)
            return {"cancelled": True}
        job_service.transition(session, job, JobStatus.PREPROCESSING, progress=0.01)
        session.commit()

    try:
        with session_scope() as session:
            job = job_service.get_job(session, job_id)
            result = pipeline.run_job(session, job)

            output = {
                "pages_total": result.pages_total,
                "pages_completed": result.pages_completed,
                "pages_failed": result.pages_failed,
                "quality": result.quality,
                "providers": result.provider_summary,
                "page_results": [
                    {
                        "page": outcome.page_number,
                        "ok": outcome.ok,
                        "error_code": outcome.error_code,
                        "regions": outcome.region_count,
                    }
                    for outcome in result.outcomes
                ],
            }
            if result.pages_completed == 0:
                raise AppError(
                    code=ErrorCode(result.outcomes[0].error_code or ErrorCode.INTERNAL_ERROR)
                    if result.outcomes
                    else ErrorCode.INTERNAL_ERROR,
                    details={"pages_failed": result.pages_failed},
                )
            if result.pages_failed:
                job_service.mark_partial(
                    session, job, output=output, pages_failed=result.pages_failed
                )
                event = WebhookEvent.JOB_PARTIALLY_COMPLETED
            else:
                job_service.mark_completed(session, job, output=output)
                event = WebhookEvent.JOB_COMPLETED

            _auto_exports(session, job)
            session.commit()
            _notify(session, job, event)
            return output

    except AppError as exc:
        with session_scope() as session:
            job = job_service.get_job(session, job_id)
            if exc.code is ErrorCode.CANCELLED:
                if not JobStatus(job.status).is_terminal:
                    job_service.mark_cancelled(session, job)
                return {"cancelled": True}
            job_service.mark_failed(session, job, exc)
            session.commit()
            _notify(session, job, WebhookEvent.JOB_FAILED)
        raise
    except Exception as exc:  # pragma: no cover - unexpected
        log.exception("task.job_crashed", job_id=job_id)
        wrapped = AppError(code=ErrorCode.INTERNAL_ERROR, internal=f"{type(exc).__name__}: {exc}")
        with session_scope() as session:
            job = job_service.get_job(session, job_id)
            job_service.mark_failed(session, job, wrapped)
            session.commit()
        raise wrapped from exc


def _auto_exports(session: Any, job: Any) -> None:
    """Produce the formats the request asked for, so a download is ready."""
    from picglot.services import exports as export_service
    from picglot.services import projects as project_service

    formats = job.input.get("export_formats") or []
    if not formats:
        return
    project = project_service.get_project(session, job.project_id, with_pages=True)
    options = export_service.ExportOptions.from_dict(job.input.get("export_options"))
    produced: list[dict[str, Any]] = []
    for fmt in formats[:6]:
        try:
            export = export_service.build(session, project, ExportFormat(str(fmt)), options)
            produced.append({"format": str(fmt), "export_id": export.id, "bytes": export.byte_size})
        except AppError as exc:
            log.warning("task.auto_export_failed", format=str(fmt), code=str(exc.code))
    job.output = {**(job.output or {}), "exports": produced}


def _notify(session: Any, job: Any, event: WebhookEvent) -> None:
    from picglot.services import notifications

    try:
        notifications.on_job_finished(session, job, event)
    except Exception:  # pragma: no cover - notifications must never fail a job
        log.exception("task.notify_failed", job_id=job.id)


# --------------------------------------------------------------------------- #
# Exports
# --------------------------------------------------------------------------- #
@_task("picglot.build_export")
def build_export(job_id: str) -> dict[str, Any]:
    from picglot.services import exports as export_service
    from picglot.services import jobs as job_service
    from picglot.services import projects as project_service

    with session_scope() as session:
        job = job_service.get_job(session, job_id)
        job_service.transition(session, job, JobStatus.EXPORTING, progress=0.2)
        session.commit()

    try:
        with session_scope() as session:
            job = job_service.get_job(session, job_id)
            project = project_service.get_project(session, job.project_id, with_pages=True)
            options = export_service.ExportOptions.from_dict(job.input.get("options"))
            fmt = ExportFormat(str(job.input.get("format", ExportFormat.PDF)))
            export = export_service.build(session, project, fmt, options)
            output = {
                "export_id": export.id,
                "format": str(fmt),
                "byte_size": export.byte_size,
            }
            job_service.mark_completed(session, job, output=output)
            session.commit()
            _notify(session, job, WebhookEvent.EXPORT_READY)
            return output
    except AppError as exc:
        with session_scope() as session:
            job = job_service.get_job(session, job_id)
            job_service.mark_failed(session, job, exc)
            session.commit()
        raise


# --------------------------------------------------------------------------- #
# Batch
# --------------------------------------------------------------------------- #
@_task("picglot.execute_batch")
def execute_batch(job_id: str) -> dict[str, Any]:
    """Fan out to child jobs, then collect results into a ZIP and a CSV report."""
    from picglot.services import batch as batch_service
    from picglot.services import jobs as job_service

    with session_scope() as session:
        job = job_service.get_job(session, job_id)
        job_service.transition(session, job, JobStatus.QUEUED, progress=0.05)
        session.commit()

    return batch_service.run(job_id)


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #
@_task("picglot.deliver_webhook")
def deliver_webhook(delivery_id: str) -> dict[str, Any]:
    from picglot.services import webhooks

    with session_scope() as session:
        return webhooks.deliver(session, delivery_id)


@_task("picglot.send_email")
def send_email(template: str, to: str, context: dict[str, Any]) -> dict[str, Any]:
    from picglot.providers import email

    return email.send(template=template, to=to, context=context)


# --------------------------------------------------------------------------- #
# Scheduled maintenance
# --------------------------------------------------------------------------- #
@_task("picglot.lifecycle_maintenance")
def lifecycle_maintenance() -> dict[str, Any]:
    from picglot.services import lifecycle

    with session_scope() as session:
        return lifecycle.sweep(session)


@_task("picglot.stuck_jobs_maintenance")
def stuck_jobs_maintenance() -> dict[str, Any]:
    from picglot.services import jobs as job_service

    recovered = 0
    failed = 0
    with session_scope() as session:
        for job in job_service.find_stuck(session):
            if job_service.can_retry(job) or int(job.attempt) < int(job.max_attempts):
                job_service.retry(session, job, forced=True)
                recovered += 1
            else:
                job_service.mark_failed(
                    session,
                    job,
                    AppError(
                        code=ErrorCode.INTERNAL_ERROR,
                        internal="worker stopped responding; job timed out",
                    ),
                )
                failed += 1
        session.commit()
    if recovered or failed:
        log.warning("maintenance.stuck_jobs", recovered=recovered, failed=failed)
    return {"recovered": recovered, "failed": failed}


@_task("picglot.webhook_retry_maintenance")
def webhook_retry_maintenance() -> dict[str, Any]:
    from picglot.services import webhooks

    with session_scope() as session:
        return webhooks.retry_due(session)


@_task("picglot.monthly_credits_maintenance")
def monthly_credits_maintenance() -> dict[str, Any]:
    from sqlalchemy import select

    from picglot.db.models import User
    from picglot.domain import plans
    from picglot.services import credits as credit_service

    granted = 0
    period = datetime.now(UTC).strftime("%Y-%m")
    with session_scope() as session:
        users = session.execute(
            select(User).where(User.deleted_at.is_(None), User.status == "active")
        ).scalars()
        for user in users:
            spec = plans.by_code(user.plan_code)
            if not spec or spec.monthly_credits <= 0:
                continue
            outcome = credit_service.grant_monthly(
                session, user=user, amount=spec.monthly_credits, period=period
            )
            if outcome and not outcome.duplicate:
                granted += 1
        session.commit()
    return {"granted": granted, "period": period}


@_task("picglot.provider_health_maintenance")
def provider_health_maintenance() -> dict[str, Any]:
    from sqlalchemy import select

    from picglot.db.models import ProviderConfiguration
    from picglot.providers import ocr as ocr_providers
    from picglot.providers import translation as translation_providers

    reports: list[dict[str, Any]] = []
    for provider in [
        *ocr_providers.available_providers(),
        *translation_providers.available_providers(),
    ]:
        report = provider.health()
        reports.append(report.as_dict())

    with session_scope() as session:
        for report in reports:
            row = session.execute(
                select(ProviderConfiguration).where(
                    ProviderConfiguration.kind == report["kind"],
                    ProviderConfiguration.name == report["name"],
                )
            ).scalar_one_or_none()
            if row is None:
                row = ProviderConfiguration(kind=report["kind"], name=report["name"])
                session.add(row)
            row.health_status = report["state"]
            row.health_checked_at = datetime.now(UTC)
            row.health_detail = (report["detail"] or "")[:255]
        session.commit()
    return {"checked": len(reports)}
