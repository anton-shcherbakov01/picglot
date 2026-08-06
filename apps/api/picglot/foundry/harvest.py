"""Cutting individual letters out of a line of photographed text.

The picture holds the letters and the OCR holds the string; what neither holds
is which pixels are which letter. This joins them: the ink is split into marks,
the marks are put in reading order, and if there are exactly as many marks as
there are letters, the pairing is unambiguous.

When it is not unambiguous the line is dropped, whole. A line where the marks
outnumber the letters is a dotted i or a broken stroke; one where the letters
outnumber the marks is two letters touching. Both are guessable and neither is
worth guessing — a font built from a mispaired sample is a font where some
letter is quietly drawn as a different one, which is far worse than a font with
that letter missing, because a missing letter is visibly missing.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

from picglot.foundry.outlines import GlyphImage

#: Marks smaller than this fraction of the line's ink area are specks.
MIN_MARK_AREA_RATIO = 0.004

#: Marks this close together are one letter: the dot of an i sits directly over
#: its stem and a photographed stroke can break in two. Kept deliberately tight
#: — as a fraction of the ink height — because over-merging turns two letters
#: into one, and the count check then throws the line away.
MERGE_GAP_RATIO = 0.02

#: Lines shorter than this have too few letters to be worth the risk.
MIN_LETTERS = 2


@dataclass(slots=True)
class HarvestedLine:
    """The letters recovered from one line, and what they were cut from."""

    glyphs: dict[str, GlyphImage]
    baseline: float
    em_pixels: float


def harvest_line(
    image: Image.Image,
    box: tuple[int, int, int, int],
    text: str,
) -> HarvestedLine | None:
    """Pair the marks inside ``box`` with the letters of ``text``.

    Returns None when the pairing is not certain.
    """
    letters = [char for char in text if not char.isspace()]
    if len(letters) < MIN_LETTERS:
        return None

    mask = _ink(image, box)
    if mask is None:
        return None

    marks = _marks(mask)
    if len(marks) != len(letters):
        return None

    baseline, em_pixels = _metrics(mask)
    if em_pixels <= 0:
        return None

    advances = _advances(marks, _followed_by_space(text))
    glyphs: dict[str, GlyphImage] = {}
    for letter, (left, right), advance in zip(letters, marks, advances, strict=True):
        if letter in glyphs:
            continue  # the first sample of a letter is as good as the second
        column = np.zeros_like(mask)
        column[:, left:right] = mask[:, left:right]
        glyph = GlyphImage.from_canvas(
            column,
            baseline=baseline,
            em_pixels=em_pixels,
            pen_x=float(left),
            advance=advance,
        )
        if glyph is not None:
            glyphs[letter] = glyph
    return HarvestedLine(glyphs, baseline, em_pixels) if glyphs else None


def _followed_by_space(text: str) -> list[bool]:
    """For each non-space character, whether a space comes next."""
    flags: list[bool] = []
    for index, char in enumerate(text):
        if char.isspace():
            continue
        rest = text[index + 1 :]
        flags.append(bool(rest) and rest[0].isspace())
    return flags


def _advances(marks: list[tuple[int, int]], before_space: list[bool]) -> list[float]:
    """How far the pen moves after each letter.

    Taken from where the *next* letter starts, not from how wide this one is:
    the blank between two letters belongs to the pair and is what sets the
    rhythm of a line. A letter followed by a word space would otherwise be
    handed the width of the space as well, so those take the median instead.
    """
    starts = [left for left, _right in marks]
    widths = [right - left for left, right in marks]
    raw = [
        float(starts[index + 1] - starts[index]) if index + 1 < len(marks) else float("nan")
        for index in range(len(marks))
    ]

    clean = [
        value
        for value, skipped in zip(raw, before_space, strict=True)
        if value == value and not skipped
    ]
    typical_gap = (
        float(np.median([value - width for value, width in zip(clean, widths, strict=False)]))
        if clean
        else 0.0
    )
    return [
        float(width + typical_gap) if (value != value or skipped) else value
        for value, width, skipped in zip(raw, widths, before_space, strict=True)
    ]


def _ink(image: Image.Image, box: tuple[int, int, int, int]) -> np.ndarray | None:
    """Binarise the line so ink is True, using the page's own polarity."""
    left, top, right, bottom = (round(value) for value in box)
    left, top = max(0, left), max(0, top)
    right, bottom = min(image.width, right), min(image.height, bottom)
    if right - left < 8 or bottom - top < 8:
        return None

    gray = np.asarray(image.crop((left, top, right, bottom)).convert("L"))
    if gray.size == 0:
        return None
    _threshold, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = binary > 0
    # Ink is the minority of a line of text; whichever class is smaller is it.
    if np.count_nonzero(mask) * 2 > mask.size:
        mask = ~mask
    return mask if mask.any() else None


def _marks(mask: np.ndarray) -> list[tuple[int, int]]:
    """Columns each mark spans, left to right, with strays merged into them."""
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8
    )
    if count <= 1:
        return []

    total_ink = float(np.count_nonzero(mask))
    spans: list[tuple[int, int]] = []
    for index in range(1, count):
        area = float(stats[index, cv2.CC_STAT_AREA])
        if area < total_ink * MIN_MARK_AREA_RATIO:
            continue
        left = int(stats[index, cv2.CC_STAT_LEFT])
        spans.append((left, left + int(stats[index, cv2.CC_STAT_WIDTH])))
    if not spans:
        return []

    spans.sort()
    # A letter can be more than one mark — an i is two, and a photographed
    # stroke can break in half. Overlapping or all-but-touching spans belong to
    # the same letter, and the gap that separates real letters is the yardstick.
    ink_rows = np.flatnonzero(mask.any(axis=1))
    ink_height = float(ink_rows[-1] - ink_rows[0] + 1) if ink_rows.size else float(mask.shape[0])
    tolerance = max(1.0, ink_height * MERGE_GAP_RATIO)
    merged: list[list[int]] = [list(spans[0])]
    for left, right in spans[1:]:
        if left - merged[-1][1] <= tolerance:
            merged[-1][1] = max(merged[-1][1], right)
        else:
            merged.append([left, right])
    return [(left, right) for left, right in merged]


def _metrics(mask: np.ndarray) -> tuple[float, float]:
    """Where the baseline sits in the line, and how many pixels an em is.

    The baseline is the row most letters stand on, not the bottom of the ink:
    descenders hang below it and would drag it down. Rows are counted from the
    bottom until the ink thins out, which is where the descenders stop and the
    body of the line begins.
    """
    rows = np.count_nonzero(mask, axis=1)
    if not rows.any():
        return 0.0, 0.0
    occupied = np.flatnonzero(rows > 0)
    top, bottom = int(occupied[0]), int(occupied[-1])

    body = rows.max() * 0.25
    baseline = bottom
    for row in range(bottom, top, -1):
        if rows[row] >= body:
            baseline = row
            break

    # Cap height is roughly seven tenths of an em in most faces and hands, so
    # the band from the top of the ink to the baseline gives the em.
    cap_height = max(1.0, float(baseline - top))
    return float(baseline) + 1.0, cap_height / 0.7
