"""Reading the shape of the original lettering off the pixels.

The product's promise is that translated text comes back looking like the text
it replaced. Nothing here identifies a typeface by name — that needs a font
database and a classifier, and guessing wrong is worse than not guessing. What
it does establish is the handful of properties a reader actually notices when
they are wrong:

  * **weight** — stroke thickness against letter height;
  * **slant** — whether the letters lean;
  * **hand vs print** — whether the strokes were drawn or set.

Each is measured, each has a confidence, and a measurement that is not
confident changes nothing: the caller keeps the default it already had. A
paragraph redrawn in the wrong weight is a worse outcome than one redrawn in
the default, because the first looks like a bug and the second looks like a
choice.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

from picglot.core.logging import get_logger
from picglot.domain.enums import FontClass

log = get_logger(__name__)

#: Below this the crop has too few ink pixels for any statistic to mean much.
MIN_INK_PIXELS = 120
#: Crops shorter than this lose the stroke/height ratio to rounding.
MIN_BAND_HEIGHT = 10

#: Stroke width over letter height. Across DejaVu, Liberation and FreeFont, in
#: capitals and in mixed case, regular weights top out at 0.153 and bold starts
#: at 0.208. The gap between the two thresholds is deliberately left undecided
#: rather than split down the middle: a weight this close to the line is not
#: worth overriding a default for.
REGULAR_STROKE_RATIO = 0.17
BOLD_STROKE_RATIO = 0.19

#: Degrees of lean before it reads as italic rather than as a crooked scan.
ITALIC_DEGREES = 8.0
#: Past this the crop is skewed, not italic — page deskew should have caught it.
MAX_PLAUSIBLE_SLANT = 32.0

#: How far the feet of the letters wander off the baseline, over letter height.
#: Set type puts every letter on the same line to the pixel; a hand cannot.
#: Descenders are excluded first, which is what makes this hold regardless of
#: case or script: across DejaVu, Liberation and FreeFont it reads 0.009–0.012
#: for printed Latin and Cyrillic, upper or mixed case, and 0.034–0.056 for
#: Caveat. Letter-height spread was the obvious alternative and is not usable —
#: it collapses on text set in capitals, which is most of what gets translated
#: on a poster or a greeting card.
HANDWRITING_BASELINE_JITTER = 0.020

#: Spread of stroke widths — stroke contrast. A serif is thin where its stem is
#: thick, which is exactly what this measures; a grotesque holds one width
#: throughout. Across DejaVu, Liberation and FreeFont, regular *and* bold, sans
#: tops out at 0.19 and serif starts at 0.23. Bold grotesques sit at the high
#: end of the sans range, which is why the cut is here and not lower.
SERIF_STROKE_SPREAD = 0.21


@dataclass(slots=True)
class TypefaceEstimate:
    """What the pixels support, and how strongly."""

    font_class: FontClass | None = None
    bold: bool | None = None
    italic: bool | None = None
    stroke_ratio: float = 0.0
    slant_degrees: float = 0.0
    baseline_jitter: float = 0.0
    stroke_spread: float = 0.0
    confidence: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "font_class": str(self.font_class) if self.font_class else None,
            "bold": self.bold,
            "italic": self.italic,
            "stroke_ratio": round(self.stroke_ratio, 4),
            "slant_degrees": round(self.slant_degrees, 2),
            "baseline_jitter": round(self.baseline_jitter, 4),
            "stroke_spread": round(self.stroke_spread, 3),
            "confidence": round(self.confidence, 3),
        }


def estimate(image: Image.Image, box: tuple[int, int, int, int]) -> TypefaceEstimate:
    """Measure the lettering inside ``box``. Never raises; returns empty on doubt."""
    try:
        crop = _prepare(image, box)
    except Exception as exc:  # pragma: no cover - malformed crop
        log.debug("typeface.crop_failed", error=type(exc).__name__)
        return TypefaceEstimate()
    if crop is None:
        return TypefaceEstimate()

    mask, band_height = crop
    ink = int(np.count_nonzero(mask))
    if ink < MIN_INK_PIXELS or band_height < MIN_BAND_HEIGHT:
        return TypefaceEstimate()

    stroke = _stroke_width(mask)
    ratio = stroke / band_height if band_height else 0.0
    slant = _slant_degrees(mask)
    # Shape is measured upright. A leaning stem is rasterised as a staircase,
    # and the steps read as varying stroke width — enough to push a bold italic
    # grotesque past the serif threshold on contrast it does not have.
    upright = _deshear(mask, slant) if abs(slant) >= ITALIC_DEGREES else mask
    baseline_jitter, stroke_spread = _shape_spreads(upright, stroke)

    result = TypefaceEstimate(
        stroke_ratio=ratio,
        slant_degrees=slant,
        baseline_jitter=baseline_jitter,
        stroke_spread=stroke_spread,
    )

    # Weight. The undecided band between the two thresholds stays undecided.
    if ratio >= BOLD_STROKE_RATIO:
        result.bold = True
    elif 0 < ratio <= REGULAR_STROKE_RATIO:
        result.bold = False

    # Class. Feet that will not sit still on the baseline mean a hand drew it,
    # whatever the strokes look like; failing that, stroke contrast separates a
    # serif from a grotesque.
    if baseline_jitter >= HANDWRITING_BASELINE_JITTER:
        result.font_class = FontClass.HANDWRITING
        margin = (baseline_jitter - HANDWRITING_BASELINE_JITTER) * 6
    elif stroke_spread >= SERIF_STROKE_SPREAD:
        result.font_class = FontClass.SERIF
        margin = stroke_spread - SERIF_STROKE_SPREAD
    else:
        result.font_class = FontClass.SANS
        margin = min(
            (HANDWRITING_BASELINE_JITTER - baseline_jitter) * 6,
            SERIF_STROKE_SPREAD - stroke_spread,
        )

    # Slant, but only for set type. Script faces lean by construction — Caveat
    # measures 18° — so reporting that as italic would send the search after a
    # cursive cut of a face that is already cursive, and score the upright cut
    # of it worse than an unrelated family.
    if result.font_class is not FontClass.HANDWRITING and abs(slant) <= MAX_PLAUSIBLE_SLANT:
        result.italic = abs(slant) >= ITALIC_DEGREES

    # Confidence tracks how much ink there was to measure and how far the
    # deciding metric sits from its threshold — a value on the cutoff is a coin
    # toss and must not move anything.
    volume = min(1.0, ink / (MIN_INK_PIXELS * 8))
    decisiveness = min(1.0, max(0.0, margin) * 16)
    result.confidence = round(0.5 * volume + 0.5 * decisiveness, 3)
    return result


# --------------------------------------------------------------------------- #
# Measurements
# --------------------------------------------------------------------------- #
def _prepare(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[np.ndarray, float] | None:
    """Crop, binarise so ink is white, and report the height of the letter band.

    The crop is taken with a margin around the box it was asked for. How
    tightly a box is drawn around its text is not something a caller controls —
    an engine that returns pixel-tight boxes gives capitals that touch every
    edge, and a letter as tall as its crop is indistinguishable from a rule or
    a border to everything measured downstream. Adding the margin here means no
    measurement depends on the habits of whatever produced the box. The band
    height reported back is measured from the ink, so the padding cannot
    deflate a single ratio taken against it.
    """
    left, top, right, bottom = (round(value) for value in box)
    margin = max(2, round((bottom - top) * 0.12))
    left, top = max(0, left - margin), max(0, top - margin)
    right, bottom = min(image.width, right + margin), min(image.height, bottom + margin)
    if right - left < 8 or bottom - top < 8:
        return None

    gray = np.asarray(image.crop((left, top, right, bottom)).convert("L"))
    if gray.size == 0:
        return None

    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    _, mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Otsu does not know which side is ink. Ink is the minority of a text crop,
    # so whichever class is smaller is the lettering.
    if np.count_nonzero(mask) * 2 > mask.size:
        mask = cv2.bitwise_not(mask)

    rows = np.count_nonzero(mask, axis=1).astype(np.float64)
    if not rows.any():
        return None
    # The letter band is where the ink actually is, not the full crop: a box
    # padded with background would otherwise deflate every ratio measured
    # against its height.
    threshold = rows.max() * 0.15
    occupied = np.flatnonzero(rows >= threshold)
    if occupied.size == 0:
        return None
    band_height = float(occupied[-1] - occupied[0] + 1)
    return mask, band_height


#: Percentile of the stroke ridge taken as *the* stroke width. The median is
#: the obvious choice and it fails on serifs set in capitals: the serifs are
#: thin, there are two of them per stem, and they drag the middle of the
#: distribution down until a bold face measures lighter than a regular one.
#: The upper third of the ridge is the stems, which is what carries the weight.
STROKE_PERCENTILE = 65


def _stroke_width(mask: np.ndarray) -> float:
    """Twice the distance-to-edge along the ridge of the strokes.

    Averaging the distance transform over all ink underestimates badly — most
    ink sits near an edge. The ridge (local maxima of the distance transform) is
    the centre line of each stroke, where the distance is the half-width.
    """
    distance = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    dilated = cv2.dilate(distance, np.ones((3, 3), np.uint8))
    ridge = distance[(distance > 0) & (distance >= dilated - 1e-6)]
    if ridge.size == 0:
        return 0.0
    return float(np.percentile(ridge, STROKE_PERCENTILE)) * 2.0


def _slant_degrees(mask: np.ndarray) -> float:
    """The lean of the vertical strokes, in degrees, positive leaning right.

    Shearing the crop back and forth and scoring the column histogram finds it:
    when the strokes stand upright their ink stacks into a few tall columns, so
    the sum of squared column heights peaks. Any other angle smears them.
    """
    height, width = mask.shape
    if height < 4 or width < 4:
        return 0.0

    best_angle, best_score = 0.0, -1.0
    for angle in np.arange(-MAX_PLAUSIBLE_SLANT, MAX_PLAUSIBLE_SLANT + 0.5, 2.0):
        shear = math.tan(math.radians(float(angle)))
        matrix = np.array([[1, shear, -shear * height / 2], [0, 1, 0]], dtype=np.float32)
        warped = cv2.warpAffine(
            mask, matrix, (int(width), int(height)), flags=cv2.INTER_NEAREST, borderValue=0
        )
        columns = np.count_nonzero(warped, axis=0).astype(np.float64)
        score = float(np.square(columns).sum())
        if score > best_score:
            best_score, best_angle = score, float(angle)
    # The shear that straightens the text is the opposite of its lean.
    return -best_angle


def _deshear(mask: np.ndarray, slant_degrees: float) -> np.ndarray:
    """Stand the letters up again, undoing the lean ``_slant_degrees`` found."""
    height, width = mask.shape
    shear = math.tan(math.radians(-slant_degrees))
    matrix = np.array([[1, shear, -shear * height / 2], [0, 1, 0]], dtype=np.float32)
    return cv2.warpAffine(
        mask, matrix, (int(width), int(height)), flags=cv2.INTER_NEAREST, borderValue=0
    )


def _shape_spreads(mask: np.ndarray, stroke: float) -> tuple[float, float]:
    """``(baseline_jitter, stroke_spread)`` — the two that separate the classes.

    Both are relative measures, so they survive a change of resolution: the
    baseline wobble is divided by letter height and the stroke spread by mean
    stroke width, and both of those scale with the crop while the ratios do not.
    """
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    baseline_jitter = 0.0
    if count > 2:
        # Ignore specks and anything spanning the crop — dirt and rules, not
        # letters.
        height = mask.shape[0]
        areas = stats[1:, cv2.CC_STAT_AREA]
        heights = stats[1:, cv2.CC_STAT_HEIGHT].astype(np.float64)
        tops = stats[1:, cv2.CC_STAT_TOP].astype(np.float64)
        keep = (areas >= max(6.0, stroke * stroke)) & (heights <= height * 0.98)
        heights, tops = heights[keep], tops[keep]
        if heights.size >= 4:
            bottoms = tops + heights
            mean_height = max(1.0, float(np.mean(heights)))
            # Only the marks that actually sit on the baseline may speak for it.
            # Descenders hang below it by design ("у р д g p y"), and apostrophes,
            # quotes and the tittles of "i й" float well above it — both would
            # otherwise read as a hand that cannot hold a line.
            offsets = np.abs(bottoms - float(np.median(bottoms)))
            seated = bottoms[offsets <= mean_height * 0.15]
            if seated.size >= 3:
                baseline_jitter = float(np.std(seated)) / mean_height

    distance = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    dilated = cv2.dilate(distance, np.ones((3, 3), np.uint8))
    ridge = distance[(distance > 0) & (distance >= dilated - 1e-6)]
    stroke_spread = float(np.std(ridge)) / max(0.5, float(np.mean(ridge))) if ridge.size else 0.0
    return baseline_jitter, stroke_spread
