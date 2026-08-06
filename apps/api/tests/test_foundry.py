"""The font foundry: tracing letters off a page and building a face from them.

Every test here goes through a real TrueType binary — built, then loaded by the
same renderer the product uses. A font that only satisfies its own data
structures is not evidence of anything.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from picglot.foundry import build, donor, harvest, outlines, service
from picglot.vision import fonts, typeface

SAMPLE = "Handgloves"


def _installed(text: str = SAMPLE) -> fonts.FontFile:
    file = fonts.registry.find(text=text)
    assert file is not None, "no installed font can draw the test string"
    return file


def _picture_of(text: str, *, size: int = 80, font_path: str | None = None) -> Image.Image:
    """A picture of ``text``, as a page would carry it."""
    path = font_path or str(_installed(text).path)
    face = ImageFont.truetype(path, size)
    left, top, right, bottom = (round(value) for value in face.getbbox(text))
    image = Image.new("RGB", (right - left + 60, bottom - top + 60), (255, 255, 255))
    ImageDraw.Draw(image).text((30 - left, 30 - top), text, font=face, fill=(20, 20, 20))
    return image


def _built(glyphs: dict[str, outlines.GlyphImage], size: int) -> ImageFont.FreeTypeFont:
    data = build.build_font(
        {char: outlines.trace(glyph) for char, glyph in glyphs.items()}, family="PicGlot Test"
    )
    return ImageFont.truetype(io.BytesIO(data), size)


def _ink(font: ImageFont.FreeTypeFont, text: str) -> np.ndarray:
    left, top, right, bottom = (round(value) for value in font.getbbox(text))
    image = Image.new("L", (right - left + 20, bottom - top + 20), 0)
    ImageDraw.Draw(image).text((10 - left, 10 - top), text, font=font, fill=255)
    return np.asarray(image) > 127


def _overlap(first: np.ndarray, second: np.ndarray) -> float:
    """How much two renderings of the same words agree, ink against ink.

    Both are cropped to their ink and brought to a common size first: the
    comparison is of letterforms, and a couple of percent of difference in
    total width would otherwise slide every letter past its counterpart and
    report two identical alphabets as nothing alike.
    """
    left = _crop(first)
    right = _crop(second)
    if left.size == 0 or right.size == 0:
        return 0.0
    resized = (
        np.asarray(
            Image.fromarray((right * 255).astype(np.uint8)).resize(
                (left.shape[1], left.shape[0]), Image.Resampling.BILINEAR
            )
        )
        > 127
    )
    union = np.count_nonzero(left | resized)
    return 0.0 if union == 0 else float(np.count_nonzero(left & resized) / union)


def _crop(mask: np.ndarray) -> np.ndarray:
    rows = np.flatnonzero(mask.any(axis=1))
    columns = np.flatnonzero(mask.any(axis=0))
    if rows.size == 0 or columns.size == 0:
        return np.zeros((0, 0), dtype=bool)
    return mask[rows[0] : rows[-1] + 1, columns[0] : columns[-1] + 1]


# --------------------------------------------------------------------------- #
# Tracing and building
# --------------------------------------------------------------------------- #
def test_letters_cut_from_a_picture_come_back_as_the_same_letters():
    file = _installed()
    image = _picture_of(SAMPLE, font_path=str(file.path))
    line = harvest.harvest_line(image, (0, 0, image.width, image.height), SAMPLE)
    assert line is not None, "the sample line could not be split into letters"
    assert set(line.glyphs) == set(SAMPLE)

    built = _built(line.glyphs, 80)
    original = ImageFont.truetype(str(file.path), 80)
    assert _overlap(_ink(original, SAMPLE), _ink(built, SAMPLE)) > 0.75


def test_the_rhythm_of_a_line_survives_the_round_trip():
    # Advances come from where the next letter starts, not from how wide this
    # one is: get that wrong and the words are set as a row of letters.
    file = _installed()
    image = _picture_of(SAMPLE, font_path=str(file.path))
    line = harvest.harvest_line(image, (0, 0, image.width, image.height), SAMPLE)
    assert line is not None

    original = ImageFont.truetype(str(file.path), 80)
    built = _built(line.glyphs, 80)
    assert built.getlength(SAMPLE) == pytest.approx(original.getlength(SAMPLE), rel=0.12)


def test_a_font_is_a_font_a_renderer_will_open():
    file = _installed()
    image = _picture_of(SAMPLE, font_path=str(file.path))
    line = harvest.harvest_line(image, (0, 0, image.width, image.height), SAMPLE)
    assert line is not None
    data = build.build_font(
        {char: outlines.trace(glyph) for char, glyph in line.glyphs.items()},
        family="PicGlot Round Trip",
    )
    reopened = ImageFont.truetype(io.BytesIO(data), 32)
    assert reopened.getlength(SAMPLE) > 0
    assert reopened.getlength(" ") > 0, "a space must still advance the pen"


# --------------------------------------------------------------------------- #
# Pairing letters to marks
# --------------------------------------------------------------------------- #
def test_a_line_that_cannot_be_paired_is_dropped_rather_than_guessed():
    # The picture says one thing and the string says another. Pairing them in
    # order would put someone else's shape behind a letter, quietly, and every
    # word set in that font afterwards would be wrong.
    image = _picture_of("abc")
    assert harvest.harvest_line(image, (0, 0, image.width, image.height), "abcdefgh") is None


def test_an_empty_box_yields_nothing():
    blank = Image.new("RGB", (200, 60), (255, 255, 255))
    assert harvest.harvest_line(blank, (0, 0, 200, 60), "text") is None


# --------------------------------------------------------------------------- #
# Deriving what the sample never showed
# --------------------------------------------------------------------------- #
def _measure(font: ImageFont.FreeTypeFont, text: str) -> tuple[float, float]:
    """Stroke weight and lean of ``text`` as set in ``font``."""
    left, top, right, bottom = (round(value) for value in font.getbbox(text))
    canvas = Image.new("L", (right - left + 40, bottom - top + 40), 255)
    ImageDraw.Draw(canvas).text((20 - left, 20 - top), text, font=font, fill=0)
    prepared = typeface._prepare(canvas, (0, 0, canvas.width, canvas.height))
    assert prepared is not None
    mask, band = prepared
    return typeface._stroke_width(mask) / band, typeface._slant_degrees(mask)


def test_a_derived_face_leans_the_way_it_was_asked_to():
    file = _installed("Handgloves")
    glyphs = donor.derive(
        "Handgloves",
        font_path=str(file.path),
        font_index=file.index,
        style=donor.StyleParameters(slant_degrees=-12.0),
    )
    assert glyphs
    _stroke, slant = _measure(_built(glyphs, 96), "Handgloves")
    assert slant == pytest.approx(-12.0, abs=3.0)


def test_a_derived_face_is_condensed_when_the_original_was():
    file = _installed("Handgloves")
    upright = _built(donor.derive("Handgloves", font_path=str(file.path)), 96)
    narrow = _built(
        donor.derive(
            "Handgloves",
            font_path=str(file.path),
            style=donor.StyleParameters(width_ratio=0.65),
        ),
        96,
    )
    assert narrow.getlength("Handgloves") < upright.getlength("Handgloves") * 0.8


def test_asking_for_more_weight_produces_more_weight():
    file = _installed("Handgloves")
    light = _measure(
        _built(
            donor.derive(
                "Handgloves",
                font_path=str(file.path),
                style=donor.StyleParameters(stroke_ratio=0.09),
            ),
            96,
        ),
        "Handgloves",
    )[0]
    heavy = _measure(
        _built(
            donor.derive(
                "Handgloves",
                font_path=str(file.path),
                style=donor.StyleParameters(stroke_ratio=0.19),
            ),
            96,
        ),
        "Handgloves",
    )[0]
    assert heavy > light * 1.3


# --------------------------------------------------------------------------- #
# The whole face
# --------------------------------------------------------------------------- #
def test_a_face_is_built_from_the_page_where_it_can_be_and_the_donor_elsewhere():
    file = _installed()
    image = _picture_of(SAMPLE, font_path=str(file.path))
    result = service.synthesize(
        image,
        [service.Sample(box=(0, 0, image.width, image.height), text=SAMPLE)],
        # The page is Latin and the translation is Cyrillic: the overlap is
        # nothing but punctuation, which is the ordinary case for this product.
        characters="Ты потрясающая Handgloves",
        family="PicGlot Mixed",
        donor_path=str(file.path),
        donor_family=file.family,
        donor_index=file.index,
    )
    assert result is not None
    assert set(result.traced) & set(SAMPLE), "letters on the page were not traced"
    assert "Ы" not in result.traced and "ы" in result.derived

    built = ImageFont.truetype(io.BytesIO(result.data), 40)
    assert built.getlength("Ты потрясающая") > 0


def test_a_page_that_gives_up_nothing_still_produces_a_face():
    # Nothing traceable — a blank page — must not mean no font: the whole
    # alphabet is derivable, and the caller asked for a font.
    file = _installed()
    blank = Image.new("RGB", (400, 120), (255, 255, 255))
    result = service.synthesize(
        blank,
        [service.Sample(box=(0, 0, 400, 120), text="nothing here")],
        characters="Привет",
        family="PicGlot Derived",
        donor_path=str(file.path),
        donor_family=file.family,
    )
    assert result is not None and result.traced == ""
    assert set("Привет") <= set(result.derived)


def test_a_generated_face_can_be_selected_by_the_renderer(tmp_path, monkeypatch):
    # The renderer picks fonts through the registry, and a face generated for
    # one job was never in a directory it scanned.
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    file = _installed()
    glyphs = donor.derive("Привет", font_path=str(file.path))
    data = build.build_font(
        {char: outlines.trace(glyph) for char, glyph in glyphs.items()},
        family="PicGlot Registered",
    )
    path = service.materialize(data, "test-generated")
    assert path is not None and path.exists()

    found = fonts.registry.find(text="Привет", family_hint="PicGlot Registered")
    assert found is not None and found.path == path
