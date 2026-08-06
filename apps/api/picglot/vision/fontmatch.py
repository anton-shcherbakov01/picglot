"""Naming the typeface on the page, and finding its counterpart for the output.

``typeface.py`` measures three properties and picks a class. That is enough to
avoid the worst mismatches and not enough to keep a promise about layout: a
condensed grotesque and a wide one are both "sans", and swapping one for the
other reflows every line.

So this compares the lettering against the fonts actually installed. Each
candidate is rendered *with the page's own text* and reduced to the same ten
measurements taken from the original crop; the nearest one is the
identification. Rendering candidates with a stand-in pangram instead was the
cheaper design and does not work — it measures the difference between two
strings as much as between two fonts, and the correct font stops winning its
own comparison.

The ten numbers are divided by their spread across the installed collection
before being compared. Unnormalised, the feature with the largest units wins:
baseline jitter varies by 0.01 from one end of the collection to the other and
slant by 0.20, so the distance could not see the difference between a hand and
a press.

Identification alone does not finish the job. The face on the page usually
cannot draw the language being translated into — a Latin-only display face and
a Russian translation — so the second half of this module finds the closest
font that *can*, by the same measurements. That is the "analogue" the layout
depends on: same proportions, same weight, same colour on the page.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from picglot.core.logging import get_logger
from picglot.domain.languages import Script
from picglot.vision import typeface
from picglot.vision.fonts import FontFile, registry

log = get_logger(__name__)

#: Size the probe strings are rendered at. Large enough that stroke widths land
#: on several pixels, small enough that fifty candidates stay under 100 ms.
PROBE_SIZE = 48

#: Beyond this distance the nearest candidate is not a match, just the least
#: bad of a bad set — the page is probably set in a face nobody installed.
#: Expressed in normalised feature units, so it is roughly "how many standard
#: deviations of the installed collection away".
MAX_IDENTIFY_DISTANCE = 1.6

#: Longest run of the original text used for comparison. Enough letters for the
#: proportions to settle, short enough that the candidate renders stay cheap.
MAX_MATCH_CHARS = 40

#: Probe strings per (script, case). Matching the case matters: capitals have
#: no x-height and no descenders, so the proportions of the same font measured
#: on capitals and on mixed case are genuinely different numbers.
PROBES: dict[tuple[str, str], str] = {
    ("latin", "upper"): "HAMBURGEFONS QUICK JUDGE",
    ("latin", "lower"): "hamburgefons quick judge",
    ("latin", "mixed"): "Hamburgefons Quick Judge",
    ("cyrillic", "upper"): "ШЕРЛОК ХОЛМС ВЫЙДЯ ЗАЩИТУ",
    ("cyrillic", "lower"): "шерлок холмс выйдя защиту",
    ("cyrillic", "mixed"): "Шерлок Холмс Выйдя Защиту",
}

#: Feature weights. Proportion and colour carry the layout, so they lead;
#: baseline jitter is nearly binary between hand and print and would otherwise
#: swamp everything else.
WEIGHTS = np.array(
    [
        1.4,  # stroke_ratio      — weight
        1.0,  # stroke_spread     — serif contrast
        0.8,  # baseline_jitter   — hand vs print
        0.9,  # slant
        1.5,  # aspect            — condensed vs wide, reflows lines
        1.0,  # ink_density       — how black the block looks
        0.7,  # roundness
        1.2,  # x_ratio           — large or small on the body
        0.6,  # gap_ratio
        1.3,  # width_spread      — separates monospace from everything else
    ],
    dtype=np.float64,
)
FEATURES = 10


@dataclass(slots=True)
class FontIdentity:
    """The installed face closest to the lettering on the page."""

    file: FontFile
    distance: float
    confident: bool

    @property
    def family(self) -> str:
        return self.file.family

    def as_dict(self) -> dict[str, object]:
        return {
            "family": self.family,
            "distance": round(self.distance, 4),
            "confident": self.confident,
        }


# --------------------------------------------------------------------------- #
# Feature extraction
# --------------------------------------------------------------------------- #
def _features(mask: np.ndarray, band_height: float) -> np.ndarray:
    """Ten scale-free numbers describing how the lettering is drawn."""
    out = np.zeros(FEATURES, dtype=np.float64)
    if band_height <= 0 or not mask.any():
        return out

    stroke = typeface._stroke_width(mask)
    slant = typeface._slant_degrees(mask)
    upright = typeface._deshear(mask, slant) if abs(slant) >= typeface.ITALIC_DEGREES else mask
    jitter, spread = typeface._shape_spreads(upright, stroke)

    out[0] = stroke / band_height
    out[1] = spread
    out[2] = jitter
    out[3] = abs(slant) / typeface.MAX_PLAUSIBLE_SLANT

    count, labels, stats, _ = cv2.connectedComponentsWithStats(upright, connectivity=8)
    widths: np.ndarray = np.zeros(0)
    if count > 2:
        areas = stats[1:, cv2.CC_STAT_AREA]
        heights = stats[1:, cv2.CC_STAT_HEIGHT].astype(np.float64)
        widths = stats[1:, cv2.CC_STAT_WIDTH].astype(np.float64)
        lefts = stats[1:, cv2.CC_STAT_LEFT].astype(np.float64)
        keep = (areas >= max(6.0, stroke * stroke)) & (heights <= upright.shape[0] * 0.98)
        heights, widths, lefts = heights[keep], widths[keep], lefts[keep]
        areas_kept = areas[keep].astype(np.float64)
        if heights.size >= 3:
            out[4] = float(np.median(widths)) / band_height
            out[7] = float(np.median(heights)) / band_height
            # Gaps between neighbouring marks — tracking, near enough.
            order = np.argsort(lefts)
            rights = (lefts + widths)[order]
            starts = lefts[order]
            gaps = starts[1:] - rights[:-1]
            gaps = gaps[gaps >= 0]
            if gaps.size:
                out[8] = float(np.median(gaps)) / band_height
            # Every glyph the same width is what makes a monospace a monospace,
            # and nothing else in this vector notices it: FreeMono has serifs
            # and the contrast to match, so without this it reads as a text
            # face and gets offered as the analogue for one.
            out[9] = float(np.std(widths)) / max(1.0, float(np.mean(widths)))
            # Roundness of the marks: 4πA/P². A circle is 1, a stick is near 0.
            scores = []
            for index in np.flatnonzero(keep)[:40] + 1:
                contours, _ = cv2.findContours(
                    (labels == index).astype(np.uint8),
                    cv2.RETR_EXTERNAL,
                    cv2.CHAIN_APPROX_NONE,
                )
                if not contours:
                    continue
                perimeter = cv2.arcLength(contours[0], True)
                if perimeter > 0:
                    scores.append(4 * np.pi * cv2.contourArea(contours[0]) / (perimeter**2))
            if scores:
                out[6] = float(np.median(scores))
            if areas_kept.size:
                # Ink over the area the marks occupy, not over the whole crop,
                # so trailing whitespace cannot lighten a block.
                spanned = float((widths * heights).sum())
                if spanned > 0:
                    out[5] = float(areas_kept.sum()) / spanned
    return out


def _fingerprint_image(image: Image.Image, box: tuple[int, int, int, int]) -> np.ndarray | None:
    prepared = typeface._prepare(image, box)
    if prepared is None:
        return None
    mask, band_height = prepared
    if np.count_nonzero(mask) < typeface.MIN_INK_PIXELS:
        return None
    return _features(mask, band_height)


@functools.lru_cache(maxsize=4096)
def _fingerprint_font(path: str, index: int, probe: str) -> tuple[float, ...] | None:
    """Render the probe with this font and measure it the same way."""
    try:
        font = ImageFont.truetype(path, size=PROBE_SIZE, index=index)
        left, top, right, bottom = font.getbbox(probe)
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            return None
        margin = PROBE_SIZE // 3
        canvas = Image.new("L", (width + margin * 2, height + margin * 2), 255)
        ImageDraw.Draw(canvas).text((margin - left, margin - top), probe, font=font, fill=0)
    except Exception:  # pragma: no cover - unusable font file
        return None
    fingerprint = _fingerprint_image(canvas, (0, 0, canvas.width, canvas.height))
    return None if fingerprint is None else tuple(fingerprint)


def _probe_for(text: str, script: Script) -> str:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        case = "mixed"
    else:
        upper = sum(1 for char in letters if char.isupper())
        ratio = upper / len(letters)
        case = "upper" if ratio > 0.85 else "lower" if ratio < 0.15 else "mixed"
    family = "cyrillic" if script is Script.CYRILLIC else "latin"
    return PROBES[(family, case)]


@functools.lru_cache(maxsize=64)
def _spread(probe: str) -> tuple[float, ...]:
    """Per-feature standard deviation across every font that can draw ``probe``.

    Without this the comparison is dominated by whichever feature happens to
    have the largest units. Baseline jitter varies by 0.01 across the whole
    collection and slant by 0.20, so an unnormalised distance cannot see the
    difference between a hand and a press at all. Dividing by the spread of the
    installed fonts puts every feature on the same footing.
    """
    required = {ord(char) for char in probe if not char.isspace()}
    rows = [
        fingerprint
        for file in registry.files
        if not (file.coverage and not required.issubset(file.coverage))
        and (fingerprint := _fingerprint_font(str(file.path), file.index, probe)) is not None
    ]
    if len(rows) < 2:
        return tuple([1.0] * FEATURES)
    spread = np.asarray(rows, dtype=np.float64).std(axis=0)
    spread[spread < 1e-6] = 1.0
    return tuple(spread)


def _distance(left: np.ndarray, right: np.ndarray, spread: tuple[float, ...]) -> float:
    delta = (left - right) / np.asarray(spread) * WEIGHTS
    return float(np.sqrt(np.square(delta).sum()))


# --------------------------------------------------------------------------- #
# Identification
# --------------------------------------------------------------------------- #
def identify(
    image: Image.Image,
    box: tuple[int, int, int, int],
    *,
    text: str = "",
    script: Script = Script.LATIN,
) -> FontIdentity | None:
    """Name the installed face closest to the lettering inside ``box``."""
    target = _fingerprint_image(image, box)
    if target is None:
        return None

    # Candidates are rendered with the page's own text rather than a stand-in.
    # Comparing a crop of "YOU ARE AMAZING" against candidates rendered with a
    # pangram measures the difference between two strings as much as between
    # two fonts, and the correct font stops winning its own comparison.
    sample = (text.strip() or _probe_for(text, script))[:MAX_MATCH_CHARS]
    required = {ord(char) for char in sample if not char.isspace()}
    spread = _spread(sample)

    best: tuple[float, FontFile] | None = None
    for file in registry.files:
        if file.coverage and not required.issubset(file.coverage):
            continue
        fingerprint = _fingerprint_font(str(file.path), file.index, sample)
        if fingerprint is None:
            continue
        distance = _distance(target, np.asarray(fingerprint), spread)
        if best is None or distance < best[0]:
            best = (distance, file)

    if best is None:
        return None
    distance, file = best
    return FontIdentity(file=file, distance=distance, confident=distance <= MAX_IDENTIFY_DISTANCE)


def counterparts(
    identity: FontIdentity,
    *,
    script: Script,
    text: str = "",
    limit: int = 4,
) -> list[str]:
    """Families that look like ``identity`` and can draw ``script``.

    The identified face is first when it covers the target script itself. When
    it does not — a Latin-only display face and a Russian translation, which is
    the common case — the list is whatever measures closest to it among the
    fonts that can, so the substitution keeps the proportions the layout was
    measured against.
    """
    # Here the comparison is font against font, so a shared probe string is the
    # right basis — and it keeps this cached across a whole document.
    probe = _probe_for(text, script)
    required = {ord(char) for char in probe if not char.isspace()}

    # When the identified face can draw the target script itself, it *is* the
    # answer — nothing measured can be closer to it than itself, and offering
    # runners-up only gives the selector a chance to prefer one of them for a
    # style it happens to have a cut of. Searching for a substitute is for the
    # case that actually needs one.
    identified = identity.file
    if not identified.coverage or required.issubset(identified.coverage):
        return [identified.family]

    spread = _spread(probe)

    reference = _fingerprint_font(str(identity.file.path), identity.file.index, probe)
    if reference is None:
        # The identified face cannot render the target script at all, so it has
        # no fingerprint there to compare against. Fall back to comparing on
        # the script it *can* draw, which is still its own proportions.
        reference = _fingerprint_font(
            str(identity.file.path), identity.file.index, PROBES[("latin", "mixed")]
        )
    if reference is None:
        return []

    scored: list[tuple[float, str]] = []
    seen: set[str] = set()
    for file in registry.files:
        if file.coverage and not required.issubset(file.coverage):
            continue
        if file.normalized_family in seen:
            continue
        fingerprint = _fingerprint_font(str(file.path), file.index, probe)
        if fingerprint is None:
            continue
        seen.add(file.normalized_family)
        scored.append(
            (_distance(np.asarray(reference), np.asarray(fingerprint), spread), file.family)
        )

    scored.sort(key=lambda item: item[0])
    return [family for _, family in scored[:limit]]
