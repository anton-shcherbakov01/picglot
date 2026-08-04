"""Job status, live progress (SSE), cancellation and retry."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from picglot.api.deps import CsrfProtected, CurrentIdentity, DbSession, Pagination
from picglot.core.errors import AppError, ErrorCode
from picglot.db.models import Job
from picglot.db.session import session_scope
from picglot.domain.enums import JobStatus
from picglot.schemas import JobListOut, JobOut
from picglot.services import batch as batch_service
from picglot.services import jobs as job_service
from picglot.services import projects as project_service

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

POLL_INTERVAL_SECONDS = 1.0
STREAM_TIMEOUT_SECONDS = 900


def _assert_access(session: Any, job: Job, identity: CurrentIdentity) -> None:
    if identity.user and (job.user_id == identity.user.id or identity.user.is_admin):
        return
    if identity.workspace_id and job.workspace_id == identity.workspace_id:
        return
    if identity.guest_id and job.guest_session_id == identity.guest_id:
        return
    if job.project_id:
        project = project_service.get_project(session, job.project_id)
        project_service.assert_can_read(
            session, project, user=identity.user, guest_session_id=identity.guest_id
        )
        return
    raise AppError(code=ErrorCode.NOT_FOUND)


@router.get("", response_model=JobListOut)
def list_jobs(
    session: DbSession,
    identity: CurrentIdentity,
    pagination: Pagination,
    job_status: str | None = None,
    job_type: str | None = None,
    parent_job_id: str | None = None,
) -> JobListOut:
    limit, offset = pagination
    statement = select(Job)
    if identity.workspace_id:
        statement = statement.where(Job.workspace_id == identity.workspace_id)
    elif identity.user:
        statement = statement.where(Job.user_id == identity.user.id)
    elif identity.guest_id:
        statement = statement.where(Job.guest_session_id == identity.guest_id)
    else:
        return JobListOut(items=[], total=0, limit=limit, offset=offset)

    if job_status:
        statement = statement.where(Job.status == job_status)
    if job_type:
        statement = statement.where(Job.type == job_type)
    if parent_job_id:
        # Children of one batch. Ownership is already scoped above, so this
        # cannot be used to read someone else's batch.
        statement = statement.where(Job.parent_job_id == parent_job_id)

    total = session.execute(select(func.count()).select_from(statement.subquery())).scalar_one()
    rows = session.execute(
        statement.order_by(Job.created_at.desc()).limit(limit).offset(offset)
    ).scalars()
    return JobListOut(
        items=[JobOut.model_validate(job_service.summarize(job)) for job in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, session: DbSession, identity: CurrentIdentity) -> JobOut:
    job = job_service.get_job(session, job_id)
    _assert_access(session, job, identity)
    return JobOut.model_validate(job_service.summarize(job))


@router.get("/{job_id}/events", summary="Live progress (Server-Sent Events)")
async def job_events(
    job_id: str, request: Request, session: DbSession, identity: CurrentIdentity
) -> StreamingResponse:
    """Streams progress until the job reaches a terminal state.

    Clients that cannot use SSE poll ``GET /jobs/{id}`` instead; both read the
    same state, so there is no behavioural difference.
    """
    job = job_service.get_job(session, job_id)
    _assert_access(session, job, identity)

    async def stream() -> AsyncIterator[bytes]:
        cursor = 0
        elapsed = 0.0
        last_status: str | None = None

        yield b": connected\n\n"
        while elapsed < STREAM_TIMEOUT_SECONDS:
            if await request.is_disconnected():
                break

            # `after_index` is keyword-only: passing the cursor positionally
            # raised TypeError inside the stream, which surfaced as a 500 on
            # every SSE connection while the job itself ran fine.
            events = await asyncio.to_thread(job_service.read_events, job_id, after_index=cursor)
            for event in events:
                cursor += 1
                last_status = event.get("status")
                payload = json.dumps(event, ensure_ascii=False)
                yield f"event: progress\ndata: {payload}\n\n".encode()

            snapshot = await asyncio.to_thread(_snapshot, job_id)
            if snapshot is None:
                break
            if JobStatus(snapshot["status"]).is_terminal:
                payload = json.dumps(snapshot, ensure_ascii=False, default=str)
                yield f"event: done\ndata: {payload}\n\n".encode()
                break
            if last_status is None:
                # No Redis events (fallback mode): stream the polled snapshot.
                payload = json.dumps(snapshot, ensure_ascii=False, default=str)
                yield f"event: progress\ndata: {payload}\n\n".encode()

            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            elapsed += POLL_INTERVAL_SECONDS
        else:
            yield b"event: timeout\ndata: {}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _snapshot(job_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        return job_service.summarize(job) if job else None


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(
    job_id: str, session: DbSession, identity: CurrentIdentity, _csrf: CsrfProtected
) -> JobOut:
    job = job_service.get_job(session, job_id)
    _assert_access(session, job, identity)
    job_service.request_cancel(session, job)
    if job.type == "batch":
        batch_service.cancel_batch(session, job.id)
    session.commit()
    return JobOut.model_validate(job_service.summarize(job))


@router.post("/{job_id}/retry", response_model=JobOut)
def retry_job(
    job_id: str, session: DbSession, identity: CurrentIdentity, _csrf: CsrfProtected
) -> JobOut:
    job = job_service.get_job(session, job_id)
    _assert_access(session, job, identity)
    job_service.retry(session, job)
    session.commit()
    return JobOut.model_validate(job_service.summarize(job))


@router.post("/{job_id}/retry-failed", response_model=JobOut)
def retry_failed_children(
    job_id: str, session: DbSession, identity: CurrentIdentity, _csrf: CsrfProtected
) -> JobOut:
    """Batch only: re-run just the files that failed."""
    job = job_service.get_job(session, job_id)
    _assert_access(session, job, identity)
    if job.type != "batch":
        raise AppError(code=ErrorCode.VALIDATION_FAILED, details={"reason": "not_a_batch_job"})
    retried = batch_service.retry_failed_children(session, job.id)
    session.commit()
    return JobOut.model_validate({**job_service.summarize(job), "output": {"retried": retried}})


@router.get("/{job_id}/timeline")
def job_timeline(job_id: str, session: DbSession, identity: CurrentIdentity) -> dict[str, Any]:
    job = job_service.get_job(session, job_id)
    _assert_access(session, job, identity)
    return {
        "job_id": job.id,
        "events": [
            {
                "stage": event.stage,
                "status": event.status,
                "progress": round(float(event.progress), 4),
                "data": event.data,
                "at": event.created_at.isoformat(),
            }
            for event in job_service.event_history(session, job.id)
        ],
    }
