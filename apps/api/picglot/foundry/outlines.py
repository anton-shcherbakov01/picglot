"""Turning a picture of a letter into a letter.

A glyph arrives here as pixels — cut out of a photograph of someone's
handwriting, or rasterised from a face being adapted — and leaves as the
quadratic contours a TrueType glyph is made of.

The pixels are traced, not approximated by a shape library: what is on the
paper is the point. What the tracing does add is curvature. A contour lifted
straight off a bitmap is a staircase, and a staircase renders as a staircase at
any size the antialiasing does not hide; every vertex the outline turns gently
at becomes an off-curve control point instead, which is the difference between
a letter and a scan of a letter.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

#: Design units per em in everything this package builds.
UNITS_PER_EM = 1000

#: Turn, in degrees, above which a vertex stays a corner. Below it the outline
#: is passing through, and the vertex becomes a control point. Stems and
#: serifs turn by right angles; the shoulder of an "n" turns by a few degrees
#: per vertex, which is exactly what wants rounding.
CORNER_DEGREES = 42.0

#: Contour simplification, as a fraction of the glyph's height. Too small and
#: every pixel of a photographed edge becomes a point; too large and the
#: letterform is sanded down.
SIMPLIFY_RATIO = 0.012

#: Contours with fewer pixels than this are dirt on the page.
MIN_CONTOUR_AREA = 8.0

Point = tuple[float, float]


@dataclass(slots=True)
class GlyphImage:
    """One letter as pixels, with what is needed to place it on a baseline."""

    #: Ink is True. Rows run top to bottom, as an image does.
    mask: np.ndarray
    #: Row of the baseline within ``mask``. May sit below it: descenders hang.
    baseline: float
    #: Pixels to one em — the size the letter was written or rendered at.
    em_pixels: float
    #: Blank to the left of the ink, and to the right of it, in pixels.
    left_bearing: float = 0.0
    right_bearing: float = 0.0

    @property
    def advance(self) -> float:
        return self.left_bearing + self.mask.shape[1] + self.right_bearing

    @classmethod
    def from_canvas(
        cls,
        mask: np.ndarray,
        *,
        baseline: float,
        em_pixels: float,
        pen_x: float = 0.0,
        advance: float | None = None,
    ) -> GlyphImage | None:
        """Cut one letter out of a canvas it was drawn on.

        ``pen_x`` is where the pen was put down and ``advance`` how far it
        moved; the blank between those and the ink is the letter's side
        bearings, which is what keeps a word from being drawn as a row of
        letters shoved together. Returns None when the canvas holds no ink.
        """
        cropped, left, top = crop_to_ink(mask)
        if not cropped.any():
            return None
        width = advance if advance is not None else float(mask.shape[1])
        return cls(
            mask=cropped,
            baseline=baseline - top,
            em_pixels=em_pixels,
            left_bearing=left - pen_x,
            right_bearing=pen_x + width - (left + cropped.shape[1]),
        )


@dataclass(slots=True)
class Contour:
    """One closed loop of a glyph outline, in font units with y pointing up."""

    points: list[Point] = field(default_factory=list)
    #: Per point: True draws through it, False bends the curve around it.
    on_curve: list[bool] = field(default_factory=list)


@dataclass(slots=True)
class GlyphOutline:
    contours: list[Contour]
    advance: int
    left_side_bearing: int

    @property
    def is_blank(self) -> bool:
        return not self.contours


def trace(glyph: GlyphImage, *, units_per_em: int = UNITS_PER_EM) -> GlyphOutline:
    """Trace one glyph image into outlines placed on the baseline."""
    scale = units_per_em / max(1.0, glyph.em_pixels)
    advance = round(glyph.advance * scale)
    bearing = round(glyph.left_bearing * scale)

    if not glyph.mask.any():
        return GlyphOutline([], advance, bearing)

    found, _hierarchy = cv2.findContours(
        glyph.mask.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE
    )
    height = float(glyph.mask.shape[0])
    epsilon = max(0.6, height * SIMPLIFY_RATIO)

    contours: list[Contour] = []
    for raw in found:
        if cv2.contourArea(raw) < MIN_CONTOUR_AREA:
            continue
        simplified = cv2.approxPolyDP(raw, epsilon, closed=True).reshape(-1, 2)
        if len(simplified) < 3:
            continue
        # Image rows run downward and font units run upward, so flipping y here
        # also flips the winding: OpenCV hands back outer contours anticlockwise
        # and holes clockwise, which is the wrong way round for TrueType until
        # the flip, and the right way round after it.
        points = [
            (
                (float(x) + glyph.left_bearing) * scale,
                (glyph.baseline - float(y)) * scale,
            )
            for x, y in simplified
        ]
        contours.append(_round_corners(points))

    return GlyphOutline(contours, advance, bearing)


def _round_corners(points: list[Point]) -> Contour:
    """Decide which vertices the outline turns at and which it curves through."""
    count = len(points)
    on_curve = [True] * count
    for index in range(count):
        before = points[index - 1]
        here = points[index]
        after = points[(index + 1) % count]
        if _turn_degrees(before, here, after) < CORNER_DEGREES:
            on_curve[index] = False

    # A contour has to start somewhere the pen can be put down, and two corners
    # in a row are needed for the shape to have any corners at all. An outline
    # that came out entirely smooth — a photographed "o" — keeps its sharpest
    # vertex as the one point that is drawn through.
    if not any(on_curve):
        sharpest = max(
            range(count),
            key=lambda index: _turn_degrees(
                points[index - 1], points[index], points[(index + 1) % count]
            ),
        )
        on_curve[sharpest] = True

    start = on_curve.index(True)
    rotated = points[start:] + points[:start]
    flags = on_curve[start:] + on_curve[:start]
    return Contour(rotated, flags)


def _turn_degrees(before: Point, here: Point, after: Point) -> float:
    """How sharply the outline turns at ``here``, 0° being straight on."""
    incoming = (here[0] - before[0], here[1] - before[1])
    outgoing = (after[0] - here[0], after[1] - here[1])
    first = math.hypot(*incoming)
    second = math.hypot(*outgoing)
    if first == 0 or second == 0:
        return 180.0
    cosine = (incoming[0] * outgoing[0] + incoming[1] * outgoing[1]) / (first * second)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def ink_bounds(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) of the ink, or None when there is none."""
    columns = np.flatnonzero(mask.any(axis=0))
    rows = np.flatnonzero(mask.any(axis=1))
    if columns.size == 0 or rows.size == 0:
        return None
    return int(columns[0]), int(rows[0]), int(columns[-1]) + 1, int(rows[-1]) + 1


def crop_to_ink(mask: np.ndarray) -> tuple[np.ndarray, int, int]:
    """Trim the blank margins, reporting how much came off the left and top."""
    bounds = ink_bounds(mask)
    if bounds is None:
        return mask, 0, 0
    left, top, right, bottom = bounds
    return mask[top:bottom, left:right], left, top
