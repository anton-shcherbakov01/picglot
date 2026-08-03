"""Authentication: registration, sessions, magic links, 2FA, guest sessions."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pyotp
from sqlalchemy import select, update
from sqlalchemy.orm import Session as DbSession

from picglot.core.config import settings
from picglot.core.errors import AppError, ErrorCode
from picglot.core.ids import token as random_token
from picglot.core.logging import get_logger
from picglot.core.ratelimit import login_attempts
from picglot.core.security import (
    decrypt_secret,
    encrypt_secret,
    generate_session_token,
    hash_ip,
    hash_password,
    hash_token,
    password_needs_rehash,
    validate_password_strength,
    verify_password,
)
from picglot.db.models import (
    GuestSession,
    LoginEvent,
    OAuthAccount,
    Session,
    TwoFactorSecret,
    User,
    VerificationToken,
)
from picglot.domain.enums import UserStatus
from picglot.services import credits

log = get_logger(__name__)

PURPOSE_VERIFY_EMAIL = "verify_email"
PURPOSE_MAGIC_LINK = "magic_link"
PURPOSE_PASSWORD_RESET = "password_reset"

MAX_FAILED_LOGINS = 8
LOCKOUT_MINUTES = 15


@dataclass(slots=True)
class AuthResult:
    user: User
    session_token: str | None = None
    requires_two_factor: bool = False
    pending_token: str | None = None


def normalize_email(email: str) -> str:
    return email.strip().lower()


def find_user(session: DbSession, email: str) -> User | None:
    return session.execute(
        select(User).where(User.email == normalize_email(email), User.deleted_at.is_(None))
    ).scalar_one_or_none()


def get_user(session: DbSession, user_id: str) -> User:
    user = session.execute(
        select(User).where(User.id == user_id, User.deleted_at.is_(None))
    ).scalar_one_or_none()
    if user is None:
        raise AppError(code=ErrorCode.NOT_FOUND, internal=f"user {user_id}")
    return user


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register(
    session: DbSession,
    *,
    email: str,
    password: str,
    name: str | None = None,
    locale: str = "en",
    marketing_opt_in: bool = False,
) -> tuple[User, str]:
    """Create an account and return ``(user, email_verification_token)``."""
    email = normalize_email(email)
    strength = validate_password_strength(password, email=email)
    if not strength.ok:
        raise AppError(
            code=ErrorCode.VALIDATION_FAILED,
            details={"field": "password", "reason": strength.reason},
        )
    if find_user(session, email) is not None:
        raise AppError(
            code=ErrorCode.CONFLICT,
            details={"field": "email"},
            internal="email already registered",
        )

    user = User(
        email=email,
        password_hash=hash_password(password),
        name=(name or "").strip()[:120] or None,
        locale=locale if locale in settings.enabled_locales else settings.default_locale,
        status=UserStatus.PENDING,
        plan_code="free",
        marketing_opt_in=marketing_opt_in,
        notification_preferences={
            "job_completed": True,
            "batch_completed": True,
            "credits_low": True,
            "product_updates": marketing_opt_in,
        },
    )
    session.add(user)
    session.flush()

    credits.get_or_create_wallet(session, user_id=user.id)
    credits.grant_signup_bonus(session, user, settings.free_monthly_credits)

    verification = issue_token(session, email=email, purpose=PURPOSE_VERIFY_EMAIL, user=user)
    log.info("auth.registered", user_id=user.id)
    return user, verification


def verify_email(session: DbSession, token: str) -> User:
    record = consume_token(session, token, PURPOSE_VERIFY_EMAIL)
    user = get_user(session, record.user_id) if record.user_id else find_user(session, record.email)
    if user is None:
        raise AppError(code=ErrorCode.TOKEN_INVALID)
    user.email_verified_at = datetime.now(UTC)
    if user.status == UserStatus.PENDING:
        user.status = UserStatus.ACTIVE
    return user


# --------------------------------------------------------------------------- #
# Password login
# --------------------------------------------------------------------------- #
def authenticate(
    session: DbSession,
    *,
    email: str,
    password: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> AuthResult:
    email = normalize_email(email)
    login_attempts(email).raise_if_blocked()
    if ip:
        login_attempts(hash_ip(ip) or ip).raise_if_blocked()

    user = find_user(session, email)
    # Always run the hash comparison so timing does not reveal account existence.
    valid = verify_password(password, user.password_hash if user else None)

    if user is None or not valid:
        _record_login(
            session,
            user,
            email,
            success=False,
            reason="invalid_credentials",
            ip=ip,
            user_agent=user_agent,
        )
        if user is not None:
            user.failed_login_count = int(user.failed_login_count) + 1
            if user.failed_login_count >= MAX_FAILED_LOGINS:
                user.locked_until = datetime.now(UTC) + timedelta(minutes=LOCKOUT_MINUTES)
                log.warning("auth.account_locked", user_id=user.id)
        raise AppError(code=ErrorCode.INVALID_CREDENTIALS)

    _assert_usable(user)

    if password_needs_rehash(user.password_hash or ""):
        user.password_hash = hash_password(password)

    user.failed_login_count = 0
    user.locked_until = None

    if two_factor_enabled(session, user):
        pending = _issue_pending_2fa(user)
        _record_login(
            session, user, email, success=True, reason="2fa_required", ip=ip, user_agent=user_agent
        )
        return AuthResult(user=user, requires_two_factor=True, pending_token=pending)

    token = create_session(session, user, ip=ip, user_agent=user_agent)
    _record_login(session, user, email, success=True, reason=None, ip=ip, user_agent=user_agent)
    return AuthResult(user=user, session_token=token)


def _assert_usable(user: User) -> None:
    if user.status == UserStatus.SUSPENDED:
        raise AppError(code=ErrorCode.ACCOUNT_LOCKED)
    if user.locked_until and user.locked_until > datetime.now(UTC):
        raise AppError(
            code=ErrorCode.ACCOUNT_LOCKED,
            details={"until": user.locked_until.isoformat()},
        )


def _record_login(
    session: DbSession,
    user: User | None,
    email: str,
    *,
    success: bool,
    reason: str | None,
    ip: str | None,
    user_agent: str | None,
    method: str = "password",
) -> None:
    session.add(
        LoginEvent(
            user_id=user.id if user else None,
            email=email,
            success=success,
            method=method,
            reason=reason,
            ip_hash=hash_ip(ip),
            user_agent=(user_agent or "")[:512] or None,
        )
    )


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def create_session(
    session: DbSession,
    user: User,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    ttl_seconds: int | None = None,
) -> str:
    raw, hashed = generate_session_token()
    session.add(
        Session(
            user_id=user.id,
            token_hash=hashed,
            ip_hash=hash_ip(ip),
            user_agent=(user_agent or "")[:512] or None,
            device_label=_device_label(user_agent),
            expires_at=datetime.now(UTC)
            + timedelta(seconds=ttl_seconds or settings.session_ttl_seconds),
            last_used_at=datetime.now(UTC),
        )
    )
    user.last_seen_at = datetime.now(UTC)
    return raw


def resolve_session(session: DbSession, raw_token: str | None) -> tuple[User, Session] | None:
    if not raw_token:
        return None
    record = session.execute(
        select(Session).where(Session.token_hash == hash_token(raw_token))
    ).scalar_one_or_none()
    if record is None or not record.is_active:
        return None
    user = session.execute(
        select(User).where(User.id == record.user_id, User.deleted_at.is_(None))
    ).scalar_one_or_none()
    if user is None or user.status == UserStatus.SUSPENDED:
        return None
    # Touch at most once a minute to avoid a write on every request.
    now = datetime.now(UTC)
    if not record.last_used_at or (now - record.last_used_at).total_seconds() > 60:
        record.last_used_at = now
        user.last_seen_at = now
    return user, record


def revoke_session(session: DbSession, session_id: str, user_id: str) -> bool:
    result = session.execute(
        update(Session)
        .where(
            Session.id == session_id,
            Session.user_id == user_id,
            Session.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    return bool(result.rowcount)


def revoke_all_sessions(session: DbSession, user_id: str, *, keep: str | None = None) -> int:
    statement = update(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None))
    if keep:
        statement = statement.where(Session.id != keep)
    return int(session.execute(statement.values(revoked_at=datetime.now(UTC))).rowcount)


def list_sessions(session: DbSession, user_id: str) -> list[Session]:
    return list(
        session.execute(
            select(Session)
            .where(Session.user_id == user_id, Session.revoked_at.is_(None))
            .order_by(Session.last_used_at.desc().nullslast())
        ).scalars()
    )


def _device_label(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    agent = user_agent.lower()
    platform = next(
        (
            name
            for token, name in (
                ("iphone", "iPhone"),
                ("ipad", "iPad"),
                ("android", "Android"),
                ("mac os", "macOS"),
                ("windows", "Windows"),
                ("linux", "Linux"),
            )
            if token in agent
        ),
        "Unknown device",
    )
    browser = next(
        (
            name
            for token, name in (
                ("edg/", "Edge"),
                ("chrome", "Chrome"),
                ("safari", "Safari"),
                ("firefox", "Firefox"),
            )
            if token in agent
        ),
        "",
    )
    return f"{browser} on {platform}".strip() if browser else platform


# --------------------------------------------------------------------------- #
# One-time tokens
# --------------------------------------------------------------------------- #
def issue_token(
    session: DbSession,
    *,
    email: str,
    purpose: str,
    user: User | None = None,
    ttl_seconds: int | None = None,
) -> str:
    ttl = ttl_seconds or {
        PURPOSE_VERIFY_EMAIL: settings.email_verification_ttl_seconds,
        PURPOSE_MAGIC_LINK: settings.magic_link_ttl_seconds,
        PURPOSE_PASSWORD_RESET: settings.password_reset_ttl_seconds,
    }.get(purpose, 3600)

    raw = random_token(32)
    session.add(
        VerificationToken(
            user_id=user.id if user else None,
            email=normalize_email(email),
            purpose=purpose,
            token_hash=hash_token(raw),
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
        )
    )
    return raw


def consume_token(session: DbSession, raw: str, purpose: str) -> VerificationToken:
    record = session.execute(
        select(VerificationToken).where(
            VerificationToken.token_hash == hash_token(raw),
            VerificationToken.purpose == purpose,
        )
    ).scalar_one_or_none()
    if record is None:
        raise AppError(code=ErrorCode.TOKEN_INVALID)
    if record.consumed_at is not None:
        raise AppError(code=ErrorCode.TOKEN_INVALID, internal="token already used")
    if record.expires_at <= datetime.now(UTC):
        raise AppError(code=ErrorCode.TOKEN_EXPIRED)
    record.consumed_at = datetime.now(UTC)
    return record


def request_magic_link(session: DbSession, email: str) -> tuple[User | None, str | None]:
    """Returns ``(user, token)``; both ``None`` when no such account exists.

    The caller always responds identically so the endpoint cannot be used to
    enumerate registered addresses.
    """
    user = find_user(session, email)
    if user is None:
        return None, None
    _assert_usable(user)
    return user, issue_token(session, email=email, purpose=PURPOSE_MAGIC_LINK, user=user)


def complete_magic_link(
    session: DbSession, raw: str, *, ip: str | None = None, user_agent: str | None = None
) -> AuthResult:
    record = consume_token(session, raw, PURPOSE_MAGIC_LINK)
    user = get_user(session, record.user_id) if record.user_id else find_user(session, record.email)
    if user is None:
        raise AppError(code=ErrorCode.TOKEN_INVALID)
    _assert_usable(user)
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)
        user.status = UserStatus.ACTIVE

    if two_factor_enabled(session, user):
        return AuthResult(
            user=user, requires_two_factor=True, pending_token=_issue_pending_2fa(user)
        )

    token = create_session(session, user, ip=ip, user_agent=user_agent)
    _record_login(
        session,
        user,
        user.email,
        success=True,
        reason=None,
        ip=ip,
        user_agent=user_agent,
        method="magic_link",
    )
    return AuthResult(user=user, session_token=token)


def request_password_reset(session: DbSession, email: str) -> tuple[User | None, str | None]:
    user = find_user(session, email)
    if user is None:
        return None, None
    return user, issue_token(session, email=email, purpose=PURPOSE_PASSWORD_RESET, user=user)


def reset_password(session: DbSession, raw: str, new_password: str) -> User:
    record = consume_token(session, raw, PURPOSE_PASSWORD_RESET)
    user = get_user(session, record.user_id) if record.user_id else find_user(session, record.email)
    if user is None:
        raise AppError(code=ErrorCode.TOKEN_INVALID)
    strength = validate_password_strength(new_password, email=user.email)
    if not strength.ok:
        raise AppError(
            code=ErrorCode.VALIDATION_FAILED,
            details={"field": "password", "reason": strength.reason},
        )
    user.password_hash = hash_password(new_password)
    user.failed_login_count = 0
    user.locked_until = None
    revoke_all_sessions(session, user.id)
    return user


def change_password(session: DbSession, user: User, current: str, new_password: str) -> None:
    if not verify_password(current, user.password_hash):
        raise AppError(code=ErrorCode.INVALID_CREDENTIALS)
    strength = validate_password_strength(new_password, email=user.email)
    if not strength.ok:
        raise AppError(
            code=ErrorCode.VALIDATION_FAILED,
            details={"field": "password", "reason": strength.reason},
        )
    user.password_hash = hash_password(new_password)


# --------------------------------------------------------------------------- #
# Two-factor
# --------------------------------------------------------------------------- #
def two_factor_enabled(session: DbSession, user: User) -> bool:
    record = session.execute(
        select(TwoFactorSecret).where(TwoFactorSecret.user_id == user.id)
    ).scalar_one_or_none()
    return record is not None and record.confirmed_at is not None


def start_two_factor(session: DbSession, user: User) -> dict[str, Any]:
    secret = pyotp.random_base32()
    backup = [secrets.token_hex(4) for _ in range(10)]
    record = session.execute(
        select(TwoFactorSecret).where(TwoFactorSecret.user_id == user.id)
    ).scalar_one_or_none()
    if record is None:
        record = TwoFactorSecret(user_id=user.id, secret_encrypted="", backup_codes=[])
        session.add(record)
    record.secret_encrypted = encrypt_secret(secret)
    record.backup_codes = [hash_token(code) for code in backup]
    record.confirmed_at = None
    session.flush()

    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=settings.totp_issuer)
    return {"secret": secret, "otpauth_url": uri, "backup_codes": backup}


def confirm_two_factor(session: DbSession, user: User, code: str) -> bool:
    record = session.execute(
        select(TwoFactorSecret).where(TwoFactorSecret.user_id == user.id)
    ).scalar_one_or_none()
    if record is None:
        raise AppError(code=ErrorCode.NOT_FOUND, internal="2FA was never started")
    if not pyotp.TOTP(decrypt_secret(record.secret_encrypted)).verify(code, valid_window=1):
        raise AppError(code=ErrorCode.TOKEN_INVALID, details={"field": "code"})
    record.confirmed_at = datetime.now(UTC)
    return True


def verify_two_factor(session: DbSession, user: User, code: str) -> bool:
    record = session.execute(
        select(TwoFactorSecret).where(TwoFactorSecret.user_id == user.id)
    ).scalar_one_or_none()
    if record is None or record.confirmed_at is None:
        return False
    if pyotp.TOTP(decrypt_secret(record.secret_encrypted)).verify(code, valid_window=1):
        return True
    # Backup codes are single use.
    hashed = hash_token(code.strip().replace(" ", ""))
    remaining = list(record.backup_codes or [])
    if hashed in remaining:
        remaining.remove(hashed)
        record.backup_codes = remaining
        log.info("auth.backup_code_used", user_id=user.id, remaining=len(remaining))
        return True
    return False


def disable_two_factor(session: DbSession, user: User) -> None:
    record = session.execute(
        select(TwoFactorSecret).where(TwoFactorSecret.user_id == user.id)
    ).scalar_one_or_none()
    if record is not None:
        session.delete(record)


def _issue_pending_2fa(user: User) -> str:
    from picglot.core.security import sign_payload

    return sign_payload({"user_id": user.id, "stage": "2fa"}, salt="pending-2fa")


def complete_two_factor(
    session: DbSession,
    pending_token: str,
    code: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> AuthResult:
    from picglot.core.security import unsign_payload

    payload = unsign_payload(pending_token, salt="pending-2fa", max_age=600)
    user = get_user(session, str(payload.get("user_id")))
    if not verify_two_factor(session, user, code):
        _record_login(
            session,
            user,
            user.email,
            success=False,
            reason="bad_2fa_code",
            ip=ip,
            user_agent=user_agent,
            method="2fa",
        )
        raise AppError(code=ErrorCode.TOKEN_INVALID, details={"field": "code"})
    token = create_session(session, user, ip=ip, user_agent=user_agent)
    _record_login(
        session,
        user,
        user.email,
        success=True,
        reason=None,
        ip=ip,
        user_agent=user_agent,
        method="2fa",
    )
    return AuthResult(user=user, session_token=token)


# --------------------------------------------------------------------------- #
# OAuth
# --------------------------------------------------------------------------- #
def upsert_oauth_user(
    session: DbSession,
    *,
    provider: str,
    provider_account_id: str,
    email: str,
    name: str | None = None,
    avatar_url: str | None = None,
    locale: str = "en",
) -> User:
    link = session.execute(
        select(OAuthAccount).where(
            OAuthAccount.provider == provider,
            OAuthAccount.provider_account_id == provider_account_id,
        )
    ).scalar_one_or_none()
    if link is not None:
        return get_user(session, link.user_id)

    user = find_user(session, email)
    if user is None:
        user = User(
            email=normalize_email(email),
            name=name,
            avatar_url=avatar_url,
            locale=locale if locale in settings.enabled_locales else settings.default_locale,
            status=UserStatus.ACTIVE,
            email_verified_at=datetime.now(UTC),
            plan_code="free",
        )
        session.add(user)
        session.flush()
        credits.get_or_create_wallet(session, user_id=user.id)
        credits.grant_signup_bonus(session, user, settings.free_monthly_credits)
    elif user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)
        user.status = UserStatus.ACTIVE

    session.add(
        OAuthAccount(
            user_id=user.id,
            provider=provider,
            provider_account_id=provider_account_id,
            email=normalize_email(email),
        )
    )
    return user


# --------------------------------------------------------------------------- #
# Guest sessions
# --------------------------------------------------------------------------- #
def create_guest_session(session: DbSession, *, ip: str | None = None) -> tuple[GuestSession, str]:
    raw = random_token(24)
    guest = GuestSession(
        token_hash=hash_token(raw),
        ip_hash=hash_ip(ip),
        expires_at=datetime.now(UTC) + timedelta(hours=settings.retention_guest_hours),
    )
    session.add(guest)
    session.flush()
    return guest, raw


def resolve_guest(session: DbSession, raw: str | None) -> GuestSession | None:
    if not raw:
        return None
    guest = session.execute(
        select(GuestSession).where(GuestSession.token_hash == hash_token(raw))
    ).scalar_one_or_none()
    if guest is None or guest.expires_at <= datetime.now(UTC):
        return None
    return guest


def guest_pages_remaining(guest: GuestSession | None) -> int:
    if guest is None:
        return settings.guest_free_pages
    return max(0, settings.guest_free_pages - int(guest.pages_used))


def consume_guest_pages(session: DbSession, guest: GuestSession, pages: int) -> None:
    remaining = guest_pages_remaining(guest)
    if pages > remaining:
        raise AppError(
            code=ErrorCode.QUOTA_EXCEEDED,
            details={
                "requested_pages": pages,
                "remaining_pages": remaining,
                "limit": settings.guest_free_pages,
                "action": "sign_up",
            },
        )
    guest.pages_used = int(guest.pages_used) + pages


def claim_guest_projects(session: DbSession, guest: GuestSession, user: User) -> int:
    """Move work done before signing up into the new account."""
    from picglot.db.models import Project

    result = session.execute(
        update(Project)
        .where(Project.guest_session_id == guest.id, Project.owner_user_id.is_(None))
        .values(
            owner_user_id=user.id,
            guest_session_id=None,
            expires_at=datetime.now(UTC)
            + timedelta(hours=settings.retention_hours(user.plan_code)),
        )
    )
    guest.claimed_by_user_id = user.id
    count = int(result.rowcount or 0)
    if count:
        log.info("auth.guest_projects_claimed", user_id=user.id, count=count)
    return count


# --------------------------------------------------------------------------- #
# Account lifecycle
# --------------------------------------------------------------------------- #
def delete_account(session: DbSession, user: User, *, hard: bool = False) -> None:
    """Soft-delete immediately; the lifecycle worker purges storage and rows."""
    now = datetime.now(UTC)
    user.deleted_at = now
    user.status = UserStatus.DELETED
    # Free the address so the person can register again.
    user.email = f"deleted+{user.id}@invalid.local"
    user.password_hash = None
    user.name = None
    user.avatar_url = None
    revoke_all_sessions(session, user.id)
    if hard:
        session.delete(user)
    log.info("auth.account_deleted", user_id=user.id, hard=hard)


def export_user_data(session: DbSession, user: User) -> dict[str, Any]:
    """GDPR-style export of everything we hold about one account."""
    from picglot.db.models import ApiKey, Project

    projects = list(
        session.execute(select(Project).where(Project.owner_user_id == user.id)).scalars()
    )
    wallet = credits.get_or_create_wallet(session, user_id=user.id)
    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "account": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "locale": user.locale,
            "timezone": user.timezone,
            "plan": user.plan_code,
            "created_at": user.created_at.isoformat(),
            "email_verified": user.is_verified,
            "notification_preferences": user.notification_preferences,
        },
        "credits": {
            "balance": wallet.balance,
            "lifetime_granted": wallet.lifetime_granted,
            "lifetime_spent": wallet.lifetime_spent,
            "ledger": [
                {
                    "created_at": entry.created_at.isoformat(),
                    "delta": entry.delta,
                    "balance_after": entry.balance_after,
                    "reason": entry.reason,
                    "note": entry.note,
                }
                for entry in credits.history(session, wallet.id, limit=1000)
            ],
        },
        "projects": [
            {
                "id": project.id,
                "name": project.name,
                "tool": project.tool_type,
                "source_language": project.source_language,
                "target_language": project.target_language,
                "created_at": project.created_at.isoformat(),
                "pages": project.page_count,
                "status": project.status,
            }
            for project in projects
        ],
        "sessions": [
            {
                "created_at": item.created_at.isoformat(),
                "device": item.device_label,
                "last_used_at": item.last_used_at.isoformat() if item.last_used_at else None,
            }
            for item in list_sessions(session, user.id)
        ],
        "api_keys": [
            {
                "name": key.name,
                "prefix": key.prefix,
                "last_four": key.last_four,
                "created_at": key.created_at.isoformat(),
                "revoked": key.revoked_at is not None,
            }
            for key in session.execute(select(ApiKey).where(ApiKey.user_id == user.id)).scalars()
        ],
        "note": (
            "Uploaded files and recognised text are stored only for the retention "
            "period shown in your privacy settings and are not included here."
        ),
    }
