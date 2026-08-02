"""PDF input and output.

Reading
-------
A PDF page may already carry a text layer. When it does we use it directly —
that is both faster and *more accurate* than rasterising and re-OCRing. Pages
without usable text are rendered to images and sent through OCR. Mixed
documents get a per-page decision.

Writing
-------
``searchable_pdf`` overlays invisible (render mode 3) text on the page image so
the visual result is byte-identical to the scan while the text is selectable
and indexable. Coordinates are mapped from image pixels to PDF points.

Memory
------
Pages are streamed one at a time; a 300-page scan never loads whole into RAM.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import fitz  # PyMuPDF
from PIL import Image

from lingoimage.core.config import settings
from lingoimage.core.errors import AppError, ErrorCode
from lingoimage.core.logging import get_logger
from lingoimage.vision.types import BoundingBox, Region

log = get_logger(__name__)

#: 200 dpi is the accuracy/размер sweet spot for OCR of printed text.
DEFAULT_DPI = 200
POINTS_PER_INCH = 72.0
#: Below this many characters a "text layer" is decoration, not content.
MIN_TEXT_LAYER_CHARS = 40


@dataclass(slots=True)
class PdfPageInfo:
    number: int
    width_points: float
    height_points: float
    rotation: int
    has_text_layer: bool
    text_char_count: int
    image_count: int


@dataclass(slots=True)
class PdfInfo:
    page_count: int
    encrypted: bool
    pages: list[PdfPageInfo] = field(default_factory=list)
    title: str | None = None
    producer: str | None = None

    @property
    def is_scanned(self) -> bool:
        return not any(page.has_text_layer for page in self.pages)

    @property
    def is_mixed(self) -> bool:
        with_text = sum(1 for page in self.pages if page.has_text_layer)
        return 0 < with_text < len(self.pages)


def _open(data: bytes) -> fitz.Document:
    try:
        document = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise AppError(code=ErrorCode.CORRUPTED_FILE, internal=str(exc)[:200]) from exc
    if document.needs_pass:
        document.close()
        raise AppError(code=ErrorCode.ENCRYPTED_PDF)
    return document


def inspect(data: bytes) -> PdfInfo:
    document = _open(data)
    try:
        pages: list[PdfPageInfo] = []
        for index in range(document.page_count):
            page = document.load_page(index)
            text = page.get_text("text") or ""
            char_count = len(text.strip())
            pages.append(
                PdfPageInfo(
                    number=index + 1,
                    width_points=page.rect.width,
                    height_points=page.rect.height,
                    rotation=page.rotation,
                    has_text_layer=char_count >= MIN_TEXT_LAYER_CHARS,
                    text_char_count=char_count,
                    image_count=len(page.get_images(full=False)),
                )
            )
        metadata = document.metadata or {}
        return PdfInfo(
            page_count=document.page_count,
            encrypted=False,
            pages=pages,
            title=metadata.get("title") or None,
            producer=metadata.get("producer") or None,
        )
    finally:
        document.close()


def page_count(data: bytes) -> int:
    document = _open(data)
    try:
        return document.page_count
    finally:
        document.close()


def render_pages(
    data: bytes,
    *,
    pages: list[int] | None = None,
    dpi: int = DEFAULT_DPI,
    max_dimension: int | None = None,
) -> Iterator[tuple[int, Image.Image]]:
    """Yield ``(page_number, image)`` one page at a time."""
    document = _open(data)
    limit = max_dimension or settings.processing_max_dimension
    try:
        wanted = pages or list(range(1, document.page_count + 1))
        for number in wanted:
            if not 1 <= number <= document.page_count:
                continue
            page = document.load_page(number - 1)
            scale = dpi / POINTS_PER_INCH
            longest = max(page.rect.width, page.rect.height) * scale
            if longest > limit:
                scale *= limit / longest
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            yield number, image
            del pixmap
    finally:
        document.close()


def extract_text_regions(data: bytes, page_number: int, *, dpi: int = DEFAULT_DPI) -> list[Region]:
    """Read the existing text layer as regions in *image pixel* coordinates."""
    from lingoimage.providers.ocr.base import make_region

    document = _open(data)
    try:
        if not 1 <= page_number <= document.page_count:
            return []
        page = document.load_page(page_number - 1)
        scale = dpi / POINTS_PER_INCH
        longest = max(page.rect.width, page.rect.height) * scale
        if longest > settings.processing_max_dimension:
            scale *= settings.processing_max_dimension / longest

        regions: list[Region] = []
        blocks = page.get_text("dict").get("blocks", [])
        for block in blocks:
            for line in block.get("lines", []):
                text = "".join(span.get("text", "") for span in line.get("spans", []))
                if not text.strip():
                    continue
                x0, y0, x1, y1 = line.get("bbox", (0, 0, 0, 0))
                polygon = [
                    (x0 * scale, y0 * scale),
                    (x1 * scale, y0 * scale),
                    (x1 * scale, y1 * scale),
                    (x0 * scale, y1 * scale),
                ]
                spans = line.get("spans", [])
                first = spans[0] if spans else {}
                region = make_region(
                    polygon,
                    text,
                    1.0,  # an embedded text layer is exact, not a guess
                    metadata={
                        "engine": "pdf_text_layer",
                        "font": first.get("font"),
                        "size_points": first.get("size"),
                    },
                )
                region.style.font_size = float(first.get("size", 12)) * scale
                flags = int(first.get("flags", 0))
                region.style.bold = bool(flags & 2**4)
                region.style.italic = bool(flags & 2**1)
                colour = first.get("color")
                if isinstance(colour, int):
                    region.style.color = f"#{colour & 0xFFFFFF:06X}"
                regions.append(region)
        return regions
    finally:
        document.close()


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #
def images_to_pdf(
    images: list[Image.Image],
    *,
    quality: int = 88,
    dpi: int = DEFAULT_DPI,
    metadata: dict[str, str] | None = None,
) -> bytes:
    """Plain image PDF, one page per image, at the original aspect ratio."""
    document = fitz.open()
    try:
        for image in images:
            width_points = image.width * POINTS_PER_INCH / dpi
            height_points = image.height * POINTS_PER_INCH / dpi
            page = document.new_page(width=width_points, height=height_points)
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, "JPEG", quality=quality, optimize=True)
            page.insert_image(page.rect, stream=buffer.getvalue())
        if metadata:
            document.set_metadata(metadata)
        return document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()


def searchable_pdf(
    pages: list[tuple[Image.Image, list[Region]]],
    *,
    dpi: int = DEFAULT_DPI,
    quality: int = 88,
    metadata: dict[str, str] | None = None,
    language: str | None = None,
) -> bytes:
    """Image pages plus an invisible, positioned text layer."""
    document = fitz.open()
    try:
        for image, regions in pages:
            scale = POINTS_PER_INCH / dpi
            width_points = image.width * scale
            height_points = image.height * scale
            page = document.new_page(width=width_points, height=height_points)

            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, "JPEG", quality=quality, optimize=True)
            page.insert_image(page.rect, stream=buffer.getvalue())

            for region in regions:
                text = region.effective_text.strip()
                if not text:
                    continue
                box = region.bounding_box
                rect = fitz.Rect(
                    box.x * scale,
                    box.y * scale,
                    box.right * scale,
                    box.bottom * scale,
                )
                if rect.is_empty or rect.width <= 1 or rect.height <= 1:
                    continue
                _insert_invisible_text(page, rect, text)

        meta = {
            "producer": f"{settings.brand_name}",
            "creator": settings.brand_name,
        }
        if language:
            meta["keywords"] = f"ocr,{language}"
        meta.update(metadata or {})
        document.set_metadata(meta)
        return document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()


def _insert_invisible_text(page: fitz.Page, rect: fitz.Rect, text: str) -> None:
    """Place text invisibly (render mode 3) so the page stays selectable.

    ``insert_textbox`` returns a negative number when the string does not fit,
    and — importantly — writes nothing in that case. We shrink until it fits,
    then fall back to a single baseline insert so a line is never silently
    dropped from the text layer.
    """
    fontname, fontfile = _pdf_font_for(text)
    size = max(2.0, min(rect.height * 0.85, rect.width / max(len(text) * 0.42, 1)))

    for _ in range(6):
        try:
            remaining = page.insert_textbox(
                rect,
                text,
                fontsize=size,
                fontname=fontname,
                fontfile=fontfile,
                render_mode=3,
                align=fitz.TEXT_ALIGN_LEFT,
            )
        except Exception:  # pragma: no cover - unusual glyph/font combinations
            remaining = -1
        if remaining >= 0:
            return
        size *= 0.7
        if size < 1.5:
            break

    try:
        page.insert_text(
            fitz.Point(rect.x0, rect.y1 - max(1.0, rect.height * 0.2)),
            text,
            fontsize=max(1.5, min(rect.height * 0.8, 10.0)),
            fontname=fontname,
            fontfile=fontfile,
            render_mode=3,
        )
    except Exception as exc:  # pragma: no cover
        log.warning("pdf.invisible_text_failed", error=type(exc).__name__)


def _pdf_font_for(text: str) -> tuple[str, str | None]:
    """Pick a PDF font that can encode ``text``.

    The built-in ``helv`` only covers Latin-1, so Cyrillic, Greek, CJK and
    friends would be dropped from the text layer. For those we embed a real
    font file discovered by the font registry.
    """
    if all(ord(char) < 0x100 for char in text):
        return "helv", None

    from lingoimage.domain.languages import detect_script
    from lingoimage.vision.fonts import registry

    file = registry.find(script=detect_script(text), text=text[:120])
    if file is None:
        return "helv", None
    # A stable name per family lets PyMuPDF reuse the embedded font object.
    return f"ocr-{file.normalized_family[:24]}", str(file.path)


def bilingual_pdf(
    pages: list[tuple[Image.Image, Image.Image]],
    *,
    dpi: int = DEFAULT_DPI,
    layout: str = "sequential",
    quality: int = 88,
) -> bytes:
    """``sequential``: original page then translated page. ``side_by_side``: both on one page."""
    document = fitz.open()
    try:
        for original, translated in pages:
            if layout == "side_by_side":
                width_points = (original.width + translated.width) * POINTS_PER_INCH / dpi
                height_points = max(original.height, translated.height) * POINTS_PER_INCH / dpi
                page = document.new_page(width=width_points, height=height_points)
                half = width_points * original.width / (original.width + translated.width)
                _place(page, original, fitz.Rect(0, 0, half, height_points), quality)
                _place(page, translated, fitz.Rect(half, 0, width_points, height_points), quality)
            else:
                for image in (original, translated):
                    width_points = image.width * POINTS_PER_INCH / dpi
                    height_points = image.height * POINTS_PER_INCH / dpi
                    page = document.new_page(width=width_points, height=height_points)
                    _place(page, image, page.rect, quality)
        return document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()


def _place(page: fitz.Page, image: Image.Image, rect: fitz.Rect, quality: int) -> None:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=quality, optimize=True)
    page.insert_image(rect, stream=buffer.getvalue())


def compress(data: bytes, *, image_quality: int = 70) -> bytes:
    """Re-encode embedded images to shrink a PDF without changing layout."""
    document = _open(data)
    try:
        for page_index in range(document.page_count):
            for image_info in document.load_page(page_index).get_images(full=True):
                xref = image_info[0]
                try:
                    raw = document.extract_image(xref)
                    image = Image.open(io.BytesIO(raw["image"])).convert("RGB")
                    buffer = io.BytesIO()
                    image.save(buffer, "JPEG", quality=image_quality, optimize=True)
                    if buffer.tell() < len(raw["image"]):
                        document.update_stream(xref, buffer.getvalue())
                except Exception:  # pragma: no cover - unusual encodings
                    continue
        return document.tobytes(garbage=4, deflate=True, clean=True)
    finally:
        document.close()


def set_password(data: bytes, password: str) -> bytes:
    document = _open(data)
    try:
        return document.tobytes(
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw=password,
            user_pw=password,
            permissions=fitz.PDF_PERM_ACCESSIBILITY | fitz.PDF_PERM_PRINT,
        )
    finally:
        document.close()


def to_pdfa(data: bytes) -> tuple[bytes, bool]:
    """Best-effort PDF/A conversion via Ghostscript when it is installed.

    Returns ``(bytes, converted)`` so callers can tell the user honestly whether
    the archival profile was actually applied.
    """
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    ghostscript = shutil.which("gs") or shutil.which("gswin64c")
    if not ghostscript:
        return data, False

    with tempfile.TemporaryDirectory(prefix="lingo-pdfa-") as directory:
        source = Path(directory) / "in.pdf"
        target = Path(directory) / "out.pdf"
        source.write_bytes(data)
        try:
            subprocess.run(  # noqa: S603 - argv list, no shell interpolation
                [
                    ghostscript,
                    "-dPDFA=2",
                    "-dBATCH",
                    "-dNOPAUSE",
                    "-dNOOUTERSAVE",
                    "-sColorConversionStrategy=UseDeviceIndependentColor",
                    "-sDEVICE=pdfwrite",
                    "-dPDFACompatibilityPolicy=1",
                    f"-sOutputFile={target}",
                    str(source),
                ],
                check=True,
                capture_output=True,
                timeout=180,
            )
        except Exception as exc:  # pragma: no cover - depends on host
            log.info("pdf.pdfa_failed", error=type(exc).__name__)
            return data, False
        if target.exists() and target.stat().st_size > 0:
            return target.read_bytes(), True
    return data, False


def sanitize(data: bytes) -> bytes:
    """Strip JavaScript, embedded files, and auto-actions from an uploaded PDF.

    Applied to every upload before processing so an attacker cannot ship an
    active payload through our pipeline or back out in an export.
    """
    try:
        import pikepdf
    except ImportError:  # pragma: no cover - pikepdf is a hard dependency
        return data

    try:
        with pikepdf.open(io.BytesIO(data)) as pdf:
            root = pdf.Root
            for key in ("/OpenAction", "/AA", "/AcroForm", "/Names"):
                if key in root:
                    if key == "/Names" and "/EmbeddedFiles" not in root["/Names"]:
                        continue
                    del root[key]
            for page in pdf.pages:
                for key in ("/AA", "/JS", "/JavaScript"):
                    if key in page:
                        del page[key]
                if "/Annots" in page:
                    kept = [
                        annotation
                        for annotation in page["/Annots"]
                        if str(annotation.get("/Subtype", "")) != "/Screen"
                        and "/JS" not in annotation
                        and "/AA" not in annotation
                    ]
                    if kept:
                        page["/Annots"] = pdf.make_indirect(kept)
                    else:
                        del page["/Annots"]
            buffer = io.BytesIO()
            pdf.save(buffer, linearize=False)
            return buffer.getvalue()
    except Exception as exc:
        log.warning("pdf.sanitize_failed", error=type(exc).__name__)
        raise AppError(code=ErrorCode.CORRUPTED_FILE, internal=str(exc)[:200]) from exc


def region_to_pdf_rect(region: Region, scale: float) -> tuple[float, float, float, float]:
    box: BoundingBox = region.bounding_box
    return (box.x * scale, box.y * scale, box.right * scale, box.bottom * scale)


def merge(documents: list[bytes]) -> bytes:
    merged = fitz.open()
    try:
        for data in documents:
            with _open(data) as source:
                merged.insert_pdf(source)
        return merged.tobytes(garbage=4, deflate=True)
    finally:
        merged.close()


def reorder(data: bytes, order: list[int]) -> bytes:
    """Reorder / drop pages. ``order`` is 1-based page numbers."""
    document = _open(data)
    try:
        indices = [number - 1 for number in order if 1 <= number <= document.page_count]
        if not indices:
            raise AppError(code=ErrorCode.VALIDATION_FAILED, internal="empty page order")
        document.select(indices)
        return document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()


def info_as_dict(info: PdfInfo) -> dict[str, Any]:
    return {
        "page_count": info.page_count,
        "encrypted": info.encrypted,
        "is_scanned": info.is_scanned,
        "is_mixed": info.is_mixed,
        "title": info.title,
        "pages": [
            {
                "number": page.number,
                "width_points": round(page.width_points, 2),
                "height_points": round(page.height_points, 2),
                "rotation": page.rotation,
                "has_text_layer": page.has_text_layer,
                "image_count": page.image_count,
            }
            for page in info.pages
        ],
    }
