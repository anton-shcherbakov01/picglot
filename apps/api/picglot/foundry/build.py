"""Assembling traced outlines into a font file a renderer will accept.

The output is a real TrueType binary: the same file the user can download and
install, and the same file the renderer loads through Pillow. Nothing here is a
preview format or an intermediate — a font that only works inside this service
would not be a font.
"""

from __future__ import annotations

import io
import unicodedata
from dataclasses import dataclass

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from picglot.foundry.outlines import UNITS_PER_EM, Contour, GlyphOutline

#: Vertical metrics as fractions of the em, used when the sample cannot say.
DEFAULT_ASCENT = 0.80
DEFAULT_DESCENT = 0.20

#: What an empty glyph is worth, as a fraction of the em: a space.
DEFAULT_SPACE_ADVANCE = 0.28


@dataclass(slots=True)
class FontMetrics:
    units_per_em: int = UNITS_PER_EM
    ascent: int = int(UNITS_PER_EM * DEFAULT_ASCENT)
    descent: int = int(UNITS_PER_EM * DEFAULT_DESCENT)
    cap_height: int | None = None
    x_height: int | None = None


def build_font(
    glyphs: dict[str, GlyphOutline],
    *,
    family: str,
    style: str = "Regular",
    metrics: FontMetrics | None = None,
    version: str = "1.000",
    designer_note: str = "",
) -> bytes:
    """Assemble ``glyphs`` — keyed by the character each draws — into a TTF."""
    metrics = metrics or FontMetrics()
    usable = {char: outline for char, outline in glyphs.items() if len(char) == 1}
    if not usable:
        raise ValueError("a font needs at least one glyph")

    names = {char: _glyph_name(char) for char in usable}
    order = [".notdef", "space", *sorted(names.values())]

    builder = FontBuilder(metrics.units_per_em, isTTF=True)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap({ord(char): names[char] for char in usable} | {0x20: "space"})

    space_advance = int(metrics.units_per_em * DEFAULT_SPACE_ADVANCE)
    pen_glyphs = {".notdef": TTGlyphPen(None).glyph(), "space": TTGlyphPen(None).glyph()}
    horizontal: dict[str, tuple[int, int]] = {
        ".notdef": (space_advance, 0),
        "space": (space_advance, 0),
    }

    for char, outline in usable.items():
        name = names[char]
        pen_glyphs[name] = _draw(outline)
        horizontal[name] = (max(0, outline.advance), outline.left_side_bearing)

    builder.setupGlyf(pen_glyphs)
    builder.setupHorizontalMetrics(horizontal)
    builder.setupHorizontalHeader(ascent=metrics.ascent, descent=-abs(metrics.descent), lineGap=0)
    builder.setupNameTable(_names(family, style, version, designer_note))
    builder.setupOS2(
        sTypoAscender=metrics.ascent,
        sTypoDescender=-abs(metrics.descent),
        sTypoLineGap=0,
        usWinAscent=metrics.ascent,
        usWinDescent=abs(metrics.descent),
        sCapHeight=metrics.cap_height or metrics.ascent,
        sxHeight=metrics.x_height or int(metrics.ascent * 0.66),
        achVendID="PGLT",
    )
    builder.setupPost(isFixedPitch=0)

    buffer = io.BytesIO()
    builder.save(buffer)
    return buffer.getvalue()


def _draw(outline: GlyphOutline) -> object:
    pen = TTGlyphPen(None)
    for contour in outline.contours:
        _draw_contour(pen, contour)
    return pen.glyph()


def _draw_contour(pen: TTGlyphPen, contour: Contour) -> None:
    """Replay one contour into the pen, off-curve runs and all."""
    points, flags = contour.points, contour.on_curve
    if len(points) < 2 or not any(flags):
        return

    pen.moveTo(points[0])
    pending: list[tuple[float, float]] = []
    for point, on_curve in zip(points[1:], flags[1:], strict=True):
        if on_curve:
            if pending:
                pen.qCurveTo(*pending, point)
                pending = []
            else:
                pen.lineTo(point)
        else:
            pending.append(point)

    # Whatever is left bends the closing segment back to where the pen started.
    if pending:
        pen.qCurveTo(*pending, points[0])
    pen.closePath()


def _glyph_name(char: str) -> str:
    """A stable, legal glyph name for a character.

    Names are cosmetic — the cmap is what maps a character to a glyph — but a
    font whose glyphs are all called `uni0041`-style hex is unreadable in every
    tool that opens it, so anything with a Unicode name gets a readable one.
    """
    if char.isascii() and char.isalpha():
        return char
    if char.isascii() and char.isdigit():
        return f"digit{char}"
    readable = unicodedata.name(char, "").lower().replace(" ", "").replace("-", "")
    if readable and readable.isalnum():
        return f"{readable[:28]}{ord(char):04X}"
    return f"uni{ord(char):04X}"


def _names(family: str, style: str, version: str, note: str) -> dict[str, str]:
    full = f"{family} {style}".strip()
    postscript = "".join(char for char in full if char.isalnum() or char == "-")[:63]
    return {
        "familyName": family,
        "styleName": style,
        "uniqueFontIdentifier": f"{postscript};PicGlot;{version}",
        "fullName": full,
        "psName": postscript or "PicGlotGenerated",
        "version": f"Version {version}",
        "description": note or "Generated by PicGlot from the lettering it was asked to reproduce.",
    }
