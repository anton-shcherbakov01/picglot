"""Stable, user-safe error taxonomy.

Every failure surfaced to a user or API client carries one of these codes. The
code is stable forever (clients switch on it), the message is localised in the
web app, and the internal detail never leaves the server logs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    # --- input -------------------------------------------------------------
    UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
    FILE_TOO_LARGE = "file_too_large"
    PAGE_LIMIT_EXCEEDED = "page_limit_exceeded"
    BATCH_LIMIT_EXCEEDED = "batch_limit_exceeded"
    CORRUPTED_FILE = "corrupted_file"
    ENCRYPTED_PDF = "encrypted_pdf"
    IMAGE_TOO_LARGE = "image_too_large"
    MALICIOUS_FILE = "malicious_file"
    # --- processing --------------------------------------------------------
    NO_TEXT_DETECTED = "no_text_detected"
    LANGUAGE_NOT_SUPPORTED = "language_not_supported"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_REJECTED = "provider_rejected"
    LOCAL_ONLY_BLOCKED = "local_only_blocked"
    EXPORT_FAILED = "export_failed"
    STORAGE_FAILED = "storage_failed"
    CANCELLED = "cancelled"
    # --- account / limits --------------------------------------------------
    INSUFFICIENT_CREDITS = "insufficient_credits"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    QUOTA_EXCEEDED = "quota_exceeded"
    PLAN_REQUIRED = "plan_required"
    # --- auth --------------------------------------------------------------
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    INVALID_CREDENTIALS = "invalid_credentials"
    EMAIL_NOT_VERIFIED = "email_not_verified"
    TWO_FACTOR_REQUIRED = "two_factor_required"
    TOKEN_INVALID = "token_invalid"
    TOKEN_EXPIRED = "token_expired"
    CSRF_FAILED = "csrf_failed"
    ACCOUNT_LOCKED = "account_locked"
    # --- generic -----------------------------------------------------------
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    VERSION_CONFLICT = "version_conflict"
    VALIDATION_FAILED = "validation_failed"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    MAINTENANCE = "maintenance"
    FEATURE_DISABLED = "feature_disabled"
    INTERNAL_ERROR = "internal_error"


#: HTTP status for each code. Anything missing defaults to 400.
STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.INVALID_CREDENTIALS: 401,
    ErrorCode.TOKEN_INVALID: 401,
    ErrorCode.TOKEN_EXPIRED: 401,
    ErrorCode.TWO_FACTOR_REQUIRED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.CSRF_FAILED: 403,
    ErrorCode.EMAIL_NOT_VERIFIED: 403,
    ErrorCode.ACCOUNT_LOCKED: 403,
    ErrorCode.PLAN_REQUIRED: 403,
    ErrorCode.LOCAL_ONLY_BLOCKED: 403,
    ErrorCode.FEATURE_DISABLED: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.VERSION_CONFLICT: 409,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.FILE_TOO_LARGE: 413,
    ErrorCode.IMAGE_TOO_LARGE: 413,
    ErrorCode.UNSUPPORTED_FILE_TYPE: 415,
    ErrorCode.VALIDATION_FAILED: 422,
    ErrorCode.INSUFFICIENT_CREDITS: 402,
    ErrorCode.QUOTA_EXCEEDED: 402,
    ErrorCode.RATE_LIMIT_EXCEEDED: 429,
    ErrorCode.PROVIDER_UNAVAILABLE: 503,
    ErrorCode.PROVIDER_TIMEOUT: 504,
    ErrorCode.MAINTENANCE: 503,
    ErrorCode.STORAGE_FAILED: 503,
    ErrorCode.INTERNAL_ERROR: 500,
}

#: Whether retrying the exact same request could succeed.
RETRYABLE_CODES: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.PROVIDER_UNAVAILABLE,
        ErrorCode.PROVIDER_TIMEOUT,
        ErrorCode.STORAGE_FAILED,
        ErrorCode.RATE_LIMIT_EXCEEDED,
        ErrorCode.INTERNAL_ERROR,
    }
)

#: Failures caused by our infrastructure — credits are refunded automatically.
INFRASTRUCTURE_CODES: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.PROVIDER_UNAVAILABLE,
        ErrorCode.PROVIDER_TIMEOUT,
        ErrorCode.STORAGE_FAILED,
        ErrorCode.EXPORT_FAILED,
        ErrorCode.INTERNAL_ERROR,
    }
)


@dataclass(slots=True)
class AppError(Exception):
    """Base class for every deliberate failure.

    ``message`` is a fallback English string; the web app renders a localised
    message keyed by ``code``. ``details`` may hold only non-sensitive scalars
    (limits, counts, provider names) — never document content.
    """

    code: ErrorCode
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    internal: str | None = None
    status_code: int | None = None

    def __post_init__(self) -> None:
        if not self.message:
            self.message = DEFAULT_MESSAGES.get(self.code, "Something went wrong.")
        if self.status_code is None:
            self.status_code = STATUS_BY_CODE.get(self.code, 400)
        # Explicit base call: @dataclass(slots=True) rebuilds the class, which
        # breaks the zero-argument super() cell.
        Exception.__init__(self, self.message)

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_CODES

    @property
    def refundable(self) -> bool:
        return self.code in INFRASTRUCTURE_CODES

    def to_payload(self, request_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": {
                "code": str(self.code),
                "message": self.message,
                "retryable": self.retryable,
            }
        }
        if self.details:
            payload["error"]["details"] = self.details
        if request_id:
            payload["error"]["request_id"] = request_id
        return payload


DEFAULT_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.UNSUPPORTED_FILE_TYPE: "This file type is not supported.",
    ErrorCode.FILE_TOO_LARGE: "The file exceeds the size limit for your plan.",
    ErrorCode.PAGE_LIMIT_EXCEEDED: "The document has more pages than your plan allows.",
    ErrorCode.BATCH_LIMIT_EXCEEDED: "Too many files in one batch for your plan.",
    ErrorCode.CORRUPTED_FILE: "The file could not be read. It may be damaged.",
    ErrorCode.ENCRYPTED_PDF: "This PDF is password protected. Remove the password and retry.",
    ErrorCode.IMAGE_TOO_LARGE: "The image resolution exceeds the processing limit.",
    ErrorCode.MALICIOUS_FILE: "The file was rejected by the security scanner.",
    ErrorCode.NO_TEXT_DETECTED: "No readable text was found in this file.",
    ErrorCode.LANGUAGE_NOT_SUPPORTED: "That language is not supported yet.",
    ErrorCode.PROVIDER_UNAVAILABLE: "The processing provider is temporarily unavailable.",
    ErrorCode.PROVIDER_TIMEOUT: "The processing provider took too long to respond.",
    ErrorCode.PROVIDER_REJECTED: "The processing provider rejected this request.",
    ErrorCode.LOCAL_ONLY_BLOCKED: "This action needs an external provider, which is disabled.",
    ErrorCode.EXPORT_FAILED: "The export could not be produced.",
    ErrorCode.STORAGE_FAILED: "File storage is temporarily unavailable.",
    ErrorCode.CANCELLED: "The job was cancelled.",
    ErrorCode.INSUFFICIENT_CREDITS: "You do not have enough credits for this operation.",
    ErrorCode.RATE_LIMIT_EXCEEDED: "Too many requests. Please slow down.",
    ErrorCode.QUOTA_EXCEEDED: "You have reached your plan quota.",
    ErrorCode.PLAN_REQUIRED: "This feature requires a higher plan.",
    ErrorCode.UNAUTHENTICATED: "Please sign in to continue.",
    ErrorCode.FORBIDDEN: "You do not have access to this resource.",
    ErrorCode.INVALID_CREDENTIALS: "Incorrect email or password.",
    ErrorCode.EMAIL_NOT_VERIFIED: "Confirm your email address to continue.",
    ErrorCode.TWO_FACTOR_REQUIRED: "Enter your two-factor code to continue.",
    ErrorCode.TOKEN_INVALID: "This link is not valid.",
    ErrorCode.TOKEN_EXPIRED: "This link has expired.",
    ErrorCode.CSRF_FAILED: "Your session expired. Reload the page and try again.",
    ErrorCode.ACCOUNT_LOCKED: "This account is locked. Contact support.",
    ErrorCode.NOT_FOUND: "Not found.",
    ErrorCode.CONFLICT: "This conflicts with the current state.",
    ErrorCode.VERSION_CONFLICT: "Someone else changed this first. Reload to see the latest.",
    ErrorCode.VALIDATION_FAILED: "Some values are not valid.",
    ErrorCode.IDEMPOTENCY_CONFLICT: "This idempotency key was used with different parameters.",
    ErrorCode.MAINTENANCE: "We are performing maintenance. Please try again shortly.",
    ErrorCode.FEATURE_DISABLED: "This feature is not enabled.",
    ErrorCode.INTERNAL_ERROR: "Something went wrong on our side.",
}


# --------------------------------------------------------------------------- #
# Convenience subclasses — they read better at call sites than AppError(code=…)
# --------------------------------------------------------------------------- #
def _err(code: ErrorCode):
    def factory(message: str = "", **kwargs: Any) -> AppError:
        return AppError(code=code, message=message, **kwargs)

    return factory


NotFound = _err(ErrorCode.NOT_FOUND)
Forbidden = _err(ErrorCode.FORBIDDEN)
Unauthenticated = _err(ErrorCode.UNAUTHENTICATED)
ValidationFailed = _err(ErrorCode.VALIDATION_FAILED)
Conflict = _err(ErrorCode.CONFLICT)
InsufficientCredits = _err(ErrorCode.INSUFFICIENT_CREDITS)
ProviderUnavailable = _err(ErrorCode.PROVIDER_UNAVAILABLE)
