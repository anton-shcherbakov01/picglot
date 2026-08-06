"""Identifying the face on the page, and substituting one that fits.

``test_typeface`` covers the class. This covers the step after it: which
*installed* font the lettering is, and — since the face on the page usually
cannot draw the language being translated into — which one takes its place
without reflowing the line.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from picglot.domain.languages import Script
from picglot.vision.fontmatch import counterparts, identify

REPO_ROOT = Path(__file__).resolve().parents[3]
CAVEAT = REPO_ROOT / "assets/fonts/Caveat-Regular.ttf"
DEJAVU = Path("/usr/share/fonts/truetype/dejavu")
LIBERATION = Path("/usr/share/fonts/truetype/liberation")

SOURCE = "YOU ARE AMAZING"
TARGET = "ТЫ ПОТРЯСАЮЩАЯ"


def render(font_path: Path, text: str, size: int = 64) -> Image.Image:
    font = ImageFont.truetype(str(font_path), size=size)
    left, top, right, bottom = font.getbbox(text)
    image = Image.new("L", (right - left + 30, bottom - top + 30), 255)
    ImageDraw.Draw(image).text((15 - left, 15 - top), text, font=font, fill=0)
    return image


def identify_font(font_path: Path, text: str = SOURCE):
    image = render(font_path, text)
    return identify(image, (0, 0, image.width, image.height), text=text, script=Script.LATIN)


def need(path: Path) -> Path:
    if not path.exists():
        pytest.skip(f"{path.name} not installed")
    return path


def test_the_handwriting_face_is_identified_as_itself():
    """Caveat ships with the repo, so this one is always decidable."""
    result = identify_font(CAVEAT)
    assert result is not None
    assert "caveat" in result.family.lower()


@pytest.mark.parametrize(
    "font,expect_serif",
    [("DejaVuSans.ttf", False), ("DejaVuSerif.ttf", True)],
)
def test_identification_lands_on_the_right_kind_of_face(font, expect_serif):
    """Naming the exact family needs that family installed; the kind does not.

    A grotesque identified as a different grotesque costs nothing — they are
    metrically close, which is the whole basis of the substitution. A grotesque
    identified as an antiqua would change every line length.
    """
    result = identify_font(need(DEJAVU / font))
    assert result is not None
    assert ("serif" in result.family.lower()) is expect_serif


def test_weight_and_slant_survive_identification():
    """These are what the reader notices, so they must not be traded away."""
    bold = identify_font(need(LIBERATION / "LiberationSans-Bold.ttf"))
    assert bold is not None and bold.file.bold is True

    italic = identify_font(need(LIBERATION / "LiberationSerif-Italic.ttf"))
    assert italic is not None and italic.file.italic is True


def test_a_latin_only_face_is_replaced_by_one_that_can_draw_russian():
    """The common case: the source face has no Cyrillic and the output needs it.

    The substitute is chosen by measurement rather than by position in a
    hard-coded list, so the replacement keeps the proportions the box was
    measured against.
    """
    result = identify_font(need(DEJAVU / "DejaVuSerif.ttf"))
    assert result is not None

    alternatives = counterparts(result, script=Script.CYRILLIC, text=TARGET)
    assert alternatives, "no Cyrillic-capable analogue offered"

    from picglot.vision.fonts import registry

    for family in alternatives:
        matches = [f for f in registry.files if f.family == family]
        assert matches, f"{family} is not an installed family"
        assert any(ord(char) in f.coverage for f in matches for char in TARGET if f.coverage)


def test_the_bundled_hand_stays_the_hand_in_russian():
    """Caveat covers Cyrillic, so the analogue for it is itself."""
    result = identify_font(CAVEAT)
    assert result is not None
    alternatives = counterparts(result, script=Script.CYRILLIC, text=TARGET)
    assert alternatives and "caveat" in alternatives[0].lower()


def test_the_whole_chain_keeps_weight_slant_and_shape():
    """Source crop in, a font that can draw the translation out."""
    from picglot.vision.fonts import load_font
    from picglot.vision.typeface import estimate

    cases = [
        (need(LIBERATION / "LiberationSans-Bold.ttf"), "sans", True, False),
        (need(LIBERATION / "LiberationSerif-Italic.ttf"), "serif", False, True),
        (CAVEAT, "caveat", False, False),
    ]
    for path, expect, want_bold, want_italic in cases:
        image = render(path, SOURCE)
        box = (0, 0, image.width, image.height)
        measured = estimate(image, box)
        result = identify(image, box, text=SOURCE, script=Script.LATIN)
        alternatives = counterparts(result, script=Script.CYRILLIC, text=TARGET) if result else []
        chosen = load_font(
            size=48,
            font_class=measured.font_class,
            script=Script.CYRILLIC,
            bold=bool(measured.bold),
            italic=bool(measured.italic),
            text=TARGET,
            family_hint=result.family if result and result.confident else None,
            family_hints=tuple(alternatives),
        )
        name = str(getattr(chosen, "path", "")).lower()
        assert expect in name, f"{path.name} → {name}"
        assert ("bold" in name) is want_bold, f"{path.name} → {name}"
        assert ("italic" in name or "oblique" in name) is want_italic, f"{path.name} → {name}"


def test_a_crop_with_no_text_is_not_identified():
    blank = Image.new("L", (300, 80), 255)
    assert identify(blank, (0, 0, 300, 80), text="anything") is None
