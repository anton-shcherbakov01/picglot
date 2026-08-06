"""Geometry and result types shared by the whole pipeline.

Everything is expressed in *pixels of the normalised page image*. The normalised
image is what the editor displays and what renders are drawn onto, so a client
never has to apply a transform to interpret coordinates.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from picglot.domain.enums import (
    FontClass,
    RegionType,
    TextAlign,
    TextDirection,
    VerticalAlign,
)

Point = tuple[float, float]


@dataclass(slots=True)
class BoundingBox:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center(self) -> Point:
        return (self.x + self.width / 2, self.y + self.height / 2)

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    def as_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BoundingBox:
        return cls(
            float(data.get("x", 0)),
            float(data.get("y", 0)),
            float(data.get("width", 0)),
            float(data.get("height", 0)),
        )

    @classmethod
    def from_points(cls, points: list[Point]) -> BoundingBox:
        if not points:
            return cls(0, 0, 0, 0)
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        return cls(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def expand(self, pixels: float, *, bounds: tuple[int, int] | None = None) -> BoundingBox:
        box = BoundingBox(
            self.x - pixels,
            self.y - pixels,
            self.width + pixels * 2,
            self.height + pixels * 2,
        )
        if bounds:
            return box.clamp(*bounds)
        return box

    def clamp(self, width: int, height: int) -> BoundingBox:
        x = max(0.0, min(self.x, width))
        y = max(0.0, min(self.y, height))
        return BoundingBox(
            x,
            y,
            max(0.0, min(self.width, width - x)),
            max(0.0, min(self.height, height - y)),
        )

    def intersection(self, other: BoundingBox) -> BoundingBox:
        x = max(self.x, other.x)
        y = max(self.y, other.y)
        right = min(self.right, other.right)
        bottom = min(self.bottom, other.bottom)
        return BoundingBox(x, y, max(0.0, right - x), max(0.0, bottom - y))

    def iou(self, other: BoundingBox) -> float:
        overlap = self.intersection(other).area
        union = self.area + other.area - overlap
        return overlap / union if union > 0 else 0.0

    def union(self, other: BoundingBox) -> BoundingBox:
        x = min(self.x, other.x)
        y = min(self.y, other.y)
        return BoundingBox(
            x, y, max(self.right, other.right) - x, max(self.bottom, other.bottom) - y
        )

    def scaled(self, factor: float) -> BoundingBox:
        return BoundingBox(
            self.x * factor, self.y * factor, self.width * factor, self.height * factor
        )

    def to_polygon(self) -> list[Point]:
        return [
            (self.x, self.y),
            (self.right, self.y),
            (self.right, self.bottom),
            (self.x, self.bottom),
        ]


@dataclass(slots=True)
class TextStyle:
    """Everything needed to redraw a piece of text convincingly."""

    font_size: float = 16.0
    font_class: FontClass = FontClass.SANS
    font_family: str | None = None
    #: Families measured closest to `font_family`, best first. Used when the
    #: identified face cannot draw the target script — the usual case when the
    #: source is Latin-only and the translation is not.
    font_fallbacks: list[str] = field(default_factory=list)
    bold: bool = False
    italic: bool = False
    underline: bool = False
    color: str = "#000000"
    background_color: str | None = None
    opacity: float = 1.0
    align: TextAlign = TextAlign.LEFT
    vertical_align: VerticalAlign = VerticalAlign.MIDDLE
    direction: TextDirection = TextDirection.LTR
    line_height: float = 1.2
    letter_spacing: float = 0.0
    #: Horizontal scale applied when drawing, so condensed or wide originals
    #: keep their proportions even when no installed face has them.
    width_ratio: float = 1.0
    outline_color: str | None = None
    outline_width: float = 0.0
    shadow: bool = False

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("font_class", "align", "vertical_align", "direction"):
            data[key] = str(data[key])
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TextStyle:
        data = dict(data or {})
        style = cls()
        for key, value in data.items():
            if not hasattr(style, key):
                continue
            if key == "font_class":
                value = FontClass(value)
            elif key == "align":
                value = TextAlign(value)
            elif key == "vertical_align":
                value = VerticalAlign(value)
            elif key == "direction":
                value = TextDirection(value)
            setattr(style, key, value)
        return style


@dataclass(slots=True)
class Region:
    """One detected block of text on one page."""

    id: str
    polygon: list[Point]
    bounding_box: BoundingBox
    text: str = ""
    normalized_text: str | None = None
    confidence: float | None = None
    rotation: float = 0.0
    region_type: RegionType = RegionType.PARAGRAPH
    detected_language: str | None = None
    line_number: int | None = None
    group_id: str | None = None
    reading_order: int = 0
    style: TextStyle = field(default_factory=TextStyle)
    low_confidence_spans: list[dict[str, Any]] = field(default_factory=list)
    corrections: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    skip_translation: bool = False

    @property
    def effective_text(self) -> str:
        return self.normalized_text if self.normalized_text is not None else self.text

    @property
    def is_empty(self) -> bool:
        return not self.effective_text.strip()

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "polygon": [list(point) for point in self.polygon],
            "bounding_box": self.bounding_box.as_dict(),
            "text": self.text,
            "normalized_text": self.normalized_text,
            "confidence": self.confidence,
            "rotation": self.rotation,
            "region_type": str(self.region_type),
            "detected_language": self.detected_language,
            "line_number": self.line_number,
            "group_id": self.group_id,
            "reading_order": self.reading_order,
            "style": self.style.as_dict(),
            "low_confidence_spans": self.low_confidence_spans,
            "corrections": self.corrections,
            "metadata": self.metadata,
            "skip_translation": self.skip_translation,
        }


@dataclass(slots=True)
class PageResult:
    page_number: int
    width: int
    height: int
    regions: list[Region] = field(default_factory=list)
    rotation: int = 0
    detected_language: str | None = None
    confidence: float | None = None
    tables: list[TableResult] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def plain_text(self) -> str:
        return "\n".join(
            region.effective_text
            for region in sorted(self.regions, key=lambda item: item.reading_order)
            if region.effective_text.strip()
        )


@dataclass(slots=True)
class TableCellResult:
    row: int
    col: int
    text: str = ""
    row_span: int = 1
    col_span: int = 1
    is_header: bool = False
    confidence: float | None = None
    bounding_box: BoundingBox | None = None
    region_id: str | None = None
    value_type: str = "text"
    numeric_value: float | None = None
    currency: str | None = None


@dataclass(slots=True)
class TableResult:
    index: int
    bounding_box: BoundingBox
    rows: int
    cols: int
    cells: list[TableCellResult] = field(default_factory=list)
    has_header: bool = True
    confidence: float | None = None
    structure_ambiguous: bool = False
    title: str | None = None

    def grid(self) -> list[list[str]]:
        matrix = [["" for _ in range(self.cols)] for _ in range(self.rows)]
        for cell in self.cells:
            if 0 <= cell.row < self.rows and 0 <= cell.col < self.cols:
                matrix[cell.row][cell.col] = cell.text
        return matrix


@dataclass(slots=True)
class OcrOutcome:
    """What an OCR provider returns for a single image."""

    regions: list[Region]
    provider: str
    model: str | None = None
    detected_language: str | None = None
    confidence: float | None = None
    duration_ms: int = 0
    cost_micro_usd: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


def polygon_rotation(polygon: list[Point]) -> float:
    """Angle in degrees of the polygon's top edge; 0 for an axis-aligned box."""
    if len(polygon) < 2:
        return 0.0
    (x1, y1), (x2, y2) = polygon[0], polygon[1]
    angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
    # Normalise to (-90, 90]: text upside down is handled by page rotation.
    while angle <= -90:
        angle += 180
    while angle > 90:
        angle -= 180
    return round(angle, 2)


def polygon_area(polygon: list[Point]) -> float:
    if len(polygon) < 3:
        return 0.0
    total = 0.0
    for index in range(len(polygon)):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % len(polygon)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def normalize_polygon(points: Any) -> list[Point]:
    """Coerce provider output (numpy arrays, nested lists) into a clean polygon."""
    result: list[Point] = []
    for point in points or []:
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError, IndexError):
            continue
        result.append((round(x, 2), round(y, 2)))
    return result
