"""Plan catalogue.

Seeded into the ``plans`` table so prices can be edited from the admin panel,
but this module remains the source of truth for a fresh installation and for
tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from picglot.core.config import settings
from picglot.domain.enums import ExportFormat


@dataclass(frozen=True, slots=True)
class PlanSpec:
    code: str
    name: str
    monthly_credits: int
    price_usd_cents: int
    price_rub_kopecks: int
    max_upload_bytes: int
    max_pdf_pages: int
    max_batch_files: int
    retention_hours: int
    export_formats: tuple[ExportFormat, ...]
    priority: int
    features: tuple[str, ...] = field(default_factory=tuple)
    is_public: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "name": self.name,
            "monthly_credits": self.monthly_credits,
            "price_usd_cents": self.price_usd_cents,
            "price_rub_kopecks": self.price_rub_kopecks,
            "max_upload_bytes": self.max_upload_bytes,
            "max_pdf_pages": self.max_pdf_pages,
            "max_batch_files": self.max_batch_files,
            "retention_hours": self.retention_hours,
            "export_formats": [str(item) for item in self.export_formats],
            "priority": self.priority,
            "features": list(self.features),
        }


BASIC_EXPORTS = (ExportFormat.PNG, ExportFormat.JPG, ExportFormat.TXT, ExportFormat.JSON)
FULL_EXPORTS = tuple(ExportFormat)


def plan_specs() -> tuple[PlanSpec, ...]:
    return (
        PlanSpec(
            code="guest",
            name="Guest",
            monthly_credits=0,
            price_usd_cents=0,
            price_rub_kopecks=0,
            max_upload_bytes=settings.max_upload_bytes_guest,
            max_pdf_pages=settings.max_pdf_pages_guest,
            max_batch_files=1,
            retention_hours=settings.retention_guest_hours,
            export_formats=BASIC_EXPORTS,
            priority=0,
            features=("guest_processing",),
            is_public=False,
        ),
        PlanSpec(
            code="free",
            name="Free",
            monthly_credits=settings.free_monthly_credits,
            price_usd_cents=0,
            price_rub_kopecks=0,
            max_upload_bytes=settings.max_upload_bytes_free,
            max_pdf_pages=settings.max_pdf_pages_free,
            max_batch_files=settings.max_batch_files_free,
            retention_hours=settings.retention_free_hours,
            # Text-shaped exports cost nothing to produce, so they stay free;
            # DOCX/XLSX/searchable PDF are the paid conversions.
            export_formats=BASIC_EXPORTS
            + (
                ExportFormat.WEBP,
                ExportFormat.PDF,
                ExportFormat.MARKDOWN,
                ExportFormat.CSV,
            ),
            priority=1,
            features=("projects", "history", "basic_ocr", "basic_translation"),
        ),
        PlanSpec(
            code="pro",
            name="Pro",
            monthly_credits=settings.pro_monthly_credits,
            price_usd_cents=299,
            price_rub_kopecks=19900,
            max_upload_bytes=settings.max_upload_bytes_pro,
            max_pdf_pages=settings.max_pdf_pages_pro,
            max_batch_files=settings.max_batch_files_pro,
            retention_hours=settings.retention_pro_hours,
            export_formats=FULL_EXPORTS,
            priority=5,
            features=(
                "batch",
                "docx_export",
                "xlsx_export",
                "searchable_pdf",
                "priority_queue",
                "advanced_ocr",
                "advanced_inpaint",
                "glossary",
                "translation_memory",
                "email_notifications",
                "no_branding",
                "api_limited",
                "custom_retention",
            ),
        ),
        PlanSpec(
            code="business",
            name="Business",
            monthly_credits=settings.business_monthly_credits,
            price_usd_cents=899,
            price_rub_kopecks=69900,
            max_upload_bytes=settings.max_upload_bytes_business,
            max_pdf_pages=settings.max_pdf_pages_business,
            max_batch_files=settings.max_batch_files_business,
            retention_hours=settings.retention_business_hours,
            export_formats=FULL_EXPORTS,
            priority=9,
            features=(
                "batch",
                "docx_export",
                "xlsx_export",
                "searchable_pdf",
                "priority_queue",
                "advanced_ocr",
                "advanced_inpaint",
                "glossary",
                "translation_memory",
                "email_notifications",
                "no_branding",
                "api_full",
                "webhooks",
                "workspace",
                "audit_log",
                "central_billing",
                "priority_support",
                "custom_retention",
                "local_only_processing",
            ),
        ),
    )


@dataclass(frozen=True, slots=True)
class CreditPack:
    code: str
    credits: int
    price_usd_cents: int
    price_rub_kopecks: int


#: Packs price a credit above the subscription rate — buying without committing
#: to a month costs more per credit, and the bigger the pack the smaller that
#: premium. Pro at 1000 credits/month stays the cheaper way to get volume, which
#: is the point of having a subscription at all.
CREDIT_PACKS: tuple[CreditPack, ...] = (
    CreditPack("pack_100", 100, 99, 4900),
    CreditPack("pack_500", 500, 299, 19900),
    CreditPack("pack_2000", 2000, 799, 59000),
)


def by_code(code: str | None) -> PlanSpec | None:
    for spec in plan_specs():
        if spec.code == code:
            return spec
    return None


def has_feature(plan_code: str | None, feature: str) -> bool:
    spec = by_code(plan_code or "guest")
    return bool(spec and feature in spec.features)
