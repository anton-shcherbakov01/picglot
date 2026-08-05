"""Object storage.

Two backends behind one interface: S3-compatible (MinIO, AWS, R2, Yandex Object
Storage) and a local filesystem backend used by unit tests and single-machine
installs.

Rules enforced here rather than at call sites:
  * storage keys are random — a leaked key cannot be guessed from a filename;
  * nothing is ever public: downloads go through short-lived signed URLs;
  * every object gets a prefix that maps to a lifecycle rule.
"""

from __future__ import annotations

import hashlib
import io
import mimetypes
import os
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Any, Protocol

from picglot.core.config import settings
from picglot.core.errors import AppError, ErrorCode
from picglot.core.ids import ulid
from picglot.core.logging import get_logger

log = get_logger(__name__)

#: How long a reachability verdict is trusted. Long enough that signing a URL
#: is never a network round trip in practice, short enough that fixing DNS or a
#: certificate takes effect without restarting anything.
ENDPOINT_PROBE_TTL_SECONDS = 300
ENDPOINT_PROBE_TIMEOUT_SECONDS = 3.0

#: (checked_at, answers, detail). Module level so every worker process keeps its own.
_endpoint_probe: tuple[float, bool, str] | None = None
_probe_lock = threading.Lock()

#: Written and read back on every probe. At the bucket root, so none of the
#: lifecycle prefixes expire it, and overwritten rather than accumulated.
PROBE_KEY = ".reachability-probe"

#: Prefix -> lifecycle intent. Keep in sync with infra/docker/minio-init.sh.
PREFIX_ORIGINAL = "originals"
PREFIX_PREVIEW = "previews"
PREFIX_INTERMEDIATE = "intermediate"
PREFIX_EXPORT = "exports"
PREFIX_GUEST = "guest"
PREFIX_UPLOAD_TMP = "uploads/tmp"
PREFIX_PUBLIC_SAMPLE = "samples"


@dataclass(slots=True)
class StoredObject:
    key: str
    byte_size: int
    checksum: str
    mime_type: str


class StorageBackend(Protocol):
    def put(self, key: str, data: bytes | IO[bytes], *, content_type: str) -> StoredObject: ...
    def get(self, key: str) -> bytes: ...
    def open(self, key: str) -> IO[bytes]: ...
    def delete(self, key: str) -> bool: ...
    def delete_many(self, keys: list[str]) -> int: ...
    def exists(self, key: str) -> bool: ...
    def size(self, key: str) -> int: ...
    def signed_download_url(
        self, key: str, *, expires_in: int, filename: str | None, content_type: str | None
    ) -> str: ...
    def signed_upload_url(
        self, key: str, *, expires_in: int, content_type: str, max_bytes: int
    ) -> dict[str, Any]: ...
    def healthy(self) -> bool: ...


def build_key(prefix: str, *, project_id: str | None = None, extension: str = "bin") -> str:
    """``exports/prj_01J…/01J…-9f3c.png`` — random, unguessable, prefix-scoped."""
    extension = extension.lstrip(".").lower() or "bin"
    unique = f"{ulid()}-{os.urandom(4).hex()}"
    scope = project_id or "shared"
    return f"{prefix}/{scope}/{unique}.{extension}"


def checksum_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def api_download_url(key: str, *, filename: str | None = None, ttl: int | None = None) -> str:
    """A signed URL the API serves itself.

    Used whenever the object store is not reachable from a browser — always for
    the local backend, and for S3 when the public endpoint is missing or points
    somewhere only the cluster can resolve. Possession of the URL is the
    authorisation, exactly as with a presigned S3 URL, and it expires the same
    way.
    """
    from picglot.core.security import sign_payload

    payload = {
        "key": key,
        "filename": filename,
        "ttl": ttl or settings.signed_url_ttl_seconds,
    }
    signature = sign_payload(payload, salt="file-download")
    path = f"/api/v1/files/{signature}"
    # `PUBLIC_API_URL` defaults to localhost, and prefixing that would swap one
    # unopenable link for another — the exact failure this function exists to
    # avoid. A path resolves against whatever origin the page was served from,
    # which is by definition one the visitor can reach.
    if not settings.api_url_reachable_by_browser:
        return path
    return f"{settings.public_api_url.rstrip('/')}{path}"


# --------------------------------------------------------------------------- #
# S3
# --------------------------------------------------------------------------- #
class S3Storage:
    def __init__(self) -> None:
        self._client: Any = None
        self._public_client: Any = None
        self._lock = threading.Lock()

    def _make_client(self, endpoint: str) -> Any:
        import boto3
        from botocore.config import Config

        return boto3.client(
            "s3",
            endpoint_url=endpoint or None,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id or None,
            aws_secret_access_key=settings.s3_secret_access_key or None,
            use_ssl=settings.s3_use_ssl,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"},
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=5,
                read_timeout=60,
            ),
        )

    @property
    def client(self) -> Any:
        if self._client is None:
            with self._lock:
                if self._client is None:
                    self._client = self._make_client(settings.s3_endpoint_url)
        return self._client

    @property
    def public_client(self) -> Any:
        """Signs URLs against the browser-visible endpoint (differs in Docker)."""
        if settings.s3_browser_endpoint == settings.s3_endpoint_url.rstrip("/"):
            return self.client
        if self._public_client is None:
            with self._lock:
                if self._public_client is None:
                    self._public_client = self._make_client(settings.s3_browser_endpoint)
        return self._public_client

    def public_endpoint_answers(self) -> bool:
        """Can a visitor actually fetch an object through a presigned URL?

        A well-formed address proves nothing: `https://s3.example.com` looks
        perfect whether or not it has a DNS record, a certificate covering that
        name, or a vhost that reaches the store. Neither does *an* answer — a
        reverse proxy that never forwards the `Host` header reaches the store
        and answers every presigned request with 403, because the signature is
        recomputed over the wrong host. All of these fail the same silent way:
        the browser drops the image and reports nothing we can see.

        So this fetches, and only a fetch that returned the bytes counts.
        """
        return self.public_endpoint_verdict()[0]

    def public_endpoint_verdict(self) -> tuple[bool, str]:
        """``(usable, detail)`` — detail names the fault when it is not usable."""
        global _endpoint_probe
        now = time.monotonic()
        cached = _endpoint_probe
        if cached is not None and now - cached[0] < ENDPOINT_PROBE_TTL_SECONDS:
            return cached[1], cached[2]

        with _probe_lock:
            cached = _endpoint_probe
            if cached is not None and now - cached[0] < ENDPOINT_PROBE_TTL_SECONDS:
                return cached[1], cached[2]
            usable, detail = self._probe_public_endpoint()
            _endpoint_probe = (time.monotonic(), usable, detail)
            return usable, detail

    def _probe_public_endpoint(self) -> tuple[bool, str]:
        """Round-trip one object through the browser-visible address.

        The probe object is written first, through the in-cluster client, so
        every answer is unambiguous — which the previous "any status under 500"
        rule was not, since it accepted the one status that proves the thing we
        are testing for is broken:

        * **200** — DNS, TLS, routing *and* signing all work. Presigned URLs are
          safe to hand out.
        * **403** — the address is reached, but the store rejects our signature.
          A visitor's `<img>` gets exactly this and shows nothing. Usually the
          reverse proxy in front of the store does not pass the original `Host`
          on (SigV4 signs it), and otherwise a skewed clock, a region that
          disagrees with `S3_REGION`, or credentials that cannot read the
          bucket.
        * **404** — something answers, but not from the bucket the API just
          wrote to: the vhost points at a different store, or a different
          bucket.

        Certificates are verified exactly as a browser would, which is the whole
        point — an expired or mismatched certificate is invisible to `curl -k`
        and fatal to an `<img>`.
        """
        import httpx

        try:
            self.client.put_object(
                Bucket=settings.s3_bucket,
                Key=PROBE_KEY,
                Body=b"ok",
                ContentType="text/plain",
            )
            url = self.public_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.s3_bucket, "Key": PROBE_KEY},
                ExpiresIn=60,
            )
            response = httpx.get(url, timeout=ENDPOINT_PROBE_TIMEOUT_SECONDS)
        except Exception as exc:
            detail = f"no answer from {settings.s3_browser_endpoint} ({type(exc).__name__})"
            log.warning(
                "storage.public_endpoint_unreachable",
                endpoint=settings.s3_browser_endpoint,
                error=type(exc).__name__,
                fault=str(exc)[:200],
                effect="objects are streamed through the API instead",
            )
            return False, detail

        if response.is_success:
            return True, ""

        if response.status_code in (401, 403):
            detail = "presigned URLs are rejected by the store (403)"
            log.warning(
                "storage.presigned_urls_rejected",
                endpoint=settings.s3_browser_endpoint,
                status=response.status_code,
                fault=response.text[:200],
                cause="the proxy in front of the store most likely rewrites the Host "
                "header, which SigV4 signs; also check clock skew, S3_REGION and the "
                "credentials' read access to the bucket",
                effect="objects are streamed through the API instead",
            )
            return False, detail

        detail = f"the endpoint answered {response.status_code} for an object that exists"
        log.warning(
            "storage.public_endpoint_wrong_target",
            endpoint=settings.s3_browser_endpoint,
            status=response.status_code,
            bucket=settings.s3_bucket,
            cause="the vhost reaches a different store or bucket than the API writes to"
            if response.status_code == 404
            else "the store itself is failing",
            effect="objects are streamed through the API instead",
        )
        return False, detail

    def _extra_args(self, content_type: str) -> dict[str, Any]:
        extra: dict[str, Any] = {"ContentType": content_type}
        if settings.s3_server_side_encryption:
            extra["ServerSideEncryption"] = settings.s3_server_side_encryption
        return extra

    def put(self, key: str, data: bytes | IO[bytes], *, content_type: str) -> StoredObject:
        payload = data if isinstance(data, bytes) else data.read()
        try:
            self.client.put_object(
                Bucket=settings.s3_bucket,
                Key=key,
                Body=payload,
                **self._extra_args(content_type),
            )
        except Exception as exc:
            log.error("storage.put_failed", key_prefix=key.split("/")[0], error=type(exc).__name__)
            raise AppError(code=ErrorCode.STORAGE_FAILED, internal=str(exc)) from exc
        return StoredObject(
            key=key,
            byte_size=len(payload),
            checksum=checksum_bytes(payload),
            mime_type=content_type,
        )

    def get(self, key: str) -> bytes:
        try:
            response = self.client.get_object(Bucket=settings.s3_bucket, Key=key)
            return response["Body"].read()
        except Exception as exc:
            if _is_not_found(exc):
                raise AppError(code=ErrorCode.NOT_FOUND, internal=f"missing object {key}") from exc
            raise AppError(code=ErrorCode.STORAGE_FAILED, internal=str(exc)) from exc

    def open(self, key: str) -> IO[bytes]:
        return io.BytesIO(self.get(key))

    def delete(self, key: str) -> bool:
        try:
            self.client.delete_object(Bucket=settings.s3_bucket, Key=key)
            return True
        except Exception as exc:  # pragma: no cover
            log.warning("storage.delete_failed", error=type(exc).__name__)
            return False

    def delete_many(self, keys: list[str]) -> int:
        removed = 0
        for chunk_start in range(0, len(keys), 1000):
            chunk = keys[chunk_start : chunk_start + 1000]
            try:
                response = self.client.delete_objects(
                    Bucket=settings.s3_bucket,
                    Delete={"Objects": [{"Key": key} for key in chunk], "Quiet": True},
                )
                removed += len(chunk) - len(response.get("Errors", []))
            except Exception as exc:  # pragma: no cover
                log.warning("storage.bulk_delete_failed", error=type(exc).__name__)
        return removed

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=settings.s3_bucket, Key=key)
            return True
        except Exception:
            return False

    def size(self, key: str) -> int:
        try:
            return int(self.client.head_object(Bucket=settings.s3_bucket, Key=key)["ContentLength"])
        except Exception as exc:
            raise AppError(code=ErrorCode.NOT_FOUND, internal=str(exc)) from exc

    def signed_download_url(
        self,
        key: str,
        *,
        expires_in: int | None = None,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str:
        # A presigned URL is signed against a host. If that host is one only the
        # cluster can resolve, or one that does not answer, the URL is dead on
        # arrival in a browser — so serve the bytes rather than hand out a link
        # that cannot be opened.
        if settings.serve_files_through_api or not self.public_endpoint_answers():
            return api_download_url(key, filename=filename, ttl=expires_in)

        params: dict[str, Any] = {"Bucket": settings.s3_bucket, "Key": key}
        if filename:
            params["ResponseContentDisposition"] = _content_disposition(filename)
        if content_type:
            params["ResponseContentType"] = content_type
        try:
            return self.public_client.generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=expires_in or settings.signed_url_ttl_seconds,
            )
        except Exception as exc:
            raise AppError(code=ErrorCode.STORAGE_FAILED, internal=str(exc)) from exc

    def signed_upload_url(
        self,
        key: str,
        *,
        expires_in: int | None = None,
        content_type: str,
        max_bytes: int,
    ) -> dict[str, Any]:
        """Presigned POST so the browser uploads straight to storage."""
        conditions: list[Any] = [
            {"Content-Type": content_type},
            ["content-length-range", 1, max_bytes],
        ]
        try:
            post = self.public_client.generate_presigned_post(
                Bucket=settings.s3_bucket,
                Key=key,
                Fields={"Content-Type": content_type},
                Conditions=conditions,
                ExpiresIn=expires_in or settings.signed_url_ttl_seconds,
            )
        except Exception as exc:
            raise AppError(code=ErrorCode.STORAGE_FAILED, internal=str(exc)) from exc
        return {"method": "POST", "url": post["url"], "fields": post["fields"], "key": key}

    def healthy(self) -> bool:
        try:
            self.client.head_bucket(Bucket=settings.s3_bucket)
            return True
        except Exception as exc:  # pragma: no cover
            log.warning("storage.health_failed", error=type(exc).__name__)
            return False

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=settings.s3_bucket)
        except Exception:
            try:
                self.client.create_bucket(Bucket=settings.s3_bucket)
                log.info("storage.bucket_created", bucket=settings.s3_bucket)
            except Exception as exc:  # pragma: no cover
                log.error("storage.bucket_create_failed", error=type(exc).__name__)
                raise AppError(code=ErrorCode.STORAGE_FAILED, internal=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Local filesystem
# --------------------------------------------------------------------------- #
class LocalStorage:
    """Filesystem backend. Signed URLs are HMAC tokens served by the API."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.storage_root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Defence in depth against traversal in a key built from user input.
        candidate = (self.root / key).resolve()
        if not str(candidate).startswith(str(self.root.resolve())):
            raise AppError(code=ErrorCode.FORBIDDEN, internal=f"path traversal attempt: {key}")
        return candidate

    def put(self, key: str, data: bytes | IO[bytes], *, content_type: str) -> StoredObject:
        payload = data if isinstance(data, bytes) else data.read()
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".part")
        temporary.write_bytes(payload)
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:  # pragma: no cover - Windows
            pass
        return StoredObject(key, len(payload), checksum_bytes(payload), content_type)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise AppError(code=ErrorCode.NOT_FOUND, internal=f"missing object {key}")
        return path.read_bytes()

    def open(self, key: str) -> IO[bytes]:
        return io.BytesIO(self.get(key))

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if path.exists():
            path.unlink()
            return True
        return False

    def delete_many(self, keys: list[str]) -> int:
        return sum(1 for key in keys if self.delete(key))

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def size(self, key: str) -> int:
        path = self._path(key)
        if not path.exists():
            raise AppError(code=ErrorCode.NOT_FOUND)
        return path.stat().st_size

    def signed_download_url(
        self,
        key: str,
        *,
        expires_in: int | None = None,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str:
        return api_download_url(key, filename=filename, ttl=expires_in)

    def signed_upload_url(
        self,
        key: str,
        *,
        expires_in: int | None = None,
        content_type: str,
        max_bytes: int,
    ) -> dict[str, Any]:
        from picglot.core.security import sign_payload

        signature = sign_payload(
            {"key": key, "content_type": content_type, "max_bytes": max_bytes},
            salt="local-upload",
        )
        return {
            "method": "PUT",
            "url": f"{settings.public_api_url.rstrip('/')}/api/v1/files/upload/{signature}",
            "fields": {},
            "key": key,
        }

    def healthy(self) -> bool:
        try:
            probe = self.root / ".health"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return True
        except Exception:  # pragma: no cover
            return False

    def ensure_bucket(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def used_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file())

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.root).free


_backend: Any = None
_backend_lock = threading.Lock()


def get_storage() -> Any:
    global _backend
    if _backend is None:
        with _backend_lock:
            if _backend is None:
                _backend = S3Storage() if settings.storage_backend == "s3" else LocalStorage()
    return _backend


def reset_storage() -> None:
    global _backend
    with _backend_lock:
        _backend = None


def guess_content_type(filename: str, fallback: str = "application/octet-stream") -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or fallback


def _content_disposition(filename: str) -> str:
    """RFC 5987 encoding so non-ASCII filenames survive the round trip."""
    from urllib.parse import quote

    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "download"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def _is_not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    code = response.get("Error", {}).get("Code")
    return code in {"NoSuchKey", "404", "NotFound"}


def expiry_for(hours: int) -> datetime:
    return datetime.now(UTC) + timedelta(hours=hours)
