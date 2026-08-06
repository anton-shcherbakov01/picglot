"""Choosing a face that looks like the one in the picture.

Style metadata from an OCR engine says "text", occasionally "handwritten", and
never which typeface. Rendering from that alone puts every job on the page in
the same neutral grotesque, and a hand-lettered poster comes back looking like
a spreadsheet.

The original glyphs are in the image, so this module compares against them
directly: the *source* string is drawn in each installed candidate face at the
same ink height as the photographed text, and whichever rendering overlaps the
photograph best wins. No thresholds for "is this a serif", no per-font tuning —
the comparison is between two pictures of the same words.

The match also reports a horizontal scale. Condensed hand lettering has no
equivalent among the handful of faces a server has installed, but its
proportions can still be reproduced by squeezing the winner to the same
width-per-height, which is what :attr:`TextStyle.width_ratio` does downstream.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageDraw

from picglot.domain.enums import FontClass
from picglot.vision import fonts
from picglot.vision.types import BoundingBox

#: Height, in pixels, both the photographed and the rendered text are scaled to
#: before they are compared. Small enough to keep the comparison cheap, large
#: enough to keep stems, bowls and serifs distinguishable.
COMPARE_HEIGHT = 40
#: Below this the crop is mostly sensor noise and any match would be a guess.
MIN_INK_HEIGHT = 9
#: Long lines cost more to render and add nothing: the shapes repeat.
MAX_SAMPLE_CHARS = 60
#: Ink coverage outside this band means the crop is not a line of text
#: (an empty box, or one swallowed by a dark background).
INK_FRACTION_RANGE = (0.02, 0.62)
#: How far the drawn text may be condensed or stretched to match the original.
WIDTH_RATIO_RANGE = (0.55, 1.8)
#: Ratios this close to 1 are measurement noise — binarising a photograph and
#: rescaling both pictures to a common height is worth a few percent either way.
WIDTH_RATIO_DEADBAND = 0.08
#: Overlap below this is a coincidence, not a resemblance.
MIN_SCORE = 0.34


@dataclass(slots=True)
class TypefaceMatch:
    """What the photographed lettering turned out to look like."""

    family: str
    font_class: FontClass
    bold: bool
    italic: bool
    width_ratio: float
    score: float


def match_text(image: Image.Image, box: BoundingBox, text: str) -> TypefaceMatch | None:
    """Identify the face one *single line* of source text is set in.

    ``None`` means the crop did not give enough to go on — the caller keeps
    whatever defaults it had rather than acting on a coin flip.
    """
    sample = _sample_text(text)
    if not sample:
        return None
    photographed = _ink_mask(image, box)
    if photographed is None:
        return None

    candidates = fonts.registry.candidates(sample)
    if not candidates:
        return None

    best: TypefaceMatch | None = None
    for file in candidates:
        drawn = _render_mask(file, sample)
        if drawn is None:
            continue
        score = _overlap(photographed, drawn)
        if best is not None and score <= best.score:
            continue
        best = TypefaceMatch(
            family=file.family,
            font_class=_class_for(file.normalized_family),
            bold=file.bold,
            italic=file.italic,
            width_ratio=_width_ratio(photographed.shape[1], drawn.shape[1]),
            score=score,
        )

    if best is None or best.score < MIN_SCORE:
        return None
    return best


def _width_ratio(photographed_width: int, drawn_width: int) -> float:
    """How much narrower or wider the original is than the face that won."""
    ratio = photographed_width / max(1, drawn_width)
    if abs(ratio - 1.0) <= WIDTH_RATIO_DEADBAND:
        return 1.0
    return min(WIDTH_RATIO_RANGE[1], max(WIDTH_RATIO_RANGE[0], ratio))


# --------------------------------------------------------------------------- #
# The photographed text
# --------------------------------------------------------------------------- #
def _sample_text(text: str) -> str:
    """The string to draw with each candidate, or "" if it cannot be used."""
    sample = " ".join(text.split())
    if not sample or len(sample) > MAX_SAMPLE_CHARS:
        return ""
    if not any(char.isalnum() for char in sample):
        return ""
    # Candidates are drawn straight through Pillow, which lays glyphs out in
    # the order given: a right-to-left string would be compared against its own
    # mirror image. Better to report no match than to pick a face from that.
    directions = [unicodedata.bidirectional(char) for char in sample]
    rtl = sum(1 for direction in directions if direction in {"R", "AL"})
    return "" if rtl > len(sample) * 0.2 else sample


def _ink_mask(image: Image.Image, box: BoundingBox) -> np.ndarray | None:
    """Binarise the box into ink/paper and crop tightly to the ink.

    Returns a boolean array scaled to :data:`COMPARE_HEIGHT`, or ``None`` when
    the crop holds no usable line of text.
    """
    left, top = int(max(0, box.x)), int(max(0, box.y))
    right = int(min(image.width, box.right))
    bottom = int(min(image.height, box.bottom))
    if right - left < 6 or bottom - top < MIN_INK_HEIGHT:
        return None

    gray = np.asarray(image.convert("L").crop((left, top, right, bottom)), dtype=np.uint8)
    if gray.size == 0:
        return None

    # Otsu splits the crop into two populations; the paper is whichever one the
    # border pixels belong to, so the ink is simply the other one. Deciding by
    # "dark is ink" instead would invert every light-on-dark sign.
    _threshold, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    border = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]])
    paper_is_bright = float(np.median(border)) >= float(np.mean(gray))
    ink = binary == 0 if paper_is_bright else binary == 255

    fraction = float(ink.mean())
    if not INK_FRACTION_RANGE[0] <= fraction <= INK_FRACTION_RANGE[1]:
        return None
    return _normalize(ink)


def _normalize(ink: np.ndarray) -> np.ndarray | None:
    """Crop to the ink and scale it to the common comparison height."""
    rows = np.flatnonzero(ink.any(axis=1))
    columns = np.flatnonzero(ink.any(axis=0))
    if rows.size == 0 or columns.size == 0:
        return None
    tight = ink[rows[0] : rows[-1] + 1, columns[0] : columns[-1] + 1]
    if tight.shape[0] < MIN_INK_HEIGHT or tight.shape[1] < 4:
        return None

    scale = COMPARE_HEIGHT / tight.shape[0]
    width = max(4, min(4000, round(tight.shape[1] * scale)))
    resized = Image.fromarray((tight * 255).astype(np.uint8)).resize(
        (width, COMPARE_HEIGHT), Image.Resampling.BILINEAR
    )
    return np.asarray(resized) > 127


# --------------------------------------------------------------------------- #
# The candidate faces
# --------------------------------------------------------------------------- #
def _render_mask(file: fonts.FontFile, text: str) -> np.ndarray | None:
    """Draw ``text`` in ``file`` and return its ink at the comparison height."""
    drawn = _draw(file, text, COMPARE_HEIGHT * 1.4)
    if drawn is None:
        return None
    # One correction pass: point size is not ink height, and how far apart the
    # two are depends on the face (its cap height, and whether the string has
    # descenders at all).
    height = drawn.shape[0]
    if height <= 0:
        return None
    corrected = _draw(file, text, COMPARE_HEIGHT * 1.4 * COMPARE_HEIGHT / height)
    return _normalize(corrected if corrected is not None else drawn)


def _draw(file: fonts.FontFile, text: str, size: float) -> np.ndarray | None:
    font = fonts.open_font_file(file, min(160.0, max(8.0, size)))
    if font is None:
        return None
    try:
        left, top, right, bottom = font.getbbox(text)
    except Exception:  # pragma: no cover - bitmap fallback face
        return None
    width, height = int(right - left) + 4, int(bottom - top) + 4
    if width <= 4 or height <= 4:
        return None

    canvas = Image.new("L", (width, height), 0)
    ImageDraw.Draw(canvas).text((2 - left, 2 - top), text, font=font, fill=255)
    ink = np.asarray(canvas) > 127
    rows = np.flatnonzero(ink.any(axis=1))
    columns = np.flatnonzero(ink.any(axis=0))
    if rows.size == 0 or columns.size == 0:
        return None
    return ink[rows[0] : rows[-1] + 1, columns[0] : columns[-1] + 1]


def _overlap(photographed: np.ndarray, drawn: np.ndarray) -> float:
    """Intersection over union once both are stretched to the same frame.

    Stretching is deliberate: the difference in width is reported separately as
    ``width_ratio`` and reproduced at render time, so what is compared here is
    what the output will actually look like.
    """
    if drawn.shape != photographed.shape:
        resized = Image.fromarray((drawn * 255).astype(np.uint8)).resize(
            (photographed.shape[1], photographed.shape[0]), Image.Resampling.BILINEAR
        )
        drawn = np.asarray(resized) > 127
    union = np.count_nonzero(photographed | drawn)
    if union == 0:
        return 0.0
    return float(np.count_nonzero(photographed & drawn) / union)


def _class_for(normalized_family: str) -> FontClass:
    if any(marker in normalized_family for marker in ("mono", "courier", "consol")):
        return FontClass.MONO
    if any(
        marker in normalized_family
        for marker in ("serif", "times", "georgia", "roman", "charter", "garamond")
    ):
        return FontClass.SERIF
    if any(
        marker in normalized_family for marker in ("comic", "script", "hand", "humor", "caveat")
    ):
        return FontClass.HANDWRITING
    return FontClass.SANS
