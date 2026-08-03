"""Payment provider abstraction with Stripe and YooKassa adapters.

Both adapters implement the same three operations — start a checkout, verify an
inbound webhook, and normalise the event — so billing logic never branches on
the provider.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from picglot.core.config import settings
from picglot.core.errors import AppError, ErrorCode
from picglot.core.logging import get_logger
from picglot.core.metrics import payment_webhook_failures_total

log = get_logger(__name__)


@dataclass(slots=True)
class CheckoutSession:
    url: str
    provider: str
    session_id: str | None = None
    test_mode: bool = True


@dataclass(slots=True)
class NormalizedEvent:
    """A payment event reduced to what billing actually needs."""

    kind: str  # payment_succeeded | payment_failed | subscription_updated | refund | ignored
    event_id: str
    provider: str
    payment_id: str | None = None
    subscription_id: str | None = None
    customer_id: str | None = None
    amount_minor: int = 0
    currency: str = "USD"
    status: str = ""
    plan_code: str | None = None
    pack_code: str | None = None
    user_id: str | None = None
    workspace_id: str | None = None
    period_end: str | None = None
    cancel_at_period_end: bool = False
    refunded_minor: int = 0
    invoice_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class PaymentProvider(Protocol):
    name: str

    def available(self) -> bool: ...
    def create_checkout(self, **kwargs: Any) -> CheckoutSession: ...
    def verify_and_parse(self, body: bytes, headers: dict[str, str]) -> NormalizedEvent: ...


class StripeProvider:
    name = "stripe"
    API = "https://api.stripe.com/v1"

    def available(self) -> bool:
        return bool(settings.stripe_secret_key)

    def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        try:
            response = httpx.post(
                f"{self.API}{path}",
                data=data,
                auth=(settings.stripe_secret_key, ""),
                timeout=25,
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            raise AppError(
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                details={"provider": self.name},
                internal=str(exc)[:250],
            ) from exc

    def create_checkout(
        self,
        *,
        kind: str,
        price_id: str | None,
        amount_minor: int,
        currency: str,
        quantity: int,
        success_url: str,
        cancel_url: str,
        customer_email: str | None,
        metadata: dict[str, str],
        coupon: str | None = None,
        product_name: str = "Credits",
    ) -> CheckoutSession:
        data: dict[str, Any] = {
            "mode": "subscription" if kind == "subscription" else "payment",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": metadata.get("user_id", ""),
            "allow_promotion_codes": "true",
        }
        if customer_email:
            data["customer_email"] = customer_email
        for key, value in metadata.items():
            data[f"metadata[{key}]"] = value

        if price_id:
            data["line_items[0][price]"] = price_id
            data["line_items[0][quantity]"] = quantity
        else:
            data["line_items[0][price_data][currency]"] = currency.lower()
            data["line_items[0][price_data][unit_amount]"] = amount_minor
            data["line_items[0][price_data][product_data][name]"] = product_name
            data["line_items[0][quantity]"] = quantity
        if coupon:
            data["discounts[0][coupon]"] = coupon

        payload = self._post("/checkout/sessions", data)
        return CheckoutSession(
            url=str(payload["url"]),
            provider=self.name,
            session_id=str(payload.get("id")),
            test_mode=not str(settings.stripe_secret_key).startswith("sk_live_"),
        )

    def verify_and_parse(self, body: bytes, headers: dict[str, str]) -> NormalizedEvent:
        signature = headers.get("stripe-signature", "")
        if not settings.stripe_webhook_secret:
            payment_webhook_failures_total.labels(
                provider=self.name, reason="no_secret_configured"
            ).inc()
            raise AppError(
                code=ErrorCode.FORBIDDEN,
                internal="STRIPE_WEBHOOK_SECRET is not configured; refusing unsigned webhook",
            )
        if not _verify_stripe_signature(body, signature, settings.stripe_webhook_secret):
            payment_webhook_failures_total.labels(provider=self.name, reason="bad_signature").inc()
            raise AppError(code=ErrorCode.FORBIDDEN, internal="invalid stripe signature")

        event = json.loads(body)
        return _normalize_stripe(event)


def _verify_stripe_signature(body: bytes, header: str, secret: str, tolerance: int = 300) -> bool:
    import time

    parts = dict(piece.split("=", 1) for piece in header.split(",") if "=" in piece)
    try:
        timestamp = int(parts.get("t", ""))
    except ValueError:
        return False
    if abs(int(time.time()) - timestamp) > tolerance:
        return False
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    # Stripe may send several v1 signatures during a secret rotation.
    provided = [
        value
        for key, value in (piece.split("=", 1) for piece in header.split(",") if "=" in piece)
        if key == "v1"
    ]
    return any(hmac.compare_digest(expected, candidate) for candidate in provided)


def _normalize_stripe(event: dict[str, Any]) -> NormalizedEvent:
    kind = str(event.get("type", ""))
    obj = event.get("data", {}).get("object", {})
    metadata = obj.get("metadata") or {}
    base = NormalizedEvent(
        kind="ignored",
        event_id=str(event.get("id", "")),
        provider="stripe",
        raw=event,
        user_id=metadata.get("user_id"),
        workspace_id=metadata.get("workspace_id"),
        plan_code=metadata.get("plan_code"),
        pack_code=metadata.get("pack_code"),
    )

    if kind == "checkout.session.completed":
        base.kind = "payment_succeeded"
        base.payment_id = str(obj.get("payment_intent") or obj.get("id"))
        base.subscription_id = obj.get("subscription")
        base.customer_id = obj.get("customer")
        base.amount_minor = int(obj.get("amount_total") or 0)
        base.currency = str(obj.get("currency", "usd")).upper()
        base.status = "succeeded"
    elif kind in {"invoice.paid", "invoice.payment_succeeded"}:
        base.kind = "payment_succeeded"
        base.payment_id = str(obj.get("payment_intent") or obj.get("id"))
        base.subscription_id = obj.get("subscription")
        base.customer_id = obj.get("customer")
        base.amount_minor = int(obj.get("amount_paid") or 0)
        base.currency = str(obj.get("currency", "usd")).upper()
        base.invoice_url = obj.get("hosted_invoice_url")
        base.status = "succeeded"
    elif kind in {"invoice.payment_failed", "charge.failed"}:
        base.kind = "payment_failed"
        base.payment_id = str(obj.get("payment_intent") or obj.get("id"))
        base.subscription_id = obj.get("subscription")
        base.status = "failed"
    elif kind.startswith("customer.subscription."):
        base.kind = "subscription_updated"
        base.subscription_id = str(obj.get("id"))
        base.customer_id = obj.get("customer")
        base.status = str(obj.get("status", ""))
        base.cancel_at_period_end = bool(obj.get("cancel_at_period_end"))
        period_end = obj.get("current_period_end")
        if period_end:
            from datetime import UTC, datetime

            base.period_end = datetime.fromtimestamp(int(period_end), UTC).isoformat()
        items = (obj.get("items") or {}).get("data") or [{}]
        price_id = (items[0].get("price") or {}).get("id")
        base.plan_code = metadata.get("plan_code") or _plan_for_price(price_id)
    elif kind in {"charge.refunded", "charge.refund.updated"}:
        base.kind = "refund"
        base.payment_id = str(obj.get("payment_intent") or obj.get("id"))
        base.refunded_minor = int(obj.get("amount_refunded") or 0)
        base.status = "refunded"

    return base


def _plan_for_price(price_id: str | None) -> str | None:
    if not price_id:
        return None
    return {
        settings.stripe_price_pro_monthly: "pro",
        settings.stripe_price_business_monthly: "business",
    }.get(price_id)


class YooKassaProvider:
    name = "yookassa"
    API = "https://api.yookassa.ru/v3"

    def available(self) -> bool:
        return bool(settings.yookassa_shop_id and settings.yookassa_secret_key)

    def _auth(self) -> str:
        raw = f"{settings.yookassa_shop_id}:{settings.yookassa_secret_key}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def create_checkout(
        self,
        *,
        kind: str,
        amount_minor: int,
        currency: str,
        success_url: str,
        metadata: dict[str, str],
        description: str = "",
        **_kwargs: Any,
    ) -> CheckoutSession:
        body = {
            "amount": {
                "value": f"{amount_minor / 100:.2f}",
                "currency": currency if currency in {"RUB", "USD", "EUR"} else "RUB",
            },
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": success_url},
            "description": description[:128] or "PicGlot",
            "metadata": metadata,
            "save_payment_method": kind == "subscription",
        }
        try:
            response = httpx.post(
                f"{self.API}/payments",
                json=body,
                headers={
                    "Authorization": self._auth(),
                    "Idempotence-Key": str(uuid.uuid4()),
                    "Content-Type": "application/json",
                },
                timeout=25,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise AppError(
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                details={"provider": self.name},
                internal=str(exc)[:250],
            ) from exc

        return CheckoutSession(
            url=str(payload["confirmation"]["confirmation_url"]),
            provider=self.name,
            session_id=str(payload.get("id")),
            test_mode=bool(payload.get("test", settings.billing_test_mode)),
        )

    def verify_and_parse(self, body: bytes, headers: dict[str, str]) -> NormalizedEvent:
        """YooKassa authenticates by source IP; we additionally require a shared secret."""
        secret = settings.yookassa_webhook_secret
        if secret:
            provided = headers.get("x-picglot-webhook-secret", "")
            if not hmac.compare_digest(secret, provided):
                payment_webhook_failures_total.labels(provider=self.name, reason="bad_secret").inc()
                raise AppError(code=ErrorCode.FORBIDDEN, internal="yookassa secret mismatch")
        elif settings.is_production:
            payment_webhook_failures_total.labels(
                provider=self.name, reason="no_secret_configured"
            ).inc()
            raise AppError(
                code=ErrorCode.FORBIDDEN,
                internal="YOOKASSA_WEBHOOK_SECRET must be set in production",
            )

        event = json.loads(body)
        obj = event.get("object", {})
        metadata = obj.get("metadata") or {}
        event_type = str(event.get("event", ""))
        amount = obj.get("amount", {})
        normalized = NormalizedEvent(
            kind="ignored",
            event_id=str(obj.get("id", "")) + ":" + event_type,
            provider=self.name,
            payment_id=str(obj.get("id", "")),
            amount_minor=round(float(amount.get("value", 0)) * 100),
            currency=str(amount.get("currency", "RUB")),
            user_id=metadata.get("user_id"),
            workspace_id=metadata.get("workspace_id"),
            plan_code=metadata.get("plan_code"),
            pack_code=metadata.get("pack_code"),
            raw=event,
        )
        if event_type == "payment.succeeded":
            normalized.kind = "payment_succeeded"
            normalized.status = "succeeded"
        elif event_type == "payment.canceled":
            normalized.kind = "payment_failed"
            normalized.status = "failed"
        elif event_type == "refund.succeeded":
            normalized.kind = "refund"
            normalized.status = "refunded"
            normalized.refunded_minor = normalized.amount_minor
            normalized.payment_id = str(obj.get("payment_id") or normalized.payment_id)
        return normalized


_PROVIDERS: dict[str, Any] = {"stripe": StripeProvider(), "yookassa": YooKassaProvider()}


def get_provider(name: str | None = None) -> Any:
    provider = _PROVIDERS.get(name or settings.billing_provider)
    if provider is None:
        raise AppError(
            code=ErrorCode.FEATURE_DISABLED,
            details={"provider": name or settings.billing_provider},
            internal="unknown or manual billing provider",
        )
    if not provider.available():
        raise AppError(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            details={"provider": provider.name},
            internal=f"{provider.name} credentials are not configured",
        )
    return provider


def health() -> dict[str, Any]:
    return {
        "provider": settings.billing_provider,
        "enabled": settings.billing_enabled,
        "test_mode": settings.billing_test_mode,
        "configured": {name: provider.available() for name, provider in _PROVIDERS.items()},
    }
