"""Job lifecycle: creation, progress, events, cancellation, retry, dispatch."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lingoimage.core.config import settings
from lingoimage.core.errors import AppError, ErrorCode
from lingoimage.core.ids import ulid
from lingoimage.core.logging import get_logger
from lingoimage.core.metrics import job_duration, jobs_total, queue_depth
from lingoimage.core.redis import get_redis
from lingoimage.db.models import Job, JobEvent, Project, User
from lingoimage.domain import credits as credit_rules
from lingoimage.domain import plans
from lingoimage.domain.enums import (
    ACTIVE_JOB_STATUSES,
    STAGE_WEIGHTS,
    JobStatus,
    JobType,
    ToolType,
)
from lingoimage.services import credits as credit_service

log = get_logger(__name__)

EVENT_STREAM_TTL = 3600
EVENT_STREAM_MAX = 400


def create_job(
    session: Session,
    *,
    project: Project,
    job_type: JobType | str,
    user: User | None = None,
    workspace_id: str | None = None,
    guest_session_id: str | None = None,
    api_key_id: str | None = None,
    payload: dict[str, Any] | None = None,
    pages_total: int = 1,
    idempotency_key: str | None = None,
    charge: bool = True,
) -> Job:
    """Create a job, reserve its credits, and hand it to the queue."""
    if idempotency_key:
        existing = session.execute(
            select(Job).where(Job.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if existing is not None:
            log.info("job.idempotent_replay", job_id=existing.id)
            return existing

    plan_code = _plan_code(user, workspace_id, guest_session_id)
    payload = dict(payload or {})
    estimate = credit_rules.estimate_for_job(
        job_type=JobType(str(job_type)),
        tool=ToolType(project.tool_type),
        pages=pages_total,
        translate=bool(payload.get("translate")) and bool(project.target_language),
        options=payload.get("options") or {},
    )

    job = Job(
        project_id=project.id,
        user_id=user.id if user else None,
        workspace_id=workspace_id,
        guest_session_id=guest_session_id,
        api_key_id=api_key_id,
        type=str(job_type),
        status=JobStatus.CREATED,
        priority=_priority(plan_code),
        queue=_queue_for(job_type, payload),
        pages_total=pages_total,
        input=payload,
        max_attempts=settings.job_max_attempts,
        idempotency_key=idempotency_key,
        cost_estimate=estimate.as_dict(),
    )
    session.add(job)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        if idempotency_key:
            duplicate = session.execute(
                select(Job).where(Job.idempotency_key == idempotency_key)
            ).scalar_one()
            return duplicate
        raise

    _enforce_concurrency(session, job)

    if charge and estimate.total > 0 and (user or workspace_id):
        credit_service.charge_job(session, job, estimate.total)

    transition(session, job, JobStatus.QUEUED, progress=0.0)
    session.flush()
    return job


def _plan_code(user: User | None, workspace_id: str | None, guest_session_id: str | None) -> str:
    if guest_session_id and not user:
        return "guest"
    return user.plan_code if user else "free"


def _priority(plan_code: str) -> int:
    spec = plans.by_code(plan_code)
    return spec.priority if spec else 1


def _queue_for(job_type: JobType | str, payload: dict[str, Any]) -> str:
    if str(job_type) == JobType.EXPORT:
        return "export"
    if payload.get("advanced_inpaint") and settings.queue_gpu_enabled:
        return "gpu"
    return settings.queue_default


def _enforce_concurrency(session: Session, job: Job) -> None:
    """Stop one account from filling every worker."""
    if job.user_id:
        active = session.execute(
            select(func.count(Job.id)).where(
                Job.user_id == job.user_id,
                Job.status.in_([str(status) for status in ACTIVE_JOB_STATUSES]),
                Job.id != job.id,
            )
        ).scalar_one()
        if int(active) >= settings.max_concurrent_jobs_per_user:
            raise AppError(
                code=ErrorCode.RATE_LIMIT_EXCEEDED,
                details={
                    "reason": "concurrent_jobs",
                    "limit": settings.max_concurrent_jobs_per_user,
                },
            )
    if job.workspace_id:
        active = session.execute(
            select(func.count(Job.id)).where(
                Job.workspace_id == job.workspace_id,
                Job.status.in_([str(status) for status in ACTIVE_JOB_STATUSES]),
                Job.id != job.id,
            )
        ).scalar_one()
        if int(active) >= settings.max_concurrent_jobs_per_workspace:
            raise AppError(
                code=ErrorCode.RATE_LIMIT_EXCEEDED,
                details={
                    "reason": "workspace_concurrent_jobs",
                    "limit": settings.max_concurrent_jobs_per_workspace,
                },
            )


def get_job(session: Session, job_id: str) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise AppError(code=ErrorCode.NOT_FOUND, internal=f"job {job_id}")
    return job


def transition(
    session: Session,
    job: Job,
    status: JobStatus | str,
    *,
    progress: float | None = None,
    message_key: str | None = None,
    data: dict[str, Any] | None = None,
) -> Job:
    status = JobStatus(str(status))
    job.status = str(status)
    job.current_stage = str(status) if status in ACTIVE_JOB_STATUSES else job.current_stage
    if progress is not None:
        job.progress = max(0.0, min(1.0, progress))
    if status is JobStatus.PREPROCESSING and job.started_at is None:
        job.started_at = datetime.now(UTC)
    job.heartbeat_at = datetime.now(UTC)

    event = JobEvent(
        job_id=job.id,
        stage=str(status),
        status=str(status),
        progress=job.progress,
        message_key=message_key,
        data=data or {},
    )
    session.add(event)
    _publish(job, event)
    return job


def update_progress(
    session: Session,
    job: Job,
    *,
    stage: JobStatus | str,
    progress: float,
    data: dict[str, Any] | None = None,
) -> None:
    transition(session, job, stage, progress=progress, data=data)
    session.flush()


def stage_progress(stage: JobStatus, within_stage: float = 0.0) -> float:
    """Blend a stage's own progress into the overall figure."""
    completed = 0.0
    for status, weight in STAGE_WEIGHTS.items():
        if status is stage:
            return min(1.0, completed + weight * max(0.0, min(1.0, within_stage)))
        completed += weight
    return min(1.0, completed)


def mark_completed(
    session: Session, job: Job, *, output: dict[str, Any], partial: bool = False
) -> Job:
    status = JobStatus.PARTIALLY_COMPLETED if partial else JobStatus.COMPLETED
    job.output = output
    job.completed_at = datetime.now(UTC)
    job.progress = 1.0
    transition(session, job, status, progress=1.0)
    jobs_total.labels(type=job.type, status=str(status)).inc()
    if job.started_at:
        job_duration.labels(type=job.type).observe(
            (job.completed_at - job.started_at).total_seconds()
        )
    return job


def mark_failed(session: Session, job: Job, error: AppError, *, refund: bool = True) -> Job:
    reference = f"err_{ulid()}"
    job.error_code = str(error.code)
    job.error_message_safe = error.message[:512]
    job.internal_error_reference = reference
    job.completed_at = datetime.now(UTC)
    transition(
        session,
        job,
        JobStatus.FAILED,
        data={"error_code": str(error.code), "reference": reference},
    )
    jobs_total.labels(type=job.type, status=str(JobStatus.FAILED)).inc()

    if refund and error.refundable:
        credit_service.refund_job(session, job, note=f"failed: {error.code}")

    log.error(
        "job.failed",
        job_id=job.id,
        code=str(error.code),
        reference=reference,
        internal=(error.internal or "")[:300],
    )
    return job


def mark_partial(session: Session, job: Job, *, output: dict[str, Any], pages_failed: int) -> Job:
    """Some pages succeeded: keep them and refund the rest."""
    refund = credit_rules.refund_amount(
        int(job.credits_charged or 0), int(job.pages_total or 0), int(job.pages_completed or 0)
    )
    if refund > 0:
        credit_service.refund_job(
            session, job, refund, note=f"{pages_failed} page(s) could not be processed"
        )
    return mark_completed(session, job, output=output, partial=True)


def request_cancel(session: Session, job: Job) -> Job:
    """Cancel now if it has not started; otherwise signal the worker."""
    if JobStatus(job.status).is_terminal:
        raise AppError(code=ErrorCode.CONFLICT, details={"status": job.status})

    job.cancel_requested = True
    try:
        get_redis().setex(f"job:cancel:{job.id}", 7200, "1")
    except Exception:  # pragma: no cover
        pass

    if job.status in {str(JobStatus.CREATED), str(JobStatus.QUEUED)}:
        mark_cancelled(session, job)
    return job


def cancel_requested(session: Session, job: Job) -> bool:
    if job.cancel_requested:
        return True
    try:
        if get_redis().exists(f"job:cancel:{job.id}"):
            job.cancel_requested = True
            return True
    except Exception:  # pragma: no cover
        pass
    return False


def mark_cancelled(session: Session, job: Job) -> Job:
    job.cancelled_at = datetime.now(UTC)
    job.completed_at = datetime.now(UTC)
    transition(session, job, JobStatus.CANCELLED)
    jobs_total.labels(type=job.type, status=str(JobStatus.CANCELLED)).inc()
    # Nothing was produced, so the whole charge comes back.
    credit_service.refund_job(session, job, note="cancelled")
    return job


def can_retry(job: Job) -> bool:
    return (
        job.status == str(JobStatus.FAILED)
        and int(job.attempt) < int(job.max_attempts)
        and job.error_code in {str(code) for code in _RETRYABLE}
    )


_RETRYABLE = {
    ErrorCode.PROVIDER_UNAVAILABLE,
    ErrorCode.PROVIDER_TIMEOUT,
    ErrorCode.STORAGE_FAILED,
    ErrorCode.INTERNAL_ERROR,
    ErrorCode.RATE_LIMIT_EXCEEDED,
}


def retry(session: Session, job: Job, *, forced: bool = False) -> Job:
    if not forced and not can_retry(job):
        raise AppError(
            code=ErrorCode.CONFLICT,
            details={"status": job.status, "attempt": job.attempt},
            internal="job is not retryable",
        )
    job.attempt = int(job.attempt) + 1
    job.error_code = None
    job.error_message_safe = None
    job.completed_at = None
    job.cancel_requested = False
    job.progress = 0.0
    transition(session, job, JobStatus.QUEUED)
    session.flush()
    dispatch(job)
    return job


def backoff_seconds(attempt: int) -> int:
    return min(600, int(5 * (2 ** max(0, attempt - 1))))


def heartbeat(session: Session, job: Job) -> None:
    job.heartbeat_at = datetime.now(UTC)
    session.flush()


def find_stuck(session: Session) -> list[Job]:
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.job_stuck_after_seconds)
    return list(
        session.execute(
            select(Job).where(
                Job.status.in_([str(status) for status in ACTIVE_JOB_STATUSES]),
                Job.heartbeat_at.is_not(None),
                Job.heartbeat_at < cutoff,
            )
        ).scalars()
    )


# --------------------------------------------------------------------------- #
# Event stream (SSE / polling)
# --------------------------------------------------------------------------- #
def _publish(job: Job, event: JobEvent) -> None:
    payload = {
        "job_id": job.id,
        "status": event.status,
        "stage": event.stage,
        "progress": round(float(event.progress), 4),
        "message_key": event.message_key,
        "data": event.data,
        "at": datetime.now(UTC).isoformat(),
    }
    try:
        client = get_redis()
        key = f"job:events:{job.id}"
        client.rpush(key, json.dumps(payload))
        client.ltrim(key, -EVENT_STREAM_MAX, -1)
        client.expire(key, EVENT_STREAM_TTL)
    except Exception:  # pragma: no cover - events also live in the database
        pass


def read_events(job_id: str, *, after_index: int = 0) -> list[dict[str, Any]]:
    try:
        raw = get_redis().lrange(f"job:events:{job_id}", after_index, -1)
    except Exception:  # pragma: no cover
        return []
    events: list[dict[str, Any]] = []
    for item in raw:
        try:
            events.append(json.loads(item))
        except json.JSONDecodeError:
            continue
    return events


def event_history(session: Session, job_id: str, limit: int = 100) -> list[JobEvent]:
    return list(
        session.execute(
            select(JobEvent)
            .where(JobEvent.job_id == job_id)
            .order_by(JobEvent.created_at.asc())
            .limit(limit)
        ).scalars()
    )


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #
def dispatch(job: Job) -> None:
    """Hand the job to the configured queue backend."""
    from lingoimage.workers import dispatch as worker_dispatch

    worker_dispatch.enqueue(job.id, queue=job.queue, priority=job.priority)
    try:
        queue_depth.labels(queue=job.queue).inc()
    except Exception:  # pragma: no cover
        pass


def summarize(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "project_id": job.project_id,
        "type": job.type,
        "status": job.status,
        "stage": job.current_stage,
        "progress": round(float(job.progress), 4),
        "pages_total": job.pages_total,
        "pages_completed": job.pages_completed,
        "pages_failed": job.pages_failed,
        "credits_charged": job.credits_charged,
        "credits_refunded": job.credits_refunded,
        "cost_estimate": job.cost_estimate,
        "error": (
            {
                "code": job.error_code,
                "message": job.error_message_safe,
                "reference": job.internal_error_reference,
                "retryable": can_retry(job),
            }
            if job.error_code
            else None
        ),
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "output": job.output or {},
    }
