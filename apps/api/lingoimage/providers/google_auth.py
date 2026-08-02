"""Google service-account authentication (JWT bearer grant).

Implemented directly against the OAuth2 token endpoint so the project does not
need the full ``google-auth`` dependency tree in the worker image. Tokens are
cached in memory until shortly before they expire.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from lingoimage.core.config import settings
from lingoimage.core.errors import AppError, ErrorCode
from lingoimage.core.logging import get_logger

log = get_logger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"

_cache: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()


def load_credentials() -> dict[str, Any] | None:
    """``GOOGLE_VISION_CREDENTIALS_JSON`` is either inline JSON or a file path."""
    raw = settings.google_vision_credentials_json.strip()
    if not raw:
        return None
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AppError(
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                details={"provider": "google"},
                internal=f"GOOGLE_VISION_CREDENTIALS_JSON is not valid JSON: {exc}",
            ) from exc
    path = Path(raw)
    if not path.exists():
        raise AppError(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            details={"provider": "google"},
            internal=f"credentials file not found: {raw}",
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def access_token(scope: str = "https://www.googleapis.com/auth/cloud-platform") -> str:
    now = time.time()
    with _lock:
        cached = _cache.get(scope)
        if cached and cached[1] - 60 > now:
            return cached[0]

    credentials = load_credentials()
    if not credentials:
        raise AppError(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            details={"provider": "google"},
            internal="GOOGLE_VISION_CREDENTIALS_JSON is not set",
        )

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    issued = int(now)
    header = {"alg": "RS256", "typ": "JWT", "kid": credentials.get("private_key_id")}
    claims = {
        "iss": credentials["client_email"],
        "scope": scope,
        "aud": TOKEN_URL,
        "iat": issued,
        "exp": issued + 3600,
    }
    signing_input = (
        _b64(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64(json.dumps(claims, separators=(",", ":")).encode())
    ).encode("ascii")

    private_key = serialization.load_pem_private_key(
        credentials["private_key"].encode("utf-8"), password=None
    )
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())  # type: ignore[union-attr]
    assertion = signing_input.decode("ascii") + "." + _b64(signature)

    try:
        response = httpx.post(
            TOKEN_URL,
            data={"grant_type": _GRANT, "assertion": assertion},
            timeout=15.0,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise AppError(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            details={"provider": "google"},
            internal=f"token exchange failed: {exc}",
        ) from exc

    token = str(payload["access_token"])
    expires_at = now + float(payload.get("expires_in", 3600))
    with _lock:
        _cache[scope] = (token, expires_at)
    return token


def clear_cache() -> None:
    with _lock:
        _cache.clear()
