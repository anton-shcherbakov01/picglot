"""Project, asset and page persistence."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from typing import Any

from PIL import Image
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from picglot.core.config import settings
from picglot.core.errors import AppError, ErrorCode
from picglot.core.logging import get_logger
from picglot.db.models import (
    Asset,
    DocumentPage,
    Export,
    Project,
    RegionTranslation,
    TableCell,
    TableRecord,
    TextRegion,
    User,
    WorkspaceMember,
)
from picglot.domain.enums import AssetKind, ProjectStatus, ToolType, WorkspaceRole
from picglot.services import storage
from picglot.vision.types import BoundingBox, Region, TextStyle

log = get_logger(__name__)


def create_project(
    session: Session,
    *,
    tool_type: ToolType | str,
    name: str,
    owner_user_id: str | None = None,
    workspace_id: str | None = None,
    guest_session_id: str | None = None,
    source_language: str | None = None,
    target_language: str | None = None,
    plan_code: str | None = None,
    settings_payload: dict[str, Any] | None = None,
) -> Project:
    retention = settings.retention_hours(plan_code or ("guest" if guest_session_id else "free"))
    project = Project(
        owner_user_id=owner_user_id,
        workspace_id=workspace_id,
        guest_session_id=guest_session_id,
        name=name[:255] or "Untitled",
        tool_type=str(tool_type),
        source_language=source_language,
        target_language=target_language,
        status=ProjectStatus.DRAFT,
        settings=settings_payload or {},
        expires_at=datetime.now(UTC) + timedelta(hours=retention),
    )
    session.add(project)
    session.flush()
    return project


def get_project(session: Session, project_id: str, *, with_pages: bool = False) -> Project:
    statement = select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
    if with_pages:
        statement = statement.options(
            selectinload(Project.pages)
            .selectinload(DocumentPage.regions)
            .selectinload(TextRegion.translations)
        )
    project = session.execute(statement).scalar_one_or_none()
    if project is None:
        raise AppError(code=ErrorCode.NOT_FOUND, internal=f"project {project_id}")
    return project


def assert_can_read(
    session: Session,
    project: Project,
    *,
    user: User | None,
    guest_session_id: str | None = None,
) -> None:
    """Ownership check. Never leaks whether a foreign project exists."""
    if user is not None:
        if project.owner_user_id == user.id:
            return
        if user.is_admin:
            return
        if project.workspace_id and _workspace_role(session, project.workspace_id, user.id):
            return
    elif guest_session_id and project.guest_session_id == guest_session_id:
        return
    raise AppError(code=ErrorCode.NOT_FOUND, internal="project access denied")


def assert_can_write(
    session: Session,
    project: Project,
    *,
    user: User | None,
    guest_session_id: str | None = None,
) -> None:
    if user is not None and project.owner_user_id == user.id:
        return
    if user is not None and project.workspace_id:
        role = _workspace_role(session, project.workspace_id, user.id)
        if role and WorkspaceRole(role).can(WorkspaceRole.EDITOR):
            return
    if guest_session_id and project.guest_session_id == guest_session_id:
        return
    raise AppError(code=ErrorCode.NOT_FOUND, internal="project write denied")


def _workspace_role(session: Session, workspace_id: str, user_id: str) -> str | None:
    return session.execute(
        select(WorkspaceMember.role).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    ).scalar_one_or_none()


def list_projects(
    session: Session,
    *,
    user_id: str | None = None,
    workspace_id: str | None = None,
    guest_session_id: str | None = None,
    tool_type: str | None = None,
    status: str | None = None,
    search: str | None = None,
    folder_id: str | None = None,
    include_deleted: bool = False,
    limit: int = 20,
    offset: int = 0,
    order: str = "updated_desc",
) -> tuple[list[Project], int]:
    statement = select(Project)
    if not include_deleted:
        statement = statement.where(Project.deleted_at.is_(None))
    else:
        statement = statement.where(Project.deleted_at.is_not(None))

    if workspace_id:
        statement = statement.where(Project.workspace_id == workspace_id)
    elif user_id:
        statement = statement.where(Project.owner_user_id == user_id)
    elif guest_session_id:
        statement = statement.where(Project.guest_session_id == guest_session_id)
    else:
        return [], 0

    if tool_type:
        statement = statement.where(Project.tool_type == tool_type)
    if status:
        statement = statement.where(Project.status == status)
    if folder_id:
        statement = statement.where(Project.folder_id == folder_id)
    if search:
        statement = statement.where(Project.name.ilike(f"%{search[:80]}%"))

    total = session.execute(select(func.count()).select_from(statement.subquery())).scalar_one()

    ordering = {
        "updated_desc": Project.updated_at.desc(),
        "updated_asc": Project.updated_at.asc(),
        "created_desc": Project.created_at.desc(),
        "created_asc": Project.created_at.asc(),
        "name_asc": Project.name.asc(),
        "name_desc": Project.name.desc(),
    }.get(order, Project.updated_at.desc())

    rows = list(session.execute(statement.order_by(ordering).limit(limit).offset(offset)).scalars())
    return rows, int(total)


def rename_project(session: Session, project: Project, name: str) -> Project:
    project.name = (name or "").strip()[:255] or project.name
    return project


def duplicate_project(session: Session, project: Project) -> Project:
    """Copy structure and text; storage objects are shared by reference."""
    clone = Project(
        owner_user_id=project.owner_user_id,
        workspace_id=project.workspace_id,
        guest_session_id=project.guest_session_id,
        folder_id=project.folder_id,
        name=f"{project.name} (copy)"[:255],
        tool_type=project.tool_type,
        source_language=project.source_language,
        target_language=project.target_language,
        status=project.status,
        page_count=project.page_count,
        settings=dict(project.settings or {}),
        expires_at=project.expires_at,
    )
    session.add(clone)
    session.flush()

    for page in project.pages:
        page_clone = DocumentPage(
            project_id=clone.id,
            source_asset_id=page.source_asset_id,
            normalized_asset_id=page.normalized_asset_id,
            preview_asset_id=page.preview_asset_id,
            thumbnail_asset_id=page.thumbnail_asset_id,
            cleaned_asset_id=page.cleaned_asset_id,
            rendered_asset_id=page.rendered_asset_id,
            page_number=page.page_number,
            width=page.width,
            height=page.height,
            rotation=page.rotation,
            detected_language=page.detected_language,
            status=page.status,
            ocr_confidence=page.ocr_confidence,
            metadata_json=dict(page.metadata_json or {}),
        )
        session.add(page_clone)
        session.flush()
        for region in page.regions:
            region_clone = TextRegion(
                page_id=page_clone.id,
                region_type=region.region_type,
                polygon=list(region.polygon or []),
                bounding_box=dict(region.bounding_box or {}),
                rotation=region.rotation,
                reading_order=region.reading_order,
                line_number=region.line_number,
                group_id=region.group_id,
                detected_language=region.detected_language,
                source_text=region.source_text,
                normalized_text=region.normalized_text,
                confidence=region.confidence,
                style=dict(region.style or {}),
                metadata_json=dict(region.metadata_json or {}),
                skip_translation=region.skip_translation,
            )
            session.add(region_clone)
            session.flush()
            for translation in region.translations:
                session.add(
                    RegionTranslation(
                        text_region_id=region_clone.id,
                        target_language=translation.target_language,
                        translated_text=translation.translated_text,
                        provider=translation.provider,
                        model=translation.model,
                        source=translation.source,
                        confidence=translation.confidence,
                        character_count=translation.character_count,
                        is_active=translation.is_active,
                    )
                )
    return clone


def soft_delete(session: Session, project: Project) -> None:
    project.deleted_at = datetime.now(UTC)
    project.expires_at = datetime.now(UTC) + timedelta(hours=settings.retention_trash_hours)


def restore(session: Session, project: Project, *, plan_code: str | None = None) -> None:
    project.deleted_at = None
    project.expires_at = datetime.now(UTC) + timedelta(
        hours=settings.retention_hours(plan_code or "free")
    )


def purge(session: Session, project: Project) -> int:
    """Delete storage objects then the rows. Returns objects removed."""
    backend = storage.get_storage()
    keys = [asset.storage_key for asset in project.assets]
    removed = backend.delete_many(keys) if keys else 0
    session.delete(project)
    log.info("project.purged", project_id=project.id, objects=removed)
    return removed


# --------------------------------------------------------------------------- #
# Assets
# --------------------------------------------------------------------------- #
def store_asset(
    session: Session,
    project: Project,
    *,
    data: bytes,
    kind: AssetKind | str,
    mime_type: str,
    extension: str,
    original_filename: str | None = None,
    width: int | None = None,
    height: int | None = None,
    page_count: int | None = None,
    metadata: dict[str, Any] | None = None,
    ttl_hours: int | None = None,
) -> Asset:
    prefix = {
        AssetKind.ORIGINAL: storage.PREFIX_ORIGINAL,
        AssetKind.EXPORT: storage.PREFIX_EXPORT,
        AssetKind.PREVIEW: storage.PREFIX_PREVIEW,
        AssetKind.THUMBNAIL: storage.PREFIX_PREVIEW,
    }.get(AssetKind(str(kind)), storage.PREFIX_INTERMEDIATE)
    if project.guest_session_id:
        prefix = storage.PREFIX_GUEST

    key = storage.build_key(prefix, project_id=project.id, extension=extension)
    stored = storage.get_storage().put(key, data, content_type=mime_type)

    if ttl_hours is None:
        ttl_hours = (
            settings.retention_intermediate_hours
            if AssetKind(str(kind)) in {AssetKind.INTERMEDIATE, AssetKind.MASK, AssetKind.CLEANED}
            else None
        )
    expires_at = datetime.now(UTC) + timedelta(hours=ttl_hours) if ttl_hours else project.expires_at

    asset = Asset(
        project_id=project.id,
        kind=str(kind),
        storage_key=stored.key,
        original_filename=original_filename,
        mime_type=mime_type,
        byte_size=stored.byte_size,
        width=width,
        height=height,
        page_count=page_count,
        checksum=stored.checksum,
        metadata_json=metadata or {},
        expires_at=expires_at,
    )
    session.add(asset)
    session.flush()
    return asset


def store_image_asset(
    session: Session,
    project: Project,
    image: Image.Image,
    *,
    kind: AssetKind | str,
    fmt: str = "PNG",
    quality: int = 92,
    metadata: dict[str, Any] | None = None,
) -> Asset:
    from picglot.vision.preprocess import encode

    data = encode(image, fmt, quality)
    extension = {"PNG": "png", "JPEG": "jpg", "JPG": "jpg", "WEBP": "webp"}.get(fmt.upper(), "png")
    mime = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[extension]
    return store_asset(
        session,
        project,
        data=data,
        kind=kind,
        mime_type=mime,
        extension=extension,
        width=image.width,
        height=image.height,
        metadata=metadata,
    )


def load_image_asset(asset: Asset | None) -> Image.Image | None:
    if asset is None:
        return None
    data = storage.get_storage().get(asset.storage_key)
    return Image.open(io.BytesIO(data)).convert("RGB")


def get_asset(session: Session, asset_id: str) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise AppError(code=ErrorCode.NOT_FOUND, internal=f"asset {asset_id}")
    return asset


# --------------------------------------------------------------------------- #
# Pages and regions
# --------------------------------------------------------------------------- #
def create_page(
    session: Session,
    project: Project,
    *,
    page_number: int,
    width: int,
    height: int,
    source_asset_id: str | None = None,
    rotation: int = 0,
    metadata: dict[str, Any] | None = None,
) -> DocumentPage:
    page = DocumentPage(
        project_id=project.id,
        page_number=page_number,
        width=width,
        height=height,
        rotation=rotation,
        source_asset_id=source_asset_id,
        status="pending",
        metadata_json=metadata or {},
    )
    session.add(page)
    session.flush()
    return page


def replace_regions(
    session: Session, page: DocumentPage, regions: list[Region]
) -> list[TextRegion]:
    """Persist a fresh OCR result, discarding any previous machine output."""
    for existing in list(page.regions):
        if not existing.edited_by_user:
            session.delete(existing)
    session.flush()

    rows: list[TextRegion] = []
    for region in regions:
        row = TextRegion(
            id=region.id,
            page_id=page.id,
            region_type=str(region.region_type),
            polygon=[list(point) for point in region.polygon],
            bounding_box=region.bounding_box.as_dict(),
            rotation=region.rotation,
            reading_order=region.reading_order,
            line_number=region.line_number,
            group_id=region.group_id,
            detected_language=region.detected_language,
            source_text=region.text,
            normalized_text=region.normalized_text,
            corrections=region.corrections,
            confidence=region.confidence,
            low_confidence_spans=region.low_confidence_spans,
            style=region.style.as_dict(),
            metadata_json=region.metadata,
            skip_translation=region.skip_translation,
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def region_to_domain(row: TextRegion) -> Region:
    return Region(
        id=row.id,
        polygon=[(float(point[0]), float(point[1])) for point in (row.polygon or [])],
        bounding_box=BoundingBox.from_dict(row.bounding_box or {}),
        text=row.source_text or "",
        normalized_text=row.normalized_text,
        confidence=row.confidence,
        rotation=float(row.rotation or 0),
        region_type=row.region_type,  # type: ignore[arg-type]
        detected_language=row.detected_language,
        line_number=row.line_number,
        group_id=row.group_id,
        reading_order=int(row.reading_order or 0),
        style=TextStyle.from_dict(row.style or {}),
        low_confidence_spans=list(row.low_confidence_spans or []),
        corrections=list(row.corrections or []),
        metadata=dict(row.metadata_json or {}),
        skip_translation=bool(row.skip_translation),
    )


def active_translation(row: TextRegion, target_language: str) -> RegionTranslation | None:
    for translation in row.translations:
        if translation.target_language == target_language and translation.is_active:
            return translation
    return None


def save_tables(session: Session, page: DocumentPage, tables: list[Any]) -> list[TableRecord]:
    for existing in list(page.tables):
        session.delete(existing)
    session.flush()

    records: list[TableRecord] = []
    for table in tables:
        record = TableRecord(
            page_id=page.id,
            index_on_page=table.index,
            bounding_box=table.bounding_box.as_dict(),
            row_count=table.rows,
            column_count=table.cols,
            has_header=table.has_header,
            confidence=table.confidence,
            structure_ambiguous=table.structure_ambiguous,
            title=table.title,
        )
        session.add(record)
        session.flush()
        for cell in table.cells:
            session.add(
                TableCell(
                    table_id=record.id,
                    row=cell.row,
                    col=cell.col,
                    row_span=cell.row_span,
                    col_span=cell.col_span,
                    raw_text=cell.text,
                    value_type=cell.value_type,
                    numeric_value=cell.numeric_value,
                    currency=cell.currency,
                    is_header=cell.is_header,
                    confidence=cell.confidence,
                    region_id=cell.region_id,
                )
            )
        records.append(record)
    session.flush()
    return records


def touch(project: Project) -> None:
    project.updated_at = datetime.now(UTC)
    project.document_version = int(project.document_version) + 1


def find_cached_export(
    session: Session, project_id: str, fmt: str, settings_hash: str
) -> Export | None:
    """Re-exporting with identical settings must not re-run or re-charge."""
    return (
        session.execute(
            select(Export)
            .where(
                Export.project_id == project_id,
                Export.format == fmt,
                Export.settings_hash == settings_hash,
                or_(Export.expires_at.is_(None), Export.expires_at > datetime.now(UTC)),
            )
            .order_by(Export.created_at.desc())
        )
        .scalars()
        .first()
    )
