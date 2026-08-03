"""Table structure recovery.

Two paths, tried in order:

``ruled``     the table has drawn lines — recover the grid from the line mask.
              This is exact when it works (spreadsheets, invoices, forms).
``unruled``   no lines — cluster OCR boxes into rows and columns by position.
              Works on price lists and screenshots; flagged as ambiguous when
              the column clustering is not clean, so the UI can ask the user to
              check rather than pretending it is certain.

Cell values are typed afterwards (number / currency / percent / date) so the
XLSX export contains real numbers rather than strings.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from datetime import datetime

import cv2
import numpy as np
from PIL import Image

from picglot.core.logging import get_logger
from picglot.vision.preprocess import to_cv
from picglot.vision.types import BoundingBox, Region, TableCellResult, TableResult

log = get_logger(__name__)

_CURRENCY = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₽": "RUB", "₴": "UAH", "₸": "KZT"}
#: Integers and decimals, with optional thousands grouping in either the
#: US ("1,234.56") or European ("1.234,56") convention. Spaces (including
#: the non-breaking kind OCR loves to emit) count as group separators.
_NUMBER = re.compile(
    r"^[-+(]?\s*(?:"
    r"\d{1,3}(?:[ .,\u00a0\u202f]\d{3})+(?:[.,]\d{1,6})?"  # grouped
    r"|\d{1,15}(?:[.,]\d{1,6})?"  # plain
    r")\s*\)?$"
)
_PERCENT = re.compile(r"^[-+]?\s*\d{1,3}(?:[.,]\d{1,4})?\s*%$")
_DATE_FORMATS = (
    "%d.%m.%Y",
    "%d.%m.%y",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%d %b %Y",
    "%b %d, %Y",
    "%d.%m",
    "%Y/%m/%d",
)
_FORMULA = re.compile(r"^=\s*[A-Za-z0-9_$:+\-*/().,\s]+$")


@dataclass(slots=True)
class TableOptions:
    min_line_length_ratio: float = 0.35
    row_tolerance_ratio: float = 0.6
    detect_header: bool = True
    max_tables: int = 8


# --------------------------------------------------------------------------- #
# Ruled tables
# --------------------------------------------------------------------------- #
def detect_ruled_tables(
    image: Image.Image, options: TableOptions | None = None
) -> list[tuple[BoundingBox, list[float], list[float]]]:
    """Return (bounds, x-separators, y-separators) for each ruled table found."""
    options = options or TableOptions()
    gray = cv2.cvtColor(to_cv(image), cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 15, -2
    )

    height, width = binary.shape
    h_size = max(10, int(width * options.min_line_length_ratio / 4))
    v_size = max(10, int(height * options.min_line_length_ratio / 4))

    horizontal = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (h_size, 1))
    )
    vertical = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_size))
    )
    grid = cv2.dilate(cv2.add(horizontal, vertical), np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    tables: list[tuple[BoundingBox, list[float], list[float]]] = []

    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[: options.max_tables]:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        if box_width < width * 0.2 or box_height < height * 0.08:
            continue
        region_h = horizontal[y : y + box_height, x : x + box_width]
        region_v = vertical[y : y + box_height, x : x + box_width]
        y_lines = _line_positions(region_h.sum(axis=1), box_width * 255 * 0.4)
        x_lines = _line_positions(region_v.sum(axis=0), box_height * 255 * 0.4)
        if len(y_lines) < 2 or len(x_lines) < 2:
            continue
        tables.append(
            (
                BoundingBox(float(x), float(y), float(box_width), float(box_height)),
                [float(x + value) for value in x_lines],
                [float(y + value) for value in y_lines],
            )
        )
    return tables


def _line_positions(projection: np.ndarray, threshold: float, merge_within: int = 6) -> list[int]:
    """Collapse thick drawn lines into a single coordinate each."""
    hits = [index for index, value in enumerate(projection) if value >= threshold]
    if not hits:
        return []
    groups: list[list[int]] = [[hits[0]]]
    for index in hits[1:]:
        if index - groups[-1][-1] <= merge_within:
            groups[-1].append(index)
        else:
            groups.append([index])
    return [int(sum(group) / len(group)) for group in groups]


def build_ruled_table(
    index: int,
    bounds: BoundingBox,
    x_lines: list[float],
    y_lines: list[float],
    regions: list[Region],
) -> TableResult:
    rows = len(y_lines) - 1
    cols = len(x_lines) - 1
    cells: list[TableCellResult] = []

    for row in range(rows):
        for col in range(cols):
            cell_box = BoundingBox(
                x_lines[col],
                y_lines[row],
                x_lines[col + 1] - x_lines[col],
                y_lines[row + 1] - y_lines[row],
            )
            texts: list[str] = []
            confidences: list[float] = []
            region_id: str | None = None
            for region in regions:
                overlap = region.bounding_box.intersection(cell_box).area
                if overlap > region.bounding_box.area * 0.5:
                    texts.append(region.effective_text)
                    region_id = region_id or region.id
                    if region.confidence is not None:
                        confidences.append(region.confidence)
            text = " ".join(part for part in texts if part).strip()
            cells.append(
                _typed_cell(
                    row,
                    col,
                    text,
                    bounding_box=cell_box,
                    region_id=region_id,
                    confidence=(
                        round(sum(confidences) / len(confidences), 4) if confidences else None
                    ),
                )
            )

    table = TableResult(
        index=index,
        bounding_box=bounds,
        rows=rows,
        cols=cols,
        cells=cells,
        confidence=_table_confidence(cells),
    )
    _mark_header(table)
    return table


# --------------------------------------------------------------------------- #
# Unruled tables
# --------------------------------------------------------------------------- #
def build_unruled_table(index: int, regions: list[Region]) -> TableResult | None:
    """Infer a grid from OCR box positions alone."""
    usable = [region for region in regions if region.effective_text.strip()]
    if len(usable) < 4:
        return None

    heights = [region.bounding_box.height for region in usable]
    tolerance = statistics.median(heights) * 0.6

    rows: list[list[Region]] = []
    for region in sorted(usable, key=lambda item: item.bounding_box.center[1]):
        placed = False
        for row in rows:
            if abs(row[0].bounding_box.center[1] - region.bounding_box.center[1]) <= tolerance:
                row.append(region)
                placed = True
                break
        if not placed:
            rows.append([region])
    for row in rows:
        row.sort(key=lambda item: item.bounding_box.x)

    if len(rows) < 2:
        return None

    boundaries = _column_boundaries(usable)
    if len(boundaries) < 2:
        return None
    cols = len(boundaries) - 1

    cells: list[TableCellResult] = []
    ambiguous = False
    for row_index, row in enumerate(rows):
        occupied: dict[int, list[Region]] = {}
        for region in row:
            centre = region.bounding_box.center[0]
            col_index = max(
                0,
                min(
                    cols - 1,
                    next(
                        (
                            position
                            for position in range(cols)
                            if boundaries[position] <= centre < boundaries[position + 1]
                        ),
                        cols - 1,
                    ),
                ),
            )
            occupied.setdefault(col_index, []).append(region)
        if any(len(items) > 1 for items in occupied.values()):
            ambiguous = True

        for col_index in range(cols):
            members = occupied.get(col_index, [])
            text = " ".join(region.effective_text for region in members).strip()
            confidences = [region.confidence for region in members if region.confidence is not None]
            box = members[0].bounding_box if members else None
            for extra in members[1:]:
                box = box.union(extra.bounding_box) if box else extra.bounding_box
            cells.append(
                _typed_cell(
                    row_index,
                    col_index,
                    text,
                    bounding_box=box,
                    region_id=members[0].id if members else None,
                    confidence=(
                        round(sum(confidences) / len(confidences), 4) if confidences else None
                    ),
                )
            )

    bounds = usable[0].bounding_box
    for region in usable[1:]:
        bounds = bounds.union(region.bounding_box)

    table = TableResult(
        index=index,
        bounding_box=bounds,
        rows=len(rows),
        cols=cols,
        cells=cells,
        confidence=_table_confidence(cells),
        structure_ambiguous=ambiguous,
    )
    _mark_header(table)
    return table


def _column_boundaries(regions: list[Region]) -> list[float]:
    """Find vertical gaps that no text crosses — those are column separators."""
    if not regions:
        return []
    left = min(region.bounding_box.x for region in regions)
    right = max(region.bounding_box.right for region in regions)
    width = int(right - left)
    if width <= 0:
        return []

    occupancy = np.zeros(width + 1, dtype=np.int32)
    for region in regions:
        start = int(region.bounding_box.x - left)
        end = int(region.bounding_box.right - left)
        occupancy[max(0, start) : min(width, end) + 1] += 1

    min_gap = max(
        6, int(statistics.median([region.bounding_box.height for region in regions]) * 0.8)
    )

    boundaries = [left]
    run_start: int | None = None
    for position in range(width + 1):
        if occupancy[position] == 0:
            run_start = position if run_start is None else run_start
        elif run_start is not None:
            if position - run_start >= min_gap:
                boundaries.append(left + (run_start + position) / 2)
            run_start = None
    boundaries.append(right + 1)
    return boundaries


def _mark_header(table: TableResult) -> None:
    """First row is a header when it is all text and the row below is not."""
    if table.rows < 2:
        table.has_header = False
        return
    first = [cell for cell in table.cells if cell.row == 0 and cell.text.strip()]
    second = [cell for cell in table.cells if cell.row == 1 and cell.text.strip()]
    if not first or not second:
        table.has_header = False
        return
    first_texty = sum(1 for cell in first if cell.value_type == "text") / len(first)
    second_typed = sum(1 for cell in second if cell.value_type != "text") / len(second)
    table.has_header = first_texty > 0.75 and second_typed > 0.3
    if table.has_header:
        for cell in table.cells:
            if cell.row == 0:
                cell.is_header = True


def _table_confidence(cells: list[TableCellResult]) -> float | None:
    scores = [cell.confidence for cell in cells if cell.confidence is not None]
    if not scores:
        return None
    filled = sum(1 for cell in cells if cell.text.strip()) / max(len(cells), 1)
    return round((sum(scores) / len(scores)) * (0.6 + 0.4 * filled), 4)


# --------------------------------------------------------------------------- #
# Cell typing
# --------------------------------------------------------------------------- #
def _typed_cell(
    row: int,
    col: int,
    text: str,
    *,
    bounding_box: BoundingBox | None = None,
    region_id: str | None = None,
    confidence: float | None = None,
) -> TableCellResult:
    value_type, numeric, currency = classify_value(text)
    return TableCellResult(
        row=row,
        col=col,
        text=text,
        bounding_box=bounding_box,
        region_id=region_id,
        confidence=confidence,
        value_type=value_type,
        numeric_value=numeric,
        currency=currency,
    )


def classify_value(text: str) -> tuple[str, float | None, str | None]:
    """Return ``(value_type, numeric_value, currency)`` for a cell string."""
    stripped = text.strip()
    if not stripped:
        return "text", None, None

    if _FORMULA.match(stripped):
        return "formula", None, None

    if _PERCENT.match(stripped):
        number = _to_float(stripped.rstrip("%"))
        return ("percent", number / 100 if number is not None else None, None)

    currency = next((code for symbol, code in _CURRENCY.items() if symbol in stripped), None)
    if currency:
        cleaned = stripped
        for symbol in _CURRENCY:
            cleaned = cleaned.replace(symbol, "")
        number = _to_float(cleaned)
        if number is not None:
            return "currency", number, currency

    if _NUMBER.match(stripped):
        number = _to_float(stripped)
        if number is not None:
            return "number", number, None

    for fmt in _DATE_FORMATS:
        try:
            datetime.strptime(stripped, fmt)
            return "date", None, None
        except ValueError:
            continue

    return "text", None, None


def _to_float(text: str) -> float | None:
    cleaned = text.strip().replace(" ", "").replace(" ", "")
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    # "1.234,56" (European) vs "1,234.56" (US): the last separator is decimal.
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        cleaned = cleaned.replace(",", "." if len(parts[-1]) in {1, 2} else "")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def extract_tables(
    image: Image.Image, regions: list[Region], options: TableOptions | None = None
) -> list[TableResult]:
    options = options or TableOptions()
    tables: list[TableResult] = []

    ruled = detect_ruled_tables(image, options)
    consumed: set[str] = set()
    for index, (bounds, x_lines, y_lines) in enumerate(ruled):
        inside = [
            region
            for region in regions
            if region.bounding_box.intersection(bounds).area > region.bounding_box.area * 0.5
        ]
        if len(inside) < 2:
            continue
        table = build_ruled_table(index, bounds, x_lines, y_lines, inside)
        tables.append(table)
        consumed.update(region.id for region in inside)

    if not tables:
        remaining = [region for region in regions if region.id not in consumed]
        table = build_unruled_table(0, remaining)
        if table is not None:
            tables.append(table)

    log.info(
        "tables.extracted",
        count=len(tables),
        ruled=len(ruled),
        ambiguous=sum(1 for table in tables if table.structure_ambiguous),
    )
    return tables


def merge_cells(table: TableResult, cells: list[tuple[int, int]]) -> TableResult:
    """Editor action: merge a rectangular selection into the top-left cell."""
    if not cells:
        return table
    rows = [row for row, _ in cells]
    cols = [col for _, col in cells]
    top, left = min(rows), min(cols)
    bottom, right = max(rows), max(cols)

    keep: list[TableCellResult] = []
    merged_text: list[str] = []
    for cell in table.cells:
        inside = top <= cell.row <= bottom and left <= cell.col <= right
        if not inside:
            keep.append(cell)
            continue
        if cell.text.strip():
            merged_text.append(cell.text.strip())
        if cell.row == top and cell.col == left:
            cell.row_span = bottom - top + 1
            cell.col_span = right - left + 1
            keep.append(cell)
    for cell in keep:
        if cell.row == top and cell.col == left:
            cell.text = " ".join(merged_text)
            cell.value_type, cell.numeric_value, cell.currency = classify_value(cell.text)
    table.cells = keep
    return table
