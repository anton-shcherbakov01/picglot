"""Password hashing, opaque token handling, signing and HMAC helpers.

Design notes
------------
* Passwords use Argon2id (memory-hard) with parameters from configuration.
* Session tokens, share tokens and API keys are random secrets; only a SHA-256
  hash is persisted, so a database dump cannot be replayed against the service.
* Every comparison of a secret uses ``hmac.compare_digest``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from dataclasses import dataclass
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from lingoimage.core.config import settings
from lingoimage.core.errors import AppError, ErrorCode
from lingoimage.core.ids import token as random_token

_hasher = PasswordHasher(
    time_cost=settings.argon2_time_cost,
    memory_cost=settings.argon2_memory_cost,
    parallelism=settings.argon2_parallelism,
    hash_len=32,
    salt_len=16,
)

# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "12345678",
        "123456789",
        "qwerty123",
        "password1",
        "iloveyou",
        "admin123",
        "welcome1",
        "letmein1",
        "1q2w3e4r",
        "qwertyuiop",
    }
)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        # Spend comparable time so that "user does not exist" is not detectable.
        _hasher.hash(password)
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


@dataclass(slots=True)
class PasswordCheck:
    ok: bool
    reason: str | None = None


def validate_password_strength(password: str, *, email: str | None = None) -> PasswordCheck:
    if len(password) < settings.password_min_length:
        return PasswordCheck(False, f"at_least_{settings.password_min_length}_characters")
    if len(password) > 256:
        return PasswordCheck(False, "too_long")
    lowered = password.lower()
    if lowered in _COMMON_PASSWORDS:
        return PasswordCheck(False, "too_common")
    if email and email.split("@")[0].lower() in lowered and len(email.split("@")[0]) >= 4:
        return PasswordCheck(False, "contains_email")
    classes = sum(
        bool(pattern.search(password))
        for pattern in (
            re.compile(r"[a-z]"),
            re.compile(r"[A-Z]"),
            re.compile(r"\d"),
            re.compile(r"[^\w\s]"),
        )
    )
    if classes < 3:
        return PasswordCheck(False, "needs_more_character_classes")
    return PasswordCheck(True)


# --------------------------------------------------------------------------- #
# Opaque tokens (sessions, share links, API keys, verification links)
# --------------------------------------------------------------------------- #
def hash_token(value: str) -> str:
    """Deterministic, keyed hash — a stolen database cannot replay tokens."""
    return hmac.new(
        settings.secret_key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_token_hash(value: str, stored_hash: str) -> bool:
    for key in settings.signing_keys():
        candidate = hmac.new(key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()
        if hmac.compare_digest(candidate, stored_hash):
            return True
    return False


API_KEY_PREFIX_LIVE = "lik_live_"
API_KEY_PREFIX_TEST = "lik_test_"


@dataclass(slots=True)
class GeneratedApiKey:
    plaintext: str
    hashed: str
    prefix: str
    last_four: str


def generate_api_key(test_mode: bool = False) -> GeneratedApiKey:
    prefix = API_KEY_PREFIX_TEST if test_mode else API_KEY_PREFIX_LIVE
    secret = random_token(32)
    plaintext = f"{prefix}{secret}"
    return GeneratedApiKey(
        plaintext=plaintext,
        hashed=hash_token(plaintext),
        prefix=prefix,
        last_four=secret[-4:],
    )


def generate_session_token() -> tuple[str, str]:
    value = random_token(32)
    return value, hash_token(value)


def hash_ip(ip: str | None) -> str | None:
    """Store a keyed hash of the IP: enough to spot abuse, not a raw identifier."""
    if not ip:
        return None
    return hmac.new(
        settings.secret_key.encode("utf-8"), ip.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]


# --------------------------------------------------------------------------- #
# Signed, expiring payloads (email verification, magic links, resets)
# --------------------------------------------------------------------------- #
def _serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt=salt)


def sign_payload(payload: dict[str, Any], *, salt: str) -> str:
    return _serializer(salt).dumps(payload)


def unsign_payload(value: str, *, salt: str, max_age: int) -> dict[str, Any]:
    last_error: Exception | None = None
    for key in settings.signing_keys():
        serializer = URLSafeTimedSerializer(key, salt=salt)
        try:
            data = serializer.loads(value, max_age=max_age)
            if not isinstance(data, dict):
                raise AppError(code=ErrorCode.TOKEN_INVALID)
            return data
        except SignatureExpired as exc:
            raise AppError(code=ErrorCode.TOKEN_EXPIRED) from exc
        except BadSignature as exc:
            last_error = exc
            continue
    raise AppError(code=ErrorCode.TOKEN_INVALID, internal=str(last_error))


# --------------------------------------------------------------------------- #
# Webhook signatures (outbound to customers, inbound from payment providers)
# --------------------------------------------------------------------------- #
WEBHOOK_TOLERANCE_SECONDS = 300


def sign_webhook(secret: str, timestamp: int, body: bytes) -> str:
    signed = f"{timestamp}.".encode() + body
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def verify_webhook_signature(
    secret: str,
    header: str | None,
    body: bytes,
    *,
    tolerance: int = WEBHOOK_TOLERANCE_SECONDS,
    now: int | None = None,
) -> bool:
    """Verify a ``t=…,v1=…`` signature header, rejecting stale timestamps."""
    if not header or not secret:
        return False
    parts = dict(piece.split("=", 1) for piece in header.split(",") if "=" in piece)
    try:
        timestamp = int(parts.get("t", ""))
    except ValueError:
        return False
    signature = parts.get("v1", "")
    if not signature:
        return False
    current = int(time.time()) if now is None else now
    if abs(current - timestamp) > tolerance:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


# --------------------------------------------------------------------------- #
# Provider secrets at rest
# --------------------------------------------------------------------------- #
def encrypt_secret(plaintext: str) -> str:
    """Envelope-encrypt a provider credential with a key derived from SECRET_KEY.

    Uses AES-GCM via ``cryptography``; the nonce is prepended to the ciphertext.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = hashlib.sha256(("provider-secrets:" + settings.secret_key).encode()).digest()
    nonce = hashlib.sha256(random_token(16).encode()).digest()[:12]
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    raw = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
    key = hashlib.sha256(("provider-secrets:" + settings.secret_key).encode()).digest()
    return AESGCM(key).decrypt(raw[:12], raw[12:], None).decode("utf-8")


def mask_secret(value: str | None, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep:
        return "•" * len(value)
    return "•" * (len(value) - keep) + value[-keep:]
