"""Explainable quality score.

The score is never presented as an accuracy percentage — we cannot measure that
without ground truth. It is a *confidence in the result*, assembled from signals
we genuinely observe, with each contributing factor listed so the user can jump
straight to the blocks that need review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lingoimage.domain.enums import QualityBand
from lingoimage.vision.types import PageResult, Region

#: Weight of each signal in the final score. They sum to 1.
WEIGHTS = {
    "ocr_confidence": 0.32,
    "suspicious_characters": 0.12,
    "language_detection": 0.10,
    "translation_coverage": 0.18,
    "text_overflow": 0.12,
    "inpaint_quality": 0.10,
    "table_structure": 0.06,
}


@dataclass(slots=True)
class QualityFactor:
    key: str
    score: float  # 0..1, higher is better
    weight: float
    detail: str = ""
    region_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "score": round(self.score, 3),
            "weight": self.weight,
            "detail": self.detail,
            "region_ids": self.region_ids[:25],
        }


@dataclass(slots=True)
class QualityReport:
    score: float
    band: QualityBand
    factors: list[QualityFactor] = field(default_factory=list)

    @property
    def problem_regions(self) -> list[str]:
        ids: list[str] = []
        for factor in self.factors:
            if factor.score < 0.7:
                ids.extend(factor.region_ids)
        return list(dict.fromkeys(ids))[:50]

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 3),
            "band": str(self.band),
            "factors": [factor.as_dict() for factor in self.factors],
            "problem_regions": self.problem_regions,
            # Explicitly not an accuracy claim — worded this way in the UI too.
            "meaning": "confidence_in_result_not_measured_accuracy",
        }


def band_for(score: float) -> QualityBand:
    if score >= 0.85:
        return QualityBand.HIGH
    if score >= 0.7:
        return QualityBand.MEDIUM
    if score >= 0.5:
        return QualityBand.REVIEW_RECOMMENDED
    return QualityBand.LOW


def assess(
    pages: list[PageResult],
    *,
    translations: dict[str, str] | None = None,
    target_language: str | None = None,
    overflowed_region_ids: list[str] | None = None,
    inpaint_quality: float | None = None,
) -> QualityReport:
    regions = [region for page in pages for region in page.regions]
    factors: list[QualityFactor] = []

    factors.append(_ocr_confidence(regions))
    factors.append(_suspicious_characters(regions))
    factors.append(_language_detection(regions))
    if target_language:
        factors.append(_translation_coverage(regions, translations or {}))
    factors.append(_overflow(regions, overflowed_region_ids or []))
    if inpaint_quality is not None:
        factors.append(
            QualityFactor(
                "inpaint_quality",
                inpaint_quality,
                WEIGHTS["inpaint_quality"],
                "background restoration behind removed text",
            )
        )
    table_factor = _tables(pages)
    if table_factor is not None:
        factors.append(table_factor)

    total_weight = sum(factor.weight for factor in factors) or 1.0
    score = sum(factor.score * factor.weight for factor in factors) / total_weight
    return QualityReport(score=round(score, 4), band=band_for(score), factors=factors)


def _ocr_confidence(regions: list[Region]) -> QualityFactor:
    scored = [region for region in regions if region.confidence is not None]
    if not scored:
        return QualityFactor(
            "ocr_confidence", 0.6, WEIGHTS["ocr_confidence"], "no confidence reported by engine"
        )
    average = sum(region.confidence or 0 for region in scored) / len(scored)
    weak = [region.id for region in scored if (region.confidence or 1) < 0.7]
    return QualityFactor(
        "ocr_confidence",
        round(average, 4),
        WEIGHTS["ocr_confidence"],
        f"{len(weak)} of {len(scored)} blocks recognised with low confidence",
        weak,
    )


def _suspicious_characters(regions: list[Region]) -> QualityFactor:
    flagged = [region.id for region in regions if region.low_confidence_spans]
    if not regions:
        return QualityFactor("suspicious_characters", 1.0, WEIGHTS["suspicious_characters"])
    ratio = len(flagged) / len(regions)
    return QualityFactor(
        "suspicious_characters",
        round(max(0.0, 1.0 - ratio * 1.5), 4),
        WEIGHTS["suspicious_characters"],
        f"{len(flagged)} blocks contain characters worth checking",
        flagged,
    )


def _language_detection(regions: list[Region]) -> QualityFactor:
    if not regions:
        return QualityFactor("language_detection", 1.0, WEIGHTS["language_detection"])
    detected = [region for region in regions if region.detected_language]
    ratio = len(detected) / len(regions)
    languages = {region.detected_language for region in detected}
    detail = f"language identified for {len(detected)} of {len(regions)} blocks"
    if len(languages) > 2:
        detail += f"; {len(languages)} different languages present"
        ratio *= 0.85
    return QualityFactor(
        "language_detection",
        round(ratio, 4),
        WEIGHTS["language_detection"],
        detail,
        [region.id for region in regions if not region.detected_language],
    )


def _translation_coverage(regions: list[Region], translations: dict[str, str]) -> QualityFactor:
    translatable = [
        region
        for region in regions
        if not region.skip_translation and region.effective_text.strip()
    ]
    if not translatable:
        return QualityFactor("translation_coverage", 1.0, WEIGHTS["translation_coverage"])
    missing = [region.id for region in translatable if not translations.get(region.id, "").strip()]
    covered = 1.0 - len(missing) / len(translatable)
    return QualityFactor(
        "translation_coverage",
        round(covered, 4),
        WEIGHTS["translation_coverage"],
        f"{len(missing)} of {len(translatable)} blocks were not translated",
        missing,
    )


def _overflow(regions: list[Region], overflowed: list[str]) -> QualityFactor:
    if not regions:
        return QualityFactor("text_overflow", 1.0, WEIGHTS["text_overflow"])
    ratio = len(overflowed) / len(regions)
    return QualityFactor(
        "text_overflow",
        round(max(0.0, 1.0 - ratio * 2.0), 4),
        WEIGHTS["text_overflow"],
        f"{len(overflowed)} blocks did not fit their original area",
        overflowed,
    )


def _tables(pages: list[PageResult]) -> QualityFactor | None:
    tables = [table for page in pages for table in page.tables]
    if not tables:
        return None
    ambiguous = sum(1 for table in tables if table.structure_ambiguous)
    confidences = [table.confidence for table in tables if table.confidence is not None]
    base = sum(confidences) / len(confidences) if confidences else 0.75
    penalty = ambiguous / len(tables) * 0.4
    return QualityFactor(
        "table_structure",
        round(max(0.0, base - penalty), 4),
        WEIGHTS["table_structure"],
        f"{ambiguous} of {len(tables)} tables have an uncertain row/column structure",
    )


def reasons_for_user(report: QualityReport) -> list[dict[str, Any]]:
    """The factors worth showing, worst first, as i18n keys plus numbers."""
    return [
        {
            "key": factor.key,
            "score": round(factor.score, 2),
            "detail": factor.detail,
            "region_ids": factor.region_ids[:10],
        }
        for factor in sorted(report.factors, key=lambda item: item.score)
        if factor.score < 0.9
    ]
