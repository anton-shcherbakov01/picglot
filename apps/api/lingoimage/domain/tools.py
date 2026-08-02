"""Tool catalogue.

The web app renders its navigation, routes and upload constraints from this
table (served by ``GET /api/v1/config``), so a tool is enabled or disabled in
exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass

from lingoimage.core.config import settings
from lingoimage.domain.enums import ExportFormat, JobType, ToolType

IMAGE_INPUTS = ("jpg", "jpeg", "png", "webp", "heic", "heif", "tiff", "tif", "bmp", "gif")
DOCUMENT_INPUTS = ("pdf",)
ALL_INPUTS = IMAGE_INPUTS + DOCUMENT_INPUTS


@dataclass(frozen=True, slots=True)
class ToolSpec:
    type: ToolType
    slug: str
    job_type: JobType
    #: Translation is part of the default pipeline for this tool.
    translates: bool
    accepts: tuple[str, ...]
    exports: tuple[ExportFormat, ...]
    #: Multiplier applied on top of the base page cost.
    credit_multiplier: float = 1.0
    requires_feature: str | None = None
    multipage: bool = True
    default_render: bool = False
    icon: str = "sparkles"
    #: i18n key prefix — the web app resolves `tools.<key>.title` etc.
    i18n_key: str = ""

    def __post_init__(self) -> None:
        if not self.i18n_key:
            object.__setattr__(self, "i18n_key", self.slug.replace("-", "_"))

    @property
    def enabled(self) -> bool:
        if self.slug not in settings.enabled_tools:
            return False
        if self.requires_feature:
            return bool(getattr(settings, f"feature_{self.requires_feature}", True))
        return True


IMAGE_EXPORTS = (
    ExportFormat.PNG,
    ExportFormat.JPG,
    ExportFormat.WEBP,
    ExportFormat.PDF,
    ExportFormat.JSON,
)
TEXT_EXPORTS = (
    ExportFormat.TXT,
    ExportFormat.MARKDOWN,
    ExportFormat.DOCX,
    ExportFormat.JSON,
    ExportFormat.PDF_SEARCHABLE,
)

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        type=ToolType.IMAGE_TRANSLATOR,
        slug="image-translator",
        job_type=JobType.FULL_PIPELINE,
        translates=True,
        accepts=ALL_INPUTS,
        exports=IMAGE_EXPORTS + (ExportFormat.TXT, ExportFormat.DOCX),
        default_render=True,
        icon="languages",
    ),
    ToolSpec(
        type=ToolType.TRANSLATE_PHOTO,
        slug="translate-photo",
        job_type=JobType.FULL_PIPELINE,
        translates=True,
        accepts=IMAGE_INPUTS,
        exports=IMAGE_EXPORTS + (ExportFormat.TXT,),
        default_render=True,
        multipage=False,
        icon="camera",
    ),
    ToolSpec(
        type=ToolType.SCREENSHOT_TRANSLATOR,
        slug="screenshot-translator",
        job_type=JobType.FULL_PIPELINE,
        translates=True,
        accepts=IMAGE_INPUTS,
        exports=IMAGE_EXPORTS + (ExportFormat.TXT, ExportFormat.JSON),
        default_render=True,
        multipage=False,
        icon="monitor",
    ),
    ToolSpec(
        type=ToolType.IMAGE_TO_TEXT,
        slug="image-to-text",
        job_type=JobType.OCR,
        translates=False,
        accepts=ALL_INPUTS,
        exports=TEXT_EXPORTS + (ExportFormat.CSV,),
        icon="type",
    ),
    ToolSpec(
        type=ToolType.JPG_TO_WORD,
        slug="jpg-to-word",
        job_type=JobType.OCR,
        translates=False,
        accepts=ALL_INPUTS,
        exports=(
            ExportFormat.DOCX,
            ExportFormat.PDF_SEARCHABLE,
            ExportFormat.TXT,
            ExportFormat.JSON,
        ),
        icon="file-text",
    ),
    ToolSpec(
        type=ToolType.IMAGE_TO_EXCEL,
        slug="image-to-excel",
        job_type=JobType.TABLE_EXTRACTION,
        translates=False,
        accepts=ALL_INPUTS,
        exports=(ExportFormat.XLSX, ExportFormat.CSV, ExportFormat.JSON, ExportFormat.DOCX),
        credit_multiplier=1.5,
        requires_feature="tables",
        icon="table",
    ),
    ToolSpec(
        type=ToolType.HANDWRITING_TO_TEXT,
        slug="handwriting-to-text",
        job_type=JobType.OCR,
        translates=False,
        accepts=IMAGE_INPUTS + DOCUMENT_INPUTS,
        exports=(
            ExportFormat.TXT,
            ExportFormat.DOCX,
            ExportFormat.PDF_SEARCHABLE,
            ExportFormat.JSON,
        ),
        credit_multiplier=2.0,
        requires_feature="handwriting",
        icon="pen-line",
    ),
    ToolSpec(
        type=ToolType.PDF_TRANSLATOR,
        slug="pdf-translator",
        job_type=JobType.FULL_PIPELINE,
        translates=True,
        accepts=DOCUMENT_INPUTS + IMAGE_INPUTS,
        exports=(
            ExportFormat.PDF,
            ExportFormat.PDF_SEARCHABLE,
            ExportFormat.PDF_BILINGUAL,
            ExportFormat.DOCX,
            ExportFormat.TXT,
            ExportFormat.JSON,
        ),
        default_render=True,
        icon="file-type-2",
    ),
    ToolSpec(
        type=ToolType.PDF_OCR,
        slug="pdf-ocr",
        job_type=JobType.OCR,
        translates=False,
        accepts=DOCUMENT_INPUTS + IMAGE_INPUTS,
        exports=(
            ExportFormat.PDF_SEARCHABLE,
            ExportFormat.TXT,
            ExportFormat.DOCX,
            ExportFormat.JSON,
        ),
        icon="scan-text",
    ),
    ToolSpec(
        type=ToolType.DOCUMENT_SCANNER,
        slug="document-scanner",
        job_type=JobType.OCR,
        translates=False,
        accepts=IMAGE_INPUTS + DOCUMENT_INPUTS,
        exports=(
            ExportFormat.PDF,
            ExportFormat.PDF_SEARCHABLE,
            ExportFormat.PNG,
            ExportFormat.DOCX,
            ExportFormat.TXT,
        ),
        icon="scan-line",
    ),
    ToolSpec(
        type=ToolType.RECEIPT_SCANNER,
        slug="receipt-scanner",
        job_type=JobType.RECEIPT_EXTRACTION,
        translates=False,
        accepts=IMAGE_INPUTS + DOCUMENT_INPUTS,
        exports=(ExportFormat.JSON, ExportFormat.CSV, ExportFormat.XLSX, ExportFormat.PDF),
        credit_multiplier=1.5,
        requires_feature="receipts",
        icon="receipt",
    ),
    ToolSpec(
        type=ToolType.INVOICE_OCR,
        slug="invoice-ocr",
        job_type=JobType.RECEIPT_EXTRACTION,
        translates=False,
        accepts=IMAGE_INPUTS + DOCUMENT_INPUTS,
        exports=(ExportFormat.JSON, ExportFormat.CSV, ExportFormat.XLSX, ExportFormat.PDF),
        credit_multiplier=1.5,
        requires_feature="receipts",
        icon="file-spreadsheet",
    ),
    ToolSpec(
        type=ToolType.BATCH,
        slug="batch",
        job_type=JobType.BATCH,
        translates=True,
        accepts=ALL_INPUTS,
        exports=(ExportFormat.ZIP, ExportFormat.CSV),
        requires_feature="batch",
        icon="layers",
    ),
)

BY_SLUG: dict[str, ToolSpec] = {tool.slug: tool for tool in TOOLS}
BY_TYPE: dict[ToolType, ToolSpec] = {tool.type: tool for tool in TOOLS}


def get(slug: str | ToolType) -> ToolSpec | None:
    return BY_SLUG.get(str(slug))


def require(slug: str | ToolType) -> ToolSpec:
    tool = get(slug)
    if tool is None:
        from lingoimage.core.errors import AppError, ErrorCode

        raise AppError(code=ErrorCode.NOT_FOUND, details={"tool": str(slug)})
    if not tool.enabled:
        from lingoimage.core.errors import AppError, ErrorCode

        raise AppError(code=ErrorCode.FEATURE_DISABLED, details={"tool": tool.slug})
    return tool


def enabled_tools() -> list[ToolSpec]:
    return [tool for tool in TOOLS if tool.enabled]


def accepts_extension(tool: ToolSpec, extension: str) -> bool:
    return extension.lower().lstrip(".") in tool.accepts


#: Format landing pages: slug -> (tool, accepted extension, output hint).
#: Slugs must not collide with a tool slug — the tool already owns that path,
#: and a duplicate would violate the (path, locale) uniqueness of `seo_pages`.
FORMAT_PAGES: tuple[tuple[str, ToolType, str, str], ...] = (
    ("jpg-to-text", ToolType.IMAGE_TO_TEXT, "jpg", "txt"),
    ("png-to-text", ToolType.IMAGE_TO_TEXT, "png", "txt"),
    ("heic-to-text", ToolType.IMAGE_TO_TEXT, "heic", "txt"),
    ("screenshot-to-text", ToolType.IMAGE_TO_TEXT, "png", "txt"),
    ("png-to-word", ToolType.JPG_TO_WORD, "png", "docx"),
    ("pdf-to-word", ToolType.JPG_TO_WORD, "pdf", "docx"),
    ("photo-table-to-excel", ToolType.IMAGE_TO_EXCEL, "jpg", "xlsx"),
    ("screenshot-table-to-excel", ToolType.IMAGE_TO_EXCEL, "png", "xlsx"),
    ("scanned-pdf-to-word", ToolType.PDF_OCR, "pdf", "docx"),
    ("scanned-pdf-to-text", ToolType.PDF_OCR, "pdf", "txt"),
)

#: Guard against re-introducing a collision.
assert not ({slug for slug, *_ in FORMAT_PAGES} & set(BY_SLUG)), (
    "a format page slug collides with a tool slug"
)
