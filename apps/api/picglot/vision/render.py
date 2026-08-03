"""Drawing translated text back onto the image.

The hard part is fitting: a German translation of an English button is often
40% longer, and a Chinese one half the width. The fitter binary-searches the
font size against *real* glyph metrics, wraps with script-aware rules, and
reports honestly when the text still does not fit rather than silently
clipping it.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageDraw

from picglot.core.logging import get_logger
from picglot.domain import languages
from picglot.domain.enums import FontClass, RenderMode, TextAlign, TextDirection, VerticalAlign
from picglot.domain.languages import Script
from picglot.vision.fonts import load_font, measure, text_width
from picglot.vision.layout import hex_to_rgb
from picglot.vision.types import BoundingBox, Region, TextStyle

log = get_logger(__name__)

MIN_FONT_SIZE = 7.0
#: How far a box may grow beyond its original size before we stop and warn.
MAX_BOX_GROWTH = 1.25


@dataclass(slots=True)
class FitResult:
    lines: list[str]
    font_size: float
    line_height: float
    total_height: float
    max_line_width: float
    overflow: bool = False
    truncated: bool = False
    grew_box: bool = False

    @property
    def fits(self) -> bool:
        return not (self.overflow or self.truncated)


@dataclass(slots=True)
class RenderReport:
    regions_rendered: int = 0
    overflowed: list[str] = field(default_factory=list)
    shrunk: list[str] = field(default_factory=list)
    missing_glyphs: list[str] = field(default_factory=list)
    mode: str = RenderMode.TRANSLATION_ONLY

    def as_dict(self) -> dict[str, Any]:
        return {
            "regions_rendered": self.regions_rendered,
            "overflowed": self.overflowed[:50],
            "shrunk": self.shrunk[:50],
            "missing_glyphs": self.missing_glyphs[:50],
            "mode": str(self.mode),
        }


# --------------------------------------------------------------------------- #
# Text shaping helpers
# --------------------------------------------------------------------------- #
def shape_bidi(text: str, direction: TextDirection) -> str:
    """Apply Arabic joining and the bidi algorithm for right-to-left scripts.

    Pillow draws glyphs in the order given, so RTL text must be reordered here.
    """
    if direction is not TextDirection.RTL or not text:
        return text
    shaped = text
    try:
        import arabic_reshaper

        shaped = arabic_reshaper.reshape(text)
    except Exception:  # pragma: no cover - optional dependency
        pass
    try:
        from bidi.algorithm import get_display

        return get_display(shaped)
    except Exception:  # pragma: no cover
        return shaped


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return (
        0x3040 <= code <= 0x30FF
        or 0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xAC00 <= code <= 0xD7AF
        or 0xF900 <= code <= 0xFAFF
    )


#: Characters that may not start a CJK line.
_CJK_NO_START = "、。，．！？：；）】》」』〕〉》”’%"
#: Characters that may not end a CJK line.
_CJK_NO_END = "（【《「『〔〈“‘"


def wrap_text(
    text: str,
    font: Any,
    max_width: float,
    *,
    letter_spacing: float = 0.0,
    allow_break_anywhere: bool = False,
) -> list[str]:
    """Wrap to ``max_width`` using measured widths, respecting CJK line rules."""
    if not text:
        return [""]
    if max_width <= 0:
        return text.split("\n")

    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        lines.extend(
            _wrap_paragraph(paragraph, font, max_width, letter_spacing, allow_break_anywhere)
        )
    return lines or [""]


def _wrap_paragraph(
    paragraph: str,
    font: Any,
    max_width: float,
    letter_spacing: float,
    allow_break_anywhere: bool,
) -> list[str]:
    cjk_heavy = sum(1 for char in paragraph if _is_cjk(char)) > len(paragraph) * 0.3
    tokens = list(paragraph) if (cjk_heavy or allow_break_anywhere) else paragraph.split(" ")
    joiner = "" if (cjk_heavy or allow_break_anywhere) else " "

    lines: list[str] = []
    current = ""
    for token in tokens:
        candidate = f"{current}{joiner}{token}" if current else token
        if text_width(candidate, font, letter_spacing) <= max_width or not current:
            current = candidate
            continue
        if cjk_heavy and token and token[0] in _CJK_NO_START and current:
            # Pull the punctuation onto the previous line instead of orphaning it.
            current += token
            lines.append(current)
            current = ""
            continue
        if cjk_heavy and current and current[-1] in _CJK_NO_END:
            lines.append(current[:-1])
            current = current[-1] + token
            continue
        lines.append(current)
        current = token

    if current:
        lines.append(current)

    # A single word longer than the box must still be broken.
    result: list[str] = []
    for line in lines:
        if text_width(line, font, letter_spacing) <= max_width or len(line) <= 1:
            result.append(line)
            continue
        result.extend(_hard_break(line, font, max_width, letter_spacing))
    return result


def _hard_break(line: str, font: Any, max_width: float, letter_spacing: float) -> list[str]:
    pieces: list[str] = []
    current = ""
    for char in line:
        candidate = current + char
        if text_width(candidate, font, letter_spacing) > max_width and current:
            pieces.append(current)
            current = char
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


# --------------------------------------------------------------------------- #
# Fitting
# --------------------------------------------------------------------------- #
def fit_text(
    text: str,
    box: BoundingBox,
    style: TextStyle,
    *,
    script: Script = Script.LATIN,
    min_size: float = MIN_FONT_SIZE,
    allow_growth: bool = True,
) -> tuple[FitResult, Any]:
    """Binary-search the largest font size at which ``text`` fits ``box``."""
    text = text.strip()
    if not text:
        return FitResult([], style.font_size, style.font_size, 0, 0), _font_for(style, script, "")

    low, high = min_size, max(min_size, style.font_size * 1.6)
    best: FitResult | None = None
    best_font: Any = None

    for _ in range(18):  # ~0.01px resolution over any realistic range
        size = (low + high) / 2
        font = _font_for(style, script, text, size)
        lines = wrap_text(text, font, box.width, letter_spacing=style.letter_spacing)
        metrics = measure(text[:1] or "A", font, line_height_ratio=style.line_height)
        line_height = max(metrics.line_height, size * style.line_height)
        total_height = line_height * len(lines)
        widest = max((text_width(line, font, style.letter_spacing) for line in lines), default=0.0)

        if total_height <= box.height and widest <= box.width:
            best = FitResult(lines, size, line_height, total_height, widest)
            best_font = font
            low = size
        else:
            high = size
        if high - low < 0.25:
            break

    if best is not None and best_font is not None:
        return best, best_font

    # Nothing fit even at the minimum: render at the floor and report honestly.
    font = _font_for(style, script, text, min_size)
    lines = wrap_text(text, font, box.width, letter_spacing=style.letter_spacing)
    metrics = measure("A", font, line_height_ratio=style.line_height)
    line_height = max(metrics.line_height, min_size * style.line_height)
    total_height = line_height * len(lines)
    widest = max((text_width(line, font, style.letter_spacing) for line in lines), default=0.0)

    result = FitResult(
        lines=lines,
        font_size=min_size,
        line_height=line_height,
        total_height=total_height,
        max_line_width=widest,
        overflow=True,
    )
    if allow_growth and total_height <= box.height * MAX_BOX_GROWTH:
        result.grew_box = True
        result.overflow = False
    return result, font


def _font_for(style: TextStyle, script: Script, text: str, size: float | None = None) -> Any:
    return load_font(
        size=size if size is not None else style.font_size,
        font_class=style.font_class if isinstance(style.font_class, FontClass) else FontClass.SANS,
        script=script,
        bold=style.bold,
        italic=style.italic,
        text=text[:120],
        family_hint=style.font_family,
    )


def missing_glyphs(text: str, font: Any) -> list[str]:
    """Characters the chosen font cannot draw — surfaced as a warning, not tofu."""
    path = getattr(font, "path", None)
    if not path:
        return []
    try:
        from fontTools.ttLib import TTFont

        with TTFont(str(path), lazy=True, fontNumber=0) as parsed:
            covered: set[int] = set()
            for table in parsed["cmap"].tables:
                if table.isUnicode():
                    covered.update(table.cmap.keys())
    except Exception:
        return []
    missing = {
        char
        for char in text
        if ord(char) > 32
        and ord(char) not in covered
        and unicodedata.category(char) not in {"Cc", "Cf", "Zs"}
    }
    return sorted(missing)[:10]


# --------------------------------------------------------------------------- #
# Drawing
# --------------------------------------------------------------------------- #
def draw_region(
    image: Image.Image,
    region: Region,
    text: str,
    *,
    target_language: str | None = None,
    report: RenderReport | None = None,
) -> Image.Image:
    """Draw ``text`` inside ``region``'s box using the region's style."""
    if not text.strip():
        return image

    style = region.style
    language = languages.get(target_language or region.detected_language)
    script = language.script if language else Script.LATIN
    if language and language.is_rtl:
        style.direction = TextDirection.RTL

    box = region.bounding_box
    fit, font = fit_text(text, box, style, script=script)

    if report is not None:
        report.regions_rendered += 1
        if fit.overflow:
            report.overflowed.append(region.id)
        if fit.font_size < style.font_size * 0.75:
            report.shrunk.append(region.id)
        absent = missing_glyphs(text, font)
        if absent:
            report.missing_glyphs.append(region.id)
            log.warning("render.missing_glyphs", region=region.id, count=len(absent))

    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    color = (*hex_to_rgb(style.color), int(max(0.0, min(1.0, style.opacity)) * 255))
    outline = hex_to_rgb(style.outline_color) if style.outline_color else None

    if style.direction is TextDirection.VERTICAL_RL:
        _draw_vertical(draw, fit, font, box, style, color, outline)
    else:
        _draw_horizontal(draw, fit, font, box, style, color, outline)

    if abs(region.rotation) > 0.5:
        layer = _rotate_layer(layer, region, fit)

    return Image.alpha_composite(image.convert("RGBA"), layer).convert("RGB")


def _draw_horizontal(
    draw: ImageDraw.ImageDraw,
    fit: FitResult,
    font: Any,
    box: BoundingBox,
    style: TextStyle,
    color: tuple[int, int, int, int],
    outline: tuple[int, int, int] | None,
) -> None:
    block_height = fit.total_height
    if style.vertical_align is VerticalAlign.TOP:
        y = box.y
    elif style.vertical_align is VerticalAlign.BOTTOM:
        y = box.bottom - block_height
    else:
        y = box.y + (box.height - block_height) / 2
    y = max(box.y - 2, y)

    for line in fit.lines:
        display = shape_bidi(line, style.direction)
        width = text_width(display, font, style.letter_spacing)

        if style.align is TextAlign.CENTER:
            x = box.x + (box.width - width) / 2
        elif style.align is TextAlign.RIGHT or style.direction is TextDirection.RTL:
            x = box.right - width
        else:
            x = box.x

        _draw_line(draw, (x, y), display, font, style, color, outline)
        y += fit.line_height


def _draw_line(
    draw: ImageDraw.ImageDraw,
    position: tuple[float, float],
    text: str,
    font: Any,
    style: TextStyle,
    color: tuple[int, int, int, int],
    outline: tuple[int, int, int] | None,
) -> None:
    x, y = position
    stroke_width = round(style.outline_width) if outline else 0

    if style.shadow:
        shadow_color = (0, 0, 0, int(color[3] * 0.45))
        _draw_with_spacing(draw, (x + 1.5, y + 1.5), text, font, style, shadow_color, None, 0)

    _draw_with_spacing(draw, (x, y), text, font, style, color, outline, stroke_width)

    if style.underline:
        metrics = measure(text, font, line_height_ratio=style.line_height)
        underline_y = y + metrics.ascent + max(1.0, metrics.descent * 0.35)
        width = text_width(text, font, style.letter_spacing)
        draw.line(
            [(x, underline_y), (x + width, underline_y)],
            fill=color,
            width=max(1, stroke_width or 1),
        )


def _draw_with_spacing(
    draw: ImageDraw.ImageDraw,
    position: tuple[float, float],
    text: str,
    font: Any,
    style: TextStyle,
    color: tuple[int, int, int, int],
    outline: tuple[int, int, int] | None,
    stroke_width: int,
) -> None:
    x, y = position
    kwargs: dict[str, Any] = {"font": font, "fill": color}
    if outline and stroke_width:
        kwargs["stroke_width"] = stroke_width
        kwargs["stroke_fill"] = (*outline, color[3])

    if not style.letter_spacing:
        draw.text((x, y), text, **kwargs)
        return
    for char in text:
        draw.text((x, y), char, **kwargs)
        x += text_width(char, font) + style.letter_spacing


def _draw_vertical(
    draw: ImageDraw.ImageDraw,
    fit: FitResult,
    font: Any,
    box: BoundingBox,
    style: TextStyle,
    color: tuple[int, int, int, int],
    outline: tuple[int, int, int] | None,
) -> None:
    """Vertical CJK: columns run right-to-left, characters top-to-bottom."""
    char_height = fit.line_height
    per_column = max(1, int(box.height // max(char_height, 1)))
    text = "".join(fit.lines)
    columns = [text[index : index + per_column] for index in range(0, len(text), per_column)] or [
        ""
    ]

    column_width = max(char_height, box.width / max(len(columns), 1))
    x = box.right - column_width
    for column in columns:
        y = box.y
        for char in column:
            width = text_width(char, font)
            _draw_line(draw, (x + (column_width - width) / 2, y), char, font, style, color, outline)
            y += char_height
        x -= column_width
        if x < box.x - column_width:
            break


def _rotate_layer(layer: Image.Image, region: Region, fit: FitResult) -> Image.Image:
    """Rotate the drawn text around the region centre to match the original."""
    box = region.bounding_box
    padding = int(max(box.width, box.height, fit.max_line_width))
    crop_box = (
        int(max(0, box.x - padding)),
        int(max(0, box.y - padding)),
        int(min(layer.width, box.right + padding)),
        int(min(layer.height, box.bottom + padding)),
    )
    if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
        return layer
    patch = layer.crop(crop_box)
    rotated = patch.rotate(-region.rotation, resample=Image.Resampling.BICUBIC, expand=False)
    result = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    result.paste(rotated, crop_box[:2])
    return result


# --------------------------------------------------------------------------- #
# Page composition
# --------------------------------------------------------------------------- #
def render_page(
    background: Image.Image,
    regions: list[Region],
    translations: dict[str, str],
    *,
    target_language: str | None = None,
    mode: RenderMode = RenderMode.TRANSLATION_ONLY,
    original: Image.Image | None = None,
) -> tuple[Image.Image, RenderReport]:
    """Compose the final image for one page."""
    report = RenderReport(mode=str(mode))

    if mode is RenderMode.SIDE_BY_SIDE and original is not None:
        translated, report = render_page(
            background,
            regions,
            translations,
            target_language=target_language,
            mode=RenderMode.TRANSLATION_ONLY,
        )
        return _side_by_side(original, translated), report

    canvas = (
        original if mode is RenderMode.OVERLAY and original is not None else background
    ).copy()

    for region in regions:
        text = translations.get(region.id, "")
        if not text.strip():
            continue
        if mode is RenderMode.BILINGUAL:
            canvas = _draw_bilingual(canvas, region, text, target_language, report)
        else:
            canvas = draw_region(
                canvas, region, text, target_language=target_language, report=report
            )
    return canvas, report


def _draw_bilingual(
    image: Image.Image,
    region: Region,
    translation: str,
    target_language: str | None,
    report: RenderReport,
) -> Image.Image:
    """Original on top, translation underneath, inside the same footprint."""
    box = region.bounding_box
    half = box.height / 2

    top = Region(
        id=f"{region.id}:src",
        polygon=[],
        bounding_box=BoundingBox(box.x, box.y, box.width, half),
        style=TextStyle.from_dict(region.style.as_dict()),
        detected_language=region.detected_language,
        rotation=region.rotation,
    )
    top.style.opacity = 0.75
    bottom = Region(
        id=region.id,
        polygon=[],
        bounding_box=BoundingBox(box.x, box.y + half, box.width, half),
        style=TextStyle.from_dict(region.style.as_dict()),
        detected_language=target_language,
        rotation=region.rotation,
    )
    bottom.style.bold = True

    image = draw_region(image, top, region.effective_text, report=report)
    return draw_region(image, bottom, translation, target_language=target_language, report=report)


def _side_by_side(original: Image.Image, translated: Image.Image, gap: int = 16) -> Image.Image:
    height = max(original.height, translated.height)
    canvas = Image.new("RGB", (original.width + translated.width + gap, height), (255, 255, 255))
    canvas.paste(original, (0, 0))
    canvas.paste(translated, (original.width + gap, 0))
    return canvas


def add_watermark(image: Image.Image, text: str, *, opacity: float = 0.28) -> Image.Image:
    """Used only for shared links when the owner asks for it."""
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    size = max(14, int(min(image.size) * 0.045))
    font = load_font(size=size, text=text)
    width = text_width(text, font)
    step_x = int(width + size * 4)
    step_y = int(size * 6)
    for y in range(0, image.height + step_y, step_y):
        for x in range(-step_x, image.width + step_x, step_x):
            draw.text((x, y), text, font=font, fill=(120, 120, 120, int(255 * opacity)))
    return Image.alpha_composite(image.convert("RGBA"), layer).convert("RGB")
