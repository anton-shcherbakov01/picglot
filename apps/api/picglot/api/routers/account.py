"""Account area: credits, API keys, webhooks, glossaries, privacy and data."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import select

from picglot.api.deps import (
    CsrfProtected,
    CurrentIdentity,
    CurrentUser,
    DbSession,
    Pagination,
)
from picglot.core.config import settings
from picglot.core.errors import AppError, ErrorCode
from picglot.core.security import generate_api_key
from picglot.db.models import (
    ApiKey,
    Glossary,
    GlossaryTerm,
    TranslationMemoryEntry,
    WebhookEndpoint,
)
from picglot.schemas import (
    ApiKeyCreatedOut,
    ApiKeyCreateRequest,
    ApiKeyOut,
    GlossaryCreateRequest,
    GlossaryOut,
    GlossaryTermIn,
    LedgerEntryOut,
    TranslationMemoryOut,
    UserOut,
    WalletOut,
    WebhookCreatedOut,
    WebhookCreateRequest,
    WebhookDeliveryOut,
    WebhookOut,
)
from picglot.services import auth as auth_service
from picglot.services import credits as credit_service
from picglot.services import webhooks as webhook_service

router = APIRouter(prefix="/api/v1/account", tags=["account"])


# --------------------------------------------------------------------------- #
# Profile and credits
# --------------------------------------------------------------------------- #
@router.patch("/profile", response_model=UserOut)
def update_profile(
    payload: dict[str, Any], session: DbSession, user: CurrentUser, _csrf: CsrfProtected
) -> UserOut:
    if "name" in payload:
        user.name = (str(payload["name"]) or "").strip()[:120] or None
    if "locale" in payload and payload["locale"] in settings.enabled_locales:
        user.locale = str(payload["locale"])
    if "timezone" in payload:
        user.timezone = str(payload["timezone"])[:64]
    if "notification_preferences" in payload and isinstance(
        payload["notification_preferences"], dict
    ):
        user.notification_preferences = {
            key: bool(value) for key, value in payload["notification_preferences"].items()
        }
    if "retention_override_hours" in payload:
        from picglot.domain import plans

        if not plans.has_feature(user.plan_code, "custom_retention"):
            raise AppError(code=ErrorCode.PLAN_REQUIRED, details={"feature": "custom_retention"})
        value = payload["retention_override_hours"]
        user.retention_override_hours = int(value) if value is not None else None
    session.commit()
    return UserOut.model_validate(user)


@router.get("/wallet", response_model=WalletOut)
def wallet(session: DbSession, user: CurrentUser, identity: CurrentIdentity) -> WalletOut:
    record = credit_service.get_or_create_wallet(
        session,
        user_id=None if identity.workspace_id else user.id,
        workspace_id=identity.workspace_id,
    )
    session.commit()
    return WalletOut(
        balance=record.balance,
        lifetime_granted=record.lifetime_granted,
        lifetime_spent=record.lifetime_spent,
        plan_code=user.plan_code,
    )


@router.get("/wallet/ledger", response_model=list[LedgerEntryOut])
def ledger(
    session: DbSession, user: CurrentUser, identity: CurrentIdentity, pagination: Pagination
) -> list[LedgerEntryOut]:
    limit, offset = pagination
    record = credit_service.get_or_create_wallet(
        session,
        user_id=None if identity.workspace_id else user.id,
        workspace_id=identity.workspace_id,
    )
    return [
        LedgerEntryOut.model_validate(entry)
        for entry in credit_service.history(session, record.id, limit=limit, offset=offset)
    ]


@router.get("/usage")
def usage(
    session: DbSession, user: CurrentUser, identity: CurrentIdentity, days: int = 30
) -> dict[str, Any]:
    record = credit_service.get_or_create_wallet(
        session,
        user_id=None if identity.workspace_id else user.id,
        workspace_id=identity.workspace_id,
    )
    summary = credit_service.usage_summary(session, record.id, days=min(365, max(1, days)))
    return {**summary, "balance": record.balance, "plan": user.plan_code}


# --------------------------------------------------------------------------- #
# API keys
# --------------------------------------------------------------------------- #
@router.get("/api-keys", response_model=list[ApiKeyOut])
def list_api_keys(session: DbSession, user: CurrentUser) -> list[ApiKeyOut]:
    rows = session.execute(
        select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())
    ).scalars()
    return [
        ApiKeyOut(
            id=row.id,
            name=row.name,
            prefix=row.prefix,
            last_four=row.last_four,
            scopes=list(row.scopes or []),
            test_mode=row.test_mode,
            created_at=row.created_at,
            last_used_at=row.last_used_at,
            expires_at=row.expires_at,
            revoked=row.revoked_at is not None,
        )
        for row in rows
    ]


@router.post("/api-keys", response_model=ApiKeyCreatedOut, status_code=201)
def create_api_key(
    payload: ApiKeyCreateRequest, session: DbSession, user: CurrentUser, _csrf: CsrfProtected
) -> ApiKeyCreatedOut:
    if not settings.feature_public_api:
        raise AppError(code=ErrorCode.FEATURE_DISABLED, details={"feature": "api"})
    from picglot.domain import plans

    if not (
        plans.has_feature(user.plan_code, "api_limited")
        or plans.has_feature(user.plan_code, "api_full")
    ):
        raise AppError(code=ErrorCode.PLAN_REQUIRED, details={"feature": "api"})

    generated = generate_api_key(test_mode=payload.test_mode)
    record = ApiKey(
        user_id=user.id,
        name=payload.name,
        key_hash=generated.hashed,
        prefix=generated.prefix,
        last_four=generated.last_four,
        scopes=payload.scopes or ["jobs:write", "jobs:read", "exports:read"],
        test_mode=payload.test_mode,
        expires_at=(
            datetime.now(UTC) + timedelta(days=payload.expires_in_days)
            if payload.expires_in_days
            else None
        ),
    )
    session.add(record)
    session.commit()
    return ApiKeyCreatedOut(
        id=record.id,
        name=record.name,
        prefix=record.prefix,
        last_four=record.last_four,
        scopes=list(record.scopes),
        test_mode=record.test_mode,
        created_at=record.created_at,
        last_used_at=None,
        expires_at=record.expires_at,
        key=generated.plaintext,
    )


@router.delete("/api-keys/{key_id}", status_code=204)
def revoke_api_key(
    key_id: str, session: DbSession, user: CurrentUser, _csrf: CsrfProtected
) -> Response:
    record = session.get(ApiKey, key_id)
    if record is None or record.user_id != user.id:
        raise AppError(code=ErrorCode.NOT_FOUND)
    record.revoked_at = datetime.now(UTC)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Webhooks
# --------------------------------------------------------------------------- #
@router.get("/webhooks", response_model=list[WebhookOut])
def list_webhooks(session: DbSession, user: CurrentUser) -> list[WebhookOut]:
    rows = session.execute(
        select(WebhookEndpoint).where(WebhookEndpoint.user_id == user.id)
    ).scalars()
    return [WebhookOut.model_validate(row) for row in rows]


@router.post("/webhooks", response_model=WebhookCreatedOut, status_code=201)
def create_webhook(
    payload: WebhookCreateRequest, session: DbSession, user: CurrentUser, _csrf: CsrfProtected
) -> WebhookCreatedOut:
    from picglot.domain import plans

    if not settings.feature_webhooks:
        raise AppError(code=ErrorCode.FEATURE_DISABLED, details={"feature": "webhooks"})
    if not plans.has_feature(user.plan_code, "webhooks"):
        raise AppError(code=ErrorCode.PLAN_REQUIRED, details={"feature": "webhooks"})

    endpoint, secret = webhook_service.create_endpoint(
        session,
        url=payload.url,
        events=payload.events,
        user_id=user.id,
        description=payload.description,
    )
    session.commit()
    return WebhookCreatedOut(**WebhookOut.model_validate(endpoint).model_dump(), secret=secret)


@router.post("/webhooks/{endpoint_id}/rotate")
def rotate_webhook_secret(
    endpoint_id: str, session: DbSession, user: CurrentUser, _csrf: CsrfProtected
) -> dict[str, str]:
    endpoint = session.get(WebhookEndpoint, endpoint_id)
    if endpoint is None or endpoint.user_id != user.id:
        raise AppError(code=ErrorCode.NOT_FOUND)
    secret = webhook_service.rotate_secret(session, endpoint)
    session.commit()
    return {"secret": secret}


@router.get("/webhooks/{endpoint_id}/deliveries", response_model=list[WebhookDeliveryOut])
def webhook_deliveries(
    endpoint_id: str, session: DbSession, user: CurrentUser
) -> list[WebhookDeliveryOut]:
    endpoint = session.get(WebhookEndpoint, endpoint_id)
    if endpoint is None or endpoint.user_id != user.id:
        raise AppError(code=ErrorCode.NOT_FOUND)
    return [
        WebhookDeliveryOut.model_validate(row)
        for row in webhook_service.deliveries_for(session, endpoint_id)
    ]


@router.post("/webhooks/deliveries/{delivery_id}/replay", response_model=WebhookDeliveryOut)
def replay_delivery(
    delivery_id: str, session: DbSession, user: CurrentUser, _csrf: CsrfProtected
) -> WebhookDeliveryOut:
    from picglot.db.models import WebhookDelivery

    original = session.get(WebhookDelivery, delivery_id)
    if original is None:
        raise AppError(code=ErrorCode.NOT_FOUND)
    endpoint = session.get(WebhookEndpoint, original.endpoint_id)
    if endpoint is None or endpoint.user_id != user.id:
        raise AppError(code=ErrorCode.NOT_FOUND)
    replayed = webhook_service.replay(session, delivery_id)
    session.commit()
    return WebhookDeliveryOut.model_validate(replayed)


@router.delete("/webhooks/{endpoint_id}", status_code=204)
def delete_webhook(
    endpoint_id: str, session: DbSession, user: CurrentUser, _csrf: CsrfProtected
) -> Response:
    endpoint = session.get(WebhookEndpoint, endpoint_id)
    if endpoint is None or endpoint.user_id != user.id:
        raise AppError(code=ErrorCode.NOT_FOUND)
    session.delete(endpoint)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Glossaries and translation memory
# --------------------------------------------------------------------------- #
@router.get("/glossaries", response_model=list[GlossaryOut])
def list_glossaries(session: DbSession, user: CurrentUser) -> list[GlossaryOut]:
    rows = session.execute(
        select(Glossary).where(Glossary.user_id == user.id, Glossary.deleted_at.is_(None))
    ).scalars()
    return [
        GlossaryOut(
            id=row.id,
            name=row.name,
            source_language=row.source_language,
            target_language=row.target_language,
            version=row.version,
            is_default=row.is_default,
            term_count=len(row.terms),
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post("/glossaries", response_model=GlossaryOut, status_code=201)
def create_glossary(
    payload: GlossaryCreateRequest, session: DbSession, user: CurrentUser, _csrf: CsrfProtected
) -> GlossaryOut:
    from picglot.domain import languages, plans

    if not plans.has_feature(user.plan_code, "glossary"):
        raise AppError(code=ErrorCode.PLAN_REQUIRED, details={"feature": "glossary"})
    languages.require(payload.source_language)
    languages.require(payload.target_language)

    glossary = Glossary(
        user_id=user.id,
        name=payload.name,
        source_language=payload.source_language,
        target_language=payload.target_language,
        is_default=payload.is_default,
    )
    session.add(glossary)
    session.flush()
    for term in payload.terms[:5000]:
        session.add(GlossaryTerm(glossary_id=glossary.id, **term.model_dump()))
    session.commit()
    return GlossaryOut(
        id=glossary.id,
        name=glossary.name,
        source_language=glossary.source_language,
        target_language=glossary.target_language,
        version=glossary.version,
        is_default=glossary.is_default,
        term_count=len(payload.terms),
        created_at=glossary.created_at,
    )


@router.put("/glossaries/{glossary_id}/terms", response_model=GlossaryOut)
def replace_terms(
    glossary_id: str,
    payload: list[GlossaryTermIn],
    session: DbSession,
    user: CurrentUser,
    _csrf: CsrfProtected,
) -> GlossaryOut:
    glossary = session.get(Glossary, glossary_id)
    if glossary is None or glossary.user_id != user.id:
        raise AppError(code=ErrorCode.NOT_FOUND)
    for term in list(glossary.terms):
        session.delete(term)
    session.flush()
    for term in payload[:5000]:
        session.add(GlossaryTerm(glossary_id=glossary.id, **term.model_dump()))
    glossary.version = int(glossary.version) + 1
    session.commit()
    return GlossaryOut(
        id=glossary.id,
        name=glossary.name,
        source_language=glossary.source_language,
        target_language=glossary.target_language,
        version=glossary.version,
        is_default=glossary.is_default,
        term_count=len(payload),
        created_at=glossary.created_at,
    )


@router.get("/translation-memory", response_model=list[TranslationMemoryOut])
def translation_memory(
    session: DbSession, user: CurrentUser, pagination: Pagination
) -> list[TranslationMemoryOut]:
    limit, offset = pagination
    rows = session.execute(
        select(TranslationMemoryEntry)
        .where(TranslationMemoryEntry.user_id == user.id)
        .order_by(TranslationMemoryEntry.frequency.desc())
        .limit(limit)
        .offset(offset)
    ).scalars()
    return [TranslationMemoryOut.model_validate(row) for row in rows]


@router.delete("/translation-memory/{entry_id}", status_code=204)
def delete_memory_entry(
    entry_id: str, session: DbSession, user: CurrentUser, _csrf: CsrfProtected
) -> Response:
    entry = session.get(TranslationMemoryEntry, entry_id)
    if entry is None or entry.user_id != user.id:
        raise AppError(code=ErrorCode.NOT_FOUND)
    session.delete(entry)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Privacy
# --------------------------------------------------------------------------- #
@router.get("/export-data")
def export_data(session: DbSession, user: CurrentUser) -> dict[str, Any]:
    return auth_service.export_user_data(session, user)


@router.delete("", status_code=204)
def delete_account(
    session: DbSession, user: CurrentUser, response: Response, _csrf: CsrfProtected
) -> Response:
    from picglot.services import notifications

    email = user.email
    notifications.send_email(session, user, "data_deleted", to=email)
    auth_service.delete_account(session, user)
    session.commit()
    from picglot.api.deps import clear_session_cookie

    clear_session_cookie(response)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
