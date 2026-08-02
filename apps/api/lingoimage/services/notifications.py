"""User-facing notifications: email plus outbound webhooks, honouring preferences."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from lingoimage.core.config import settings
from lingoimage.core.logging import get_logger
from lingoimage.db.models import Job, NotificationLog, User
from lingoimage.domain.enums import WebhookEvent
from lingoimage.services import webhooks

log = get_logger(__name__)


def _wants(user: User | None, key: str, default: bool = True) -> bool:
    if user is None:
        return False
    preferences = user.notification_preferences or {}
    return bool(preferences.get(key, default))


def send_email(
    session: Session,
    user: User | None,
    template: str,
    *,
    to: str | None = None,
    context: dict[str, Any] | None = None,
    preference_key: str | None = None,
) -> bool:
    address = to or (user.email if user else None)
    if not address:
        return False
    if preference_key and not _wants(user, preference_key):
        return False

    payload = {"locale": (user.locale if user else settings.default_locale), **(context or {})}

    if settings.queue_backend == "inline":
        from lingoimage.providers import email as email_provider

        result = email_provider.send(template=template, to=address, context=payload)
    else:
        from lingoimage.workers.celery_app import celery_app

        celery_app.send_task(
            "lingoimage.send_email",
            args=[template, address, payload],
            queue="notifications",
        )
        result = {"delivered": True, "provider": "queued"}

    session.add(
        NotificationLog(
            user_id=user.id if user else None,
            channel="email",
            template=template,
            status="sent" if result.get("delivered") else "failed",
            error=str(result.get("error")) if result.get("error") else None,
        )
    )
    return bool(result.get("delivered"))


def on_job_finished(session: Session, job: Job, event: WebhookEvent) -> None:
    user = session.get(User, job.user_id) if job.user_id else None
    locale = user.locale if user else settings.default_locale
    project_url = f"{settings.public_web_url}/{locale}/app/projects/{job.project_id}"

    webhooks.enqueue(
        session,
        user_id=job.user_id,
        workspace_id=job.workspace_id,
        event=event,
        payload={
            "job_id": job.id,
            "project_id": job.project_id,
            "type": job.type,
            "status": job.status,
            "pages_total": job.pages_total,
            "pages_completed": job.pages_completed,
            "pages_failed": job.pages_failed,
            "error_code": job.error_code,
            "exports": (job.output or {}).get("exports", []),
        },
    )

    if user is None:
        return
    if event is WebhookEvent.JOB_FAILED:
        send_email(
            session,
            user,
            "job_failed",
            context={
                "action_url": project_url,
                "lines": [f"Reference: {job.internal_error_reference}"]
                if job.internal_error_reference
                else [],
            },
            preference_key="job_failed",
        )
    elif job.type == "batch":
        send_email(
            session,
            user,
            "batch_completed",
            context={"action_url": project_url},
            preference_key="batch_completed",
        )
    else:
        send_email(
            session,
            user,
            "job_completed",
            context={"action_url": project_url},
            preference_key="job_completed",
        )

    _maybe_warn_low_credits(session, user)


def _maybe_warn_low_credits(session: Session, user: User) -> None:
    from lingoimage.services import credits as credit_service

    wallet = credit_service.get_or_create_wallet(session, user_id=user.id)
    if 0 < int(wallet.balance) <= 5:
        send_email(
            session,
            user,
            "credits_low",
            context={
                "action_url": f"{settings.public_web_url}/{user.locale}/pricing",
                "lines": [f"Remaining credits: {wallet.balance}"],
            },
            preference_key="credits_low",
        )


def on_security_event(session: Session, user: User, *, detail: str) -> None:
    send_email(
        session,
        user,
        "security_alert",
        context={
            "action_url": f"{settings.public_web_url}/{user.locale}/app/security",
            "lines": [detail],
        },
        preference_key="security_alert",
    )


def on_payment(session: Session, user: User, *, succeeded: bool, invoice_url: str | None) -> None:
    send_email(
        session,
        user,
        "payment_succeeded" if succeeded else "payment_failed",
        context={
            "action_url": invoice_url or f"{settings.public_web_url}/{user.locale}/app/billing",
        },
    )


def on_invitation(
    session: Session, email: str, *, workspace_name: str, url: str, locale: str = "en"
) -> None:
    send_email(
        session,
        None,
        "workspace_invitation",
        to=email,
        context={"action_url": url, "locale": locale, "lines": [f"Workspace: {workspace_name}"]},
    )
