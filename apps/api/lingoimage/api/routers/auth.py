"""Authentication endpoints."""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Request, Response, status

from lingoimage.api.deps import (
    CsrfProtected,
    CurrentIdentity,
    CurrentUser,
    DbSession,
    clear_session_cookie,
    client_ip,
    set_session_cookie,
)
from lingoimage.core.config import settings
from lingoimage.core.errors import AppError, ErrorCode
from lingoimage.core.logging import get_logger
from lingoimage.schemas import (
    AuthResponse,
    ChangePasswordRequest,
    LoginRequest,
    MagicLinkRequest,
    PasswordResetRequest,
    RegisterRequest,
    SessionOut,
    TokenRequest,
    TwoFactorRequest,
    UserOut,
)
from lingoimage.services import auth as auth_service
from lingoimage.services import notifications

log = get_logger(__name__)
router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _issue_csrf(response: Response) -> str:
    token = secrets.token_urlsafe(24)
    response.set_cookie(
        settings.csrf_cookie_name,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=False,  # the SPA must read it to echo it back
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    return token


def _link(path: str, token: str, locale: str) -> str:
    return f"{settings.public_web_url}/{locale}{path}?token={token}"


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    session: DbSession,
    identity: CurrentIdentity,
) -> AuthResponse:
    user, verification_token = auth_service.register(
        session,
        email=payload.email,
        password=payload.password,
        name=payload.name,
        locale=payload.locale,
        marketing_opt_in=payload.marketing_opt_in,
    )

    # Anything processed as a guest moves into the new account.
    if identity.guest is not None:
        auth_service.claim_guest_projects(session, identity.guest, user)

    notifications.send_email(
        session,
        user,
        "verify_email",
        context={"action_url": _link("/auth/verify", verification_token, user.locale)},
    )

    token = auth_service.create_session(
        session, user, ip=client_ip(request), user_agent=request.headers.get("user-agent")
    )
    session.commit()
    set_session_cookie(response, token)
    return AuthResponse(user=UserOut.model_validate(user), csrf_token=_issue_csrf(response))


@router.post("/login", response_model=AuthResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
    identity: CurrentIdentity,
) -> AuthResponse:
    result = auth_service.authenticate(
        session,
        email=payload.email,
        password=payload.password,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    if result.requires_two_factor:
        session.commit()
        return AuthResponse(requires_two_factor=True, pending_token=result.pending_token)

    if identity.guest is not None:
        auth_service.claim_guest_projects(session, identity.guest, result.user)
    session.commit()
    set_session_cookie(response, result.session_token or "")
    return AuthResponse(user=UserOut.model_validate(result.user), csrf_token=_issue_csrf(response))


@router.post("/2fa/verify", response_model=AuthResponse)
def verify_two_factor(
    payload: TwoFactorRequest, request: Request, response: Response, session: DbSession
) -> AuthResponse:
    if not payload.pending_token:
        raise AppError(code=ErrorCode.VALIDATION_FAILED, details={"field": "pending_token"})
    result = auth_service.complete_two_factor(
        session,
        payload.pending_token,
        payload.code,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    session.commit()
    set_session_cookie(response, result.session_token or "")
    return AuthResponse(user=UserOut.model_validate(result.user), csrf_token=_issue_csrf(response))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response, session: DbSession, identity: CurrentIdentity, _csrf: CsrfProtected
) -> Response:
    if identity.user and identity.session_id:
        auth_service.revoke_session(session, identity.session_id, identity.user.id)
        session.commit()
    clear_session_cookie(response)
    response.delete_cookie(settings.csrf_cookie_name, path="/")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_everywhere(
    response: Response, session: DbSession, user: CurrentUser, _csrf: CsrfProtected
) -> Response:
    auth_service.revoke_all_sessions(session, user.id)
    session.commit()
    clear_session_cookie(response)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.get("/csrf")
def csrf(response: Response) -> dict[str, str]:
    return {"csrf_token": _issue_csrf(response)}


@router.post("/verify-email", response_model=UserOut)
def verify_email(payload: TokenRequest, session: DbSession) -> UserOut:
    user = auth_service.verify_email(session, payload.token)
    session.commit()
    return UserOut.model_validate(user)


@router.post("/resend-verification", status_code=status.HTTP_202_ACCEPTED)
def resend_verification(session: DbSession, user: CurrentUser) -> dict[str, bool]:
    if user.is_verified:
        return {"sent": False}
    token = auth_service.issue_token(
        session, email=user.email, purpose=auth_service.PURPOSE_VERIFY_EMAIL, user=user
    )
    notifications.send_email(
        session,
        user,
        "verify_email",
        context={"action_url": _link("/auth/verify", token, user.locale)},
    )
    session.commit()
    return {"sent": True}


@router.post("/magic-link", status_code=status.HTTP_202_ACCEPTED)
def magic_link(payload: MagicLinkRequest, session: DbSession) -> dict[str, bool]:
    """Always reports success — the response must not reveal whether an account exists."""
    user, token = auth_service.request_magic_link(session, payload.email)
    if user and token:
        notifications.send_email(
            session,
            user,
            "magic_link",
            context={"action_url": _link("/auth/magic", token, user.locale)},
        )
    session.commit()
    return {"sent": True}


@router.post("/magic-link/verify", response_model=AuthResponse)
def magic_link_verify(
    payload: TokenRequest, request: Request, response: Response, session: DbSession
) -> AuthResponse:
    result = auth_service.complete_magic_link(
        session,
        payload.token,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    if result.requires_two_factor:
        session.commit()
        return AuthResponse(requires_two_factor=True, pending_token=result.pending_token)
    session.commit()
    set_session_cookie(response, result.session_token or "")
    return AuthResponse(user=UserOut.model_validate(result.user), csrf_token=_issue_csrf(response))


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
def forgot_password(payload: MagicLinkRequest, session: DbSession) -> dict[str, bool]:
    user, token = auth_service.request_password_reset(session, payload.email)
    if user and token:
        notifications.send_email(
            session,
            user,
            "password_reset",
            context={"action_url": _link("/auth/reset", token, user.locale)},
        )
    session.commit()
    return {"sent": True}


@router.post("/reset-password", response_model=UserOut)
def reset_password(payload: PasswordResetRequest, session: DbSession) -> UserOut:
    user = auth_service.reset_password(session, payload.token, payload.password)
    notifications.on_security_event(session, user, detail="Your password was reset.")
    session.commit()
    return UserOut.model_validate(user)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    session: DbSession,
    user: CurrentUser,
    identity: CurrentIdentity,
    _csrf: CsrfProtected,
) -> Response:
    auth_service.change_password(session, user, payload.current_password, payload.new_password)
    auth_service.revoke_all_sessions(session, user.id, keep=identity.session_id)
    notifications.on_security_event(session, user, detail="Your password was changed.")
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sessions", response_model=list[SessionOut])
def sessions(session: DbSession, user: CurrentUser, identity: CurrentIdentity) -> list[SessionOut]:
    return [
        SessionOut(
            id=record.id,
            device_label=record.device_label,
            created_at=record.created_at,
            last_used_at=record.last_used_at,
            current=record.id == identity.session_id,
        )
        for record in auth_service.list_sessions(session, user.id)
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(
    session_id: str, session: DbSession, user: CurrentUser, _csrf: CsrfProtected
) -> Response:
    if not auth_service.revoke_session(session, session_id, user.id):
        raise AppError(code=ErrorCode.NOT_FOUND)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Two-factor management
# --------------------------------------------------------------------------- #
@router.post("/2fa/start")
def start_two_factor(session: DbSession, user: CurrentUser, _csrf: CsrfProtected) -> dict[str, Any]:
    result = auth_service.start_two_factor(session, user)
    session.commit()
    return result


@router.post("/2fa/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_two_factor(
    payload: TwoFactorRequest, session: DbSession, user: CurrentUser, _csrf: CsrfProtected
) -> Response:
    auth_service.confirm_two_factor(session, user, payload.code)
    notifications.on_security_event(session, user, detail="Two-factor authentication was enabled.")
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/2fa", status_code=status.HTTP_204_NO_CONTENT)
def disable_two_factor(
    payload: TwoFactorRequest, session: DbSession, user: CurrentUser, _csrf: CsrfProtected
) -> Response:
    if not auth_service.verify_two_factor(session, user, payload.code):
        raise AppError(code=ErrorCode.TOKEN_INVALID, details={"field": "code"})
    auth_service.disable_two_factor(session, user)
    notifications.on_security_event(session, user, detail="Two-factor authentication was disabled.")
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Google OAuth
# --------------------------------------------------------------------------- #
@router.get("/oauth/google/start")
def google_start(request: Request) -> dict[str, str]:
    if not (settings.google_oauth_enabled and settings.google_oauth_client_id):
        raise AppError(code=ErrorCode.FEATURE_DISABLED, details={"provider": "google"})
    from urllib.parse import urlencode

    from lingoimage.core.security import sign_payload

    state = sign_payload({"ip": client_ip(request)}, salt="oauth-state")
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": f"{settings.public_api_url}/api/v1/auth/oauth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return {"url": f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"}


@router.get("/oauth/google/callback", response_model=AuthResponse)
def google_callback(
    code: str, state: str, request: Request, response: Response, session: DbSession
) -> AuthResponse:
    import httpx

    from lingoimage.core.security import unsign_payload

    if not settings.google_oauth_enabled:
        raise AppError(code=ErrorCode.FEATURE_DISABLED)
    unsign_payload(state, salt="oauth-state", max_age=900)

    try:
        token_response = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "redirect_uri": f"{settings.public_api_url}/api/v1/auth/oauth/google/callback",
                "grant_type": "authorization_code",
            },
            timeout=15,
        )
        token_response.raise_for_status()
        access_token = token_response.json()["access_token"]
        profile_response = httpx.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        profile_response.raise_for_status()
        profile = profile_response.json()
    except Exception as exc:
        raise AppError(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            details={"provider": "google"},
            internal=str(exc)[:200],
        ) from exc

    if not profile.get("email_verified"):
        raise AppError(code=ErrorCode.EMAIL_NOT_VERIFIED)

    user = auth_service.upsert_oauth_user(
        session,
        provider="google",
        provider_account_id=str(profile["sub"]),
        email=str(profile["email"]),
        name=profile.get("name"),
        avatar_url=profile.get("picture"),
        locale=str(profile.get("locale", settings.default_locale))[:2],
    )
    token = auth_service.create_session(
        session, user, ip=client_ip(request), user_agent=request.headers.get("user-agent")
    )
    session.commit()
    set_session_cookie(response, token)
    return AuthResponse(user=UserOut.model_validate(user), csrf_token=_issue_csrf(response))
