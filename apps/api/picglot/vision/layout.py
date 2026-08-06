"""Layout analysis: grouping OCR lines into blocks, reading order, region typing.

OCR engines return lines (sometimes words). Everything downstream — translation
quality, DOCX structure, Markdown headings, reading order for screen readers —
depends on turning those lines back into paragraphs, columns and headings. That
reconstruction lives here so every provider benefits from it equally.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

from PIL import Image

from picglot.core.ids import ulid
from picglot.domain import languages
from picglot.domain.enums import FontClass, RegionType, TextAlign, TextDirection
from picglot.vision import typeface
from picglot.vision.preprocess import dominant_color
from picglot.vision.types import BoundingBox, Region

#: Below this the measurement is not decisive enough to override a default —
#: too little ink, or a metric sitting on its own threshold.
MIN_TYPEFACE_CONFIDENCE = 0.5

_BULLET = re.compile(r"^\s*([•·▪◦‣∙*+\-–—]|\(?[a-zA-Z0-9]{1,3}[.)])\s+")
_NUMERIC = re.compile(r"^[\s\d.,;:%+\-()/$€£¥₽]+$")
_URL_OR_EMAIL = re.compile(r"(https?://|www\.|[\w.+-]+@[\w-]+\.[\w.]+)", re.IGNORECASE)
_FORMULA = re.compile(r"[=∑∫√±≤≥≈∞π](?:.*[=∑∫√±≤≥≈∞π])?")


@dataclass(slots=True)
class LayoutOptions:
    #: Vertical gap (as a multiple of line height) that still counts as the same paragraph.
    paragraph_gap_ratio: float = 0.75
    #: Horizontal overlap needed for two lines to belong to the same block.
    min_horizontal_overlap: float = 0.35
    #: Font size multiple above the body text that promotes a line to a heading.
    heading_size_ratio: float = 1.25
    detect_columns: bool = True
    merge_lines: bool = True


def analyse(
    regions: list[Region],
    *,
    page_size: tuple[int, int],
    image: Image.Image | None = None,
    options: LayoutOptions | None = None,
    ui_mode: bool = False,
) -> list[Region]:
    """Group, order, classify and style raw OCR regions.

    ``ui_mode`` (screenshots) keeps short strings separate: a button label must
    not be merged into the paragraph next to it.
    """
    options = options or LayoutOptions()
    usable = [region for region in regions if not region.is_empty]
    if not usable:
        return []

    for region in usable:
        region.style.font_size = _estimate_font_size(region)

    columns = (
        _split_columns(usable, page_size[0]) if options.detect_columns and not ui_mode else [usable]
    )

    ordered: list[Region] = []
    reading_order = 0
    for column in columns:
        blocks = (
            _group_into_blocks(column, options)
            if options.merge_lines and not ui_mode
            else [[region] for region in _sort_reading_order(column)]
        )
        for block in blocks:
            group_id = f"grp_{ulid()}"
            merged = _merge_block(block, group_id) if len(block) > 1 else block[0]
            merged.group_id = group_id
            merged.reading_order = reading_order
            reading_order += 1
            ordered.append(merged)

    body_size = _body_font_size(ordered)
    for region in ordered:
        region.region_type = _classify(region, body_size, ui_mode=ui_mode)
        _apply_style(region, image, body_size)
        if region.detected_language is None:
            region.detected_language = languages.guess_language(region.effective_text)
    return ordered


# --------------------------------------------------------------------------- #
# Grouping
# --------------------------------------------------------------------------- #
def _sort_reading_order(regions: list[Region]) -> list[Region]:
    """Top-to-bottom, then left-to-right within a band of one line height."""
    if not regions:
        return []
    heights = [region.bounding_box.height for region in regions if region.bounding_box.height > 0]
    band = statistics.median(heights) * 0.6 if heights else 10.0
    return sorted(
        regions,
        key=lambda region: (round(region.bounding_box.y / max(band, 1.0)), region.bounding_box.x),
    )


def _horizontal_overlap(first: BoundingBox, second: BoundingBox) -> float:
    overlap = min(first.right, second.right) - max(first.x, second.x)
    if overlap <= 0:
        return 0.0
    return overlap / max(1.0, min(first.width, second.width))


def _group_into_blocks(regions: list[Region], options: LayoutOptions) -> list[list[Region]]:
    lines = _sort_reading_order(regions)
    blocks: list[list[Region]] = []
    current: list[Region] = []

    for region in lines:
        if not current:
            current = [region]
            continue

        previous = current[-1]
        gap = region.bounding_box.y - previous.bounding_box.bottom
        line_height = max(previous.bounding_box.height, region.bounding_box.height, 1.0)
        overlap = _horizontal_overlap(previous.bounding_box, region.bounding_box)
        size_ratio = _ratio(previous.style.font_size, region.style.font_size)

        same_block = (
            gap <= line_height * options.paragraph_gap_ratio
            and overlap >= options.min_horizontal_overlap
            and size_ratio <= 1.35
            and not _BULLET.match(region.text)
        )
        if same_block:
            current.append(region)
        else:
            blocks.append(current)
            current = [region]

    if current:
        blocks.append(current)
    return blocks


def _split_columns(regions: list[Region], page_width: int) -> list[list[Region]]:
    """Detect multi-column layouts via a vertical projection gap."""
    if len(regions) < 8 or page_width <= 0:
        return [regions]

    occupancy = [0] * page_width
    for region in regions:
        start = max(0, int(region.bounding_box.x))
        end = min(page_width, int(region.bounding_box.right))
        for x in range(start, end):
            occupancy[x] += 1

    # A gutter is a wide run of columns nothing occupies, away from the margins.
    margin = int(page_width * 0.15)
    gaps: list[tuple[int, int]] = []
    run_start: int | None = None
    for x in range(margin, page_width - margin):
        if occupancy[x] == 0:
            run_start = x if run_start is None else run_start
        elif run_start is not None:
            if x - run_start >= page_width * 0.05:
                gaps.append((run_start, x))
            run_start = None

    if not gaps:
        return [regions]
    # Use the widest gutter only — deeper nesting is rare and error prone.
    gutter = max(gaps, key=lambda item: item[1] - item[0])
    split_at = (gutter[0] + gutter[1]) / 2

    left = [region for region in regions if region.bounding_box.center[0] < split_at]
    right = [region for region in regions if region.bounding_box.center[0] >= split_at]
    if len(left) < 3 or len(right) < 3:
        return [regions]
    return [left, right]


def _merge_block(block: list[Region], group_id: str) -> Region:
    """Join lines into one paragraph region, repairing hyphenation."""
    base = block[0]
    box = base.bounding_box
    for region in block[1:]:
        box = box.union(region.bounding_box)

    pieces: list[str] = []
    for index, region in enumerate(block):
        text = region.effective_text.strip()
        if not text:
            continue
        if pieces and pieces[-1].endswith(("-", "‑", "–")) and not text[:1].isupper():
            pieces[-1] = pieces[-1][:-1] + text
            continue
        pieces.append(text)
        region.line_number = index

    confidences = [region.confidence for region in block if region.confidence is not None]
    merged = Region(
        id=base.id,
        polygon=box.to_polygon(),
        bounding_box=box,
        text=" ".join(pieces),
        confidence=min(confidences) if confidences else None,
        rotation=base.rotation,
        region_type=base.region_type,
        detected_language=base.detected_language,
        group_id=group_id,
        style=base.style,
        metadata={
            **base.metadata,
            "merged_lines": len(block),
            "line_boxes": [region.bounding_box.as_dict() for region in block],
        },
    )
    merged.style.font_size = statistics.median(
        [region.style.font_size for region in block] or [merged.style.font_size]
    )
    return merged


# --------------------------------------------------------------------------- #
# Classification and styling
# --------------------------------------------------------------------------- #
def _estimate_font_size(region: Region) -> float:
    """Cap height ≈ box height; a single line box is the best signal we have."""
    height = region.bounding_box.height
    lines = max(1, region.metadata.get("merged_lines", 1))
    return max(6.0, round(height / lines * 0.78, 1))


def _body_font_size(regions: list[Region]) -> float:
    """Median font size weighted by character count.

    Headings are large but short. Weighting by length stops a two-word title
    from dragging the "body size" up and hiding itself from heading detection.
    """
    samples: list[float] = []
    for region in regions:
        size = region.style.font_size
        if size <= 0:
            continue
        weight = max(1, len(region.effective_text) // 8)
        samples.extend([size] * min(weight, 40))
    return statistics.median(samples) if samples else 16.0


def _ratio(first: float, second: float) -> float:
    if min(first, second) <= 0:
        return 1.0
    return max(first, second) / min(first, second)


def _classify(region: Region, body_size: float, *, ui_mode: bool) -> RegionType:
    text = region.effective_text.strip()
    if not text:
        return RegionType.UNKNOWN
    if region.region_type not in {RegionType.PARAGRAPH, RegionType.UNKNOWN}:
        return region.region_type  # a provider already told us something specific

    words = text.split()
    if _BULLET.match(text):
        return RegionType.LIST_ITEM
    if _NUMERIC.fullmatch(text) and any(char.isdigit() for char in text):
        return RegionType.NUMERIC_FIELD
    if _FORMULA.search(text) and len(words) <= 12:
        return RegionType.FORMULA
    if ui_mode and len(words) <= 4 and len(text) <= 28:
        return RegionType.UI_LABEL
    if region.style.font_size >= body_size * 1.25 and len(words) <= 14:
        return RegionType.HEADING
    if region.style.font_size <= body_size * 0.8 and len(words) <= 25:
        return RegionType.CAPTION if len(words) <= 12 else RegionType.FOOTNOTE
    return RegionType.PARAGRAPH


def _apply_style(region: Region, image: Image.Image | None, body_size: float) -> None:
    style = region.style
    text = region.effective_text

    language = languages.get(region.detected_language)
    if language and language.is_rtl:
        style.direction = TextDirection.RTL
        style.align = TextAlign.RIGHT
    elif region.metadata.get("vertical"):
        style.direction = TextDirection.VERTICAL_RL

    if region.region_type is RegionType.HEADING:
        style.bold = True
        style.line_height = 1.15
    elif region.region_type is RegionType.UI_LABEL:
        style.line_height = 1.1
        style.align = TextAlign.CENTER

    # What the lettering actually looks like, read off the pixels. Without this
    # every region is redrawn in the same grotesque whatever it replaced, which
    # is the one thing the product is for.
    measured = None
    if image is not None:
        box = region.bounding_box
        measured = typeface.estimate(
            image, (int(box.x), int(box.y), int(box.right), int(box.bottom))
        )
        region.metadata["typeface"] = measured.as_dict()

    if region.region_type is RegionType.HANDWRITING:
        # The tool was told the page is handwritten; that beats a measurement.
        style.font_class = FontClass.HANDWRITING
    elif _looks_monospaced(text):
        style.font_class = FontClass.MONO
    elif (
        measured is not None
        and measured.font_class is not None
        and measured.confidence >= MIN_TYPEFACE_CONFIDENCE
    ):
        style.font_class = measured.font_class

    # Weight and slant come from the ink rather than from the region's role: a
    # heading set in a light face is a light heading, and drawing it bold
    # because headings are usually bold is exactly the mismatch to avoid.
    if measured is not None and measured.confidence >= MIN_TYPEFACE_CONFIDENCE:
        if measured.bold is not None:
            style.bold = measured.bold
        if measured.italic is not None:
            style.italic = measured.italic

    if image is not None:
        _sample_colors(region, image)

    if style.font_size <= 0:
        style.font_size = body_size


def _looks_monospaced(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 8:
        return False
    symbols = sum(1 for char in stripped if char in "{}[]()<>;=|_/\\")
    return symbols / len(stripped) > 0.12


def _sample_colors(region: Region, image: Image.Image) -> None:
    """Estimate ink and paper colour by comparing the text band to its surround."""
    box = region.bounding_box
    inner = (int(box.x), int(box.y), int(box.right), int(box.bottom))
    outer = box.expand(max(4.0, box.height * 0.4), bounds=image.size)
    outer_box = (int(outer.x), int(outer.y), int(outer.right), int(outer.bottom))

    background = dominant_color(image, outer_box)
    foreground = _darkest_or_lightest(image, inner, background)

    region.style.background_color = _to_hex(background)
    region.style.color = _to_hex(foreground)
    if _contrast(foreground, background) < 2.5:
        region.style.outline_color = "#FFFFFF" if _luminance(foreground) < 128 else "#000000"
        region.style.outline_width = max(1.0, region.style.font_size * 0.06)


def _darkest_or_lightest(
    image: Image.Image, box: tuple[int, int, int, int], background: tuple[int, int, int]
) -> tuple[int, int, int]:
    left, top, right, bottom = box
    left, top = max(0, left), max(0, top)
    right, bottom = min(image.width, right), min(image.height, bottom)
    if right <= left or bottom <= top:
        return (0, 0, 0)
    crop = image.crop((left, top, right, bottom)).convert("RGB")
    crop = crop.resize((min(48, crop.width), min(48, crop.height)))
    pixels = list(crop.getdata())
    if not pixels:
        return (0, 0, 0)
    background_luminance = _luminance(background)
    # Ink is whichever extreme is furthest from the paper.
    pixels.sort(key=_luminance)
    return pixels[-1] if background_luminance < 128 else pixels[0]


def _luminance(color: tuple[int, int, int]) -> float:
    red, green, blue = color[:3]
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    lighter = max(_luminance(first), _luminance(second)) + 5
    darker = min(_luminance(first), _luminance(second)) + 5
    return lighter / darker


def _to_hex(color: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(max(0, min(255, int(value))) for value in color[:3]))


def hex_to_rgb(
    value: str | None, fallback: tuple[int, int, int] = (0, 0, 0)
) -> tuple[int, int, int]:
    if not value:
        return fallback
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(char * 2 for char in value)
    if len(value) != 6:
        return fallback
    try:
        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
    except ValueError:
        return fallback


def assign_reading_order(regions: list[Region]) -> list[Region]:
    """Re-number reading order after manual edits in the editor."""
    for index, region in enumerate(_sort_reading_order(regions)):
        region.reading_order = index
    return sorted(regions, key=lambda region: region.reading_order)


def detect_protected_spans(text: str) -> list[tuple[int, int]]:
    """URLs, emails and codes that translation must not rewrite."""
    return [match.span() for match in _URL_OR_EMAIL.finditer(text)]
