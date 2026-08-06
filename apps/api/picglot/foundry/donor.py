"""Letters the sample never showed us.

A photograph of English lettering contains no Ж, and a sheet of someone's
handwriting is never complete either — but the translation needs a whole
alphabet, so the rest has to come from somewhere.

It comes from the closest installed face, bent to match what *was* measured:
the weight of the strokes, the lean, the width, the size of the lower case
against the capitals. Those four carry nearly all of what a reader recognises
as "the same lettering", which is why a bold condensed original redrawn in a
regular grotesque looks wrong even when the shapes underneath are identical.

What this cannot do is invent a letterform. A Ж derived from Noto Sans is a
Noto Sans Ж that has been made heavier, narrower and slanted to match; it is
not the Ж the hand that wrote the sample would have written. That gap is the
one a trained model closes, and nothing measured off four Latin letters will.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from picglot.foundry.outlines import GlyphImage

#: Pixels per em the donor is rasterised at. High enough that a stroke is tens
#: of pixels wide, so thickening it by a fraction of one is still meaningful.
RENDER_SIZE = 256

#: Weight is changed by growing or shrinking the strokes. Past this much of the
#: em the letterform stops being the donor's — counters fill in, thin strokes
#: vanish — and the request is refused rather than answered with a blob.
MAX_WEIGHT_DELTA = 0.06

#: Slant is applied as a shear. Beyond this the result is a parallelogram with
#: letters in it, not italics.
MAX_SLANT_DEGREES = 24.0


@dataclass(slots=True)
class StyleParameters:
    """What the sample said about how its letters are drawn.

    Every field is a ratio or an angle, never a pixel count: the sample and the
    donor are different sizes and the whole point is to compare them.
    """

    #: Stroke width over the height of the letter band.
    stroke_ratio: float | None = None
    #: Lean, in degrees, as `vision.typeface` reports it: negative leans right.
    #: The convention is not arbitrary here — these numbers come from that
    #: measurement, and flipping the sign on the way in draws backslanted text.
    slant_degrees: float = 0.0
    #: Width of the lettering over the donor's width for the same string.
    width_ratio: float = 1.0

    def is_neutral(self) -> bool:
        return (
            self.stroke_ratio is None
            and abs(self.slant_degrees) < 1.0
            and abs(self.width_ratio - 1.0) < 0.02
        )


def derive(
    characters: str,
    *,
    font_path: str,
    font_index: int = 0,
    style: StyleParameters | None = None,
) -> dict[str, GlyphImage]:
    """Rasterise ``characters`` from a donor face and bend them into style."""
    style = style or StyleParameters()
    try:
        face = ImageFont.truetype(font_path, size=RENDER_SIZE, index=font_index)
    except OSError:  # pragma: no cover - unreadable donor
        return {}

    ascent, _descent = face.getmetrics()
    shear = _shear(style.slant_degrees, face)
    stretch = style.width_ratio if abs(style.width_ratio - 1.0) >= 0.02 else 1.0
    wanted = dict.fromkeys(char for char in characters if not char.isspace())
    grow = _calibrate(face, list(wanted)[:12], ascent, shear, stretch, style.stroke_ratio)

    produced: dict[str, GlyphImage] = {}
    for char in wanted:
        mask, pen_x, advance, baseline = _transform(face, char, ascent, shear, stretch, grow)
        if mask is None:
            continue
        glyph = GlyphImage.from_canvas(
            mask, baseline=baseline, em_pixels=RENDER_SIZE, pen_x=pen_x, advance=advance
        )
        if glyph is not None:
            produced[char] = glyph
    return produced


def _transform(
    face: ImageFont.FreeTypeFont,
    char: str,
    ascent: int,
    shear: float,
    stretch: float,
    grow: int,
) -> tuple[np.ndarray | None, float, float, float]:
    """One donor letter, leaned, squeezed and re-weighted, in that order."""
    mask, pen_x, advance, baseline = _render(face, char, ascent)
    if mask is None:
        return None, pen_x, advance, baseline
    if shear:
        mask, pen_x, advance = _shear_mask(mask, shear, baseline, pen_x, advance)
    if stretch != 1.0:
        mask, pen_x, advance = _stretch(mask, stretch, pen_x, advance)
    if grow:
        mask = _reweight(mask, grow)
    return mask, pen_x, advance, baseline


def _calibrate(
    face: ImageFont.FreeTypeFont,
    probe: list[str],
    ascent: int,
    shear: float,
    stretch: float,
    target: float | None,
) -> int:
    """How much to grow the strokes by, decided by measuring, not by arithmetic.

    Working out the growth from the difference in ratios is a calculation that
    holds only while the letters stay letters. Squeeze a face to two thirds and
    thicken it back and the counters start to close: the ink stops being
    strokes and the measured weight runs away from the target rather than
    towards it — asked for 0.17 it produced 0.20 and the letters were mush.

    So the derived letters are measured, with the same measurement that read
    the original off the page — and measured on a *font built from them*, not
    on the bitmaps they came from. Tracing an outline and rasterising it again
    does not preserve stroke width to the pixel, and calibrating against the
    bitmap left a face a third heavier than the one asked for. Whatever the
    round trip does to the weight, doing the round trip is the only way to
    find out.

    A handful of builds decide it: one to see where the strokes land untouched,
    then the growth that residual implies, then two steps either side of it —
    the response is not linear, so the estimate names the neighbourhood and the
    measurements pick the winner inside it.
    """
    if not probe:
        return 0
    # With no measurement to aim at, the aim is the donor itself: whatever the
    # round trip does to stroke width, undo it. That reference has to be taken
    # the same way as the candidates — same string, same size, same measurement
    # — or the correction chases the difference between two probes instead.
    aim = target if target is not None else _reference_ratio(face, "".join(probe))
    if aim is None:
        return 0

    limit = int(MAX_WEIGHT_DELTA * RENDER_SIZE * 0.5)
    measured = _built_stroke_ratio(face, probe, ascent, shear, stretch, 0)
    if measured is None:
        return 0

    # Growing by one pixel widens a stroke by two, in a canvas RENDER_SIZE tall.
    step = (aim - measured) * RENDER_SIZE * 0.5
    guess = max(-limit, min(limit, round(step)))

    best, best_error = 0, abs(measured - aim)
    for candidate in {guess, guess - 2, guess - 1, guess + 1, guess + 2}:
        if candidate == 0 or not -limit <= candidate <= limit:
            continue
        found = _built_stroke_ratio(face, probe, ascent, shear, stretch, candidate)
        if found is None:
            continue
        error = abs(found - aim)
        if error < best_error:
            best, best_error = candidate, error
    return best


def _reference_ratio(face: ImageFont.FreeTypeFont, text: str) -> float | None:
    """The donor's own weight, measured exactly as a candidate is measured."""
    return _measure_text(face, text)


#: Size the calibration font is rasterised at. Any fixed size does, as long as
#: it is the same one every time: the measurement is a comparison, not a value.
CALIBRATION_SIZE = 96


def _built_stroke_ratio(
    face: ImageFont.FreeTypeFont,
    probe: list[str],
    ascent: int,
    shear: float,
    stretch: float,
    grow: int,
) -> float | None:
    """Weight of the probe letters after a full trip through a built font."""
    import io

    from picglot.foundry import build, outlines

    traced = {}
    for char in probe:
        mask, pen_x, advance, baseline = _transform(face, char, ascent, shear, stretch, grow)
        if mask is None:
            continue
        glyph = GlyphImage.from_canvas(
            mask, baseline=baseline, em_pixels=RENDER_SIZE, pen_x=pen_x, advance=advance
        )
        if glyph is not None:
            traced[char] = outlines.trace(glyph)
    if not traced:
        return None

    try:
        data = build.build_font(traced, family="PicGlot Calibration")
        rendered = ImageFont.truetype(io.BytesIO(data), size=CALIBRATION_SIZE)
    except Exception:  # pragma: no cover - a probe that will not build
        return None

    return _measure_text(rendered, "".join(traced))


def _measure_text(face: ImageFont.FreeTypeFont, text: str) -> float | None:
    """Stroke width over band height for ``text`` set in ``face``."""
    from picglot.vision import typeface

    sized = face if face.size == CALIBRATION_SIZE else face.font_variant(size=CALIBRATION_SIZE)
    left, top, right, bottom = (round(value) for value in sized.getbbox(text))
    if right <= left or bottom <= top:
        return None
    canvas = Image.new("L", (right - left + 40, bottom - top + 40), 255)
    ImageDraw.Draw(canvas).text((20 - left, 20 - top), text, font=sized, fill=0)
    prepared = typeface._prepare(canvas, (0, 0, canvas.width, canvas.height))
    if prepared is None:
        return None
    mask, band_height = prepared
    return None if band_height <= 0 else typeface._stroke_width(mask) / band_height


# --------------------------------------------------------------------------- #
# Rasterising the donor
# --------------------------------------------------------------------------- #
def _render(
    face: ImageFont.FreeTypeFont, char: str, ascent: int
) -> tuple[np.ndarray | None, float, float, float]:
    """Draw one character with room around it for the transforms to work in."""
    advance = float(face.getlength(char))
    margin = RENDER_SIZE
    width = int(advance) + margin * 2
    height = RENDER_SIZE * 3
    baseline = float(RENDER_SIZE + ascent)

    canvas = Image.new("L", (width, height), 0)
    ImageDraw.Draw(canvas).text((margin, RENDER_SIZE), char, font=face, fill=255)
    mask = np.asarray(canvas) > 127
    if not mask.any():
        return None, 0.0, 0.0, baseline
    return mask, float(margin), advance, baseline


def _stroke_ratio(face: ImageFont.FreeTypeFont) -> float | None:
    """How heavy the donor itself is, measured the same way as the sample."""
    from picglot.vision import typeface

    probe = "Hamburgefons"
    left, top, right, bottom = (round(value) for value in face.getbbox(probe))
    if right <= left or bottom <= top:
        return None
    canvas = Image.new("L", (right - left + 40, bottom - top + 40), 255)
    ImageDraw.Draw(canvas).text((20 - left, 20 - top), probe, font=face, fill=0)
    prepared = typeface._prepare(canvas, (0, 0, canvas.width, canvas.height))
    if prepared is None:
        return None
    mask, band_height = prepared
    if band_height <= 0:
        return None
    return typeface._stroke_width(mask) / band_height


# --------------------------------------------------------------------------- #
# The transforms
# --------------------------------------------------------------------------- #
def _reweight(mask: np.ndarray, pixels: int) -> np.ndarray:
    radius = abs(pixels)
    if radius < 1:
        return mask
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
    operation = cv2.dilate if pixels > 0 else cv2.erode
    return operation(mask.astype(np.uint8), kernel).astype(bool)


def _shear(target_degrees: float, face: ImageFont.FreeTypeFont) -> float:
    """The extra lean to add, over whatever lean the donor already has.

    Negated on the way out: the measurement calls a right lean negative, and
    the shear that produces a right lean is positive.
    """
    donor = _donor_slant(face)
    delta = max(-MAX_SLANT_DEGREES, min(MAX_SLANT_DEGREES, target_degrees - donor))
    return 0.0 if abs(delta) < 1.0 else math.tan(math.radians(-delta))


def _donor_slant(face: ImageFont.FreeTypeFont) -> float:
    from picglot.vision import typeface

    probe = "Hamburgefons"
    left, top, right, bottom = (round(value) for value in face.getbbox(probe))
    if right <= left or bottom <= top:
        return 0.0
    canvas = Image.new("L", (right - left + 40, bottom - top + 40), 255)
    ImageDraw.Draw(canvas).text((20 - left, 20 - top), probe, font=face, fill=0)
    prepared = typeface._prepare(canvas, (0, 0, canvas.width, canvas.height))
    return 0.0 if prepared is None else typeface._slant_degrees(prepared[0])


def _shear_mask(
    mask: np.ndarray, shear: float, baseline: float, pen_x: float, advance: float
) -> tuple[np.ndarray, float, float]:
    """Lean the letter, pivoting on the baseline so it stays standing on it."""
    height, width = mask.shape
    matrix = np.array([[1.0, -shear, shear * baseline], [0.0, 1.0, 0.0]], dtype=np.float32)
    warped = cv2.warpAffine(
        mask.astype(np.uint8), matrix, (width, height), flags=cv2.INTER_NEAREST, borderValue=0
    ).astype(bool)
    # The pen does not move: a slanted letter starts where the upright one did
    # and the extra width it takes is side bearing, which is how italics work.
    return warped, pen_x, advance


def _stretch(
    mask: np.ndarray, ratio: float, pen_x: float, advance: float
) -> tuple[np.ndarray, float, float]:
    """Condense or extend, keeping the height and the baseline where they are."""
    height, width = mask.shape
    target = max(1, round(width * ratio))
    resized = cv2.resize(
        mask.astype(np.uint8), (target, height), interpolation=cv2.INTER_NEAREST
    ).astype(bool)
    return resized, pen_x * ratio, advance * ratio
