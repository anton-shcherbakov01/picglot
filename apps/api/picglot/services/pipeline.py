"""The processing pipeline.

One function — ``run_job`` — drives every tool. Stages are explicit so progress,
cancellation, partial success and per-stage metrics all work uniformly:

    preprocessing → detecting → recognizing → [translating] →
    [inpainting → rendering] → exporting

Partial success is a first-class outcome: if page 7 of 20 fails, the other 19
are saved, the job ends ``partially_completed``, and the user is refunded for
the failed page.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from PIL import Image
from sqlalchemy.orm import Session

from picglot.core.config import settings
from picglot.core.errors import AppError, ErrorCode
from picglot.core.logging import get_logger
from picglot.core.metrics import observe_stage, pages_processed_total
from picglot.db.models import DocumentPage, Job, Project, ProviderUsage, RegionTranslation
from picglot.domain import tools as tool_catalog
from picglot.domain.enums import (
    AssetKind,
    JobStatus,
    JobType,
    ProjectStatus,
    RegionType,
    RenderMode,
    ToolType,
    TranslationSource,
)
from picglot.providers import ocr as ocr_providers
from picglot.services import jobs as job_service
from picglot.services import projects as project_service
from picglot.services import translation as translation_service
from picglot.vision import inpaint, layout, normalize, quality, render, tables
from picglot.vision import pdf as pdf_tools
from picglot.vision.preprocess import (
    PreprocessOptions,
    load_image,
    make_preview,
    make_thumbnail,
    preprocess,
)
from picglot.vision.types import PageResult, Region

log = get_logger(__name__)


@dataclass(slots=True)
class PageOutcome:
    page_number: int
    ok: bool
    error_code: str | None = None
    region_count: int = 0
    confidence: float | None = None


@dataclass(slots=True)
class PipelineResult:
    pages_total: int
    pages_completed: int
    pages_failed: int
    outcomes: list[PageOutcome] = field(default_factory=list)
    quality: dict[str, Any] = field(default_factory=dict)
    provider_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> JobStatus:
        if self.pages_completed == 0:
            return JobStatus.FAILED
        if self.pages_failed:
            return JobStatus.PARTIALLY_COMPLETED
        return JobStatus.COMPLETED


def run_job(session: Session, job: Job) -> PipelineResult:
    """Execute a job to completion. Raises only on unrecoverable failure."""
    project = project_service.get_project(session, job.project_id, with_pages=True)
    spec = tool_catalog.require(project.tool_type)
    options = dict(job.input.get("options") or {})

    source_asset = _source_asset(project)
    if source_asset is None:
        raise AppError(code=ErrorCode.NOT_FOUND, internal="project has no source asset")

    from picglot.services import storage as storage_service

    raw = storage_service.get_storage().get(source_asset.storage_key)
    is_pdf = source_asset.mime_type == "application/pdf"

    page_numbers = _requested_pages(job, source_asset, raw, is_pdf)
    job_service.update_progress(
        session,
        job,
        stage=JobStatus.PREPROCESSING,
        progress=0.02,
        data={"pages_total": len(page_numbers)},
    )
    job.pages_total = len(page_numbers)
    session.flush()

    translate = bool(job.input.get("translate", spec.translates)) and bool(project.target_language)
    render_mode = RenderMode(str(job.input.get("render_mode", RenderMode.TRANSLATION_ONLY)))
    should_render = spec.default_render and translate and job.type != JobType.OCR

    outcomes: list[PageOutcome] = []
    page_results: list[PageResult] = []
    overflowed: list[str] = []
    inpaint_scores: list[float] = []
    provider_counts: dict[str, int] = {}

    for index, page_number in enumerate(page_numbers):
        if job_service.cancel_requested(session, job):
            job_service.mark_cancelled(session, job)
            raise AppError(code=ErrorCode.CANCELLED)

        base_progress = 0.05 + (index / max(len(page_numbers), 1)) * 0.9
        try:
            outcome, page_result, page_overflow, page_inpaint = _process_page(
                session,
                job=job,
                project=project,
                spec=spec,
                raw=raw,
                is_pdf=is_pdf,
                page_number=page_number,
                options=options,
                translate=translate,
                should_render=should_render,
                render_mode=render_mode,
                base_progress=base_progress,
                provider_counts=provider_counts,
            )
            outcomes.append(outcome)
            if page_result is not None:
                page_results.append(page_result)
            overflowed.extend(page_overflow)
            if page_inpaint is not None:
                inpaint_scores.append(page_inpaint)
            pages_processed_total.labels(tool=project.tool_type).inc()
        except AppError as exc:
            if exc.code is ErrorCode.CANCELLED:
                raise
            log.warning(
                "pipeline.page_failed",
                job_id=job.id,
                page=page_number,
                code=str(exc.code),
            )
            outcomes.append(PageOutcome(page_number, ok=False, error_code=str(exc.code)))
            _mark_page_failed(session, project, page_number, str(exc.code))
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("pipeline.page_crashed", job_id=job.id, page=page_number)
            outcomes.append(
                PageOutcome(page_number, ok=False, error_code=str(ErrorCode.INTERNAL_ERROR))
            )
            _mark_page_failed(session, project, page_number, str(ErrorCode.INTERNAL_ERROR))
            del exc
        session.commit()

    completed = sum(1 for outcome in outcomes if outcome.ok)
    failed = len(outcomes) - completed

    report = quality.assess(
        page_results,
        translations=_translation_map(session, project),
        target_language=project.target_language if translate else None,
        overflowed_region_ids=overflowed,
        inpaint_quality=(sum(inpaint_scores) / len(inpaint_scores) if inpaint_scores else None),
    )
    project.quality_score = report.score
    project.quality_band = str(report.band)
    project.quality_reasons = quality.reasons_for_user(report)
    project.page_count = len(page_numbers)
    project.status = (
        ProjectStatus.READY
        if failed == 0 and completed > 0
        else ProjectStatus.PARTIAL
        if completed
        else ProjectStatus.FAILED
    )
    project_service.touch(project)

    job.pages_completed = completed
    job.pages_failed = failed
    session.flush()

    return PipelineResult(
        pages_total=len(page_numbers),
        pages_completed=completed,
        pages_failed=failed,
        outcomes=outcomes,
        quality=report.as_dict(),
        provider_summary=provider_counts,
    )


# --------------------------------------------------------------------------- #
# One page
# --------------------------------------------------------------------------- #
def _process_page(
    session: Session,
    *,
    job: Job,
    project: Project,
    spec: tool_catalog.ToolSpec,
    raw: bytes,
    is_pdf: bool,
    page_number: int,
    options: dict[str, Any],
    translate: bool,
    should_render: bool,
    render_mode: RenderMode,
    base_progress: float,
    provider_counts: dict[str, int],
) -> tuple[PageOutcome, PageResult | None, list[str], float | None]:
    # ---------------------------------------------------------- preprocessing
    job_service.update_progress(
        session,
        job,
        stage=JobStatus.PREPROCESSING,
        progress=base_progress,
        data={"page": page_number},
    )
    with observe_stage("preprocessing"):
        source_image, text_layer_regions = _load_page(raw, is_pdf, page_number, options)
        preprocess_options = PreprocessOptions.for_tool(spec.slug, options.get("preprocess"))
        working, preprocess_report = (
            (source_image, None)
            if text_layer_regions
            else preprocess(source_image, preprocess_options)
        )

    page = _upsert_page(session, project, page_number, working, preprocess_report)

    normalized_asset = project_service.store_image_asset(
        session, project, working, kind=AssetKind.NORMALIZED, fmt="PNG"
    )
    page.normalized_asset_id = normalized_asset.id
    preview_asset = project_service.store_image_asset(
        session, project, make_preview(working), kind=AssetKind.PREVIEW, fmt="JPEG", quality=88
    )
    page.preview_asset_id = preview_asset.id
    thumbnail_asset = project_service.store_image_asset(
        session, project, make_thumbnail(working), kind=AssetKind.THUMBNAIL, fmt="JPEG", quality=80
    )
    page.thumbnail_asset_id = thumbnail_asset.id

    # ------------------------------------------------------------- recognition
    job_service.update_progress(
        session,
        job,
        stage=JobStatus.DETECTING,
        progress=base_progress + 0.08,
        data={"page": page_number},
    )

    if text_layer_regions:
        # The PDF already carries exact text — no OCR needed for this page.
        regions = text_layer_regions
        ocr_provider = "pdf_text_layer"
        ocr_confidence: float | None = 1.0
        detected_language = None
    else:
        job_service.update_progress(
            session,
            job,
            stage=JobStatus.RECOGNIZING,
            progress=base_progress + 0.15,
            data={"page": page_number},
        )
        with observe_stage("recognizing"):
            purpose = (
                "handwriting"
                if spec.type is ToolType.HANDWRITING_TO_TEXT or options.get("handwriting")
                else "table"
                if spec.type is ToolType.IMAGE_TO_EXCEL
                else "text"
            )
            chain_result = ocr_providers.recognize(
                working,
                languages=[project.source_language] if project.source_language else [],
                purpose=purpose,
                handwriting=purpose == "handwriting",
                hints={"sparse_text": spec.type is ToolType.SCREENSHOT_TRANSLATOR},
            )
        outcome = chain_result.value
        regions = outcome.regions
        ocr_provider = chain_result.provider
        ocr_confidence = outcome.confidence
        detected_language = outcome.detected_language
        provider_counts[ocr_provider] = provider_counts.get(ocr_provider, 0) + 1
        _record_usage(session, job, "ocr", outcome)

    if not regions:
        raise AppError(code=ErrorCode.NO_TEXT_DETECTED, details={"page": page_number})

    regions = normalize.normalize_regions(
        regions,
        normalize.NormalizeOptions(
            fix_lookalikes=bool(options.get("fix_ocr_errors", True)),
            collapse_line_breaks=not bool(options.get("keep_line_breaks", True)),
        ),
    )
    regions = layout.analyse(
        regions,
        page_size=working.size,
        image=working,
        ui_mode=spec.type is ToolType.SCREENSHOT_TRANSLATOR,
    )
    if spec.type is ToolType.HANDWRITING_TO_TEXT:
        for region in regions:
            region.region_type = RegionType.HANDWRITING

    page.detected_language = detected_language or _dominant_language(regions)
    page.ocr_confidence = ocr_confidence
    # Keep the persisted rows: ``page.regions`` is an already-loaded collection
    # and will not contain the rows we just inserted.
    region_rows = project_service.replace_regions(session, page, regions)

    if project.source_language is None and page.detected_language:
        project.source_language = page.detected_language

    # ------------------------------------------------------------------ tables
    page_tables: list[Any] = []
    if spec.type in {ToolType.IMAGE_TO_EXCEL} or options.get("extract_tables"):
        with observe_stage("tables"):
            page_tables = tables.extract_tables(working, regions)
        project_service.save_tables(session, page, page_tables)

    # ------------------------------------------------------------- translation
    translations: dict[str, str] = {}
    if translate:
        job_service.update_progress(
            session,
            job,
            stage=JobStatus.TRANSLATING,
            progress=base_progress + 0.4,
            data={"page": page_number},
        )
        with observe_stage("translating"):
            translations = translation_service.translate_regions(
                session,
                regions=regions,
                source_language=project.source_language,
                target_language=project.target_language or "en",
                user_id=project.owner_user_id,
                workspace_id=project.workspace_id,
                glossary_id=(project.settings or {}).get("glossary_id"),
                formality=(project.settings or {}).get("formality"),
                job=job,
            )
        _persist_translations(session, region_rows, translations, project.target_language or "en")

    # ------------------------------------------------- inpainting and rendering
    overflowed: list[str] = []
    inpaint_score: float | None = None
    if should_render:
        job_service.update_progress(
            session,
            job,
            stage=JobStatus.INPAINTING,
            progress=base_progress + 0.62,
            data={"page": page_number},
        )
        renderable = [region for region in regions if translations.get(region.id, "").strip()]
        with observe_stage("inpainting"):
            if render_mode is RenderMode.OVERLAY:
                cleaned, mask, inpaint_report = working.copy(), None, None
            else:
                cleaned, mask, inpaint_report = inpaint.remove_text(
                    working,
                    renderable,
                    strategy=(
                        "advanced"
                        if options.get("advanced_inpaint") and settings.advanced_inpaint_enabled
                        else None
                    ),
                )
                inpaint_score = inpaint_report.quality

        if mask is not None:
            cleaned_asset = project_service.store_image_asset(
                session, project, cleaned, kind=AssetKind.CLEANED, fmt="PNG"
            )
            page.cleaned_asset_id = cleaned_asset.id

        job_service.update_progress(
            session,
            job,
            stage=JobStatus.RENDERING,
            progress=base_progress + 0.75,
            data={"page": page_number},
        )
        with observe_stage("rendering"):
            rendered, render_report = render.render_page(
                cleaned,
                renderable,
                translations,
                target_language=project.target_language,
                mode=render_mode,
                original=working,
            )
        overflowed = render_report.overflowed
        rendered_asset = project_service.store_image_asset(
            session,
            project,
            rendered,
            kind=AssetKind.RENDERED,
            fmt="PNG",
            metadata={"render": render_report.as_dict()},
        )
        page.rendered_asset_id = rendered_asset.id

    page.status = "completed"
    session.flush()

    page_result = PageResult(
        page_number=page_number,
        width=working.width,
        height=working.height,
        regions=regions,
        detected_language=page.detected_language,
        confidence=ocr_confidence,
        tables=page_tables,
    )
    return (
        PageOutcome(page_number, True, region_count=len(regions), confidence=ocr_confidence),
        page_result,
        overflowed,
        inpaint_score,
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _load_page(
    raw: bytes, is_pdf: bool, page_number: int, options: dict[str, Any]
) -> tuple[Image.Image, list[Region]]:
    if not is_pdf:
        return load_image(raw), []

    image = None
    for number, rendered in pdf_tools.render_pages(raw, pages=[page_number]):
        if number == page_number:
            image = rendered
            break
    if image is None:
        raise AppError(code=ErrorCode.CORRUPTED_FILE, internal=f"page {page_number} missing")

    if options.get("force_ocr"):
        return image, []
    text_regions = pdf_tools.extract_text_regions(raw, page_number)
    # Only trust an embedded layer when it actually contains content.
    total_characters = sum(len(region.text.strip()) for region in text_regions)
    if total_characters >= pdf_tools.MIN_TEXT_LAYER_CHARS:
        return image, text_regions
    return image, []


def _requested_pages(job: Job, asset: Any, raw: bytes, is_pdf: bool) -> list[int]:
    if not is_pdf:
        return [1]
    total = asset.page_count or pdf_tools.page_count(raw)
    requested = job.input.get("pages")
    if not requested:
        return list(range(1, total + 1))
    if isinstance(requested, dict):
        start = max(1, int(requested.get("from", 1)))
        end = min(total, int(requested.get("to", total)))
        return list(range(start, end + 1))
    return [number for number in requested if 1 <= int(number) <= total]


def _source_asset(project: Project) -> Any:
    originals = [asset for asset in project.assets if asset.kind == AssetKind.ORIGINAL]
    return originals[0] if originals else None


def _find_page(session: Session, project: Project, page_number: int) -> DocumentPage | None:
    """Look the page up in the database, not in ``project.pages``.

    The relationship was loaded before this job started, so a page inserted
    during the run is absent from it — trusting the collection would insert a
    duplicate and trip the (project_id, page_number) unique constraint.
    """
    from sqlalchemy import select as sa_select

    return session.execute(
        sa_select(DocumentPage).where(
            DocumentPage.project_id == project.id,
            DocumentPage.page_number == page_number,
        )
    ).scalar_one_or_none()


def _upsert_page(
    session: Session,
    project: Project,
    page_number: int,
    image: Image.Image,
    report: Any,
) -> DocumentPage:
    existing = _find_page(session, project, page_number)
    if existing is None:
        existing = project_service.create_page(
            session,
            project,
            page_number=page_number,
            width=image.width,
            height=image.height,
        )
    existing.width = image.width
    existing.height = image.height
    existing.status = "processing"
    existing.error_code = None
    if report is not None:
        existing.rotation = report.rotation_applied
        existing.metadata_json = {
            **(existing.metadata_json or {}),
            "preprocess": report.as_dict(),
        }
    session.flush()
    return existing


def _mark_page_failed(
    session: Session, project: Project, page_number: int, error_code: str
) -> None:
    page = _find_page(session, project, page_number)
    if page is None:
        page = project_service.create_page(
            session, project, page_number=page_number, width=0, height=0
        )
    page.status = "failed"
    page.error_code = error_code
    session.flush()


def _persist_translations(
    session: Session,
    region_rows: list[Any],
    translations: dict[str, str],
    target_language: str,
) -> None:
    by_id = {region.id: region for region in region_rows}
    for region_id, text in translations.items():
        row = by_id.get(region_id)
        if row is None:
            continue
        for existing in row.translations:
            if existing.target_language == target_language:
                existing.is_active = False
        session.add(
            RegionTranslation(
                text_region_id=row.id,
                target_language=target_language,
                translated_text=text,
                provider=translation_service.last_provider_for(region_id) or "",
                source=str(
                    translation_service.last_source_for(region_id) or TranslationSource.PROVIDER
                ),
                character_count=len(text),
                is_active=True,
            )
        )
    session.flush()


def _translation_map(session: Session, project: Project) -> dict[str, str]:
    result: dict[str, str] = {}
    for page in project.pages:
        for region in page.regions:
            for translation in region.translations:
                if translation.is_active:
                    result[region.id] = translation.translated_text
    return result


def _dominant_language(regions: list[Region]) -> str | None:
    counts: dict[str, int] = {}
    for region in regions:
        if region.detected_language:
            counts[region.detected_language] = counts.get(region.detected_language, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])[0]


def _record_usage(session: Session, job: Job, kind: str, outcome: Any) -> None:
    session.add(
        ProviderUsage(
            kind=kind,
            provider=outcome.provider,
            model=outcome.model,
            job_id=job.id,
            units=1,
            unit_kind="page",
            cost_micro_usd=getattr(outcome, "cost_micro_usd", 0),
            latency_ms=getattr(outcome, "duration_ms", None),
            success=True,
        )
    )


def settings_hash(payload: dict[str, Any]) -> str:
    import json

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def estimate_pages(raw: bytes, is_pdf: bool) -> int:
    return pdf_tools.page_count(raw) if is_pdf else 1


def now() -> datetime:
    return datetime.now(UTC)
