"""Upload validation.

Extension is a hint, never a decision. Every upload is identified by its magic
bytes, checked against decompression-bomb limits, scanned when ClamAV is
configured, and sanitised (PDFs) before anything else touches it.
"""

from __future__ import annotations

import re
import socket
import unicodedata
import zlib
from dataclasses import dataclass
from typing import Any

from picglot.core.config import settings
from picglot.core.errors import AppError, ErrorCode
from picglot.core.logging import get_logger

log = get_logger(__name__)

#: magic prefix -> (mime, canonical extension)
MAGIC_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"GIF87a", "image/gif", "gif"),
    (b"GIF89a", "image/gif", "gif"),
    (b"BM", "image/bmp", "bmp"),
    (b"II*\x00", "image/tiff", "tiff"),
    (b"MM\x00*", "image/tiff", "tiff"),
    (b"%PDF-", "application/pdf", "pdf"),
)

SUPPORTED_INPUT_EXTENSIONS = frozenset(
    {"jpg", "jpeg", "png", "webp", "heic", "heif", "tiff", "tif", "bmp", "gif", "pdf"}
)

#: Anything here is refused outright regardless of what the bytes claim.
DANGEROUS_EXTENSIONS = frozenset(
    {
        "exe",
        "dll",
        "so",
        "dylib",
        "bat",
        "cmd",
        "com",
        "scr",
        "msi",
        "ps1",
        "sh",
        "bash",
        "jar",
        "app",
        "deb",
        "rpm",
        "js",
        "mjs",
        "vbs",
        "php",
        "py",
        "rb",
        "pl",
        "html",
        "htm",
        "svg",
        "xhtml",
        "xml",
        "swf",
    }
)

_UNSAFE_NAME = re.compile(r"[^\w\-. ]", re.UNICODE)
_MAX_FILENAME = 180


@dataclass(slots=True)
class FileIdentity:
    mime_type: str
    extension: str
    byte_size: int
    is_pdf: bool
    is_image: bool
    detail: dict[str, Any]


def safe_filename(raw: str | None, *, fallback: str = "upload") -> str:
    """Normalise a user filename for storage and Content-Disposition.

    Defends against path traversal, NTFS alternate data streams, RTL-override
    spoofing and absurd lengths — none of which should ever reach the filesystem.
    """
    if not raw:
        return fallback
    name = unicodedata.normalize("NFC", raw)
    # Strip directory components from every convention.
    name = name.replace("\\", "/").split("/")[-1]
    name = name.split(":")[-1] if ":" in name else name
    # Remove bidi overrides used to disguise an extension.
    name = "".join(char for char in name if unicodedata.category(char) != "Cf")
    name = _UNSAFE_NAME.sub("_", name).strip(" .")
    if not name or name in {".", ".."}:
        return fallback
    if len(name) > _MAX_FILENAME:
        stem, _, extension = name.rpartition(".")
        keep = _MAX_FILENAME - len(extension) - 1
        name = f"{stem[:keep]}.{extension}" if extension else name[:_MAX_FILENAME]
    return name


def extension_of(filename: str | None) -> str:
    if not filename or "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def identify(data: bytes, *, filename: str | None = None) -> FileIdentity:
    """Determine the true type from content. Raises on anything unsupported."""
    if not data:
        raise AppError(code=ErrorCode.CORRUPTED_FILE, internal="empty upload")

    claimed = extension_of(filename)
    if claimed in DANGEROUS_EXTENSIONS:
        raise AppError(
            code=ErrorCode.UNSUPPORTED_FILE_TYPE,
            details={"extension": claimed},
            internal=f"refused dangerous extension {claimed!r}",
        )

    header = data[:32]
    for signature, mime, extension in MAGIC_SIGNATURES:
        if header.startswith(signature):
            return _finalise(data, mime, extension, claimed)

    # Container formats need a look past the first bytes.
    if len(data) >= 12:
        if header[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return _finalise(data, "image/webp", "webp", claimed)
        if data[4:8] == b"ftyp":
            brand = data[8:12]
            if brand in {b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1"}:
                return _finalise(data, "image/heic", "heic", claimed)
            if brand in {b"avif", b"avis"}:
                return _finalise(data, "image/avif", "avif", claimed)

    looks_like_svg = (
        header.lstrip()[:5].lower() in {b"<?xml", b"<svg "} or b"<svg" in data[:512].lower()
    )
    if looks_like_svg and not settings.allow_svg_upload:
        raise AppError(
            code=ErrorCode.UNSUPPORTED_FILE_TYPE,
            details={"extension": "svg"},
            internal="SVG upload is disabled (ALLOW_SVG_UPLOAD=false)",
        )

    raise AppError(
        code=ErrorCode.UNSUPPORTED_FILE_TYPE,
        details={"claimed_extension": claimed or None},
        internal=f"unrecognised magic bytes: {header[:8]!r}",
    )


def _finalise(data: bytes, mime: str, extension: str, claimed: str) -> FileIdentity:
    is_pdf = extension == "pdf"
    if extension not in SUPPORTED_INPUT_EXTENSIONS and extension != "avif":
        raise AppError(code=ErrorCode.UNSUPPORTED_FILE_TYPE, details={"detected": extension})

    detail: dict[str, Any] = {}
    if claimed and claimed not in {extension, "jpeg" if extension == "jpg" else extension}:
        # Not fatal, but recorded: a mismatch is a mild abuse signal.
        detail["extension_mismatch"] = {"claimed": claimed, "detected": extension}
        log.info("upload.extension_mismatch", claimed=claimed, detected=extension)

    if is_pdf:
        _guard_pdf_bomb(data)
    else:
        _guard_image_bomb(data, extension)

    return FileIdentity(
        mime_type=mime,
        extension=extension,
        byte_size=len(data),
        is_pdf=is_pdf,
        is_image=not is_pdf,
        detail=detail,
    )


def _guard_image_bomb(data: bytes, extension: str) -> None:
    """Reject images whose declared dimensions exceed the pixel budget.

    Checked from the header, before any decoder allocates memory.
    """
    from PIL import Image

    try:
        import io

        with Image.open(io.BytesIO(data)) as probe:
            width, height = probe.size
    except Exception as exc:
        raise AppError(code=ErrorCode.CORRUPTED_FILE, internal=str(exc)[:200]) from exc

    pixels = width * height
    if pixels > settings.max_image_pixels:
        raise AppError(
            code=ErrorCode.IMAGE_TOO_LARGE,
            details={
                "width": width,
                "height": height,
                "pixels": pixels,
                "limit": settings.max_image_pixels,
            },
        )
    # A tiny file that expands enormously is the classic decompression bomb.
    if pixels > 4_000_000 and len(data) > 0 and pixels / len(data) > 2000:
        log.warning(
            "upload.suspicious_compression_ratio",
            extension=extension,
            ratio=round(pixels / len(data), 1),
        )
        raise AppError(
            code=ErrorCode.MALICIOUS_FILE,
            details={"reason": "compression_ratio"},
            internal=f"pixels/byte = {pixels / len(data):.0f}",
        )


def _guard_pdf_bomb(data: bytes) -> None:
    """Reject PDFs with absurd object counts or stream expansion ratios."""
    if data.count(b"/ObjStm") > 5000 or data.count(b" obj") > 200_000:
        raise AppError(
            code=ErrorCode.MALICIOUS_FILE,
            details={"reason": "object_count"},
            internal="PDF contains an implausible number of objects",
        )
    # Sample the first few streams: a 1 KB stream expanding to 100 MB is hostile.
    total_inflated = 0
    for match in re.finditer(rb"stream\r?\n", data[:4_000_000]):
        start = match.end()
        end = data.find(b"endstream", start)
        if end == -1:
            break
        chunk = data[start:end]
        if len(chunk) < 64:
            continue
        try:
            inflated = zlib.decompressobj().decompress(chunk, 50_000_000)
        except Exception:
            continue
        total_inflated += len(inflated)
        if len(inflated) > 40_000_000 or total_inflated > 200_000_000:
            raise AppError(
                code=ErrorCode.MALICIOUS_FILE,
                details={"reason": "stream_expansion"},
                internal=f"stream inflated to {len(inflated)} bytes",
            )


def enforce_size(byte_size: int, plan_code: str | None) -> None:
    limit = settings.upload_limit_bytes(plan_code)
    if byte_size > limit:
        raise AppError(
            code=ErrorCode.FILE_TOO_LARGE,
            details={
                "byte_size": byte_size,
                "limit_bytes": limit,
                "limit_mb": round(limit / 1024 / 1024, 1),
                "plan": plan_code or "guest",
            },
        )


def enforce_pages(page_count: int, plan_code: str | None) -> None:
    limit = settings.page_limit(plan_code)
    if page_count > limit:
        raise AppError(
            code=ErrorCode.PAGE_LIMIT_EXCEEDED,
            details={"pages": page_count, "limit": limit, "plan": plan_code or "guest"},
        )


def scan_for_malware(data: bytes) -> None:
    """ClamAV INSTREAM scan. No-op unless ``ANTIVIRUS_ENABLED`` is set."""
    if not settings.antivirus_enabled:
        return
    try:
        with socket.create_connection(
            (settings.clamav_host, settings.clamav_port), timeout=20
        ) as sock:
            sock.sendall(b"zINSTREAM\0")
            view = memoryview(data)
            for offset in range(0, len(data), 65536):
                chunk = view[offset : offset + 65536]
                sock.sendall(len(chunk).to_bytes(4, "big") + bytes(chunk))
            sock.sendall((0).to_bytes(4, "big"))
            response = sock.recv(4096).decode("utf-8", "replace")
    except OSError as exc:
        # Scanner down: fail closed only in production, where it is mandatory.
        log.error("antivirus.unreachable", error=type(exc).__name__)
        if settings.is_production:
            raise AppError(
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                details={"provider": "antivirus"},
                internal=str(exc)[:200],
            ) from exc
        return

    if "FOUND" in response:
        signature = response.split(":")[-1].strip()
        log.warning("antivirus.detected", signature=signature[:80])
        raise AppError(
            code=ErrorCode.MALICIOUS_FILE,
            details={"scanner": "clamav"},
            internal=f"clamav: {signature}",
        )


def prepare_upload(
    data: bytes, *, filename: str | None, plan_code: str | None
) -> tuple[bytes, FileIdentity]:
    """The single entry point every upload path must use."""
    enforce_size(len(data), plan_code)
    identity = identify(data, filename=filename)
    scan_for_malware(data)

    if identity.is_pdf:
        from picglot.vision import pdf as pdf_tools

        data = pdf_tools.sanitize(data)
        pages = pdf_tools.page_count(data)
        enforce_pages(pages, plan_code)
        identity.detail["page_count"] = pages
    else:
        identity.detail["page_count"] = 1

    return data, identity


def page_count_for(data: bytes, identity: FileIdentity) -> int:
    if identity.is_pdf:
        from picglot.vision import pdf as pdf_tools

        return pdf_tools.page_count(data)
    return 1
