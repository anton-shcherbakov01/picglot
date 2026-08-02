"""Database schema.

Conventions
-----------
* Primary keys are prefixed, sortable string ids (``prj_01J…``) — safe to expose.
* Money is stored in minor units (cents/kopecks) as integers, never floats.
* Credits are integers; the ledger is append-only and the wallet balance is
  derived from it under a row lock (see ``services/credits.py``).
* Anything holding user content carries an ``expires_at`` so the lifecycle
  worker can delete it without a bespoke rule per table.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lingoimage.db.base import (
    Base,
    JsonType,
    SoftDeleteMixin,
    TimestampMixin,
    UtcDateTime,
    id_column,
)
from lingoimage.domain.enums import (
    AssetKind,
    DeliveryStatus,
    JobStatus,
    JobType,
    LedgerReason,
    PaymentStatus,
    ProjectStatus,
    RegionType,
    SharePermission,
    SubscriptionStatus,
    UserStatus,
    WorkspaceRole,
)

# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


class User(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"

    id: Mapped[str] = id_column("usr")
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    email_verified_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    password_hash: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str | None] = mapped_column(String(120))
    avatar_url: Mapped[str | None] = mapped_column(String(1024))
    locale: Mapped[str] = mapped_column(String(12), default="en", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=UserStatus.PENDING, nullable=False)
    admin_role: Mapped[str | None] = mapped_column(String(24), index=True)
    plan_code: Mapped[str] = mapped_column(String(24), default="free", nullable=False, index=True)
    marketing_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notification_preferences: Mapped[dict[str, Any]] = mapped_column(
        JsonType, default=dict, nullable=False
    )
    retention_override_hours: Mapped[int | None] = mapped_column(Integer)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)

    sessions: Mapped[list[Session]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    projects: Mapped[list[Project]] = relationship(back_populates="owner")
    wallet: Mapped[CreditWallet | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_users_status_created", "status", "created_at"),
        Index(
            "ix_users_active_email",
            "email",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    @property
    def is_verified(self) -> bool:
        return self.email_verified_at is not None

    @property
    def is_admin(self) -> bool:
        return self.admin_role is not None


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = id_column("ses")
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    device_label: Mapped[str | None] = mapped_column(String(120))
    expires_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), nullable=False, index=True)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    last_used_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="sessions")

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and self.expires_at > dt.datetime.now(dt.UTC)


class OAuthAccount(Base, TimestampMixin):
    __tablename__ = "oauth_accounts"

    id: Mapped[str] = id_column("oau")
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))

    __table_args__ = (
        UniqueConstraint("provider", "provider_account_id", name="uq_oauth_provider_account"),
    )


class VerificationToken(Base):
    __tablename__ = "verification_tokens"

    id: Mapped[str] = id_column("vtk")
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    expires_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), nullable=False, index=True)
    consumed_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False
    )


class TwoFactorSecret(Base, TimestampMixin):
    __tablename__ = "two_factor_secrets"

    id: Mapped[str] = id_column("tfa")
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    backup_codes: Mapped[list[Any]] = mapped_column(JsonType, default=list, nullable=False)


class LoginEvent(Base):
    __tablename__ = "login_events"

    id: Mapped[str] = id_column("lge")
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    method: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(48))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False, index=True
    )


class GuestSession(Base):
    __tablename__ = "guest_sessions"

    id: Mapped[str] = id_column("gst")
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    pages_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    claimed_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    expires_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), nullable=False, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------------- #
# Workspaces
# --------------------------------------------------------------------------- #


class Workspace(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "workspaces"

    id: Mapped[str] = id_column("wsp")
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    billing_email: Mapped[str | None] = mapped_column(String(320))
    plan_code: Mapped[str] = mapped_column(String(24), default="free", nullable=False)
    retention_override_hours: Mapped[int | None] = mapped_column(Integer)
    settings_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)

    members: Mapped[list[WorkspaceMember]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=WorkspaceRole.VIEWER)
    monthly_credit_limit: Mapped[int | None] = mapped_column(Integer)
    joined_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False
    )

    workspace: Mapped[Workspace] = relationship(back_populates="members")
    user: Mapped[User] = relationship()

    __table_args__ = (Index("ix_workspace_members_user", "user_id"),)


class WorkspaceInvitation(Base, TimestampMixin):
    __tablename__ = "workspace_invitations"

    id: Mapped[str] = id_column("inv")
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=WorkspaceRole.EDITOR)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    invited_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), nullable=False)
    accepted_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    revoked_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())

    __table_args__ = (
        UniqueConstraint("workspace_id", "email", name="uq_workspace_invitation_email"),
    )


# --------------------------------------------------------------------------- #
# Projects and content
# --------------------------------------------------------------------------- #


class Project(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "projects"

    id: Mapped[str] = id_column("prj")
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    guest_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("guest_sessions.id", ondelete="CASCADE"), index=True
    )
    folder_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_folders.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    source_language: Mapped[str | None] = mapped_column(String(16))
    target_language: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ProjectStatus.DRAFT, index=True
    )
    page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Float)
    quality_band: Mapped[str | None] = mapped_column(String(24))
    quality_reasons: Mapped[list[Any]] = mapped_column(JsonType, default=list, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    document_version: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, server_default=text("1")
    )
    expires_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), index=True)
    last_opened_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())

    owner: Mapped[User | None] = relationship(back_populates="projects")
    pages: Mapped[list[DocumentPage]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="DocumentPage.page_number"
    )
    assets: Mapped[list[Asset]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    jobs: Mapped[list[Job]] = relationship(back_populates="project", cascade="all, delete-orphan")
    exports: Mapped[list[Export]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_projects_owner_updated", "owner_user_id", "updated_at"),
        Index(
            "ix_projects_expiry_sweep", "expires_at", postgresql_where=text("deleted_at IS NULL")
        ),
        CheckConstraint(
            "owner_user_id IS NOT NULL OR guest_session_id IS NOT NULL",
            name="owner_or_guest_present",
        ),
    )


class ProjectFolder(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "project_folders"

    id: Mapped[str] = id_column("fld")
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_folders.id", ondelete="CASCADE")
    )


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = id_column("ast")
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default=AssetKind.ORIGINAL)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer)
    checksum: Mapped[str | None] = mapped_column(String(64), index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    expires_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="assets")

    __table_args__ = (Index("ix_assets_project_kind", "project_id", "kind"),)


class DocumentPage(Base, TimestampMixin):
    __tablename__ = "document_pages"

    id: Mapped[str] = id_column("pag")
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL")
    )
    normalized_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL")
    )
    preview_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL")
    )
    thumbnail_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL")
    )
    cleaned_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL")
    )
    rendered_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL")
    )
    mask_asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"))
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rotation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detected_language: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    error_code: Mapped[str | None] = mapped_column(String(48))
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)

    project: Mapped[Project] = relationship(back_populates="pages")
    regions: Mapped[list[TextRegion]] = relationship(
        back_populates="page", cascade="all, delete-orphan", order_by="TextRegion.reading_order"
    )
    tables: Mapped[list[TableRecord]] = relationship(
        back_populates="page", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("project_id", "page_number", name="uq_document_page_number"),
    )


class TextRegion(Base, TimestampMixin):
    __tablename__ = "text_regions"

    id: Mapped[str] = id_column("reg")
    page_id: Mapped[str] = mapped_column(
        ForeignKey("document_pages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_region_id: Mapped[str | None] = mapped_column(
        ForeignKey("text_regions.id", ondelete="SET NULL")
    )
    region_type: Mapped[str] = mapped_column(
        String(24), nullable=False, default=RegionType.PARAGRAPH
    )
    polygon: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    bounding_box: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    rotation: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reading_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    line_number: Mapped[int | None] = mapped_column(Integer)
    group_id: Mapped[str | None] = mapped_column(String(40), index=True)
    detected_language: Mapped[str | None] = mapped_column(String(16))
    source_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    normalized_text: Mapped[str | None] = mapped_column(Text)
    corrections: Mapped[list[Any]] = mapped_column(JsonType, default=list, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    low_confidence_spans: Mapped[list[Any]] = mapped_column(JsonType, default=list, nullable=False)
    style: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    skip_translation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    edited_by_user: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    page: Mapped[DocumentPage] = relationship(back_populates="regions")
    translations: Mapped[list[RegionTranslation]] = relationship(
        back_populates="region", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_text_regions_page_order", "page_id", "reading_order"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"
        ),
    )


class RegionTranslation(Base):
    __tablename__ = "region_translations"

    id: Mapped[str] = id_column("trn")
    text_region_id: Mapped[str] = mapped_column(
        ForeignKey("text_regions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_language: Mapped[str] = mapped_column(String(16), nullable=False)
    translated_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    model: Mapped[str | None] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="provider")
    confidence: Mapped[float | None] = mapped_column(Float)
    glossary_id: Mapped[str | None] = mapped_column(
        ForeignKey("glossaries.id", ondelete="SET NULL")
    )
    glossary_version: Mapped[int | None] = mapped_column(Integer)
    character_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_micro_usd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    alternatives: Mapped[list[Any]] = mapped_column(JsonType, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False
    )

    region: Mapped[TextRegion] = relationship(back_populates="translations")

    __table_args__ = (
        Index("ix_region_translations_active", "text_region_id", "target_language", "is_active"),
    )


class TableRecord(Base, TimestampMixin):
    __tablename__ = "tables"

    id: Mapped[str] = id_column("tbl")
    page_id: Mapped[str] = mapped_column(
        ForeignKey("document_pages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    index_on_page: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[str | None] = mapped_column(String(255))
    bounding_box: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    column_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    has_header: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    structure_ambiguous: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)

    page: Mapped[DocumentPage] = relationship(back_populates="tables")
    cells: Mapped[list[TableCell]] = relationship(
        back_populates="table", cascade="all, delete-orphan"
    )


class TableCell(Base):
    __tablename__ = "table_cells"

    id: Mapped[str] = id_column("cel")
    table_id: Mapped[str] = mapped_column(
        ForeignKey("tables.id", ondelete="CASCADE"), nullable=False, index=True
    )
    row: Mapped[int] = mapped_column(Integer, nullable=False)
    col: Mapped[int] = mapped_column(Integer, nullable=False)
    row_span: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    col_span: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    value_type: Mapped[str] = mapped_column(String(16), nullable=False, default="text")
    numeric_value: Mapped[float | None] = mapped_column(Float)
    date_value: Mapped[dt.date | None] = mapped_column(DateTime(timezone=False))
    currency: Mapped[str | None] = mapped_column(String(8))
    formula: Mapped[str | None] = mapped_column(String(512))
    is_header: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    region_id: Mapped[str | None] = mapped_column(
        ForeignKey("text_regions.id", ondelete="SET NULL")
    )
    style: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    edited_by_user: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    table: Mapped[TableRecord] = relationship(back_populates="cells")

    __table_args__ = (UniqueConstraint("table_id", "row", "col", name="uq_table_cell_position"),)


class ExtractedDocument(Base, TimestampMixin):
    """Structured extraction result (receipt / invoice) with evidence links."""

    __tablename__ = "extracted_documents"

    id: Mapped[str] = id_column("exd")
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_id: Mapped[str | None] = mapped_column(ForeignKey("document_pages.id", ondelete="CASCADE"))
    document_type: Mapped[str] = mapped_column(String(24), nullable=False, default="receipt")
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    fields: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    line_items: Mapped[list[Any]] = mapped_column(JsonType, default=list, nullable=False)
    field_confidence: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(80))


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = id_column("job")
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    parent_job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    guest_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("guest_sessions.id", ondelete="CASCADE")
    )
    api_key_id: Mapped[str | None] = mapped_column(ForeignKey("api_keys.id", ondelete="SET NULL"))
    type: Mapped[str] = mapped_column(String(32), nullable=False, default=JobType.FULL_PIPELINE)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=JobStatus.CREATED, index=True
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    queue: Mapped[str] = mapped_column(String(24), nullable=False, default="cpu")
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    current_stage: Mapped[str | None] = mapped_column(String(24))
    pages_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pages_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pages_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(48), index=True)
    error_message_safe: Mapped[str | None] = mapped_column(String(512))
    internal_error_reference: Mapped[str | None] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    credits_charged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    credits_refunded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_estimate: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    heartbeat_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), index=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    completed_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False, index=True
    )

    project: Mapped[Project | None] = relationship(back_populates="jobs")
    events: Mapped[list[JobEvent]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobEvent.created_at"
    )

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),
        Index("ix_jobs_queue_pending", "status", "priority", "created_at"),
        Index("ix_jobs_user_created", "user_id", "created_at"),
        Index("ix_jobs_stuck", "status", "heartbeat_at"),
    )

    @property
    def is_terminal(self) -> bool:
        return JobStatus(self.status).is_terminal


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[str] = id_column("jev")
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    message_key: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False, index=True
    )

    job: Mapped[Job] = relationship(back_populates="events")


class Export(Base):
    __tablename__ = "exports"

    id: Mapped[str] = id_column("exp")
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    format: Mapped[str] = mapped_column(String(24), nullable=False)
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"))
    settings: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    settings_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    download_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="exports")

    __table_args__ = (Index("ix_exports_project_format", "project_id", "format"),)


# --------------------------------------------------------------------------- #
# Billing
# --------------------------------------------------------------------------- #


class Plan(Base, TimestampMixin):
    __tablename__ = "plans"

    id: Mapped[str] = id_column("pln")
    code: Mapped[str] = mapped_column(String(24), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    monthly_credits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_usd_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_rub_kopecks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_upload_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=10485760)
    max_pdf_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    max_batch_files: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    retention_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=168)
    queue_priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    export_formats: Mapped[list[Any]] = mapped_column(JsonType, default=list, nullable=False)
    features: Mapped[list[Any]] = mapped_column(JsonType, default=list, nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    external_ids: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)


class Subscription(Base, TimestampMixin):
    __tablename__ = "subscriptions"

    id: Mapped[str] = id_column("sub")
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    plan_code: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=SubscriptionStatus.INCOMPLETE, index=True
    )
    provider: Mapped[str] = mapped_column(String(24), nullable=False, default="stripe")
    provider_subscription_id: Mapped[str | None] = mapped_column(String(128), index=True)
    provider_customer_id: Mapped[str | None] = mapped_column(String(128), index=True)
    current_period_start: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    current_period_end: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), index=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    canceled_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    grace_until: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)

    __table_args__ = (
        UniqueConstraint("provider", "provider_subscription_id", name="uq_subscription_external"),
    )


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"

    id: Mapped[str] = id_column("pay")
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL")
    )
    subscription_id: Mapped[str | None] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_payment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=PaymentStatus.PENDING, index=True
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="subscription")
    credits_granted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    refunded_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    invoice_url: Mapped[str | None] = mapped_column(String(1024))
    coupon_code: Mapped[str | None] = mapped_column(String(48))
    tax_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)

    __table_args__ = (
        UniqueConstraint("provider", "provider_payment_id", name="uq_payment_external"),
    )


class CreditWallet(Base, TimestampMixin):
    __tablename__ = "credit_wallets"

    id: Mapped[str] = id_column("wal")
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True, index=True
    )
    balance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lifetime_granted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lifetime_spent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    monthly_grant_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())

    user: Mapped[User | None] = relationship(back_populates="wallet")

    __table_args__ = (
        # A wallet may legitimately go negative when a paid-for grant is clawed
        # back after a refund or chargeback and the credits were already spent —
        # that is more honest than silently absorbing the loss. Ordinary spending
        # can never go below zero; ``services.credits.apply`` enforces that and
        # only bypasses it for explicit clawbacks. The bound here is a sanity
        # rail against a runaway bug, not the business rule.
        CheckConstraint("balance > -1000000", name="balance_sane"),
        CheckConstraint(
            "(user_id IS NOT NULL) <> (workspace_id IS NOT NULL)", name="wallet_single_owner"
        ),
    )


class CreditLedgerEntry(Base):
    """Append-only. Never updated, never deleted — the wallet balance is its sum."""

    __tablename__ = "credit_ledger_entries"

    id: Mapped[str] = id_column("led")
    wallet_id: Mapped[str] = mapped_column(
        ForeignKey("credit_wallets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False, default=LedgerReason.JOB_CHARGE)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    payment_id: Mapped[str | None] = mapped_column(ForeignKey("payments.id", ondelete="SET NULL"))
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(String(255))
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = (
        Index("ix_ledger_wallet_created", "wallet_id", "created_at"),
        CheckConstraint("delta <> 0", name="delta_non_zero"),
    )


class Coupon(Base, TimestampMixin):
    __tablename__ = "coupons"

    id: Mapped[str] = id_column("cpn")
    code: Mapped[str] = mapped_column(String(48), nullable=False, unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="percent")
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    applies_to_plan: Mapped[str | None] = mapped_column(String(24))
    max_redemptions: Mapped[int | None] = mapped_column(Integer)
    redemption_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_from: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    valid_until: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# --------------------------------------------------------------------------- #
# API access
# --------------------------------------------------------------------------- #


class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"

    id: Mapped[str] = id_column("key")
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    last_four: Mapped[str] = mapped_column(String(8), nullable=False)
    scopes: Mapped[list[Any]] = mapped_column(JsonType, default=list, nullable=False)
    test_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    expires_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    revoked_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())

    @property
    def is_active(self) -> bool:
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > dt.datetime.now(dt.UTC)


class WebhookEndpoint(Base, TimestampMixin):
    __tablename__ = "webhook_endpoints"

    id: Mapped[str] = id_column("whk")
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    events: Mapped[list[Any]] = mapped_column(JsonType, default=list, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    disabled_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = id_column("dlv")
    endpoint_id: Mapped[str] = mapped_column(
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DeliveryStatus.PENDING, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_snippet: Mapped[str | None] = mapped_column(String(512))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    next_attempt_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), index=True)
    delivered_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = (
        UniqueConstraint("endpoint_id", "event_id", name="uq_delivery_endpoint_event"),
    )


class IdempotencyRecord(Base):
    """Guards non-job POSTs (checkout, key creation) against duplicate submits."""

    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_body: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    expires_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), nullable=False, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------------- #
# Linguistic assets
# --------------------------------------------------------------------------- #


class Glossary(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "glossaries"

    id: Mapped[str] = id_column("gls")
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    source_language: Mapped[str] = mapped_column(String(16), nullable=False)
    target_language: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    terms: Mapped[list[GlossaryTerm]] = relationship(
        back_populates="glossary", cascade="all, delete-orphan"
    )


class GlossaryTerm(Base):
    __tablename__ = "glossary_terms"

    id: Mapped[str] = id_column("gtm")
    glossary_id: Mapped[str] = mapped_column(
        ForeignKey("glossaries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_term: Mapped[str] = mapped_column(String(255), nullable=False)
    target_term: Mapped[str] = mapped_column(String(255), nullable=False)
    case_sensitive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    whole_word: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    do_not_translate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    comment: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False
    )

    glossary: Mapped[Glossary] = relationship(back_populates="terms")

    __table_args__ = (
        UniqueConstraint("glossary_id", "source_term", name="uq_glossary_term_source"),
    )


class TranslationMemoryEntry(Base):
    __tablename__ = "translation_memory_entries"

    id: Mapped[str] = id_column("tme")
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    source_language: Mapped[str] = mapped_column(String(16), nullable=False)
    target_language: Mapped[str] = mapped_column(String(16), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    target_text: Mapped[str] = mapped_column(Text, nullable=False)
    quality: Mapped[float] = mapped_column(Float, nullable=False, default=0.8)
    confirmed_by_user: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    frequency: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "workspace_id",
            "source_hash",
            "source_language",
            "target_language",
            name="uq_tm_entry",
        ),
        Index("ix_tm_lookup", "source_language", "target_language", "source_hash"),
    )


# --------------------------------------------------------------------------- #
# Sharing
# --------------------------------------------------------------------------- #


class ShareLink(Base, TimestampMixin):
    __tablename__ = "share_links"

    id: Mapped[str] = id_column("shr")
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    export_id: Mapped[str | None] = mapped_column(ForeignKey("exports.id", ondelete="CASCADE"))
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    permission: Mapped[str] = mapped_column(
        String(24), nullable=False, default=SharePermission.VIEW
    )
    password_hash: Mapped[str | None] = mapped_column(String(255))
    show_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    watermark: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expires_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), index=True)
    max_views: Mapped[int | None] = mapped_column(Integer)
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())


class ShareView(Base):
    __tablename__ = "share_views"

    id: Mapped[str] = id_column("shv")
    share_link_id: Mapped[str] = mapped_column(
        ForeignKey("share_links.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(16), nullable=False, default="view")
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------------- #
# Providers, operations, content
# --------------------------------------------------------------------------- #


class ProviderConfiguration(Base, TimestampMixin):
    __tablename__ = "provider_configurations"

    id: Mapped[str] = id_column("prv")
    kind: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(48), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    config: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    secrets_encrypted: Mapped[dict[str, Any]] = mapped_column(
        JsonType, default=dict, nullable=False
    )
    daily_cost_limit_usd: Mapped[float | None] = mapped_column(Float)
    monthly_cost_limit_usd: Mapped[float | None] = mapped_column(Float)
    timeout_seconds: Mapped[int | None] = mapped_column(Integer)
    health_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    health_checked_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    health_detail: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (UniqueConstraint("kind", "name", name="uq_provider_kind_name"),)


class ProviderUsage(Base):
    __tablename__ = "provider_usage"

    id: Mapped[str] = id_column("pus")
    kind: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    model: Mapped[str | None] = mapped_column(String(80))
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="page")
    cost_micro_usd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error_code: Mapped[str | None] = mapped_column(String(48))
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = (Index("ix_provider_usage_daily", "provider", "created_at"),)


class AuditLog(Base):
    """Append-only record of administrative and security relevant actions."""

    __tablename__ = "audit_logs"

    id: Mapped[str] = id_column("aud")
    actor_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    actor_role: Mapped[str | None] = mapped_column(String(24))
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(48))
    target_id: Mapped[str | None] = mapped_column(String(64), index=True)
    reason: Mapped[str | None] = mapped_column(String(512))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False, index=True
    )


class FeatureFlag(Base, TimestampMixin):
    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rollout_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    plan_codes: Mapped[list[Any]] = mapped_column(JsonType, default=list, nullable=False)
    locales: Mapped[list[Any]] = mapped_column(JsonType, default=list, nullable=False)
    workspace_ids: Mapped[list[Any]] = mapped_column(JsonType, default=list, nullable=False)
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        CheckConstraint("rollout_percent BETWEEN 0 AND 100", name="rollout_percent_range"),
    )


class SeoPage(Base, TimestampMixin):
    __tablename__ = "seo_pages"

    id: Mapped[str] = id_column("seo")
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    locale: Mapped[str] = mapped_column(String(12), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="tool")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    h1: Mapped[str] = mapped_column(String(255), nullable=False)
    intro: Mapped[str | None] = mapped_column(Text)
    body_sections: Mapped[list[Any]] = mapped_column(JsonType, default=list, nullable=False)
    faq: Mapped[list[Any]] = mapped_column(JsonType, default=list, nullable=False)
    tool_slug: Mapped[str | None] = mapped_column(String(48))
    source_language: Mapped[str | None] = mapped_column(String(16))
    target_language: Mapped[str | None] = mapped_column(String(16))
    noindex: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    published: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    og_image_key: Mapped[str | None] = mapped_column(String(512))

    __table_args__ = (UniqueConstraint("path", "locale", name="uq_seo_page_path_locale"),)


class BlogPost(Base, TimestampMixin):
    __tablename__ = "blog_posts"

    id: Mapped[str] = id_column("pst")
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    locale: Mapped[str] = mapped_column(String(12), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    excerpt: Mapped[str] = mapped_column(String(500), nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    author_name: Mapped[str] = mapped_column(String(120), nullable=False, default="LingoImage AI")
    tags: Mapped[list[Any]] = mapped_column(JsonType, default=list, nullable=False)
    cover_image_key: Mapped[str | None] = mapped_column(String(512))
    published_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), index=True)
    reading_minutes: Mapped[int] = mapped_column(Integer, default=4, nullable=False)

    __table_args__ = (UniqueConstraint("slug", "locale", name="uq_blog_slug_locale"),)


class Redirect(Base, TimestampMixin):
    __tablename__ = "redirects"

    id: Mapped[str] = id_column("rdr")
    from_path: Mapped[str] = mapped_column(String(512), nullable=False, unique=True, index=True)
    to_path: Mapped[str] = mapped_column(String(512), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, default=301)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Testimonial(Base, TimestampMixin):
    """Only rows explicitly marked published are ever rendered."""

    __tablename__ = "testimonials"

    id: Mapped[str] = id_column("tst")
    author_name: Mapped[str] = mapped_column(String(120), nullable=False)
    author_title: Mapped[str | None] = mapped_column(String(160))
    locale: Mapped[str] = mapped_column(String(12), nullable=False, default="en")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1024))
    consent_reference: Mapped[str | None] = mapped_column(String(255))
    published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ContactRequest(Base):
    __tablename__ = "contact_requests"

    id: Mapped[str] = id_column("cnt")
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="general")
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(40))
    request_id: Mapped[str | None] = mapped_column(String(64))
    attachment_keys: Mapped[list[Any]] = mapped_column(JsonType, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open", index=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False, index=True
    )


class AbuseEvent(Base):
    __tablename__ = "abuse_events"

    id: Mapped[str] = id_column("abs")
    kind: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    ip_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False, index=True
    )


class AnalyticsEvent(Base):
    """First-party product analytics. Never stores document content."""

    __tablename__ = "analytics_events"

    id: Mapped[str] = id_column("evt")
    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    anonymous_id: Mapped[str | None] = mapped_column(String(64), index=True)
    session_id: Mapped[str | None] = mapped_column(String(64))
    locale: Mapped[str | None] = mapped_column(String(12))
    properties: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    utm: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    first_touch: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = (Index("ix_analytics_name_created", "name", "created_at"),)


class StatusIncident(Base, TimestampMixin):
    __tablename__ = "status_incidents"

    id: Mapped[str] = id_column("inc")
    component: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="degraded")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    is_scheduled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    manual_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id: Mapped[str] = id_column("ntf")
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="email")
    template: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="sent")
    provider_message_id: Mapped[str | None] = mapped_column(String(160))
    error: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False, index=True
    )


class DeletionRecord(Base):
    """Proof of deletion without retaining any of the deleted content."""

    __tablename__ = "deletion_records"

    id: Mapped[str] = id_column("del")
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(48), nullable=False)
    objects_removed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bytes_removed: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    initiated_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=func.now(), nullable=False, index=True
    )


__all__ = [
    "AbuseEvent",
    "AnalyticsEvent",
    "ApiKey",
    "Asset",
    "AuditLog",
    "BlogPost",
    "ContactRequest",
    "Coupon",
    "CreditLedgerEntry",
    "CreditWallet",
    "DeletionRecord",
    "DocumentPage",
    "Export",
    "ExtractedDocument",
    "FeatureFlag",
    "Glossary",
    "GlossaryTerm",
    "GuestSession",
    "IdempotencyRecord",
    "Job",
    "JobEvent",
    "LoginEvent",
    "NotificationLog",
    "OAuthAccount",
    "Payment",
    "Plan",
    "Project",
    "ProjectFolder",
    "ProviderConfiguration",
    "ProviderUsage",
    "Redirect",
    "RegionTranslation",
    "SeoPage",
    "Session",
    "ShareLink",
    "ShareView",
    "StatusIncident",
    "Subscription",
    "TableCell",
    "TableRecord",
    "Testimonial",
    "TextRegion",
    "TranslationMemoryEntry",
    "TwoFactorSecret",
    "User",
    "VerificationToken",
    "WebhookDelivery",
    "WebhookEndpoint",
    "Workspace",
    "WorkspaceInvitation",
    "WorkspaceMember",
]
