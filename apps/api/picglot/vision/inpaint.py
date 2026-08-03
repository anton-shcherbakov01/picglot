"""Removing the original text from an image.

Four strategies, chosen automatically from how complex the background behind a
region is:

``solid``    flat background  -> fill with the estimated paper colour (perfect)
``telea``    mild texture     -> OpenCV Telea inpainting
``ns``       strong texture   -> OpenCV Navier-Stokes inpainting
``overlay``  photographic     -> translucent plate; honest rather than smeared

A fifth path exists for user-supplied masks (brush / eraser in the editor), and
an optional advanced model hook for installs that ship one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from picglot.core.config import settings
from picglot.core.logging import get_logger
from picglot.vision.layout import hex_to_rgb
from picglot.vision.preprocess import dominant_color, surround_complexity, to_cv, to_pil
from picglot.vision.types import BoundingBox, Region

log = get_logger(__name__)

Strategy = Literal["solid", "telea", "ns", "overlay", "advanced"]


@dataclass(slots=True)
class InpaintReport:
    strategies: dict[str, int] = field(default_factory=dict)
    regions_processed: int = 0
    #: 0..1 — how confident we are that the repair looks natural.
    quality: float = 1.0
    notes: list[str] = field(default_factory=list)

    def record(self, strategy: str) -> None:
        self.strategies[strategy] = self.strategies.get(strategy, 0) + 1
        self.regions_processed += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategies": self.strategies,
            "regions_processed": self.regions_processed,
            "quality": round(self.quality, 3),
            "notes": self.notes,
        }


def build_mask(
    size: tuple[int, int],
    regions: list[Region],
    *,
    dilation: int = 3,
    feather: float = 1.5,
) -> Image.Image:
    """White where text should disappear. Never paints outside a region."""
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for region in regions:
        polygon = [
            (float(x), float(y)) for x, y in region.polygon
        ] or region.bounding_box.to_polygon()
        if len(polygon) >= 3:
            draw.polygon(polygon, fill=255)
        else:
            box = region.bounding_box
            draw.rectangle([box.x, box.y, box.right, box.bottom], fill=255)

    if dilation > 0:
        array = np.array(mask)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilation * 2 + 1, dilation * 2 + 1))
        mask = Image.fromarray(cv2.dilate(array, kernel, iterations=1))
    if feather > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=feather))
    return mask


def choose_strategy(image: Image.Image, region: Region) -> Strategy:
    configured = settings.inpaint_strategy
    if configured != "auto":
        return configured  # type: ignore[return-value]

    box = region.bounding_box
    complexity = surround_complexity(
        image,
        (int(box.x), int(box.y), int(box.right), int(box.bottom)),
        band=max(6, int(box.height * 0.5)),
    )
    if complexity < 0.18:
        return "solid"
    if complexity < 0.45:
        return "telea"
    if complexity < 0.7:
        return "ns"
    return "overlay"


def remove_text(
    image: Image.Image,
    regions: list[Region],
    *,
    strategy: Strategy | None = None,
    user_mask: Image.Image | None = None,
) -> tuple[Image.Image, Image.Image, InpaintReport]:
    """Return ``(cleaned_image, mask, report)``."""
    report = InpaintReport()
    if not regions and user_mask is None:
        return image.copy(), Image.new("L", image.size, 0), report

    working = image.convert("RGB").copy()
    combined_mask = Image.new("L", image.size, 0)

    # Group regions by the strategy they need so each OpenCV pass runs once.
    buckets: dict[Strategy, list[Region]] = {}
    for region in regions:
        chosen = strategy or choose_strategy(image, region)
        buckets.setdefault(chosen, []).append(region)

    for chosen, bucket in buckets.items():
        mask = build_mask(image.size, bucket)
        combined_mask = Image.fromarray(np.maximum(np.array(combined_mask), np.array(mask)))
        if chosen == "solid":
            working = _fill_solid(working, bucket)
        elif chosen in {"telea", "ns"}:
            working = _cv_inpaint(working, mask, method=chosen)
        elif chosen == "advanced":
            working = _advanced_inpaint(working, mask) or _cv_inpaint(working, mask, method="ns")
        else:
            working = _overlay_plate(working, bucket)
        for _ in bucket:
            report.record(chosen)

    if user_mask is not None:
        combined_mask = Image.fromarray(
            np.maximum(np.array(combined_mask), np.array(user_mask.convert("L").resize(image.size)))
        )
        working = _cv_inpaint(working, user_mask.convert("L").resize(image.size), method="telea")
        report.notes.append("user_mask_applied")

    report.quality = _estimate_quality(report)
    return working, combined_mask, report


def _fill_solid(image: Image.Image, regions: list[Region]) -> Image.Image:
    """Paint each region with the colour sampled just outside it."""
    result = image.copy()
    draw = ImageDraw.Draw(result)
    for region in regions:
        box = region.bounding_box
        outer = box.expand(max(4.0, box.height * 0.35), bounds=image.size)
        color = hex_to_rgb(
            region.style.background_color,
            dominant_color(
                image, (int(outer.x), int(outer.y), int(outer.right), int(outer.bottom))
            ),
        )
        polygon = [(float(x), float(y)) for x, y in region.polygon]
        padded = _pad_polygon(polygon, 2.0) if len(polygon) >= 3 else box.expand(2.0).to_polygon()
        draw.polygon(padded, fill=color)
    # A gentle blur on the seams keeps the patch from looking cut out.
    return result


def _cv_inpaint(image: Image.Image, mask: Image.Image, *, method: str = "telea") -> Image.Image:
    array = to_cv(image)
    mask_array = np.array(mask.convert("L"))
    mask_array = (mask_array > 32).astype(np.uint8) * 255
    if not mask_array.any():
        return image
    flags = cv2.INPAINT_TELEA if method == "telea" else cv2.INPAINT_NS
    radius = 4 if method == "telea" else 6
    try:
        repaired = cv2.inpaint(array, mask_array, radius, flags)
    except cv2.error as exc:  # pragma: no cover - defensive
        log.warning("inpaint.cv_failed", method=method, error=str(exc)[:120])
        return image
    return to_pil(repaired)


def _overlay_plate(image: Image.Image, regions: list[Region]) -> Image.Image:
    """Semi-opaque plate: readable and honest on busy photographic backgrounds."""
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for region in regions:
        box = region.bounding_box.expand(3.0, bounds=image.size)
        base = hex_to_rgb(
            region.style.background_color,
            dominant_color(image, (int(box.x), int(box.y), int(box.right), int(box.bottom))),
        )
        draw.rounded_rectangle(
            [box.x, box.y, box.right, box.bottom],
            radius=max(2, int(box.height * 0.15)),
            fill=(*base, 232),
        )
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def _advanced_inpaint(image: Image.Image, mask: Image.Image) -> Image.Image | None:
    """Hook for an optional local diffusion/LaMa model.

    Enabled by ``ADVANCED_INPAINT_ENABLED`` and the presence of a model runner
    on the worker; returns ``None`` when unavailable so the caller falls back.
    """
    if not settings.advanced_inpaint_enabled:
        return None
    try:
        from picglot.vision.advanced_inpaint import run_model  # type: ignore[import-not-found]
    except ImportError:
        log.info("inpaint.advanced_unavailable")
        return None
    try:
        return run_model(image, mask)
    except Exception as exc:  # pragma: no cover - optional path
        log.warning("inpaint.advanced_failed", error=type(exc).__name__)
        return None


def _pad_polygon(polygon: list[tuple[float, float]], padding: float) -> list[tuple[float, float]]:
    if len(polygon) < 3:
        return polygon
    centre_x = sum(point[0] for point in polygon) / len(polygon)
    centre_y = sum(point[1] for point in polygon) / len(polygon)
    padded: list[tuple[float, float]] = []
    for x, y in polygon:
        dx, dy = x - centre_x, y - centre_y
        length = max((dx * dx + dy * dy) ** 0.5, 1e-6)
        padded.append((x + dx / length * padding, y + dy / length * padding))
    return padded


def _estimate_quality(report: InpaintReport) -> float:
    """Solid fills are perfect; overlays are the visible compromise."""
    if report.regions_processed == 0:
        return 1.0
    weights = {"solid": 1.0, "telea": 0.85, "ns": 0.8, "advanced": 0.95, "overlay": 0.55}
    total = sum(weights.get(name, 0.7) * count for name, count in report.strategies.items())
    return round(total / report.regions_processed, 3)


def apply_brush(mask: Image.Image, points: list[tuple[float, float]], radius: float) -> Image.Image:
    """Editor brush: add strokes to the removal mask."""
    result = mask.convert("L").copy()
    draw = ImageDraw.Draw(result)
    for x, y in points:
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=255)
    return result


def apply_eraser(
    mask: Image.Image, points: list[tuple[float, float]], radius: float
) -> Image.Image:
    result = mask.convert("L").copy()
    draw = ImageDraw.Draw(result)
    for x, y in points:
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=0)
    return result


def preview_mask_over(image: Image.Image, mask: Image.Image) -> Image.Image:
    """Red translucent overlay so the user can check the mask before applying."""
    tint = Image.new("RGBA", image.size, (255, 0, 0, 0))
    alpha = mask.convert("L").point(lambda value: int(value * 0.45))
    tint.putalpha(alpha)
    return Image.alpha_composite(image.convert("RGBA"), tint).convert("RGB")


def mask_bounding_boxes(mask: Image.Image, *, min_area: int = 16) -> list[BoundingBox]:
    array = (np.array(mask.convert("L")) > 32).astype(np.uint8)
    contours, _ = cv2.findContours(array, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes: list[BoundingBox] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if width * height >= min_area:
            boxes.append(BoundingBox(float(x), float(y), float(width), float(height)))
    return boxes
