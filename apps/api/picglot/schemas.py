"""Pydantic contracts. These generate the OpenAPI document and the TS client."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from picglot.domain.enums import (
    ExportFormat,
    JobStatus,
    RegionType,
    RenderMode,
    SharePermission,
    ToolType,
    WorkspaceRole,
)


class Base(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class ErrorBody(Base):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] | None = None
    request_id: str | None = None


class ErrorResponse(Base):
    error: ErrorBody


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
class RegisterRequest(Base):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    name: str | None = Field(default=None, max_length=120)
    locale: str = "en"
    marketing_opt_in: bool = False
    accept_terms: bool = True

    @field_validator("accept_terms")
    @classmethod
    def _must_accept(cls, value: bool) -> bool:
        if not value:
            raise ValueError("terms must be accepted")
        return value


class LoginRequest(Base):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class MagicLinkRequest(Base):
    email: EmailStr


class TokenRequest(Base):
    token: str = Field(min_length=8, max_length=512)


class PasswordResetRequest(Base):
    token: str
    password: str = Field(min_length=8, max_length=256)


class ChangePasswordRequest(Base):
    current_password: str
    new_password: str = Field(min_length=8, max_length=256)


class TwoFactorRequest(Base):
    code: str = Field(min_length=6, max_length=16)
    pending_token: str | None = None


class UserOut(Base):
    id: str
    email: str
    name: str | None
    avatar_url: str | None
    locale: str
    timezone: str
    plan_code: str
    status: str
    admin_role: str | None
    email_verified: bool = Field(alias="is_verified")
    created_at: datetime


class AuthResponse(Base):
    user: UserOut | None = None
    requires_two_factor: bool = False
    pending_token: str | None = None
    csrf_token: str | None = None


class SessionOut(Base):
    id: str
    device_label: str | None
    created_at: datetime
    last_used_at: datetime | None
    current: bool = False


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
class LanguageOut(Base):
    code: str
    name_en: str
    name_native: str
    script: str
    direction: str
    rtl: bool
    cjk: bool
    supports_vertical: bool
    ocr: bool
    translation: bool
    offline_translation: bool


class ToolOut(Base):
    slug: str
    type: str
    translates: bool
    accepts: list[str]
    exports: list[str]
    multipage: bool
    icon: str
    i18n_key: str
    credit_multiplier: float
    #: Locale -> slug, for locales that publish this tool under their own slug
    #: instead of the English one. Absent locales use `slug`.
    localized_slugs: dict[str, str] = Field(default_factory=dict)


class PlanOut(Base):
    code: str
    name: str
    monthly_credits: int
    price_usd_cents: int
    price_rub_kopecks: int
    max_upload_bytes: int
    max_pdf_pages: int
    max_batch_files: int
    retention_hours: int
    export_formats: list[str]
    features: list[str]


class AppConfigOut(Base):
    brand_name: str
    default_locale: str
    locales: list[str]
    tools: list[ToolOut]
    languages: list[LanguageOut]
    plans: list[PlanOut]
    credit_rules: list[dict[str, Any]]
    limits: dict[str, Any]
    features: dict[str, bool]
    translation_available: bool
    local_only_processing: bool
    maintenance_mode: bool


# --------------------------------------------------------------------------- #
# Projects, pages, regions
# --------------------------------------------------------------------------- #
class BoundingBoxOut(Base):
    x: float
    y: float
    width: float
    height: float


class TranslationOut(Base):
    text: str
    target_language: str
    provider: str | None = None
    source: str | None = None
    alternatives: list[str] = Field(default_factory=list)


class RegionOut(Base):
    id: str
    region_type: RegionType
    polygon: list[list[float]]
    bounding_box: dict[str, float]
    rotation: float
    reading_order: int
    detected_language: str | None
    source_text: str
    normalized_text: str | None
    translated_text: str | None = None
    confidence: float | None
    low_confidence_spans: list[dict[str, Any]] = Field(default_factory=list)
    corrections: list[dict[str, Any]] = Field(default_factory=list)
    style: dict[str, Any] = Field(default_factory=dict)
    skip_translation: bool = False
    edited_by_user: bool = False
    version: int = 1
    translation: TranslationOut | None = None


class TableCellOut(Base):
    row: int
    col: int
    row_span: int
    col_span: int
    text: str
    value_type: str
    numeric_value: float | None
    currency: str | None
    is_header: bool
    confidence: float | None
    region_id: str | None


class TableOut(Base):
    id: str
    index_on_page: int
    # No aliases here: FastAPI serialises by alias, so an alias would change the
    # public field name and break the generated client.
    rows: int
    cols: int
    has_header: bool
    structure_ambiguous: bool
    confidence: float | None
    bounding_box: dict[str, Any]
    cells: list[TableCellOut] = Field(default_factory=list)


class PageOut(Base):
    id: str
    page_number: int
    width: int
    height: int
    rotation: int
    status: str
    detected_language: str | None
    ocr_confidence: float | None
    error_code: str | None = None
    preview_url: str | None = None
    thumbnail_url: str | None = None
    rendered_url: str | None = None
    original_url: str | None = None
    regions: list[RegionOut] = Field(default_factory=list)
    tables: list[TableOut] = Field(default_factory=list)


class ProjectOut(Base):
    id: str
    name: str
    tool_type: str
    status: str
    source_language: str | None
    target_language: str | None
    page_count: int
    quality_score: float | None
    quality_band: str | None
    quality_reasons: list[dict[str, Any]] = Field(default_factory=list)
    document_version: int
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    thumbnail_url: str | None = None


class ProjectDetailOut(ProjectOut):
    pages: list[PageOut] = Field(default_factory=list)
    exports: list[ExportOut] = Field(default_factory=list)
    active_job: JobOut | None = None


class ProjectListOut(Base):
    items: list[ProjectOut]
    total: int
    limit: int
    offset: int


class RenameProjectRequest(Base):
    name: str = Field(min_length=1, max_length=255)


# --------------------------------------------------------------------------- #
# Processing
# --------------------------------------------------------------------------- #
class ProcessOptions(Base):
    keep_line_breaks: bool = True
    fix_ocr_errors: bool = True
    handwriting: bool = False
    extract_tables: bool = False
    advanced_inpaint: bool = False
    force_ocr: bool = False
    llm_ocr: bool = False
    preprocess: dict[str, Any] | None = None


class CreateJobRequest(Base):
    tool: ToolType
    source_language: str | None = None
    target_language: str | None = None
    translate: bool = True
    render_mode: RenderMode = RenderMode.TRANSLATION_ONLY
    pages: list[int] | dict[str, int] | None = None
    options: ProcessOptions = Field(default_factory=ProcessOptions)
    export_formats: list[ExportFormat] = Field(default_factory=list)
    project_name: str | None = None


class UploadInitRequest(Base):
    filename: str = Field(max_length=255)
    content_type: str = Field(max_length=120)
    byte_size: int = Field(gt=0)
    tool: ToolType


class UploadInitOut(Base):
    upload_id: str
    method: str
    url: str
    fields: dict[str, str] = Field(default_factory=dict)
    key: str
    max_bytes: int
    expires_in: int


class JobOut(Base):
    id: str
    project_id: str | None
    type: str
    status: JobStatus
    stage: str | None
    progress: float
    pages_total: int
    pages_completed: int
    pages_failed: int
    credits_charged: int
    credits_refunded: int
    cost_estimate: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    output: dict[str, Any] = Field(default_factory=dict)


class JobListOut(Base):
    items: list[JobOut]
    total: int
    limit: int
    offset: int


class CostEstimateOut(Base):
    pages: int
    total_credits: int
    breakdown: list[dict[str, Any]]
    balance: int | None = None
    sufficient: bool = True


# --------------------------------------------------------------------------- #
# Editor
# --------------------------------------------------------------------------- #
class RegionUpdate(Base):
    source_text: str | None = None
    translated_text: str | None = None
    region_type: RegionType | None = None
    bounding_box: BoundingBoxOut | None = None
    polygon: list[list[float]] | None = None
    rotation: float | None = None
    detected_language: str | None = None
    style: dict[str, Any] | None = None
    skip_translation: bool | None = None
    reading_order: int | None = None
    version: int | None = None


class RegionCreate(Base):
    page_id: str
    bounding_box: BoundingBoxOut
    source_text: str = ""
    translated_text: str = ""
    region_type: RegionType = RegionType.PARAGRAPH
    style: dict[str, Any] = Field(default_factory=dict)


class RegionSplitRequest(Base):
    at_character: int = Field(ge=1)


class RegionMergeRequest(Base):
    region_ids: list[str] = Field(min_length=2, max_length=50)


class RetranslateRequest(Base):
    provider: str | None = None
    formality: Literal["formal", "informal"] | None = None
    target_language: str | None = None


class ReocrRequest(Base):
    provider: str | None = None
    languages: list[str] = Field(default_factory=list)


class TableCellUpdate(Base):
    row: int
    col: int
    text: str | None = None
    value_type: str | None = None
    is_header: bool | None = None


class RerenderRequest(Base):
    render_mode: RenderMode = RenderMode.TRANSLATION_ONLY
    page_ids: list[str] | None = None
    #: Optional brush/eraser mask from the editor: a base64 PNG, white where
    #: the user wants the background repaired. Keyed by page id so a multi-page
    #: project can carry one mask per page in a single request.
    masks: dict[str, str] | None = None


# --------------------------------------------------------------------------- #
# Exports and sharing
# --------------------------------------------------------------------------- #
class ExportRequest(Base):
    format: ExportFormat
    content: Literal["source", "translation", "bilingual"] = "translation"
    quality: int = Field(default=92, ge=10, le=100)
    scale: float = Field(default=1.0, gt=0.05, le=4.0)
    include_confidence: bool = False
    include_coordinates: bool = False
    keep_line_breaks: bool = True
    pdfa: bool = False
    pdf_layout: Literal["sequential", "side_by_side"] = "sequential"
    sheet_per_page: bool = True
    password: str | None = Field(default=None, max_length=128)


class ExportOut(Base):
    id: str
    format: str
    byte_size: int
    created_at: datetime
    expires_at: datetime | None
    download_url: str | None = None


class ShareCreateRequest(Base):
    permission: SharePermission = SharePermission.VIEW
    password: str | None = Field(default=None, max_length=128)
    expires_in_hours: int | None = Field(default=168, ge=1, le=8760)
    max_views: int | None = Field(default=None, ge=1, le=100000)
    show_owner: bool = False
    watermark: bool = False
    export_id: str | None = None


class ShareOut(Base):
    id: str
    url: str
    permission: str
    expires_at: datetime | None
    max_views: int | None
    view_count: int
    has_password: bool
    watermark: bool
    created_at: datetime


# --------------------------------------------------------------------------- #
# Billing
# --------------------------------------------------------------------------- #
class WalletOut(Base):
    balance: int
    lifetime_granted: int
    lifetime_spent: int
    plan_code: str


class LedgerEntryOut(Base):
    id: str
    delta: int
    balance_after: int
    reason: str
    note: str | None
    job_id: str | None
    created_at: datetime


class CheckoutRequest(Base):
    kind: Literal["subscription", "credit_pack"] = "subscription"
    plan_code: str | None = None
    pack_code: str | None = None
    coupon: str | None = Field(default=None, max_length=48)
    success_url: str | None = None
    cancel_url: str | None = None


class CheckoutOut(Base):
    checkout_url: str
    provider: str
    session_id: str | None = None
    test_mode: bool = False


class SubscriptionOut(Base):
    id: str
    plan_code: str
    status: str
    provider: str
    current_period_end: datetime | None
    cancel_at_period_end: bool


class PaymentOut(Base):
    id: str
    amount_minor: int
    currency: str
    status: str
    kind: str
    credits_granted: int
    invoice_url: str | None
    created_at: datetime


# --------------------------------------------------------------------------- #
# API keys, webhooks, glossary
# --------------------------------------------------------------------------- #
class ApiKeyCreateRequest(Base):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=list)
    test_mode: bool = False
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiKeyOut(Base):
    id: str
    name: str
    prefix: str
    last_four: str
    scopes: list[str]
    test_mode: bool
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked: bool = False


class ApiKeyCreatedOut(ApiKeyOut):
    key: str = Field(description="Shown once. Store it securely.")


class WebhookCreateRequest(Base):
    url: str = Field(max_length=1024)
    events: list[str] = Field(default_factory=list)
    description: str | None = Field(default=None, max_length=255)


class WebhookOut(Base):
    id: str
    url: str
    events: list[str]
    description: str | None
    is_active: bool
    consecutive_failures: int
    created_at: datetime


class WebhookCreatedOut(WebhookOut):
    secret: str = Field(description="Shown once. Verify signatures with it.")


class WebhookDeliveryOut(Base):
    id: str
    event: str
    event_id: str
    status: str
    attempt: int
    response_status: int | None
    duration_ms: int | None
    created_at: datetime
    delivered_at: datetime | None


class GlossaryTermIn(Base):
    source_term: str = Field(min_length=1, max_length=255)
    target_term: str = Field(default="", max_length=255)
    case_sensitive: bool = False
    whole_word: bool = True
    do_not_translate: bool = False
    comment: str | None = Field(default=None, max_length=512)


class GlossaryCreateRequest(Base):
    name: str = Field(min_length=1, max_length=160)
    source_language: str
    target_language: str
    is_default: bool = False
    terms: list[GlossaryTermIn] = Field(default_factory=list)


class GlossaryOut(Base):
    id: str
    name: str
    source_language: str
    target_language: str
    version: int
    is_default: bool
    term_count: int = 0
    created_at: datetime


class TranslationMemoryOut(Base):
    id: str
    source_language: str
    target_language: str
    source_text: str
    target_text: str
    quality: float
    confirmed_by_user: bool
    frequency: int
    last_used_at: datetime | None


# --------------------------------------------------------------------------- #
# Workspace
# --------------------------------------------------------------------------- #
class WorkspaceCreateRequest(Base):
    name: str = Field(min_length=2, max_length=160)
    billing_email: EmailStr | None = None


class WorkspaceUpdateRequest(Base):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    billing_email: EmailStr | None = None
    retention_override_hours: int | None = Field(default=None, ge=1, le=8760)


class WorkspaceOut(Base):
    id: str
    name: str
    slug: str
    plan_code: str
    billing_email: str | None = None
    owner_id: str
    #: The calling user's role in this workspace.
    role: str
    member_count: int = 0
    retention_override_hours: int | None = None
    created_at: datetime


class WorkspaceInviteRequest(Base):
    email: EmailStr
    role: WorkspaceRole = WorkspaceRole.EDITOR


class WorkspaceMemberOut(Base):
    user_id: str
    email: str
    name: str | None
    role: str
    monthly_credit_limit: int | None = None
    joined_at: datetime


class WorkspaceMemberUpdate(Base):
    role: WorkspaceRole | None = None
    monthly_credit_limit: int | None = Field(default=None, ge=0)


class WorkspaceTransferRequest(Base):
    new_owner_id: str


class WorkspaceInvitationOut(Base):
    id: str
    email: str
    role: str
    expires_at: datetime
    created_at: datetime


class WorkspaceInvitationCreatedOut(WorkspaceInvitationOut):
    #: Returned once so the inviter can pass the link on if the email bounces.
    invitation_url: str


class WorkspaceAcceptRequest(Base):
    token: str = Field(min_length=8, max_length=256)


# --------------------------------------------------------------------------- #
# Content, support, status
# --------------------------------------------------------------------------- #
class SeoPageOut(Base):
    path: str
    locale: str
    kind: str
    title: str
    description: str
    h1: str
    intro: str | None
    body_sections: list[dict[str, Any]]
    faq: list[dict[str, Any]]
    tool_slug: str | None
    source_language: str | None
    target_language: str | None
    noindex: bool


class BlogPostOut(Base):
    slug: str
    locale: str
    title: str
    excerpt: str
    body_markdown: str | None = None
    author_name: str
    tags: list[str]
    published_at: datetime | None
    reading_minutes: int


class ContactRequestIn(Base):
    email: EmailStr
    name: str | None = Field(default=None, max_length=120)
    category: Literal["general", "billing", "technical", "abuse", "privacy", "partnership"] = (
        "general"
    )
    subject: str = Field(min_length=3, max_length=255)
    message: str = Field(min_length=10, max_length=5000)
    project_id: str | None = None
    request_id: str | None = None
    #: Honeypot — must stay empty.
    website: str = ""


class StatusComponentOut(Base):
    key: str
    state: Literal["operational", "degraded", "down", "maintenance"]
    detail: str = ""


class StatusOut(Base):
    overall: Literal["operational", "degraded", "down", "maintenance"]
    components: list[StatusComponentOut]
    incidents: list[dict[str, Any]] = Field(default_factory=list)
    checked_at: datetime


class HealthOut(Base):
    status: Literal["ok", "degraded", "error"]
    version: str
    environment: str
    checks: dict[str, Any] = Field(default_factory=dict)


class AnalyticsEventIn(Base):
    name: str = Field(max_length=64)
    properties: dict[str, Any] = Field(default_factory=dict)
    anonymous_id: str | None = Field(default=None, max_length=64)
    locale: str | None = None
    utm: dict[str, Any] = Field(default_factory=dict)


ProjectDetailOut.model_rebuild()
