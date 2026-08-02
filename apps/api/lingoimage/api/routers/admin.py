"""Admin panel API.

Two principles enforced here:
  * every mutating action writes an audit record with a mandatory reason;
  * staff cannot read customer document content — opening a file is a separate,
    audited action, not a side effect of viewing a job.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import func, select

from lingoimage.api.deps import CsrfProtected, CurrentIdentity, DbSession, Pagination, client_ip
from lingoimage.core.config import settings
from lingoimage.core.errors import AppError, ErrorCode
from lingoimage.core.logging import get_logger
from lingoimage.core.security import hash_ip
from lingoimage.db.models import (
    AuditLog,
    ContactRequest,
    FeatureFlag,
    Job,
    Payment,
    Project,
    ProviderConfiguration,
    ProviderUsage,
    SeoPage,
    StatusIncident,
    User,
)
from lingoimage.domain.enums import AdminRole, JobStatus
from lingoimage.services import credits as credit_service
from lingoimage.services import jobs as job_service

log = get_logger(__name__)
router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _audit(
    session: Any,
    request: Request,
    actor: User,
    action: str,
    *,
    target_type: str | None = None,
    target_id: str | None = None,
    reason: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_user_id=actor.id,
            actor_role=actor.admin_role,
            action=action,
            target_type=target_type,
            target_id=target_id,
            reason=reason,
            ip_hash=hash_ip(client_ip(request)),
            request_id=getattr(request.state, "request_id", None),
            data=data or {},
        )
    )


def _require_reason(reason: str | None) -> str:
    if not reason or len(reason.strip()) < 5:
        raise AppError(
            code=ErrorCode.VALIDATION_FAILED,
            details={"field": "reason", "reason": "at_least_5_characters"},
        )
    return reason.strip()[:512]


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
@router.get("/dashboard")
def dashboard(session: DbSession, identity: CurrentIdentity, days: int = 7) -> dict[str, Any]:
    identity.require_admin()
    since = datetime.now(UTC) - timedelta(days=max(1, min(90, days)))

    new_users = session.execute(
        select(func.count(User.id)).where(User.created_at >= since)
    ).scalar_one()
    active_users = session.execute(
        select(func.count(func.distinct(Job.user_id))).where(Job.created_at >= since)
    ).scalar_one()
    job_rows = session.execute(
        select(Job.status, func.count(Job.id)).where(Job.created_at >= since).group_by(Job.status)
    ).all()
    by_status = {str(row[0]): int(row[1]) for row in job_rows}
    total_jobs = sum(by_status.values())
    completed = by_status.get(str(JobStatus.COMPLETED), 0) + by_status.get(
        str(JobStatus.PARTIALLY_COMPLETED), 0
    )

    durations = session.execute(
        select(Job.started_at, Job.completed_at).where(
            Job.completed_at.is_not(None), Job.started_at.is_not(None), Job.created_at >= since
        )
    ).all()
    seconds = sorted((row[1] - row[0]).total_seconds() for row in durations if row[0] and row[1])

    def percentile(values: list[float], fraction: float) -> float:
        if not values:
            return 0.0
        index = min(len(values) - 1, int(len(values) * fraction))
        return round(values[index], 2)

    pages = session.execute(
        select(func.coalesce(func.sum(Job.pages_completed), 0)).where(Job.created_at >= since)
    ).scalar_one()
    characters = session.execute(
        select(func.coalesce(func.sum(ProviderUsage.units), 0)).where(
            ProviderUsage.kind == "translation", ProviderUsage.created_at >= since
        )
    ).scalar_one()
    provider_cost = session.execute(
        select(func.coalesce(func.sum(ProviderUsage.cost_micro_usd), 0)).where(
            ProviderUsage.created_at >= since
        )
    ).scalar_one()
    revenue = session.execute(
        select(func.coalesce(func.sum(Payment.amount_minor), 0)).where(
            Payment.status == "succeeded", Payment.created_at >= since
        )
    ).scalar_one()
    refunds = session.execute(
        select(func.coalesce(func.sum(Payment.refunded_minor), 0)).where(
            Payment.created_at >= since
        )
    ).scalar_one()
    queued = session.execute(
        select(func.count(Job.id)).where(Job.status == str(JobStatus.QUEUED))
    ).scalar_one()

    from lingoimage.services import lifecycle

    return {
        "period_days": days,
        "users": {"new": int(new_users), "active": int(active_users)},
        "jobs": {
            "total": total_jobs,
            "by_status": by_status,
            "success_rate": round(completed / total_jobs, 4) if total_jobs else None,
            "error_rate": round(by_status.get(str(JobStatus.FAILED), 0) / total_jobs, 4)
            if total_jobs
            else None,
            "queue_depth": int(queued),
            "duration_seconds": {
                "p50": percentile(seconds, 0.5),
                "p95": percentile(seconds, 0.95),
                "p99": percentile(seconds, 0.99),
                "mean": round(sum(seconds) / len(seconds), 2) if seconds else 0.0,
            },
        },
        "volume": {"pages": int(pages), "translation_characters": int(characters)},
        "money": {
            "revenue_minor": int(revenue),
            "refunds_minor": int(refunds),
            "provider_cost_usd": round(int(provider_cost) / 1_000_000, 4),
            "currency": settings.billing_currency,
        },
        "storage": lifecycle.storage_usage(session),
    }


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
@router.get("/users")
def list_users(
    session: DbSession,
    identity: CurrentIdentity,
    pagination: Pagination,
    search: str | None = None,
    plan: str | None = None,
) -> dict[str, Any]:
    identity.require_admin(AdminRole.ADMIN, AdminRole.SUPPORT, AdminRole.FINANCE, AdminRole.ANALYST)
    limit, offset = pagination
    statement = select(User)
    if search:
        statement = statement.where(User.email.ilike(f"%{search[:80]}%"))
    if plan:
        statement = statement.where(User.plan_code == plan)
    total = session.execute(select(func.count()).select_from(statement.subquery())).scalar_one()
    rows = session.execute(
        statement.order_by(User.created_at.desc()).limit(limit).offset(offset)
    ).scalars()
    return {
        "items": [
            {
                "id": row.id,
                "email": row.email,
                "name": row.name,
                "plan_code": row.plan_code,
                "status": row.status,
                "admin_role": row.admin_role,
                "verified": row.is_verified,
                "created_at": row.created_at.isoformat(),
                "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
            }
            for row in rows
        ],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@router.get("/users/{user_id}")
def user_detail(user_id: str, session: DbSession, identity: CurrentIdentity) -> dict[str, Any]:
    identity.require_admin(AdminRole.ADMIN, AdminRole.SUPPORT, AdminRole.FINANCE)
    user = session.get(User, user_id)
    if user is None:
        raise AppError(code=ErrorCode.NOT_FOUND)
    wallet = credit_service.get_or_create_wallet(session, user_id=user.id)
    jobs = session.execute(
        select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc()).limit(20)
    ).scalars()
    payments = session.execute(
        select(Payment).where(Payment.user_id == user.id).order_by(Payment.created_at.desc())
    ).scalars()
    project_count = session.execute(
        select(func.count(Project.id)).where(Project.owner_user_id == user.id)
    ).scalar_one()
    session.commit()
    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "plan_code": user.plan_code,
            "status": user.status,
            "created_at": user.created_at.isoformat(),
        },
        "credits": {
            "balance": wallet.balance,
            "granted": wallet.lifetime_granted,
            "spent": wallet.lifetime_spent,
            "ledger_sum": credit_service.recompute_balance(session, wallet.id),
        },
        "projects": int(project_count),
        "jobs": [job_service.summarize(job) for job in jobs],
        "payments": [
            {
                "id": payment.id,
                "amount_minor": payment.amount_minor,
                "currency": payment.currency,
                "status": payment.status,
                "created_at": payment.created_at.isoformat(),
            }
            for payment in payments
        ],
        # Deliberately absent: any document text or file preview.
        "note": "Document content is not available here. Use an audited file access request.",
    }


@router.post("/users/{user_id}/credits")
def adjust_credits(
    user_id: str,
    payload: dict[str, Any],
    request: Request,
    session: DbSession,
    identity: CurrentIdentity,
    _csrf: CsrfProtected,
) -> dict[str, Any]:
    actor = identity.require_admin(AdminRole.ADMIN, AdminRole.SUPPORT, AdminRole.FINANCE)
    reason = _require_reason(payload.get("reason"))
    delta = int(payload.get("delta", 0))
    if delta == 0:
        raise AppError(code=ErrorCode.VALIDATION_FAILED, details={"field": "delta"})

    user = session.get(User, user_id)
    if user is None:
        raise AppError(code=ErrorCode.NOT_FOUND)
    wallet = credit_service.get_or_create_wallet(session, user_id=user.id)
    outcome = credit_service.manual_adjustment(
        session, wallet, delta=delta, actor_user_id=actor.id, reason_text=reason
    )
    _audit(
        session,
        request,
        actor,
        "credits.adjust",
        target_type="user",
        target_id=user.id,
        reason=reason,
        data={"delta": delta},
    )
    session.commit()
    return {"balance": outcome.balance, "delta": delta}


@router.post("/users/{user_id}/status")
def set_user_status(
    user_id: str,
    payload: dict[str, Any],
    request: Request,
    session: DbSession,
    identity: CurrentIdentity,
    _csrf: CsrfProtected,
) -> dict[str, Any]:
    actor = identity.require_admin(AdminRole.ADMIN, AdminRole.SUPPORT)
    reason = _require_reason(payload.get("reason"))
    new_status = str(payload.get("status", ""))
    if new_status not in {"active", "suspended"}:
        raise AppError(code=ErrorCode.VALIDATION_FAILED, details={"field": "status"})

    user = session.get(User, user_id)
    if user is None:
        raise AppError(code=ErrorCode.NOT_FOUND)
    user.status = new_status
    if new_status == "suspended":
        from lingoimage.services import auth as auth_service

        auth_service.revoke_all_sessions(session, user.id)
    _audit(
        session,
        request,
        actor,
        "user.status",
        target_type="user",
        target_id=user.id,
        reason=reason,
        data={"status": new_status},
    )
    session.commit()
    return {"status": user.status}


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #
@router.get("/jobs")
def admin_jobs(
    session: DbSession,
    identity: CurrentIdentity,
    pagination: Pagination,
    job_status: str | None = None,
    job_type: str | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    identity.require_admin(AdminRole.ADMIN, AdminRole.SUPPORT, AdminRole.ANALYST)
    limit, offset = pagination
    statement = select(Job)
    if job_status:
        statement = statement.where(Job.status == job_status)
    if job_type:
        statement = statement.where(Job.type == job_type)
    if error_code:
        statement = statement.where(Job.error_code == error_code)
    total = session.execute(select(func.count()).select_from(statement.subquery())).scalar_one()
    rows = session.execute(
        statement.order_by(Job.created_at.desc()).limit(limit).offset(offset)
    ).scalars()
    return {
        "items": [job_service.summarize(job) for job in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@router.get("/jobs/{job_id}")
def admin_job_detail(job_id: str, session: DbSession, identity: CurrentIdentity) -> dict[str, Any]:
    identity.require_admin(AdminRole.ADMIN, AdminRole.SUPPORT, AdminRole.ANALYST)
    job = job_service.get_job(session, job_id)
    usage = session.execute(select(ProviderUsage).where(ProviderUsage.job_id == job.id)).scalars()
    return {
        "job": job_service.summarize(job),
        "timeline": [
            {
                "stage": event.stage,
                "progress": round(float(event.progress), 3),
                "at": event.created_at.isoformat(),
                "data": event.data,
            }
            for event in job_service.event_history(session, job.id)
        ],
        "provider_calls": [
            {
                "kind": row.kind,
                "provider": row.provider,
                "model": row.model,
                "units": row.units,
                "unit_kind": row.unit_kind,
                "cost_usd": round(row.cost_micro_usd / 1_000_000, 6),
                "latency_ms": row.latency_ms,
                "success": row.success,
            }
            for row in usage
        ],
        "internal_error_reference": job.internal_error_reference,
    }


@router.post("/jobs/{job_id}/retry")
def admin_retry_job(
    job_id: str,
    request: Request,
    session: DbSession,
    identity: CurrentIdentity,
    _csrf: CsrfProtected,
) -> dict[str, Any]:
    actor = identity.require_admin(AdminRole.ADMIN, AdminRole.SUPPORT)
    job = job_service.get_job(session, job_id)
    job_service.retry(session, job, forced=True)
    _audit(session, request, actor, "job.retry", target_type="job", target_id=job.id)
    session.commit()
    return job_service.summarize(job)


@router.post("/jobs/{job_id}/refund")
def admin_refund_job(
    job_id: str,
    payload: dict[str, Any],
    request: Request,
    session: DbSession,
    identity: CurrentIdentity,
    _csrf: CsrfProtected,
) -> dict[str, Any]:
    actor = identity.require_admin(AdminRole.ADMIN, AdminRole.FINANCE, AdminRole.SUPPORT)
    reason = _require_reason(payload.get("reason"))
    job = job_service.get_job(session, job_id)
    outcome = credit_service.refund_job(
        session, job, payload.get("amount"), note=reason, actor_user_id=actor.id
    )
    _audit(
        session,
        request,
        actor,
        "job.refund",
        target_type="job",
        target_id=job.id,
        reason=reason,
        data={"amount": payload.get("amount")},
    )
    session.commit()
    return {"refunded": outcome.entry.delta if outcome else 0}


# --------------------------------------------------------------------------- #
# Providers, flags, content, audit
# --------------------------------------------------------------------------- #
@router.get("/providers")
def admin_providers(session: DbSession, identity: CurrentIdentity) -> dict[str, Any]:
    identity.require_admin()
    from lingoimage.providers import ocr as ocr_providers
    from lingoimage.providers import translation as translation_providers

    rows = session.execute(select(ProviderConfiguration)).scalars()
    since = datetime.now(UTC) - timedelta(days=1)
    usage = session.execute(
        select(
            ProviderUsage.provider,
            func.count(ProviderUsage.id),
            func.sum(ProviderUsage.cost_micro_usd),
            func.avg(ProviderUsage.latency_ms),
            func.sum(func.cast(ProviderUsage.success, __import__("sqlalchemy").Integer)),
        )
        .where(ProviderUsage.created_at >= since)
        .group_by(ProviderUsage.provider)
    ).all()
    stats = {
        str(row[0]): {
            "calls": int(row[1] or 0),
            "cost_usd": round(int(row[2] or 0) / 1_000_000, 4),
            "avg_latency_ms": round(float(row[3] or 0), 1),
            "success_rate": round(int(row[4] or 0) / int(row[1] or 1), 4),
        }
        for row in usage
    }
    return {
        "health": {
            "ocr": ocr_providers.health_report(),
            "translation": translation_providers.health_report(),
        },
        "usage_24h": stats,
        "configuration": [
            {
                "kind": row.kind,
                "name": row.name,
                "enabled": row.enabled,
                "priority": row.priority,
                "health_status": row.health_status,
                "health_detail": row.health_detail,
                "daily_cost_limit_usd": row.daily_cost_limit_usd,
                # Secrets are never returned, only whether one is present.
                "has_secrets": bool(row.secrets_encrypted),
            }
            for row in rows
        ],
    }


@router.get("/feature-flags")
def list_flags(session: DbSession, identity: CurrentIdentity) -> dict[str, Any]:
    identity.require_admin()
    rows = session.execute(select(FeatureFlag)).scalars()
    return {
        "items": [
            {
                "key": row.key,
                "description": row.description,
                "enabled": row.enabled,
                "rollout_percent": row.rollout_percent,
                "plan_codes": row.plan_codes,
                "locales": row.locales,
                "kill_switch": row.kill_switch,
            }
            for row in rows
        ]
    }


@router.put("/feature-flags/{key}")
def update_flag(
    key: str,
    payload: dict[str, Any],
    request: Request,
    session: DbSession,
    identity: CurrentIdentity,
    _csrf: CsrfProtected,
) -> dict[str, Any]:
    actor = identity.require_admin(AdminRole.ADMIN, AdminRole.SUPERADMIN)
    flag = session.get(FeatureFlag, key) or FeatureFlag(key=key)
    for field in (
        "description",
        "enabled",
        "rollout_percent",
        "plan_codes",
        "locales",
        "workspace_ids",
        "kill_switch",
    ):
        if field in payload:
            setattr(flag, field, payload[field])
    session.add(flag)
    _audit(
        session,
        request,
        actor,
        "feature_flag.update",
        target_type="flag",
        target_id=key,
        data={"enabled": flag.enabled, "rollout": flag.rollout_percent},
    )
    session.commit()
    return {"key": flag.key, "enabled": flag.enabled}


@router.put("/content/pages")
def upsert_page(
    payload: dict[str, Any],
    request: Request,
    session: DbSession,
    identity: CurrentIdentity,
    _csrf: CsrfProtected,
) -> dict[str, Any]:
    actor = identity.require_admin(AdminRole.ADMIN, AdminRole.CONTENT)
    path = str(payload.get("path", "")).strip()
    locale = str(payload.get("locale", "")).strip()
    if not (path and locale):
        raise AppError(code=ErrorCode.VALIDATION_FAILED, details={"field": "path/locale"})

    row = session.execute(
        select(SeoPage).where(SeoPage.path == path, SeoPage.locale == locale)
    ).scalar_one_or_none()
    if row is None:
        row = SeoPage(path=path, locale=locale, title="", description="", h1="")
        session.add(row)
    for field in (
        "kind",
        "title",
        "description",
        "h1",
        "intro",
        "body_sections",
        "faq",
        "tool_slug",
        "source_language",
        "target_language",
        "noindex",
        "published",
    ):
        if field in payload:
            setattr(row, field, payload[field])
    _audit(
        session,
        request,
        actor,
        "content.upsert",
        target_type="seo_page",
        target_id=f"{locale}{path}",
    )
    session.commit()
    return {"path": row.path, "locale": row.locale, "published": row.published}


@router.get("/audit")
def audit_log(
    session: DbSession,
    identity: CurrentIdentity,
    pagination: Pagination,
    action: str | None = None,
) -> dict[str, Any]:
    identity.require_admin(AdminRole.ADMIN, AdminRole.SUPERADMIN)
    limit, offset = pagination
    statement = select(AuditLog)
    if action:
        statement = statement.where(AuditLog.action == action)
    rows = session.execute(
        statement.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
    ).scalars()
    return {
        "items": [
            {
                "id": row.id,
                "actor_user_id": row.actor_user_id,
                "actor_role": row.actor_role,
                "action": row.action,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "reason": row.reason,
                "created_at": row.created_at.isoformat(),
                "data": row.data,
            }
            for row in rows
        ]
    }


@router.get("/support/tickets")
def tickets(
    session: DbSession,
    identity: CurrentIdentity,
    pagination: Pagination,
    ticket_status: str = "open",
) -> dict[str, Any]:
    identity.require_admin(AdminRole.ADMIN, AdminRole.SUPPORT)
    limit, offset = pagination
    rows = session.execute(
        select(ContactRequest)
        .where(ContactRequest.status == ticket_status)
        .order_by(ContactRequest.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).scalars()
    return {
        "items": [
            {
                "id": row.id,
                "email": row.email,
                "category": row.category,
                "subject": row.subject,
                "message": row.message,
                "project_id": row.project_id,
                "request_id": row.request_id,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    }


@router.post("/status/incidents")
def create_incident(
    payload: dict[str, Any],
    request: Request,
    session: DbSession,
    identity: CurrentIdentity,
    _csrf: CsrfProtected,
) -> dict[str, Any]:
    actor = identity.require_admin(AdminRole.ADMIN, AdminRole.SUPERADMIN)
    incident = StatusIncident(
        component=str(payload.get("component", "api")),
        severity=str(payload.get("severity", "degraded")),
        title=str(payload.get("title", ""))[:255],
        body=payload.get("body"),
        is_scheduled=bool(payload.get("is_scheduled")),
        manual_override=True,
    )
    session.add(incident)
    _audit(
        session,
        request,
        actor,
        "status.incident_open",
        target_type="incident",
        target_id=incident.component,
        data={"severity": incident.severity},
    )
    session.commit()
    return {"id": incident.id}


@router.post("/status/incidents/{incident_id}/resolve", status_code=204)
def resolve_incident(
    incident_id: str,
    request: Request,
    session: DbSession,
    identity: CurrentIdentity,
    _csrf: CsrfProtected,
) -> Response:
    actor = identity.require_admin(AdminRole.ADMIN, AdminRole.SUPERADMIN)
    incident = session.get(StatusIncident, incident_id)
    if incident is None:
        raise AppError(code=ErrorCode.NOT_FOUND)
    incident.resolved_at = datetime.now(UTC)
    _audit(
        session,
        request,
        actor,
        "status.incident_resolve",
        target_type="incident",
        target_id=incident_id,
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
