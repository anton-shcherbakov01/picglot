"""Credit pricing.

Pure functions, no I/O — the same code quotes a price in the UI before a job
starts and computes the debit when it runs, so the number a user is shown is
the number they are charged.

Rules (documented for users on /pricing):
  * one standard OCR page costs 1 credit
  * translating that page costs 1 more credit
  * handwriting, table extraction and advanced inpainting apply multipliers
  * re-exporting an existing result is free
  * a job that fails for a technical reason is refunded in full
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from picglot.domain.enums import JobType, ToolType
from picglot.domain.tools import BY_TYPE

BASE_PAGE_CREDITS = 1.0
TRANSLATION_PAGE_CREDITS = 1.0

MULTIPLIERS: dict[str, float] = {
    "handwriting": 2.0,
    "table_extraction": 1.5,
    "receipt_extraction": 1.5,
    "advanced_inpaint": 1.5,
    "llm_ocr": 2.0,
    "high_resolution": 1.25,
}


@dataclass(slots=True)
class CostLine:
    label: str
    credits: float
    detail: str = ""


@dataclass(slots=True)
class CostEstimate:
    """A quote a user can understand, line by line."""

    pages: int
    total: int
    lines: list[CostLine] = field(default_factory=list)

    @property
    def breakdown(self) -> list[dict[str, object]]:
        return [
            {"label": line.label, "credits": round(line.credits, 2), "detail": line.detail}
            for line in self.lines
        ]

    def as_dict(self) -> dict[str, object]:
        return {"pages": self.pages, "total_credits": self.total, "breakdown": self.breakdown}


def estimate(
    *,
    tool: ToolType | str,
    pages: int,
    translate: bool,
    handwriting: bool = False,
    advanced_inpaint: bool = False,
    llm_ocr: bool = False,
    high_resolution: bool = False,
) -> CostEstimate:
    """Return the credit cost for a run of ``pages`` pages."""
    pages = max(1, int(pages))
    tool_type = ToolType(str(tool))
    spec = BY_TYPE.get(tool_type)
    tool_multiplier = spec.credit_multiplier if spec else 1.0

    lines: list[CostLine] = []
    per_page = BASE_PAGE_CREDITS
    lines.append(CostLine("Recognition", BASE_PAGE_CREDITS * pages, f"{pages} page(s) × 1"))

    if tool_multiplier != 1.0:
        extra = BASE_PAGE_CREDITS * pages * (tool_multiplier - 1.0)
        per_page *= tool_multiplier
        lines.append(
            CostLine(
                _tool_label(tool_type),
                extra,
                f"×{tool_multiplier:g} for {tool_type.value.replace('-', ' ')}",
            )
        )

    for enabled, key, label in (
        (handwriting, "handwriting", "Handwriting recognition"),
        (llm_ocr, "llm_ocr", "AI assisted recognition"),
        (advanced_inpaint, "advanced_inpaint", "Advanced background repair"),
        (high_resolution, "high_resolution", "High resolution"),
    ):
        if not enabled:
            continue
        multiplier = MULTIPLIERS[key]
        extra = per_page * pages * (multiplier - 1.0)
        per_page *= multiplier
        lines.append(CostLine(label, extra, f"×{multiplier:g}"))

    if translate:
        lines.append(
            CostLine("Translation", TRANSLATION_PAGE_CREDITS * pages, f"{pages} page(s) × 1")
        )

    total = math.ceil(sum(line.credits for line in lines) - 1e-9)
    return CostEstimate(pages=pages, total=max(total, 1), lines=lines)


def estimate_for_job(
    *,
    job_type: JobType,
    tool: ToolType | str,
    pages: int,
    translate: bool,
    options: dict | None = None,
) -> CostEstimate:
    options = options or {}
    # Re-exporting an already processed project never costs credits.
    if job_type is JobType.EXPORT:
        return CostEstimate(pages=pages, total=0, lines=[CostLine("Export", 0.0, "free")])
    return estimate(
        tool=tool,
        pages=pages,
        translate=translate,
        handwriting=bool(options.get("handwriting")),
        advanced_inpaint=bool(options.get("advanced_inpaint")),
        llm_ocr=bool(options.get("llm_ocr")),
        high_resolution=bool(options.get("high_resolution")),
    )


def refund_amount(charged: int, pages_total: int, pages_succeeded: int) -> int:
    """Partial success refunds the untouched pages, rounded in the user's favour."""
    if charged <= 0 or pages_total <= 0:
        return 0
    if pages_succeeded >= pages_total:
        return 0
    if pages_succeeded <= 0:
        return charged
    failed = pages_total - pages_succeeded
    return min(charged, math.ceil(charged * failed / pages_total))


def _tool_label(tool: ToolType) -> str:
    return {
        ToolType.IMAGE_TO_EXCEL: "Table extraction",
        ToolType.HANDWRITING_TO_TEXT: "Handwriting recognition",
        ToolType.RECEIPT_SCANNER: "Receipt field extraction",
        ToolType.INVOICE_OCR: "Invoice field extraction",
    }.get(tool, "Tool surcharge")


def describe_rules() -> list[dict[str, object]]:
    """Rendered verbatim on the pricing page so the model stays auditable."""
    return [
        {"key": "ocr_page", "credits": BASE_PAGE_CREDITS, "unit": "page"},
        {"key": "translation_page", "credits": TRANSLATION_PAGE_CREDITS, "unit": "page"},
        {"key": "handwriting", "multiplier": MULTIPLIERS["handwriting"]},
        {"key": "table_extraction", "multiplier": MULTIPLIERS["table_extraction"]},
        {"key": "receipt_extraction", "multiplier": MULTIPLIERS["receipt_extraction"]},
        {"key": "advanced_inpaint", "multiplier": MULTIPLIERS["advanced_inpaint"]},
        {"key": "re_export", "credits": 0},
        {"key": "failed_job_refund", "credits": 0},
        {"key": "cancelled_before_start", "credits": 0},
    ]
