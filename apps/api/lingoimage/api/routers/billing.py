"""Checkout, subscriptions, invoices and inbound payment webhooks."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select

from lingoimage.api.deps import CsrfProtected, CurrentUser, DbSession, Pagination
from lingoimage.core.config import settings
from lingoimage.core.errors import AppError, ErrorCode
from lingoimage.core.logging import get_logger
from lingoimage.db.models import Payment, Subscription, User
from lingoimage.domain import plans as plan_catalog
from lingoimage.domain.enums import PaymentStatus, SubscriptionStatus
from lingoimage.providers import payments as payment_providers
from lingoimage.schemas import CheckoutOut, CheckoutRequest, PaymentOut, SubscriptionOut
from lingoimage.services import credits as credit_service
from lingoimage.services import notifications

log = get_logger(__name__)
router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


@router.get("/plans")
def list_plans() -> dict[str, Any]:
    return {
        "plans": [spec.as_dict() for spec in plan_catalog.plan_specs() if spec.is_public],
        "credit_packs": [
            {
                "code": pack.code,
                "credits": pack.credits,
                "price_usd_cents": pack.price_usd_cents,
                "price_rub_kopecks": pack.price_rub_kopecks,
            }
            for pack in plan_catalog.CREDIT_PACKS
        ],
        "currency": settings.billing_currency,
        "provider": settings.billing_provider,
        "test_mode": settings.billing_test_mode,
    }


@router.post("/checkout", response_model=CheckoutOut)
def checkout(
    payload: CheckoutRequest, session: DbSession, user: CurrentUser, _csrf: CsrfProtected
) -> CheckoutOut:
    if not settings.billing_enabled:
        raise AppError(code=ErrorCode.FEATURE_DISABLED, details={"feature": "billing"})
    provider = payment_providers.get_provider()

    base = settings.public_web_url
    success_url = payload.success_url or f"{base}/{user.locale}/app/billing?checkout=success"
    cancel_url = payload.cancel_url or f"{base}/{user.locale}/pricing?checkout=cancelled"
    metadata = {"user_id": user.id, "kind": payload.kind}

    if payload.kind == "subscription":
        spec = plan_catalog.by_code(payload.plan_code or "")
        if spec is None or spec.code in {"guest", "free"}:
            raise AppError(code=ErrorCode.VALIDATION_FAILED, details={"field": "plan_code"})
        metadata["plan_code"] = spec.code
        price_id = {
            "pro": settings.stripe_price_pro_monthly,
            "business": settings.stripe_price_business_monthly,
        }.get(spec.code) or None
        amount = (
            spec.price_rub_kopecks if settings.billing_currency == "RUB" else spec.price_usd_cents
        )
        checkout_session = provider.create_checkout(
            kind="subscription",
            price_id=price_id,
            amount_minor=amount,
            currency=settings.billing_currency,
            quantity=1,
            success_url=success_url,
            cancel_url=cancel_url,
            customer_email=user.email,
            metadata=metadata,
            coupon=payload.coupon,
            product_name=f"{settings.brand_name} {spec.name}",
            description=f"{settings.brand_name} {spec.name}",
        )
    else:
        pack = next(
            (item for item in plan_catalog.CREDIT_PACKS if item.code == payload.pack_code), None
        )
        if pack is None:
            raise AppError(code=ErrorCode.VALIDATION_FAILED, details={"field": "pack_code"})
        metadata["pack_code"] = pack.code
        amount = (
            pack.price_rub_kopecks if settings.billing_currency == "RUB" else pack.price_usd_cents
        )
        checkout_session = provider.create_checkout(
            kind="credit_pack",
            price_id=None,
            amount_minor=amount,
            currency=settings.billing_currency,
            quantity=1,
            success_url=success_url,
            cancel_url=cancel_url,
            customer_email=user.email,
            metadata=metadata,
            coupon=payload.coupon,
            product_name=f"{pack.credits} credits",
            description=f"{pack.credits} credits",
        )

    session.commit()
    return CheckoutOut(
        checkout_url=checkout_session.url,
        provider=checkout_session.provider,
        session_id=checkout_session.session_id,
        test_mode=checkout_session.test_mode,
    )


@router.get("/subscription", response_model=SubscriptionOut | None)
def current_subscription(session: DbSession, user: CurrentUser) -> SubscriptionOut | None:
    row = (
        session.execute(
            select(Subscription)
            .where(Subscription.user_id == user.id)
            .order_by(Subscription.created_at.desc())
        )
        .scalars()
        .first()
    )
    return SubscriptionOut.model_validate(row) if row else None


@router.post("/subscription/cancel", response_model=SubscriptionOut)
def cancel_subscription(
    session: DbSession, user: CurrentUser, _csrf: CsrfProtected
) -> SubscriptionOut:
    row = (
        session.execute(
            select(Subscription).where(
                Subscription.user_id == user.id,
                Subscription.status.in_(
                    [str(SubscriptionStatus.ACTIVE), str(SubscriptionStatus.TRIALING)]
                ),
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        raise AppError(code=ErrorCode.NOT_FOUND, internal="no active subscription")

    if row.provider == "stripe" and row.provider_subscription_id and settings.stripe_secret_key:
        import httpx

        try:
            httpx.post(
                f"https://api.stripe.com/v1/subscriptions/{row.provider_subscription_id}",
                data={"cancel_at_period_end": "true"},
                auth=(settings.stripe_secret_key, ""),
                timeout=20,
            ).raise_for_status()
        except Exception as exc:
            raise AppError(
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                details={"provider": "stripe"},
                internal=str(exc)[:200],
            ) from exc

    row.cancel_at_period_end = True
    row.canceled_at = datetime.now(UTC)
    session.commit()
    return SubscriptionOut.model_validate(row)


@router.get("/payments", response_model=list[PaymentOut])
def list_payments(
    session: DbSession, user: CurrentUser, pagination: Pagination
) -> list[PaymentOut]:
    limit, offset = pagination
    rows = session.execute(
        select(Payment)
        .where(Payment.user_id == user.id)
        .order_by(Payment.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).scalars()
    return [PaymentOut.model_validate(row) for row in rows]


# --------------------------------------------------------------------------- #
# Inbound webhooks
# --------------------------------------------------------------------------- #
@router.post("/webhooks/{provider_name}", include_in_schema=False)
async def payment_webhook(
    provider_name: str, request: Request, session: DbSession
) -> dict[str, Any]:
    """Verify, deduplicate, then apply. Replays are no-ops by construction."""
    body = await request.body()
    headers = {key.lower(): value for key, value in request.headers.items()}
    provider = payment_providers.get_provider(provider_name)
    event = provider.verify_and_parse(body, headers)

    if event.kind == "ignored":
        return {"received": True, "handled": False}

    result = _apply_event(session, event)
    session.commit()
    return {"received": True, **result}


def _apply_event(session: Any, event: payment_providers.NormalizedEvent) -> dict[str, Any]:
    user = session.get(User, event.user_id) if event.user_id else None
    if user is None and event.customer_id:
        subscription = (
            session.execute(
                select(Subscription).where(Subscription.provider_customer_id == event.customer_id)
            )
            .scalars()
            .first()
        )
        if subscription and subscription.user_id:
            user = session.get(User, subscription.user_id)

    if event.kind == "payment_succeeded":
        return _apply_payment(session, event, user)
    if event.kind == "payment_failed":
        if user:
            notifications.on_payment(session, user, succeeded=False, invoice_url=event.invoice_url)
        return {"handled": "payment_failed"}
    if event.kind == "subscription_updated":
        return _apply_subscription(session, event, user)
    if event.kind == "refund":
        return _apply_refund(session, event, user)
    return {"handled": False}


def _apply_payment(
    session: Any, event: payment_providers.NormalizedEvent, user: User | None
) -> dict[str, Any]:
    existing = session.execute(
        select(Payment).where(
            Payment.provider == event.provider,
            Payment.provider_payment_id == (event.payment_id or event.event_id),
        )
    ).scalar_one_or_none()
    if existing is not None and existing.status == str(PaymentStatus.SUCCEEDED):
        # Replayed webhook — the ledger entry already exists, so do nothing.
        return {"handled": "duplicate"}

    credits_granted = 0
    if event.pack_code:
        pack = next(
            (item for item in plan_catalog.CREDIT_PACKS if item.code == event.pack_code), None
        )
        credits_granted = pack.credits if pack else 0
    elif event.plan_code:
        spec = plan_catalog.by_code(event.plan_code)
        credits_granted = spec.monthly_credits if spec else 0

    payment = existing or Payment(
        user_id=user.id if user else None,
        workspace_id=event.workspace_id,
        provider=event.provider,
        provider_payment_id=event.payment_id or event.event_id,
        amount_minor=event.amount_minor,
        currency=event.currency,
        kind="subscription" if event.plan_code else "credit_pack",
        invoice_url=event.invoice_url,
    )
    payment.status = str(PaymentStatus.SUCCEEDED)
    payment.credits_granted = credits_granted
    if existing is None:
        session.add(payment)
    session.flush()

    if credits_granted and (user or event.workspace_id):
        wallet = credit_service.get_or_create_wallet(
            session,
            user_id=None if event.workspace_id else (user.id if user else None),
            workspace_id=event.workspace_id,
        )
        credit_service.grant_purchase(
            session,
            wallet,
            amount=credits_granted,
            payment_id=payment.id,
            note=event.plan_code or event.pack_code,
        )

    if user and event.plan_code:
        user.plan_code = event.plan_code
    if user:
        notifications.on_payment(session, user, succeeded=True, invoice_url=event.invoice_url)

    log.info(
        "billing.payment_succeeded",
        provider=event.provider,
        credits=credits_granted,
        plan=event.plan_code,
    )
    return {"handled": "payment_succeeded", "credits_granted": credits_granted}


def _apply_subscription(
    session: Any, event: payment_providers.NormalizedEvent, user: User | None
) -> dict[str, Any]:
    row = session.execute(
        select(Subscription).where(
            Subscription.provider == event.provider,
            Subscription.provider_subscription_id == event.subscription_id,
        )
    ).scalar_one_or_none()
    if row is None:
        row = Subscription(
            user_id=user.id if user else None,
            workspace_id=event.workspace_id,
            plan_code=event.plan_code or "pro",
            provider=event.provider,
            provider_subscription_id=event.subscription_id,
            provider_customer_id=event.customer_id,
        )
        session.add(row)

    mapping = {
        "active": SubscriptionStatus.ACTIVE,
        "trialing": SubscriptionStatus.TRIALING,
        "past_due": SubscriptionStatus.PAST_DUE,
        "canceled": SubscriptionStatus.CANCELED,
        "unpaid": SubscriptionStatus.PAST_DUE,
        "incomplete": SubscriptionStatus.INCOMPLETE,
        "paused": SubscriptionStatus.PAUSED,
    }
    row.status = str(mapping.get(event.status, SubscriptionStatus.INCOMPLETE))
    row.cancel_at_period_end = event.cancel_at_period_end
    if event.plan_code:
        row.plan_code = event.plan_code
    if event.period_end:
        row.current_period_end = datetime.fromisoformat(event.period_end)
    session.flush()

    if user:
        if row.status in {str(SubscriptionStatus.ACTIVE), str(SubscriptionStatus.TRIALING)}:
            user.plan_code = row.plan_code
        elif row.status == str(SubscriptionStatus.CANCELED):
            user.plan_code = "free"
            notifications.send_email(
                session,
                user,
                "subscription_cancelled",
                context={"action_url": f"{settings.public_web_url}/{user.locale}/pricing"},
            )
    return {"handled": "subscription_updated", "status": row.status}


def _apply_refund(
    session: Any, event: payment_providers.NormalizedEvent, user: User | None
) -> dict[str, Any]:
    payment = session.execute(
        select(Payment).where(
            Payment.provider == event.provider,
            Payment.provider_payment_id == event.payment_id,
        )
    ).scalar_one_or_none()
    if payment is None:
        return {"handled": "refund_unknown_payment"}

    payment.refunded_minor = event.refunded_minor or payment.amount_minor
    payment.status = str(
        PaymentStatus.REFUNDED
        if payment.refunded_minor >= payment.amount_minor
        else PaymentStatus.PARTIALLY_REFUNDED
    )

    if payment.credits_granted and (payment.user_id or payment.workspace_id):
        share = payment.refunded_minor / payment.amount_minor if payment.amount_minor else 1.0
        wallet = credit_service.get_or_create_wallet(
            session,
            user_id=None if payment.workspace_id else payment.user_id,
            workspace_id=payment.workspace_id,
        )
        credit_service.revoke_for_refund(
            session,
            wallet,
            amount=round(payment.credits_granted * share),
            payment_id=payment.id,
        )
    log.info("billing.refund_applied", payment_id=payment.id, status=payment.status)
    return {"handled": "refund", "status": payment.status}
