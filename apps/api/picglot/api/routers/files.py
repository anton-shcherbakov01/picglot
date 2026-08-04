"""Signed file access for the local storage backend.

With ``STORAGE_BACKEND=s3`` the browser talks to the object store directly
through presigned URLs and none of this is reached. The local backend has no
such server, so it hands out HMAC-signed tokens pointing here — and until this
router existed every one of those URLs answered 404, which meant a self-hosted
install with local storage produced pages, thumbnails and previews that no
client could load.

The token carries the key: it is signed and expiring, so possession of the URL
is the authorisation, exactly as it is for a presigned S3 URL.
"""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, Request, Response

from picglot.core.config import settings
from picglot.core.errors import AppError, ErrorCode
from picglot.core.security import unsign_payload
from picglot.services import storage

router = APIRouter(prefix="/api/v1/files", tags=["files"])

#: Signed URLs are minted with a per-URL ttl, but the token itself is validated
#: against one ceiling. A day is well past any ttl we issue and still bounds how
#: long a leaked URL stays useful.
MAX_TOKEN_AGE_SECONDS = 86_400


def _content_disposition(filename: str) -> str:
    from urllib.parse import quote

    return f"attachment; filename*=UTF-8''{quote(filename)}"


@router.get("/local/{token}")
def download(token: str, request: Request) -> Response:
    """Serve an object the local backend signed a URL for."""
    payload = unsign_payload(token, salt="local-download", max_age=MAX_TOKEN_AGE_SECONDS)
    key = payload.get("key")
    if not isinstance(key, str) or not key:
        raise AppError(code=ErrorCode.TOKEN_INVALID)

    backend = storage.get_storage()
    data = backend.get(key)

    filename = payload.get("filename")
    guessed, _ = mimetypes.guess_type(key)
    headers = {
        "Cache-Control": f"private, max-age={settings.signed_url_ttl_seconds}",
        # The editor draws these onto a canvas from the web origin, which is a
        # different host in every deployment.
        "Access-Control-Allow-Origin": request.headers.get("origin", "*"),
        "Vary": "Origin",
    }
    if isinstance(filename, str) and filename:
        headers["Content-Disposition"] = _content_disposition(filename)

    return Response(
        content=data,
        media_type=guessed or "application/octet-stream",
        headers=headers,
    )


@router.put("/local-upload/{token}")
async def upload(token: str, request: Request) -> dict[str, object]:
    """Accept a direct browser upload for a key the local backend signed."""
    payload = unsign_payload(token, salt="local-upload", max_age=MAX_TOKEN_AGE_SECONDS)
    key = payload.get("key")
    content_type = payload.get("content_type")
    max_bytes = payload.get("max_bytes")
    if not isinstance(key, str) or not key or not isinstance(max_bytes, int):
        raise AppError(code=ErrorCode.TOKEN_INVALID)

    body = await request.body()
    if not body:
        raise AppError(code=ErrorCode.VALIDATION_FAILED, details={"reason": "empty_body"})
    # The signed ceiling is the one that counts: a client-supplied
    # Content-Length would be trivial to understate.
    if len(body) > max_bytes:
        raise AppError(
            code=ErrorCode.FILE_TOO_LARGE,
            details={"limit_mb": round(max_bytes / 1_048_576, 2)},
        )

    stored = storage.get_storage().put(
        key,
        body,
        content_type=(
            content_type if isinstance(content_type, str) else "application/octet-stream"
        ),
    )
    return {"key": stored.key, "size": stored.byte_size, "checksum": stored.checksum}
