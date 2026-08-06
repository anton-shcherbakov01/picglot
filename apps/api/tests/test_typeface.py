"""The lettering has to be measured, not assumed.

Before this, every region was redrawn in the same grotesque whatever it
replaced — the product's whole promise, reduced to "the words are in the right
place". These tests pin the three properties a reader notices immediately when
they are wrong.

Caveat ships in ``assets/fonts`` so the handwriting cases always run. The
printed cases need the distribution's DejaVu, which the Docker images install
but a bare checkout may not have, so they skip rather than fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from picglot.domain.enums import FontClass
from picglot.vision.typeface import estimate

REPO_ROOT = Path(__file__).resolve().parents[3]
CAVEAT = REPO_ROOT / "assets/fonts/Caveat-Regular.ttf"
CAVEAT_BOLD = REPO_ROOT / "assets/fonts/Caveat-Bold.ttf"
DEJAVU = Path("/usr/share/fonts/truetype/dejavu")

#: Capitals, mixed case, Cyrillic, and a line with marks that float above the
#: baseline — the apostrophe and the tittles used to read as an unsteady hand.
SAMPLES = [
    "YOU ARE AMAZING",
    "Привет мир Hello",
    "ТЫ ПОТРЯСАЮЩАЯ",
    "Don't let anyone say",
    "Иди й тест ЁЖ",
]


def render(font_path: Path, text: str, size: int = 64) -> Image.Image:
    """Draw the sample on a canvas that fits it, with room for ascenders.

    A canvas that clips the line silently removes most of the letters — the
    measurements then run on whatever survived and mean nothing.
    """
    font = ImageFont.truetype(str(font_path), size=size)
    left, top, right, bottom = font.getbbox(text)
    margin = max(8, size // 4)
    image = Image.new("L", (right - left + margin * 2, bottom - top + margin * 2), 255)
    ImageDraw.Draw(image).text((margin - left, margin - top), text, font=font, fill=0)
    return image


def measure(font_path: Path, text: str):
    image = render(font_path, text)
    return estimate(image, (0, 0, image.width, image.height))


def dejavu(name: str) -> Path:
    path = DEJAVU / name
    if not path.exists():
        pytest.skip(f"{name} not installed")
    return path


@pytest.mark.parametrize("text", SAMPLES)
def test_handwriting_is_recognised_whatever_the_case_or_script(text):
    """The feature this exists for: a drawn hand must not come back as print.

    Letter-height spread was the first candidate and collapses on capitals —
    which is most of what gets translated on a poster or a greeting card — so
    every sample here runs against the baseline measure instead.
    """
    result = measure(CAVEAT, text)
    assert result.font_class is FontClass.HANDWRITING
    assert result.confidence >= 0.5


@pytest.mark.parametrize("text", SAMPLES)
def test_printed_text_is_never_mistaken_for_handwriting(text):
    """The costlier direction: a contract redrawn in a marker face.

    Descenders, apostrophes and tittles are all separate marks sitting off the
    baseline, and counting them as wobble turned printed lines into handwriting.
    """
    for name in ("DejaVuSans.ttf", "DejaVuSerif.ttf"):
        result = measure(dejavu(name), text)
        assert result.font_class is not FontClass.HANDWRITING


def test_serif_and_sans_are_told_apart_by_stroke_contrast():
    """A serif is thin where its stem is thick; a grotesque holds one width."""
    assert measure(dejavu("DejaVuSerif.ttf"), SAMPLES[1]).font_class is FontClass.SERIF
    assert measure(dejavu("DejaVuSans.ttf"), SAMPLES[1]).font_class is FontClass.SANS


@pytest.mark.parametrize("text", ["YOU ARE AMAZING", "Привет мир Hello"])
def test_weight_survives_the_switch_to_capitals(text):
    """Serifs are thin and there are two per stem, so they drag the median down.

    Taking the median of the stroke ridge made bold capitals measure *lighter*
    than regular ones; the upper third of the ridge is the stems, which is what
    actually carries the weight.
    """
    assert measure(dejavu("DejaVuSans-Bold.ttf"), text).bold is True
    assert measure(dejavu("DejaVuSerif-Bold.ttf"), text).bold is True
    assert measure(dejavu("DejaVuSans.ttf"), text).bold is False


def test_a_lean_is_reported_for_set_type_only():
    """Script faces lean by construction, so calling that italic misdirects.

    Caveat measures 18°. Reporting it would send the font search after a cursive
    cut of a face that is already cursive, and rank the upright cut of it below
    an unrelated family.
    """
    upright = measure(dejavu("DejaVuSans.ttf"), SAMPLES[1])
    assert upright.italic is False

    hand = measure(CAVEAT, SAMPLES[1])
    assert abs(hand.slant_degrees) > 10  # it does lean
    assert hand.italic is None  # and that is not reported as italic

    oblique = DEJAVU / "DejaVuSans-Oblique.ttf"
    if oblique.exists():
        assert measure(oblique, SAMPLES[1]).italic is True


def test_a_crop_with_nothing_to_measure_decides_nothing():
    """No ink, no opinion — the caller keeps whatever default it had."""
    blank = Image.new("L", (200, 60), 255)
    result = estimate(blank, (0, 0, 200, 60))
    assert result.font_class is None
    assert result.bold is None and result.italic is None
    assert result.confidence == 0.0

    # A box smaller than a letter is refused rather than guessed at.
    tiny = estimate(render(CAVEAT, "A"), (0, 0, 4, 4))
    assert tiny.font_class is None


def test_measurements_are_independent_of_resolution():
    """Both metrics are ratios, so rendering larger must not change the verdict."""
    small = measure(CAVEAT, SAMPLES[0])
    image = render(CAVEAT, SAMPLES[0], size=110)
    large = estimate(image, (0, 0, image.width, image.height))
    assert small.font_class is large.font_class is FontClass.HANDWRITING


def test_the_bundled_handwriting_face_covers_cyrillic():
    """Selecting a handwriting class is useless if the face cannot draw Russian.

    The preference list was Comic Sans, Segoe Script and Bradley Hand — all
    absent from a Linux image — so it fell through to the grotesque at the end
    and handwriting was detected and then thrown away.
    """
    from picglot.domain.languages import Script
    from picglot.vision.fonts import load_font

    chosen = load_font(
        size=48,
        font_class=FontClass.HANDWRITING,
        script=Script.CYRILLIC,
        text="ТЫ ПОТРЯСАЮЩАЯ",
    )
    assert "caveat" in str(getattr(chosen, "path", "")).lower()


def test_bold_handwriting_reaches_the_bold_cut():
    """Both weights of the bundled face have to be reachable."""
    from picglot.domain.languages import Script
    from picglot.vision.fonts import load_font

    chosen = load_font(
        size=48,
        font_class=FontClass.HANDWRITING,
        script=Script.CYRILLIC,
        bold=True,
        text="ТЫ ПОТРЯСАЮЩАЯ",
    )
    assert "caveat" in str(getattr(chosen, "path", "")).lower()
    assert "bold" in str(getattr(chosen, "path", "")).lower()
