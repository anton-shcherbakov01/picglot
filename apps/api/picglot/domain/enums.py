"""Enumerations shared between the database, the API contract and the web app.

These values are part of the public API surface: they are generated into the
TypeScript client, so renaming one is a breaking change.
"""

from __future__ import annotations

from enum import StrEnum


class ToolType(StrEnum):
    IMAGE_TRANSLATOR = "image-translator"
    TRANSLATE_PHOTO = "translate-photo"
    SCREENSHOT_TRANSLATOR = "screenshot-translator"
    IMAGE_TO_TEXT = "image-to-text"
    JPG_TO_WORD = "jpg-to-word"
    IMAGE_TO_EXCEL = "image-to-excel"
    HANDWRITING_TO_TEXT = "handwriting-to-text"
    PDF_TRANSLATOR = "pdf-translator"
    PDF_OCR = "pdf-ocr"
    DOCUMENT_SCANNER = "document-scanner"
    RECEIPT_SCANNER = "receipt-scanner"
    INVOICE_OCR = "invoice-ocr"
    BATCH = "batch"


class JobType(StrEnum):
    OCR = "ocr"
    TRANSLATE = "translate"
    RENDER = "render"
    FULL_PIPELINE = "full_pipeline"
    TABLE_EXTRACTION = "table_extraction"
    RECEIPT_EXTRACTION = "receipt_extraction"
    EXPORT = "export"
    BATCH = "batch"
    REPROCESS_REGION = "reprocess_region"


class JobStatus(StrEnum):
    CREATED = "created"
    UPLOADING = "uploading"
    QUEUED = "queued"
    PREPROCESSING = "preprocessing"
    DETECTING = "detecting"
    RECOGNIZING = "recognizing"
    TRANSLATING = "translating"
    INPAINTING = "inpainting"
    RENDERING = "rendering"
    EXPORTING = "exporting"
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_JOB_STATUSES

    @property
    def is_active(self) -> bool:
        return self in ACTIVE_JOB_STATUSES


TERMINAL_JOB_STATUSES = frozenset(
    {
        JobStatus.COMPLETED,
        JobStatus.PARTIALLY_COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }
)

ACTIVE_JOB_STATUSES = frozenset(
    {
        JobStatus.PREPROCESSING,
        JobStatus.DETECTING,
        JobStatus.RECOGNIZING,
        JobStatus.TRANSLATING,
        JobStatus.INPAINTING,
        JobStatus.RENDERING,
        JobStatus.EXPORTING,
    }
)

#: Ordered pipeline stages with the share of total progress each contributes.
STAGE_WEIGHTS: dict[JobStatus, float] = {
    JobStatus.PREPROCESSING: 0.10,
    JobStatus.DETECTING: 0.15,
    JobStatus.RECOGNIZING: 0.30,
    JobStatus.TRANSLATING: 0.20,
    JobStatus.INPAINTING: 0.10,
    JobStatus.RENDERING: 0.10,
    JobStatus.EXPORTING: 0.05,
}


class RegionType(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    CAPTION = "caption"
    UI_LABEL = "ui_label"
    TABLE_CELL = "table_cell"
    LIST_ITEM = "list_item"
    FOOTNOTE = "footnote"
    HANDWRITING = "handwriting"
    NUMERIC_FIELD = "numeric_field"
    FORMULA = "formula"
    BARCODE = "barcode"
    DECORATIVE = "decorative"
    UNKNOWN = "unknown"


class TextAlign(StrEnum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    JUSTIFY = "justify"


class VerticalAlign(StrEnum):
    TOP = "top"
    MIDDLE = "middle"
    BOTTOM = "bottom"


class TextDirection(StrEnum):
    LTR = "ltr"
    RTL = "rtl"
    VERTICAL_RL = "vertical_rl"


class FontClass(StrEnum):
    SANS = "sans"
    SERIF = "serif"
    MONO = "mono"
    HANDWRITING = "handwriting"


class AssetKind(StrEnum):
    ORIGINAL = "original"
    NORMALIZED = "normalized"
    PREVIEW = "preview"
    THUMBNAIL = "thumbnail"
    MASK = "mask"
    CLEANED = "cleaned"
    RENDERED = "rendered"
    EXPORT = "export"
    FONT = "font"
    INTERMEDIATE = "intermediate"


class ProjectStatus(StrEnum):
    DRAFT = "draft"
    PROCESSING = "processing"
    READY = "ready"
    PARTIAL = "partial"
    FAILED = "failed"
    ARCHIVED = "archived"


class ExportFormat(StrEnum):
    PNG = "png"
    JPG = "jpg"
    WEBP = "webp"
    PDF = "pdf"
    PDF_SEARCHABLE = "pdf_searchable"
    PDF_BILINGUAL = "pdf_bilingual"
    TXT = "txt"
    MARKDOWN = "md"
    DOCX = "docx"
    XLSX = "xlsx"
    CSV = "csv"
    JSON = "json"
    ZIP = "zip"

    @property
    def media_type(self) -> str:
        return {
            ExportFormat.PNG: "image/png",
            ExportFormat.JPG: "image/jpeg",
            ExportFormat.WEBP: "image/webp",
            ExportFormat.PDF: "application/pdf",
            ExportFormat.PDF_SEARCHABLE: "application/pdf",
            ExportFormat.PDF_BILINGUAL: "application/pdf",
            ExportFormat.TXT: "text/plain; charset=utf-8",
            ExportFormat.MARKDOWN: "text/markdown; charset=utf-8",
            ExportFormat.DOCX: (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            ExportFormat.XLSX: (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            ExportFormat.CSV: "text/csv; charset=utf-8",
            ExportFormat.JSON: "application/json",
            ExportFormat.ZIP: "application/zip",
        }[self]

    @property
    def extension(self) -> str:
        return {
            ExportFormat.PDF_SEARCHABLE: "pdf",
            ExportFormat.PDF_BILINGUAL: "pdf",
            ExportFormat.MARKDOWN: "md",
        }.get(self, self.value)


class RenderMode(StrEnum):
    """How the translated image is composed."""

    TRANSLATION_ONLY = "translation_only"
    OVERLAY = "overlay"  # translation drawn on top of the untouched original
    BILINGUAL = "bilingual"  # original above, translation below, per region
    SIDE_BY_SIDE = "side_by_side"  # two panels in one image


class UserStatus(StrEnum):
    ACTIVE = "active"
    PENDING = "pending"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class WorkspaceRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"

    @property
    def rank(self) -> int:
        return {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}[self.value]

    def can(self, required: WorkspaceRole) -> bool:
        return self.rank >= required.rank


class AdminRole(StrEnum):
    SUPERADMIN = "superadmin"
    ADMIN = "admin"
    SUPPORT = "support"
    FINANCE = "finance"
    CONTENT = "content_manager"
    ANALYST = "analyst"


class LedgerReason(StrEnum):
    SIGNUP_GRANT = "signup_grant"
    MONTHLY_GRANT = "monthly_grant"
    PURCHASE = "purchase"
    SUBSCRIPTION_GRANT = "subscription_grant"
    JOB_CHARGE = "job_charge"
    JOB_REFUND = "job_refund"
    MANUAL_ADJUSTMENT = "manual_adjustment"
    REFUND = "refund"
    EXPIRY = "expiry"
    PROMO = "promo"


class SubscriptionStatus(StrEnum):
    ACTIVE = "active"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    INCOMPLETE = "incomplete"
    PAUSED = "paused"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"
    CANCELED = "canceled"


class WebhookEvent(StrEnum):
    JOB_COMPLETED = "job.completed"
    JOB_FAILED = "job.failed"
    JOB_PARTIALLY_COMPLETED = "job.partially_completed"
    BATCH_COMPLETED = "batch.completed"
    EXPORT_READY = "export.ready"
    PROJECT_DELETED = "project.deleted"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    ABANDONED = "abandoned"


class SharePermission(StrEnum):
    VIEW = "view"
    VIEW_DOWNLOAD = "view_download"


class QualityBand(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    REVIEW_RECOMMENDED = "review_recommended"
    LOW = "low"


class TranslationSource(StrEnum):
    """Where a particular translation came from — surfaced in the editor."""

    GLOSSARY = "glossary"
    TRANSLATION_MEMORY = "translation_memory"
    FUZZY_MATCH = "fuzzy_match"
    PROVIDER = "provider"
    MANUAL = "manual"
    CACHE = "cache"
    UNTRANSLATED = "untranslated"
