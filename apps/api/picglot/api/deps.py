"""FastAPI dependencies: identity, database, rate limits, CSRF, idempotency."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, Header, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from picglot.core.config import settings
from picglot.core.errors import AppError, ErrorCode
from picglot.core.logging import bind_request
from picglot.core.ratelimit import anonymous_requests, api_requests, user_requests
from picglot.core.security import constant_time_equals, hash_ip, hash_token
from picglot.db.models import ApiKey, GuestSession, IdempotencyRecord, User
from picglot.db.session import get_session
from picglot.domain.enums import AdminRole
from picglot.services import auth as auth_service

DbSession = Annotated[Session, Depends(get_session)]

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(slots=True)
class Identity:
    """Who is making this request, however they authenticated."""

    user: User | None = None
    guest: GuestSession | None = None
    api_key: ApiKey | None = None
    session_id: str | None = None

    @property
    def user_id(self) -> str | None:
        return self.user.id if self.user else None

    @property
    def workspace_id(self) -> str | None:
        if self.api_key and self.api_key.workspace_id:
            return self.api_key.workspace_id
        return None

    @property
    def guest_id(self) -> str | None:
        return self.guest.id if self.guest else None

    @property
    def plan_code(self) -> str:
        if self.user:
            return self.user.plan_code
        return "guest"

    @property
    def is_authenticated(self) -> bool:
        return self.user is not None

    def require_user(self) -> User:
        if self.user is None:
            raise AppError(code=ErrorCode.UNAUTHENTICATED)
        return self.user

    def require_verified(self) -> User:
        user = self.require_user()
        if not user.is_verified and settings.is_production:
            raise AppError(code=ErrorCode.EMAIL_NOT_VERIFIED)
        return user

    def require_admin(self, *roles: AdminRole) -> User:
        user = self.require_user()
        if not user.admin_role:
            raise AppError(code=ErrorCode.FORBIDDEN)
        # A superadmin satisfies every role requirement.
        lacks_role = roles and user.admin_role not in {str(role) for role in roles}
        if lacks_role and user.admin_role != str(AdminRole.SUPERADMIN):
            raise AppError(
                code=ErrorCode.FORBIDDEN, details={"required": [str(role) for role in roles]}
            )
        return user


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


def get_identity(
    request: Request,
    session: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> Identity:
    """Resolve identity from an API key, a session cookie, or a guest cookie."""
    identity = Identity()

    if authorization and authorization.lower().startswith("bearer "):
        raw_key = authorization[7:].strip()
        if raw_key.startswith(("lik_live_", "lik_test_")):
            identity.api_key = _resolve_api_key(session, raw_key)
            if identity.api_key.user_id:
                identity.user = session.get(User, identity.api_key.user_id)
            api_requests(identity.api_key.id).raise_if_blocked()
            _bind(request, identity)
            return identity

    cookie = request.cookies.get(settings.session_cookie_name)
    resolved = auth_service.resolve_session(session, cookie)
    if resolved is not None:
        identity.user, record = resolved
        identity.session_id = record.id
        user_requests(identity.user.id).raise_if_blocked()
        _bind(request, identity)
        return identity

    guest_cookie = request.cookies.get(settings.guest_cookie_name)
    identity.guest = auth_service.resolve_guest(session, guest_cookie)
    anonymous_requests(hash_ip(client_ip(request)) or "anon").raise_if_blocked()
    _bind(request, identity)
    return identity


def _bind(request: Request, identity: Identity) -> None:
    bind_request(
        request_id=getattr(request.state, "request_id", None),
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )


def _resolve_api_key(session: Session, raw_key: str) -> ApiKey:
    record = session.execute(
        select(ApiKey).where(ApiKey.key_hash == hash_token(raw_key))
    ).scalar_one_or_none()
    if record is None or not record.is_active:
        raise AppError(code=ErrorCode.UNAUTHENTICATED, details={"reason": "invalid_api_key"})
    now = datetime.now(UTC)
    if not record.last_used_at or (now - record.last_used_at).total_seconds() > 60:
        record.last_used_at = now
    return record


CurrentIdentity = Annotated[Identity, Depends(get_identity)]


def require_user(identity: CurrentIdentity) -> User:
    return identity.require_user()


CurrentUser = Annotated[User, Depends(require_user)]


def require_admin(identity: CurrentIdentity) -> User:
    return identity.require_admin()


AdminUser = Annotated[User, Depends(require_admin)]


def ensure_guest_session(
    request: Request, response: Response, session: Session, identity: Identity
) -> GuestSession:
    """Create a guest session on demand so anonymous users can process a file."""
    if identity.guest is not None:
        return identity.guest
    guest, raw = auth_service.create_guest_session(session, ip=client_ip(request))
    response.set_cookie(
        settings.guest_cookie_name,
        raw,
        max_age=settings.retention_guest_hours * 3600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    identity.guest = guest
    return guest


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        domain=settings.session_cookie_domain or None,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        settings.session_cookie_name,
        domain=settings.session_cookie_domain or None,
        path="/",
    )


# --------------------------------------------------------------------------- #
# CSRF
# --------------------------------------------------------------------------- #
def verify_csrf(request: Request) -> None:
    """Double-submit cookie check for cookie-authenticated state changes.

    API-key requests are exempt: they do not rely on ambient credentials, so
    they are not vulnerable to CSRF.
    """
    if request.method in SAFE_METHODS:
        return
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return
    if not request.cookies.get(settings.session_cookie_name):
        return  # guest requests carry no ambient privilege worth forging

    cookie_token = request.cookies.get(settings.csrf_cookie_name)
    header_token = request.headers.get("x-csrf-token")
    if not cookie_token or not header_token or not constant_time_equals(cookie_token, header_token):
        raise AppError(code=ErrorCode.CSRF_FAILED)


CsrfProtected = Annotated[None, Depends(verify_csrf)]


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #
def idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str | None:
    if idempotency_key and len(idempotency_key) > 128:
        raise AppError(
            code=ErrorCode.VALIDATION_FAILED,
            details={"field": "Idempotency-Key", "reason": "too_long"},
        )
    return idempotency_key


IdempotencyKey = Annotated[str | None, Depends(idempotency_key)]


def replay_or_record(
    session: Session,
    *,
    key: str | None,
    scope: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a stored response for a repeated key, or ``None`` to proceed."""
    if not key:
        return None
    request_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()
    scoped = f"{scope}:{key}"

    record = session.get(IdempotencyRecord, scoped)
    if record is not None:
        if record.request_hash != request_hash:
            raise AppError(code=ErrorCode.IDEMPOTENCY_CONFLICT)
        return record.response_body

    session.add(
        IdempotencyRecord(
            key=scoped,
            scope=scope,
            request_hash=request_hash,
            response_body={},
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
    )
    session.flush()
    return None


def store_idempotent_response(
    session: Session, *, key: str | None, scope: str, body: dict[str, Any], status: int = 200
) -> None:
    if not key:
        return
    record = session.get(IdempotencyRecord, f"{scope}:{key}")
    if record is not None:
        record.response_body = body
        record.status_code = status


# --------------------------------------------------------------------------- #
# Feature gates
# --------------------------------------------------------------------------- #
def require_feature(flag: str):
    def dependency() -> None:
        if not getattr(settings, f"feature_{flag}", True):
            raise AppError(code=ErrorCode.FEATURE_DISABLED, details={"feature": flag})

    return Depends(dependency)


def require_not_maintenance() -> None:
    if settings.maintenance_mode:
        raise AppError(code=ErrorCode.MAINTENANCE)


def paginate(limit: int = 20, offset: int = 0) -> tuple[int, int]:
    return max(1, min(100, limit)), max(0, offset)


Pagination = Annotated[tuple[int, int], Depends(paginate)]


def db() -> Generator[Session, None, None]:
    yield from get_session()
