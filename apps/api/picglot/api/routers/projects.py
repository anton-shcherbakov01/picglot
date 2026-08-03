"""Upload, processing, project access, the editor, exports and sharing."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Request, Response, UploadFile, status
from sqlalchemy import select

from picglot.api.deps import (
    CsrfProtected,
    CurrentIdentity,
    CurrentUser,
    DbSession,
    IdempotencyKey,
    Pagination,
    client_ip,
    ensure_guest_session,
    require_not_maintenance,
)
from picglot.core.config import settings
from picglot.core.errors import AppError, ErrorCode
from picglot.core.logging import get_logger
from picglot.core.ratelimit import uploads as upload_limit
from picglot.core.security import hash_ip
from picglot.db.models import Asset, DocumentPage, Export, Job, Project, TableCell, TextRegion
from picglot.domain import credits as credit_rules
from picglot.domain import tools as tool_catalog
from picglot.domain.enums import (
    AssetKind,
    ExportFormat,
    JobType,
    ProjectStatus,
    RegionType,
    RenderMode,
    ToolType,
)
from picglot.schemas import (
    CostEstimateOut,
    CreateJobRequest,
    ExportOut,
    ExportRequest,
    JobOut,
    PageOut,
    ProjectDetailOut,
    ProjectListOut,
    ProjectOut,
    RegionCreate,
    RegionMergeRequest,
    RegionOut,
    RegionSplitRequest,
    RegionUpdate,
    RenameProjectRequest,
    ReocrRequest,
    RerenderRequest,
    RetranslateRequest,
    ShareCreateRequest,
    ShareOut,
    TableCellUpdate,
    TranslationOut,
)
from picglot.services import credits as credit_service
from picglot.services import exports as export_service
from picglot.services import files as file_service
from picglot.services import jobs as job_service
from picglot.services import lifecycle, storage
from picglot.services import projects as project_service
from picglot.services import share as share_service
from picglot.services import translation as translation_service

log = get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["projects"])


# --------------------------------------------------------------------------- #
# Upload + process
# --------------------------------------------------------------------------- #
@router.post("/process", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def upload_and_process(
    request: Request,
    response: Response,
    session: DbSession,
    identity: CurrentIdentity,
    idempotency: IdempotencyKey,
    file: Annotated[UploadFile, File()],
    payload: Annotated[str, Form(alias="options")] = "{}",
) -> JobOut:
    """Single-call upload → job creation. This is what the web app uses."""
    require_not_maintenance()

    try:
        parsed = CreateJobRequest.model_validate_json(payload or "{}")
    except Exception as exc:
        raise AppError(
            code=ErrorCode.VALIDATION_FAILED,
            details={"field": "options"},
            internal=str(exc)[:200],
        ) from exc

    spec = tool_catalog.require(parsed.tool)
    limit_key = identity.user_id or hash_ip(client_ip(request)) or "anon"
    upload_limit(limit_key).raise_if_blocked()

    data = file.file.read()
    plan_code = identity.plan_code
    prepared, identity_info = file_service.prepare_upload(
        data, filename=file.filename, plan_code=plan_code
    )
    if not tool_catalog.accepts_extension(spec, identity_info.extension):
        raise AppError(
            code=ErrorCode.UNSUPPORTED_FILE_TYPE,
            details={
                "tool": spec.slug,
                "detected": identity_info.extension,
                "accepts": list(spec.accepts),
            },
        )

    pages = int(identity_info.detail.get("page_count", 1))
    guest = None
    if identity.user is None:
        guest = ensure_guest_session(request, response, session, identity)
        from picglot.services import auth as auth_service

        auth_service.consume_guest_pages(session, guest, pages)

    project = project_service.create_project(
        session,
        tool_type=parsed.tool,
        name=parsed.project_name or file_service.safe_filename(file.filename, fallback="Untitled"),
        owner_user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        guest_session_id=guest.id if guest else None,
        source_language=parsed.source_language,
        target_language=parsed.target_language,
        plan_code=plan_code,
        settings_payload={"render_mode": str(parsed.render_mode)},
    )
    project_service.store_asset(
        session,
        project,
        data=prepared,
        kind=AssetKind.ORIGINAL,
        mime_type=identity_info.mime_type,
        extension=identity_info.extension,
        original_filename=file_service.safe_filename(file.filename),
        page_count=pages,
        metadata=identity_info.detail,
    )
    project.page_count = pages
    project.status = ProjectStatus.PROCESSING

    job = job_service.create_job(
        session,
        project=project,
        job_type=spec.job_type,
        user=identity.user,
        workspace_id=identity.workspace_id,
        guest_session_id=guest.id if guest else None,
        api_key_id=identity.api_key.id if identity.api_key else None,
        idempotency_key=idempotency,
        pages_total=pages,
        payload={
            "translate": parsed.translate and bool(parsed.target_language),
            "render_mode": str(parsed.render_mode),
            "pages": parsed.pages,
            "options": parsed.options.model_dump(),
            "export_formats": [str(item) for item in parsed.export_formats],
            "filename": file_service.safe_filename(file.filename),
        },
    )
    session.commit()
    job_service.dispatch(job)
    return JobOut.model_validate(job_service.summarize(job))


@router.post("/batch", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def upload_batch(
    request: Request,
    session: DbSession,
    identity: CurrentIdentity,
    idempotency: IdempotencyKey,
    files: Annotated[list[UploadFile], File()],
    payload: Annotated[str, Form(alias="options")] = "{}",
) -> JobOut:
    """Fan a multi-file upload out to one child job per file.

    The parent job owns the ZIP and the CSV report; each child is an ordinary
    pipeline job, so a single bad file fails alone instead of the whole batch.
    """
    require_not_maintenance()

    from picglot.services import batch as batch_service

    if not settings.feature_batch:
        raise AppError(code=ErrorCode.FEATURE_DISABLED, details={"tool": "batch"})
    # Batches are an account feature: a guest has no wallet to charge or refund.
    if identity.user is None:
        raise AppError(code=ErrorCode.UNAUTHENTICATED, details={"tool": "batch"})

    try:
        parsed = CreateJobRequest.model_validate_json(payload or "{}")
    except Exception as exc:
        raise AppError(
            code=ErrorCode.VALIDATION_FAILED,
            details={"field": "options"},
            internal=str(exc)[:200],
        ) from exc

    # `tool` names the per-file tool; the batch wrapper is implied by the route.
    child_spec = tool_catalog.require(parsed.tool)
    if child_spec.type == ToolType.BATCH:
        raise AppError(code=ErrorCode.VALIDATION_FAILED, details={"reason": "batch_cannot_nest"})

    plan_code = identity.plan_code
    limit = settings.batch_file_limit(plan_code)
    if not files:
        raise AppError(code=ErrorCode.VALIDATION_FAILED, details={"field": "files"})
    if len(files) > limit:
        raise AppError(
            code=ErrorCode.BATCH_LIMIT_EXCEEDED,
            details={"limit": limit, "received": len(files), "plan": plan_code},
        )

    limit_key = identity.user_id or hash_ip(client_ip(request)) or "anon"
    upload_limit(limit_key).raise_if_blocked()

    # Validate every file before creating anything, so a rejected file does not
    # leave half a batch behind.
    prepared_files: list[tuple[bytes, Any, str, int]] = []
    for upload in files:
        data = upload.file.read()
        prepared, info = file_service.prepare_upload(
            data, filename=upload.filename, plan_code=plan_code
        )
        if not tool_catalog.accepts_extension(child_spec, info.extension):
            raise AppError(
                code=ErrorCode.UNSUPPORTED_FILE_TYPE,
                details={
                    "tool": child_spec.slug,
                    "filename": file_service.safe_filename(upload.filename),
                    "detected": info.extension,
                    "accepts": list(child_spec.accepts),
                },
            )
        pages = int(info.detail.get("page_count", 1))
        prepared_files.append((prepared, info, file_service.safe_filename(upload.filename), pages))

    total_pages = sum(pages for *_, pages in prepared_files)
    export_formats = [str(item) for item in parsed.export_formats] or [str(ExportFormat.PNG)]

    parent_project = project_service.create_project(
        session,
        tool_type=ToolType.BATCH,
        name=parsed.project_name or f"Batch of {len(prepared_files)}",
        owner_user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        guest_session_id=None,
        source_language=parsed.source_language,
        target_language=parsed.target_language,
        plan_code=plan_code,
        settings_payload={"render_mode": str(parsed.render_mode)},
    )
    parent_project.status = ProjectStatus.PROCESSING

    parent = job_service.create_job(
        session,
        project=parent_project,
        job_type=JobType.BATCH,
        user=identity.user,
        workspace_id=identity.workspace_id,
        api_key_id=identity.api_key.id if identity.api_key else None,
        idempotency_key=idempotency,
        pages_total=total_pages,
        payload={
            "tool": str(parsed.tool),
            "translate": parsed.translate and bool(parsed.target_language),
            "render_mode": str(parsed.render_mode),
            "options": parsed.options.model_dump(),
            "export_formats": export_formats,
            "file_count": len(prepared_files),
        },
    )

    for prepared, info, filename, pages in prepared_files:
        child_project = project_service.create_project(
            session,
            tool_type=parsed.tool,
            name=filename,
            owner_user_id=identity.user_id,
            workspace_id=identity.workspace_id,
            guest_session_id=None,
            source_language=parsed.source_language,
            target_language=parsed.target_language,
            plan_code=plan_code,
            settings_payload={"render_mode": str(parsed.render_mode)},
        )
        project_service.store_asset(
            session,
            child_project,
            data=prepared,
            kind=AssetKind.ORIGINAL,
            mime_type=info.mime_type,
            extension=info.extension,
            original_filename=filename,
            page_count=pages,
            metadata=info.detail,
        )
        child_project.page_count = pages
        child_project.status = ProjectStatus.PROCESSING
        batch_service.create_child(
            session,
            parent=parent,
            project=child_project,
            payload={
                "translate": parsed.translate and bool(parsed.target_language),
                "render_mode": str(parsed.render_mode),
                "pages": None,
                "options": parsed.options.model_dump(),
                "export_formats": export_formats,
                "filename": filename,
            },
            pages=pages,
            user=identity.user,
        )

    session.commit()
    job_service.dispatch(parent)
    return JobOut.model_validate(job_service.summarize(parent))


@router.post("/estimate", response_model=CostEstimateOut)
def estimate_cost(
    payload: CreateJobRequest,
    pages: int,
    session: DbSession,
    identity: CurrentIdentity,
) -> CostEstimateOut:
    """Quote a price before the user commits. Same code path as the charge."""
    estimate = credit_rules.estimate_for_job(
        job_type=tool_catalog.require(payload.tool).job_type,
        tool=payload.tool,
        pages=max(1, pages),
        translate=payload.translate and bool(payload.target_language),
        options=payload.options.model_dump(),
    )
    balance = None
    sufficient = True
    if identity.user or identity.workspace_id:
        wallet = credit_service.get_or_create_wallet(
            session,
            user_id=None if identity.workspace_id else identity.user_id,
            workspace_id=identity.workspace_id,
        )
        balance = int(wallet.balance)
        sufficient = balance >= estimate.total
    return CostEstimateOut(
        pages=estimate.pages,
        total_credits=estimate.total,
        breakdown=estimate.breakdown,
        balance=balance,
        sufficient=sufficient,
    )


# --------------------------------------------------------------------------- #
# Project access
# --------------------------------------------------------------------------- #
@router.get("/projects", response_model=ProjectListOut)
def list_projects(
    session: DbSession,
    identity: CurrentIdentity,
    pagination: Pagination,
    tool: str | None = None,
    project_status: str | None = None,
    search: str | None = None,
    folder_id: str | None = None,
    trash: bool = False,
    order: str = "updated_desc",
) -> ProjectListOut:
    limit, offset = pagination
    rows, total = project_service.list_projects(
        session,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        guest_session_id=identity.guest_id if identity.user is None else None,
        tool_type=tool,
        status=project_status,
        search=search,
        folder_id=folder_id,
        include_deleted=trash,
        limit=limit,
        offset=offset,
        order=order,
    )
    return ProjectListOut(
        items=[_project_out(session, project) for project in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/projects/{project_id}", response_model=ProjectDetailOut)
def get_project(project_id: str, session: DbSession, identity: CurrentIdentity) -> ProjectDetailOut:
    project = project_service.get_project(session, project_id, with_pages=True)
    project_service.assert_can_read(
        session, project, user=identity.user, guest_session_id=identity.guest_id
    )
    project.last_opened_at = datetime.now(UTC)
    session.commit()
    return _project_detail(session, project)


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def rename_project(
    project_id: str,
    payload: RenameProjectRequest,
    session: DbSession,
    identity: CurrentIdentity,
    _csrf: CsrfProtected,
) -> ProjectOut:
    project = project_service.get_project(session, project_id)
    project_service.assert_can_write(
        session, project, user=identity.user, guest_session_id=identity.guest_id
    )
    project_service.rename_project(session, project, payload.name)
    session.commit()
    return _project_out(session, project)


@router.post("/projects/{project_id}/duplicate", response_model=ProjectOut)
def duplicate_project(
    project_id: str, session: DbSession, identity: CurrentIdentity, _csrf: CsrfProtected
) -> ProjectOut:
    project = project_service.get_project(session, project_id, with_pages=True)
    project_service.assert_can_read(
        session, project, user=identity.user, guest_session_id=identity.guest_id
    )
    clone = project_service.duplicate_project(session, project)
    session.commit()
    return _project_out(session, clone)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: str,
    session: DbSession,
    identity: CurrentIdentity,
    _csrf: CsrfProtected,
    permanent: bool = False,
) -> Response:
    project = project_service.get_project(session, project_id)
    project_service.assert_can_write(
        session, project, user=identity.user, guest_session_id=identity.guest_id
    )
    if permanent:
        lifecycle.delete_project_now(session, project)
    else:
        project_service.soft_delete(session, project)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/projects/{project_id}/restore", response_model=ProjectOut)
def restore_project(
    project_id: str, session: DbSession, identity: CurrentIdentity, _csrf: CsrfProtected
) -> ProjectOut:
    project = session.execute(select(Project).where(Project.id == project_id)).scalar_one_or_none()
    if project is None:
        raise AppError(code=ErrorCode.NOT_FOUND)
    project_service.assert_can_write(
        session, project, user=identity.user, guest_session_id=identity.guest_id
    )
    project_service.restore(session, project, plan_code=identity.plan_code)
    session.commit()
    return _project_out(session, project)


# --------------------------------------------------------------------------- #
# Editor
# --------------------------------------------------------------------------- #
@router.patch("/projects/{project_id}/regions/{region_id}", response_model=RegionOut)
def update_region(
    project_id: str,
    region_id: str,
    payload: RegionUpdate,
    session: DbSession,
    identity: CurrentIdentity,
    _csrf: CsrfProtected,
) -> RegionOut:
    project, region = _region_for_write(session, identity, project_id, region_id)

    # Optimistic concurrency: a stale editor tab must not clobber newer text.
    if payload.version is not None and int(payload.version) != int(region.version):
        raise AppError(
            code=ErrorCode.VERSION_CONFLICT,
            details={"expected": int(region.version), "received": int(payload.version)},
        )

    if payload.source_text is not None:
        region.source_text = payload.source_text
        region.normalized_text = payload.source_text
        region.edited_by_user = True
    if payload.region_type is not None:
        region.region_type = str(payload.region_type)
    if payload.bounding_box is not None:
        region.bounding_box = payload.bounding_box.model_dump()
        region.polygon = [
            [payload.bounding_box.x, payload.bounding_box.y],
            [payload.bounding_box.x + payload.bounding_box.width, payload.bounding_box.y],
            [
                payload.bounding_box.x + payload.bounding_box.width,
                payload.bounding_box.y + payload.bounding_box.height,
            ],
            [payload.bounding_box.x, payload.bounding_box.y + payload.bounding_box.height],
        ]
    if payload.polygon is not None:
        region.polygon = payload.polygon
    if payload.rotation is not None:
        region.rotation = payload.rotation
    if payload.detected_language is not None:
        region.detected_language = payload.detected_language
    if payload.style is not None:
        region.style = {**(region.style or {}), **payload.style}
    if payload.skip_translation is not None:
        region.skip_translation = payload.skip_translation
    if payload.reading_order is not None:
        region.reading_order = payload.reading_order

    if payload.translated_text is not None:
        _set_translation(
            session,
            region,
            payload.translated_text,
            project.target_language or "en",
            manual=True,
        )
        # A human correction is the best possible memory entry.
        translation_service.tm_store(
            session,
            source_text=region.normalized_text or region.source_text,
            target_text=payload.translated_text,
            source_language=region.detected_language or project.source_language,
            target_language=project.target_language or "en",
            user_id=project.owner_user_id,
            workspace_id=project.workspace_id,
            confirmed=True,
            quality_score=1.0,
        )

    region.version = int(region.version) + 1
    region.edited_by_user = True
    project_service.touch(project)
    session.commit()
    return _region_out(region)


@router.post("/projects/{project_id}/regions", response_model=RegionOut, status_code=201)
def create_region(
    project_id: str,
    payload: RegionCreate,
    session: DbSession,
    identity: CurrentIdentity,
    _csrf: CsrfProtected,
) -> RegionOut:
    project = project_service.get_project(session, project_id, with_pages=True)
    project_service.assert_can_write(
        session, project, user=identity.user, guest_session_id=identity.guest_id
    )
    page = next((item for item in project.pages if item.id == payload.page_id), None)
    if page is None:
        raise AppError(code=ErrorCode.NOT_FOUND, details={"field": "page_id"})

    box = payload.bounding_box.model_dump()
    region = TextRegion(
        page_id=page.id,
        region_type=str(payload.region_type),
        bounding_box=box,
        polygon=[
            [box["x"], box["y"]],
            [box["x"] + box["width"], box["y"]],
            [box["x"] + box["width"], box["y"] + box["height"]],
            [box["x"], box["y"] + box["height"]],
        ],
        source_text=payload.source_text,
        normalized_text=payload.source_text,
        style=payload.style or {},
        reading_order=len(page.regions),
        edited_by_user=True,
        confidence=1.0,
    )
    session.add(region)
    session.flush()
    if payload.translated_text:
        _set_translation(
            session, region, payload.translated_text, project.target_language or "en", manual=True
        )
    project_service.touch(project)
    session.commit()
    return _region_out(region)


@router.delete("/projects/{project_id}/regions/{region_id}", status_code=204)
def delete_region(
    project_id: str,
    region_id: str,
    session: DbSession,
    identity: CurrentIdentity,
    _csrf: CsrfProtected,
) -> Response:
    project, region = _region_for_write(session, identity, project_id, region_id)
    session.delete(region)
    project_service.touch(project)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/projects/{project_id}/regions/merge", response_model=RegionOut)
def merge_regions(
    project_id: str,
    payload: RegionMergeRequest,
    session: DbSession,
    identity: CurrentIdentity,
    _csrf: CsrfProtected,
) -> RegionOut:
    project = project_service.get_project(session, project_id, with_pages=True)
    project_service.assert_can_write(
        session, project, user=identity.user, guest_session_id=identity.guest_id
    )
    regions = [
        region
        for page in project.pages
        for region in page.regions
        if region.id in set(payload.region_ids)
    ]
    if len(regions) < 2:
        raise AppError(code=ErrorCode.VALIDATION_FAILED, details={"field": "region_ids"})

    regions.sort(key=lambda item: item.reading_order)
    primary = regions[0]
    from picglot.vision.types import BoundingBox

    box = BoundingBox.from_dict(primary.bounding_box)
    texts = [primary.normalized_text or primary.source_text]
    for region in regions[1:]:
        box = box.union(BoundingBox.from_dict(region.bounding_box))
        texts.append(region.normalized_text or region.source_text)
        session.delete(region)

    primary.bounding_box = box.as_dict()
    primary.polygon = [list(point) for point in box.to_polygon()]
    primary.source_text = " ".join(text.strip() for text in texts if text.strip())
    primary.normalized_text = primary.source_text
    primary.edited_by_user = True
    primary.version = int(primary.version) + 1
    project_service.touch(project)
    session.commit()
    return _region_out(primary)


@router.post("/projects/{project_id}/regions/{region_id}/split", response_model=list[RegionOut])
def split_region(
    project_id: str,
    region_id: str,
    payload: RegionSplitRequest,
    session: DbSession,
    identity: CurrentIdentity,
    _csrf: CsrfProtected,
) -> list[RegionOut]:
    project, region = _region_for_write(session, identity, project_id, region_id)
    text = region.normalized_text or region.source_text or ""
    if not 0 < payload.at_character < len(text):
        raise AppError(code=ErrorCode.VALIDATION_FAILED, details={"field": "at_character"})

    from picglot.vision.types import BoundingBox

    box = BoundingBox.from_dict(region.bounding_box)
    ratio = payload.at_character / len(text)
    top = BoundingBox(box.x, box.y, box.width, box.height * ratio)
    bottom = BoundingBox(box.x, box.y + top.height, box.width, box.height - top.height)

    region.source_text = text[: payload.at_character].strip()
    region.normalized_text = region.source_text
    region.bounding_box = top.as_dict()
    region.polygon = [list(point) for point in top.to_polygon()]
    region.edited_by_user = True
    region.version = int(region.version) + 1

    second = TextRegion(
        page_id=region.page_id,
        region_type=region.region_type,
        bounding_box=bottom.as_dict(),
        polygon=[list(point) for point in bottom.to_polygon()],
        source_text=text[payload.at_character :].strip(),
        normalized_text=text[payload.at_character :].strip(),
        style=dict(region.style or {}),
        detected_language=region.detected_language,
        reading_order=int(region.reading_order) + 1,
        edited_by_user=True,
    )
    session.add(second)
    project_service.touch(project)
    session.commit()
    return [_region_out(region), _region_out(second)]


@router.post("/projects/{project_id}/regions/{region_id}/retranslate", response_model=RegionOut)
def retranslate_region(
    project_id: str,
    region_id: str,
    payload: RetranslateRequest,
    session: DbSession,
    identity: CurrentIdentity,
    _csrf: CsrfProtected,
) -> RegionOut:
    project, region = _region_for_write(session, identity, project_id, region_id)
    target = payload.target_language or project.target_language
    if not target:
        raise AppError(code=ErrorCode.VALIDATION_FAILED, details={"field": "target_language"})

    result = translation_service.retranslate_one(
        session,
        text=region.normalized_text or region.source_text,
        source_language=region.detected_language or project.source_language,
        target_language=target,
        provider=payload.provider,
        formality=payload.formality,
        user_id=project.owner_user_id,
        workspace_id=project.workspace_id,
    )
    _set_translation(
        session,
        region,
        result["text"],
        target,
        provider=result["provider"],
        alternatives=result.get("alternatives", []),
    )
    project_service.touch(project)
    session.commit()
    return _region_out(region)


@router.get("/projects/{project_id}/regions/{region_id}/alternatives")
def translation_alternatives(
    project_id: str, region_id: str, session: DbSession, identity: CurrentIdentity
) -> dict[str, Any]:
    project, region = _region_for_write(session, identity, project_id, region_id)
    return {
        "alternatives": translation_service.alternatives_for(
            session,
            text=region.normalized_text or region.source_text,
            source_language=region.detected_language or project.source_language,
            target_language=project.target_language or "en",
        )
    }


@router.post("/projects/{project_id}/regions/{region_id}/reocr", response_model=RegionOut)
def reocr_region(
    project_id: str,
    region_id: str,
    payload: ReocrRequest,
    session: DbSession,
    identity: CurrentIdentity,
    _csrf: CsrfProtected,
) -> RegionOut:
    """Re-run recognition on just this rectangle, at full resolution."""
    project, region = _region_for_write(session, identity, project_id, region_id)
    page = session.get(DocumentPage, region.page_id)
    if page is None:
        raise AppError(code=ErrorCode.NOT_FOUND)

    asset = session.get(Asset, page.normalized_asset_id) if page.normalized_asset_id else None
    image = project_service.load_image_asset(asset)
    if image is None:
        raise AppError(code=ErrorCode.NOT_FOUND, internal="page image unavailable")

    from picglot.providers import ocr as ocr_providers
    from picglot.vision import normalize as normalize_tools
    from picglot.vision.types import BoundingBox

    box = BoundingBox.from_dict(region.bounding_box).expand(6, bounds=image.size)
    crop = image.crop((int(box.x), int(box.y), int(box.right), int(box.bottom)))
    outcome = ocr_providers.recognize(
        crop,
        languages=payload.languages
        or ([project.source_language] if project.source_language else []),
    ).value

    text = " ".join(item.text for item in outcome.regions).strip()
    if not text:
        raise AppError(code=ErrorCode.NO_TEXT_DETECTED)

    region.source_text = text
    region.normalized_text = normalize_tools.normalize_text(text).text
    region.confidence = outcome.confidence
    region.edited_by_user = False
    region.version = int(region.version) + 1
    project_service.touch(project)
    session.commit()
    return _region_out(region)


@router.patch("/projects/{project_id}/tables/{table_id}/cells")
def update_table_cells(
    project_id: str,
    table_id: str,
    payload: list[TableCellUpdate],
    session: DbSession,
    identity: CurrentIdentity,
    _csrf: CsrfProtected,
) -> dict[str, int]:
    project = project_service.get_project(session, project_id, with_pages=True)
    project_service.assert_can_write(
        session, project, user=identity.user, guest_session_id=identity.guest_id
    )
    from picglot.vision.tables import classify_value

    updated = 0
    for update in payload:
        cell = session.execute(
            select(TableCell).where(
                TableCell.table_id == table_id,
                TableCell.row == update.row,
                TableCell.col == update.col,
            )
        ).scalar_one_or_none()
        if cell is None:
            continue
        if update.text is not None:
            cell.raw_text = update.text
            value_type, numeric, currency = classify_value(update.text)
            cell.value_type = update.value_type or value_type
            cell.numeric_value = numeric
            cell.currency = currency
        if update.is_header is not None:
            cell.is_header = update.is_header
        cell.edited_by_user = True
        updated += 1
    project_service.touch(project)
    session.commit()
    return {"updated": updated}


@router.post("/projects/{project_id}/rerender", response_model=JobOut, status_code=202)
def rerender(
    project_id: str,
    payload: RerenderRequest,
    session: DbSession,
    identity: CurrentIdentity,
    _csrf: CsrfProtected,
) -> JobOut:
    """Redraw pages after editing. Free — no recognition or translation runs."""
    project = project_service.get_project(session, project_id, with_pages=True)
    project_service.assert_can_write(
        session, project, user=identity.user, guest_session_id=identity.guest_id
    )
    _render_pages(session, project, payload.render_mode, payload.page_ids, payload.masks)
    project_service.touch(project)

    job = job_service.create_job(
        session,
        project=project,
        job_type=JobType.RENDER,
        user=identity.user,
        workspace_id=identity.workspace_id,
        guest_session_id=identity.guest_id if identity.user is None else None,
        payload={"render_mode": str(payload.render_mode)},
        pages_total=len(payload.page_ids or project.pages),
        charge=False,
    )
    job_service.mark_completed(session, job, output={"rendered": True})
    session.commit()
    return JobOut.model_validate(job_service.summarize(job))


def _render_pages(
    session: Any,
    project: Project,
    mode: RenderMode,
    page_ids: list[str] | None,
    masks: dict[str, str] | None = None,
) -> None:
    from picglot.vision import inpaint
    from picglot.vision import render as render_tools

    pages = [page for page in project.pages if not page_ids or page.id in set(page_ids)]
    for page in pages:
        image = project_service.load_image_asset(
            session.get(Asset, page.normalized_asset_id) if page.normalized_asset_id else None
        )
        if image is None:
            continue
        regions = [project_service.region_to_domain(row) for row in page.regions]
        translations = {
            row.id: translation.translated_text
            for row in page.regions
            for translation in row.translations
            if translation.is_active and translation.translated_text
        }
        renderable = [region for region in regions if translations.get(region.id, "").strip()]
        user_mask = _decode_mask(masks.get(page.id) if masks else None, image.size)
        if mode is RenderMode.OVERLAY and user_mask is None:
            background = image
        else:
            background = inpaint.remove_text(image, renderable, user_mask=user_mask)[0]
        rendered, _report = render_tools.render_page(
            background,
            renderable,
            translations,
            target_language=project.target_language,
            mode=mode,
            original=image,
        )
        asset = project_service.store_image_asset(
            session, project, rendered, kind=AssetKind.RENDERED, fmt="PNG"
        )
        page.rendered_asset_id = asset.id


#: A brush mask is a small greyscale PNG; anything larger is not a mask.
MAX_MASK_BYTES = 4 * 1024 * 1024


def _decode_mask(encoded: str | None, size: tuple[int, int]) -> Any | None:
    """Decode an editor brush mask, or return None if there isn't a usable one.

    The mask is user input like any upload: it is size-capped and parsed by
    Pillow, and a malformed one is ignored rather than failing the re-render —
    losing a brush stroke is better than losing the page.
    """
    if not encoded:
        return None

    import base64
    import binascii
    from io import BytesIO

    from PIL import Image as PILImage

    payload = encoded.split(",", 1)[-1]  # tolerate a data: URL prefix
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        log.info("rerender.mask_not_base64")
        return None
    if not raw or len(raw) > MAX_MASK_BYTES:
        log.info("rerender.mask_rejected", byte_size=len(raw))
        return None
    try:
        mask = PILImage.open(BytesIO(raw))
        mask.load()
    except Exception:
        # Pillow raises a wide range of types on a malformed image; any of them
        # mean the same thing here — there is no usable mask.
        log.info("rerender.mask_undecodable")
        return None
    return mask.convert("L").resize(size)


# --------------------------------------------------------------------------- #
# Exports
# --------------------------------------------------------------------------- #
@router.post("/projects/{project_id}/exports", response_model=ExportOut, status_code=201)
def create_export(
    project_id: str,
    payload: ExportRequest,
    session: DbSession,
    identity: CurrentIdentity,
    _csrf: CsrfProtected,
) -> ExportOut:
    project = project_service.get_project(session, project_id, with_pages=True)
    project_service.assert_can_read(
        session, project, user=identity.user, guest_session_id=identity.guest_id
    )
    _assert_format_allowed(identity, payload.format)

    options = export_service.ExportOptions.from_dict(payload.model_dump(exclude={"format"}))
    if identity.user is None:
        options.watermark = None
    export = export_service.build(session, project, payload.format, options)
    url = export_service.download_url(session, export)
    session.commit()
    return ExportOut(
        id=export.id,
        format=export.format,
        byte_size=export.byte_size,
        created_at=export.created_at,
        expires_at=export.expires_at,
        download_url=url,
    )


@router.get("/projects/{project_id}/exports", response_model=list[ExportOut])
def list_exports(project_id: str, session: DbSession, identity: CurrentIdentity) -> list[ExportOut]:
    project = project_service.get_project(session, project_id)
    project_service.assert_can_read(
        session, project, user=identity.user, guest_session_id=identity.guest_id
    )
    rows = session.execute(
        select(Export).where(Export.project_id == project.id).order_by(Export.created_at.desc())
    ).scalars()
    return [
        ExportOut(
            id=row.id,
            format=row.format,
            byte_size=row.byte_size,
            created_at=row.created_at,
            expires_at=row.expires_at,
        )
        for row in rows
    ]


@router.get("/exports/{export_id}/download")
def download_export(
    export_id: str, session: DbSession, identity: CurrentIdentity
) -> dict[str, str]:
    export = session.get(Export, export_id)
    if export is None:
        raise AppError(code=ErrorCode.NOT_FOUND)
    project = project_service.get_project(session, export.project_id)
    project_service.assert_can_read(
        session, project, user=identity.user, guest_session_id=identity.guest_id
    )
    url = export_service.download_url(session, export)
    session.commit()
    return {"url": url, "expires_in": str(settings.signed_url_ttl_seconds)}


# --------------------------------------------------------------------------- #
# Sharing
# --------------------------------------------------------------------------- #
@router.post("/projects/{project_id}/share", response_model=ShareOut, status_code=201)
def create_share(
    project_id: str,
    payload: ShareCreateRequest,
    session: DbSession,
    user: CurrentUser,
    identity: CurrentIdentity,
    _csrf: CsrfProtected,
) -> ShareOut:
    if not settings.feature_sharing:
        raise AppError(code=ErrorCode.FEATURE_DISABLED, details={"feature": "sharing"})
    project = project_service.get_project(session, project_id)
    project_service.assert_can_write(session, project, user=user)

    link, raw = share_service.create(
        session,
        project,
        created_by_id=user.id,
        permission=payload.permission,
        password=payload.password,
        expires_in_hours=payload.expires_in_hours,
        max_views=payload.max_views,
        show_owner=payload.show_owner,
        watermark=payload.watermark,
        export_id=payload.export_id,
    )
    session.commit()
    return ShareOut(
        id=link.id,
        url=f"{settings.public_web_url}/share/{raw}",
        permission=link.permission,
        expires_at=link.expires_at,
        max_views=link.max_views,
        view_count=link.view_count,
        has_password=link.password_hash is not None,
        watermark=link.watermark,
        created_at=link.created_at,
    )


@router.get("/projects/{project_id}/share", response_model=list[ShareOut])
def list_shares(project_id: str, session: DbSession, user: CurrentUser) -> list[ShareOut]:
    project = project_service.get_project(session, project_id)
    project_service.assert_can_read(session, project, user=user)
    return [
        ShareOut(
            id=link.id,
            url=f"{settings.public_web_url}/share/…",
            permission=link.permission,
            expires_at=link.expires_at,
            max_views=link.max_views,
            view_count=link.view_count,
            has_password=link.password_hash is not None,
            watermark=link.watermark,
            created_at=link.created_at,
        )
        for link in share_service.list_for_project(session, project.id)
    ]


@router.delete("/share/{share_id}", status_code=204)
def revoke_share(
    share_id: str, session: DbSession, user: CurrentUser, _csrf: CsrfProtected
) -> Response:
    from picglot.db.models import ShareLink

    link = session.get(ShareLink, share_id)
    if link is None:
        raise AppError(code=ErrorCode.NOT_FOUND)
    project = project_service.get_project(session, link.project_id)
    project_service.assert_can_write(session, project, user=user)
    share_service.revoke(session, link)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Serialisation helpers
# --------------------------------------------------------------------------- #
def _assert_format_allowed(identity: CurrentIdentity, fmt: ExportFormat) -> None:
    from picglot.domain import plans

    spec = plans.by_code(identity.plan_code)
    if spec is None:
        return
    allowed = {str(item) for item in spec.export_formats}
    if str(fmt) not in allowed:
        raise AppError(
            code=ErrorCode.PLAN_REQUIRED,
            details={"format": str(fmt), "plan": identity.plan_code, "allowed": sorted(allowed)},
        )


def _region_for_write(
    session: Any, identity: CurrentIdentity, project_id: str, region_id: str
) -> tuple[Project, TextRegion]:
    project = project_service.get_project(session, project_id, with_pages=True)
    project_service.assert_can_write(
        session, project, user=identity.user, guest_session_id=identity.guest_id
    )
    for page in project.pages:
        for region in page.regions:
            if region.id == region_id:
                return project, region
    raise AppError(code=ErrorCode.NOT_FOUND, internal=f"region {region_id}")


def _set_translation(
    session: Any,
    region: TextRegion,
    text: str,
    target_language: str,
    *,
    provider: str = "",
    manual: bool = False,
    alternatives: list[str] | None = None,
) -> None:
    from picglot.db.models import RegionTranslation
    from picglot.domain.enums import TranslationSource

    for existing in region.translations:
        if existing.target_language == target_language:
            existing.is_active = False
    session.add(
        RegionTranslation(
            text_region_id=region.id,
            target_language=target_language,
            translated_text=text,
            provider=provider,
            source=str(TranslationSource.MANUAL if manual else TranslationSource.PROVIDER),
            character_count=len(text),
            alternatives=alternatives or [],
            is_active=True,
        )
    )
    session.flush()


def _signed(session: Any, asset_id: str | None, *, ttl: int | None = None) -> str | None:
    if not asset_id:
        return None
    asset = session.get(Asset, asset_id)
    if asset is None:
        return None
    try:
        return storage.get_storage().signed_download_url(
            asset.storage_key, expires_in=ttl or settings.signed_url_ttl_seconds
        )
    except AppError:
        return None


def _region_out(region: TextRegion) -> RegionOut:
    active = next((item for item in region.translations if item.is_active), None)
    return RegionOut(
        id=region.id,
        region_type=RegionType(region.region_type),
        polygon=[[float(point[0]), float(point[1])] for point in (region.polygon or [])],
        bounding_box={key: float(value) for key, value in (region.bounding_box or {}).items()},
        rotation=float(region.rotation or 0),
        reading_order=int(region.reading_order or 0),
        detected_language=region.detected_language,
        source_text=region.source_text or "",
        normalized_text=region.normalized_text,
        translated_text=active.translated_text if active else None,
        confidence=region.confidence,
        low_confidence_spans=list(region.low_confidence_spans or []),
        corrections=list(region.corrections or []),
        style=dict(region.style or {}),
        skip_translation=bool(region.skip_translation),
        edited_by_user=bool(region.edited_by_user),
        version=int(region.version or 1),
        translation=(
            TranslationOut(
                text=active.translated_text,
                target_language=active.target_language,
                provider=active.provider or None,
                source=active.source,
                alternatives=list(active.alternatives or []),
            )
            if active
            else None
        ),
    )


def _page_out(session: Any, page: DocumentPage) -> PageOut:
    from picglot.schemas import TableCellOut, TableOut

    return PageOut(
        id=page.id,
        page_number=page.page_number,
        width=page.width,
        height=page.height,
        rotation=page.rotation,
        status=page.status,
        detected_language=page.detected_language,
        ocr_confidence=page.ocr_confidence,
        error_code=page.error_code,
        preview_url=_signed(session, page.preview_asset_id),
        thumbnail_url=_signed(session, page.thumbnail_asset_id),
        rendered_url=_signed(session, page.rendered_asset_id),
        original_url=_signed(session, page.normalized_asset_id),
        regions=[_region_out(region) for region in page.regions],
        tables=[
            TableOut(
                id=table.id,
                index_on_page=table.index_on_page,
                rows=table.row_count,
                cols=table.column_count,
                has_header=table.has_header,
                structure_ambiguous=table.structure_ambiguous,
                confidence=table.confidence,
                bounding_box=table.bounding_box or {},
                cells=[
                    TableCellOut(
                        row=cell.row,
                        col=cell.col,
                        row_span=cell.row_span,
                        col_span=cell.col_span,
                        text=cell.raw_text or "",
                        value_type=cell.value_type,
                        numeric_value=cell.numeric_value,
                        currency=cell.currency,
                        is_header=cell.is_header,
                        confidence=cell.confidence,
                        region_id=cell.region_id,
                    )
                    for cell in table.cells
                ],
            )
            for table in page.tables
        ],
    )


def _project_out(session: Any, project: Project) -> ProjectOut:
    thumbnail = None
    if project.pages:
        first = min(project.pages, key=lambda page: page.page_number)
        thumbnail = _signed(session, first.thumbnail_asset_id or first.preview_asset_id)
    return ProjectOut(
        id=project.id,
        name=project.name,
        tool_type=project.tool_type,
        status=project.status,
        source_language=project.source_language,
        target_language=project.target_language,
        page_count=project.page_count,
        quality_score=project.quality_score,
        quality_band=project.quality_band,
        quality_reasons=list(project.quality_reasons or []),
        document_version=project.document_version,
        settings=dict(project.settings or {}),
        created_at=project.created_at,
        updated_at=project.updated_at,
        expires_at=project.expires_at,
        thumbnail_url=thumbnail,
    )


def _project_detail(session: Any, project: Project) -> ProjectDetailOut:
    base = _project_out(session, project)
    active = session.execute(
        select(Job).where(Job.project_id == project.id).order_by(Job.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    exports = session.execute(
        select(Export)
        .where(Export.project_id == project.id)
        .order_by(Export.created_at.desc())
        .limit(20)
    ).scalars()
    return ProjectDetailOut(
        **base.model_dump(),
        pages=[
            _page_out(session, page)
            for page in sorted(project.pages, key=lambda page: page.page_number)
        ],
        exports=[
            ExportOut(
                id=row.id,
                format=row.format,
                byte_size=row.byte_size,
                created_at=row.created_at,
                expires_at=row.expires_at,
            )
            for row in exports
        ],
        active_job=JobOut.model_validate(job_service.summarize(active)) if active else None,
    )
