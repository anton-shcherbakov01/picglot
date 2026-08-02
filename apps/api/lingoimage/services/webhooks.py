"""Outbound webhooks: signed, retried, replayable, and SSRF-safe."""

from __future__ import annotations

import ipaddress
import socket
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lingoimage.core.config import settings
from lingoimage.core.errors import AppError, ErrorCode
from lingoimage.core.ids import ulid
from lingoimage.core.logging import get_logger
from lingoimage.core.metrics import webhook_delivery_failures_total
from lingoimage.core.security import decrypt_secret, sign_webhook
from lingoimage.db.models import WebhookDelivery, WebhookEndpoint
from lingoimage.domain.enums import DeliveryStatus, WebhookEvent

log = get_logger(__name__)

MAX_ATTEMPTS = 6
TIMEOUT_SECONDS = 15
#: 1m, 5m, 15m, 1h, 6h — then abandon.
BACKOFF_SECONDS = (60, 300, 900, 3600, 21600)


def validate_url(url: str) -> str:
    """Reject anything that could reach our own infrastructure (SSRF)."""
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"}:
        raise AppError(
            code=ErrorCode.VALIDATION_FAILED,
            details={"field": "url", "reason": "scheme_must_be_http_or_https"},
        )
    if settings.is_production and parsed.scheme != "https":
        raise AppError(
            code=ErrorCode.VALIDATION_FAILED,
            details={"field": "url", "reason": "https_required"},
        )
    host = parsed.hostname
    if not host:
        raise AppError(code=ErrorCode.VALIDATION_FAILED, details={"field": "url"})

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise AppError(
            code=ErrorCode.VALIDATION_FAILED,
            details={"field": "url", "reason": "host_not_resolvable"},
            internal=str(exc)[:120],
        ) from exc

    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
        ):
            raise AppError(
                code=ErrorCode.VALIDATION_FAILED,
                details={"field": "url", "reason": "internal_address_not_allowed"},
                internal=f"resolved to {address}",
            )
    return url


def enqueue(
    session: Session,
    *,
    user_id: str | None,
    workspace_id: str | None,
    event: WebhookEvent | str,
    payload: dict[str, Any],
    event_id: str | None = None,
) -> list[WebhookDelivery]:
    """Queue one delivery per subscribed endpoint. Idempotent per (endpoint, event_id)."""
    if not settings.feature_webhooks:
        return []

    statement = select(WebhookEndpoint).where(
        WebhookEndpoint.is_active.is_(True), WebhookEndpoint.disabled_at.is_(None)
    )
    statement = (
        statement.where(WebhookEndpoint.workspace_id == workspace_id)
        if workspace_id
        else statement.where(WebhookEndpoint.user_id == user_id)
    )

    event_id = event_id or f"evt_{ulid()}"
    deliveries: list[WebhookDelivery] = []
    for endpoint in session.execute(statement).scalars():
        if endpoint.events and str(event) not in endpoint.events:
            continue
        delivery = WebhookDelivery(
            endpoint_id=endpoint.id,
            event=str(event),
            event_id=event_id,
            payload={**payload, "event": str(event), "event_id": event_id},
            status=DeliveryStatus.PENDING,
            next_attempt_at=datetime.now(UTC),
        )
        session.add(delivery)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            continue
        deliveries.append(delivery)

    for delivery in deliveries:
        _schedule(delivery.id)
    return deliveries


def _schedule(delivery_id: str) -> None:
    if settings.queue_backend == "inline":
        from lingoimage.db.session import session_scope

        with session_scope() as session:
            deliver(session, delivery_id)
        return
    from lingoimage.workers.celery_app import celery_app

    celery_app.send_task("lingoimage.deliver_webhook", args=[delivery_id], queue="notifications")


def deliver(session: Session, delivery_id: str) -> dict[str, Any]:
    delivery = session.get(WebhookDelivery, delivery_id)
    if delivery is None:
        return {"skipped": "missing"}
    if delivery.status == DeliveryStatus.DELIVERED:
        return {"skipped": "already_delivered"}

    endpoint = session.get(WebhookEndpoint, delivery.endpoint_id)
    if endpoint is None or not endpoint.is_active:
        delivery.status = DeliveryStatus.ABANDONED
        return {"skipped": "endpoint_inactive"}

    import json

    body = json.dumps(delivery.payload, ensure_ascii=False, separators=(",", ":")).encode()
    timestamp = int(time.time())
    signature = sign_webhook(decrypt_secret(endpoint.secret_encrypted), timestamp, body)

    delivery.attempt = int(delivery.attempt) + 1
    started = time.perf_counter()
    try:
        response = httpx.post(
            endpoint.url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": f"{settings.brand_name}-Webhooks/1.0",
                "X-LingoImage-Signature": signature,
                "X-LingoImage-Event": delivery.event,
                "X-LingoImage-Event-Id": delivery.event_id,
                "X-LingoImage-Delivery": delivery.id,
                "X-LingoImage-Attempt": str(delivery.attempt),
            },
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
        )
        delivery.response_status = response.status_code
        delivery.response_snippet = response.text[:500]
        ok = 200 <= response.status_code < 300
    except Exception as exc:
        delivery.response_status = None
        delivery.response_snippet = f"{type(exc).__name__}: {exc}"[:500]
        ok = False

    delivery.duration_ms = int((time.perf_counter() - started) * 1000)

    if ok:
        delivery.status = DeliveryStatus.DELIVERED
        delivery.delivered_at = datetime.now(UTC)
        delivery.next_attempt_at = None
        endpoint.consecutive_failures = 0
        log.info("webhook.delivered", endpoint=endpoint.id, event=delivery.event)
        return {"delivered": True, "status": delivery.response_status}

    webhook_delivery_failures_total.labels(event=delivery.event).inc()
    endpoint.consecutive_failures = int(endpoint.consecutive_failures) + 1

    if delivery.attempt >= MAX_ATTEMPTS:
        delivery.status = DeliveryStatus.ABANDONED
        delivery.next_attempt_at = None
        log.warning("webhook.abandoned", endpoint=endpoint.id, event=delivery.event)
    else:
        delivery.status = DeliveryStatus.FAILED
        delay = BACKOFF_SECONDS[min(delivery.attempt - 1, len(BACKOFF_SECONDS) - 1)]
        delivery.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)

    # A dead endpoint is disabled rather than retried forever.
    if endpoint.consecutive_failures >= 25:
        endpoint.disabled_at = datetime.now(UTC)
        endpoint.is_active = False
        log.warning("webhook.endpoint_disabled", endpoint=endpoint.id)

    return {"delivered": False, "attempt": delivery.attempt}


def retry_due(session: Session, limit: int = 100) -> dict[str, Any]:
    due = list(
        session.execute(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.status == DeliveryStatus.FAILED,
                WebhookDelivery.next_attempt_at.is_not(None),
                WebhookDelivery.next_attempt_at <= datetime.now(UTC),
            )
            .limit(limit)
        ).scalars()
    )
    for delivery in due:
        deliver(session, delivery.id)
    return {"retried": len(due)}


def replay(session: Session, delivery_id: str) -> WebhookDelivery:
    """Admin/user action: send the same payload again as a *new* delivery."""
    original = session.get(WebhookDelivery, delivery_id)
    if original is None:
        raise AppError(code=ErrorCode.NOT_FOUND)
    replayed = WebhookDelivery(
        endpoint_id=original.endpoint_id,
        event=original.event,
        event_id=f"{original.event_id}:replay:{ulid()}",
        payload=original.payload,
        status=DeliveryStatus.PENDING,
        next_attempt_at=datetime.now(UTC),
    )
    session.add(replayed)
    session.flush()
    _schedule(replayed.id)
    return replayed


def create_endpoint(
    session: Session,
    *,
    url: str,
    events: list[str],
    user_id: str | None = None,
    workspace_id: str | None = None,
    description: str | None = None,
) -> tuple[WebhookEndpoint, str]:
    from lingoimage.core.ids import token
    from lingoimage.core.security import encrypt_secret

    validate_url(url)
    known = {str(item) for item in WebhookEvent}
    unknown = [event for event in events if event not in known]
    if unknown:
        raise AppError(
            code=ErrorCode.VALIDATION_FAILED,
            details={"field": "events", "unknown": unknown, "supported": sorted(known)},
        )

    secret = f"whsec_{token(24)}"
    endpoint = WebhookEndpoint(
        user_id=None if workspace_id else user_id,
        workspace_id=workspace_id,
        url=url,
        secret_encrypted=encrypt_secret(secret),
        events=events or sorted(known),
        description=description,
    )
    session.add(endpoint)
    session.flush()
    return endpoint, secret


def rotate_secret(session: Session, endpoint: WebhookEndpoint) -> str:
    from lingoimage.core.ids import token
    from lingoimage.core.security import encrypt_secret

    secret = f"whsec_{token(24)}"
    endpoint.secret_encrypted = encrypt_secret(secret)
    return secret


def deliveries_for(session: Session, endpoint_id: str, *, limit: int = 50) -> list[WebhookDelivery]:
    return list(
        session.execute(
            select(WebhookDelivery)
            .where(WebhookDelivery.endpoint_id == endpoint_id)
            .order_by(WebhookDelivery.created_at.desc())
            .limit(limit)
        ).scalars()
    )
