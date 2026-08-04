"""Unit tests for the vision primitives: geometry, layout, fitting, normalisation."""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

from picglot.domain import languages
from picglot.domain.enums import RegionType, TextDirection
from picglot.vision import fonts, layout, normalize, render, tables
from picglot.vision.types import BoundingBox, Region, TextStyle, polygon_rotation


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #
def test_bounding_box_geometry():
    box = BoundingBox(10, 20, 100, 50)
    assert box.right == 110
    assert box.bottom == 70
    assert box.center == (60, 45)
    assert box.area == 5000

    other = BoundingBox(60, 45, 100, 50)
    assert 0 < box.iou(other) < 1
    union = box.union(other)
    assert union.x == 10 and union.right == 160


def test_bounding_box_clamps_to_the_page():
    box = BoundingBox(-20, -10, 200, 200).clamp(100, 100)
    assert box.x == 0 and box.y == 0
    assert box.right <= 100 and box.bottom <= 100


def test_polygon_rotation_detects_tilt():
    assert polygon_rotation([(0, 0), (100, 0), (100, 20), (0, 20)]) == 0.0
    tilted = polygon_rotation([(0, 0), (100, 10), (100, 30), (0, 20)])
    assert 4 < tilted < 8


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #
def _region(text: str, x: float, y: float, width: float, height: float) -> Region:
    box = BoundingBox(x, y, width, height)
    return Region(
        id=f"r{int(y)}-{int(x)}",
        polygon=box.to_polygon(),
        bounding_box=box,
        text=text,
        confidence=0.95,
    )


def test_adjacent_lines_merge_into_one_paragraph():
    regions = [
        _region("The quick brown fox jumps over", 10, 10, 300, 20),
        _region("the lazy dog and keeps running", 10, 34, 300, 20),
        _region("until it reaches the fence line", 10, 58, 300, 20),
    ]
    result = layout.analyse(regions, page_size=(400, 200))
    assert len(result) == 1
    assert "quick brown fox" in result[0].text
    assert "fence line" in result[0].text


def test_distant_lines_stay_separate():
    regions = [
        _region("First paragraph text here", 10, 10, 300, 20),
        _region("Totally separate block far below", 10, 150, 300, 20),
    ]
    result = layout.analyse(regions, page_size=(400, 300))
    assert len(result) == 2


def test_large_short_text_is_classified_as_a_heading():
    regions = [
        _region("Annual Report", 10, 10, 250, 40),
        *[
            _region(
                f"Body sentence number {index} with plenty of words", 10, 70 + index * 22, 300, 16
            )
            for index in range(6)
        ],
    ]
    result = layout.analyse(regions, page_size=(400, 300))
    heading = next(r for r in result if "Annual Report" in r.effective_text)
    assert heading.region_type is RegionType.HEADING


def test_hyphenated_line_breaks_are_repaired():
    regions = [
        _region("This sentence contin-", 10, 10, 200, 18),
        _region("ues on the next line", 10, 32, 200, 18),
    ]
    result = layout.analyse(regions, page_size=(300, 100))
    assert "continues" in result[0].text


def test_two_column_layout_is_split():
    left = [_region(f"left line {i}", 10, 10 + i * 22, 120, 18) for i in range(6)]
    right = [_region(f"right line {i}", 260, 10 + i * 22, 120, 18) for i in range(6)]
    result = layout.analyse(left + right, page_size=(400, 200))
    ordered = [r.effective_text for r in sorted(result, key=lambda x: x.reading_order)]
    # Everything in the left column must be read before the right column.
    left_indexes = [i for i, t in enumerate(ordered) if "left" in t]
    right_indexes = [i for i, t in enumerate(ordered) if "right" in t]
    assert max(left_indexes) < min(right_indexes)


def test_ui_mode_keeps_short_labels_separate():
    regions = [
        _region("Save", 10, 10, 40, 18),
        _region("Cancel", 10, 32, 50, 18),
    ]
    result = layout.analyse(regions, page_size=(200, 100), ui_mode=True)
    assert len(result) == 2
    assert all(r.region_type is RegionType.UI_LABEL for r in result)


# --------------------------------------------------------------------------- #
# OCR normalisation
# --------------------------------------------------------------------------- #
def test_letter_digit_confusions_are_fixed_in_context():
    result = normalize.normalize_text("The t0tal is 5O0 units")
    assert "total" in result.text
    assert result.corrections


def test_urls_are_never_rewritten():
    original = "Visit https://example.com/a0b1 or mail me@ex4mple.io"
    result = normalize.normalize_text(original)
    assert "https://example.com/a0b1" in result.text
    assert "me@ex4mple.io" in result.text


def test_line_breaks_are_kept_by_default():
    result = normalize.normalize_text("first line\nsecond line")
    assert "\n" in result.text

    collapsed = normalize.normalize_text(
        "first line\nsecond line",
        options=normalize.NormalizeOptions(collapse_line_breaks=True),
    )
    assert "\n" not in collapsed.text


def test_raw_text_is_preserved_alongside_the_cleaned_version():
    region = _region("The t0tal", 0, 0, 10, 10)
    normalize.normalize_region(region)
    assert region.text == "The t0tal", "the original OCR string must survive"
    assert region.normalized_text != region.text


def test_a_correction_can_be_reverted_individually():
    region = _region("The t0tal", 0, 0, 10, 10)
    normalize.normalize_region(region)
    assert region.corrections
    normalize.revert_correction(region, 0)
    assert len(region.corrections) == 0


def test_low_confidence_regions_are_flagged():
    spans = normalize.low_confidence_spans("uncertain text", confidence=0.4)
    assert spans and spans[0]["reason"] == "low_region_confidence"


# --------------------------------------------------------------------------- #
# Fonts and text fitting
# --------------------------------------------------------------------------- #
def test_every_advertised_script_has_a_usable_font():
    coverage = fonts.registry.coverage_report()
    missing = [name for name, ok in coverage.items() if not ok]
    assert not missing, f"no font covers: {missing}"


def test_measurement_uses_real_glyph_metrics():
    font = fonts.load_font(size=24, text="Hello")
    narrow = fonts.text_width("iii", font)
    wide = fonts.text_width("WWW", font)
    assert wide > narrow, "a proportional font must not measure by character count"


def test_long_translations_shrink_to_fit_their_box():
    box = BoundingBox(0, 0, 200, 40)
    style = TextStyle(font_size=24)
    short, _ = render.fit_text("Exit", box, style)
    long, _ = render.fit_text("Notausgang bitte jederzeit vollständig freihalten", box, style)
    assert long.font_size < short.font_size
    assert long.total_height <= box.height * render.MAX_BOX_GROWTH


def test_text_that_cannot_fit_is_reported_not_clipped():
    box = BoundingBox(0, 0, 30, 12)
    result, _font = render.fit_text("A" * 400, box, TextStyle(font_size=12))
    assert result.overflow or result.grew_box
    assert result.lines, "the text must still be produced, not silently dropped"


def test_wrapping_respects_measured_width():
    font = fonts.load_font(size=16, text="word")
    lines = render.wrap_text("word " * 30, font, max_width=120)
    assert len(lines) > 1
    assert all(fonts.text_width(line, font) <= 130 for line in lines)


def test_cjk_wraps_between_characters():
    font = fonts.load_font(size=18, text="日本語のテキスト", script=languages.Script.KANA)
    lines = render.wrap_text("日本語のテキストです" * 4, font, max_width=90)
    assert len(lines) > 1


def test_rtl_text_is_reordered_for_drawing():
    original = "مرحبا بالعالم"
    shaped = render.shape_bidi(original, TextDirection.RTL)
    assert shaped != original or len(shaped) == len(original)
    assert render.shape_bidi(original, TextDirection.LTR) == original


def test_rendering_produces_a_changed_image():
    image = Image.new("RGB", (400, 120), (255, 255, 255))
    region = _region("", 20, 20, 360, 60)
    region.style = TextStyle(font_size=28, color="#101010")
    before = list(image.getdata())
    result = render.draw_region(image, region, "Hello world")
    assert list(result.getdata()) != before


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,expected_type",
    [
        ("1 234,56", "number"),
        ("$1,299.00", "currency"),
        ("12.5%", "percent"),
        ("01.02.2026", "date"),
        ("Coffee beans", "text"),
        ("=SUM(A1:A5)", "formula"),
    ],
)
def test_cell_values_are_typed(text, expected_type):
    value_type, _numeric, _currency = tables.classify_value(text)
    assert value_type == expected_type


def test_european_and_us_number_formats_both_parse():
    _t, european, _c = tables.classify_value("1.234,56")
    _t, american, _c = tables.classify_value("1,234.56")
    assert european == pytest.approx(1234.56)
    assert american == pytest.approx(1234.56)


def test_ruled_table_grid_is_detected():
    image = Image.new("RGB", (400, 200), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    for index in range(4):
        y = 20 + index * 40
        draw.line([(20, y), (380, y)], fill=(0, 0, 0), width=2)
    for index in range(4):
        x = 20 + index * 120
        draw.line([(x, 20), (x, 140)], fill=(0, 0, 0), width=2)

    detected = tables.detect_ruled_tables(image)
    assert detected, "a drawn grid should be recognised as a ruled table"
    _bounds, x_lines, y_lines = detected[0]
    assert len(x_lines) >= 3
    assert len(y_lines) >= 3


# --------------------------------------------------------------------------- #
# Language table
# --------------------------------------------------------------------------- #
def test_language_lookup_and_aliases():
    assert languages.get("zh").code == "zh-Hans"
    assert languages.get("en-US").code == "en"
    assert languages.get("nonexistent") is None
    assert languages.require("ru").is_rtl is False
    assert languages.get("ar").is_rtl is True
    assert languages.get("ja").is_cjk is True


def test_tesseract_language_argument_always_includes_english():
    assert "eng" in languages.tesseract_codes(["ru"])
    assert languages.tesseract_codes([]) == "eng"


def test_script_detection():
    assert languages.detect_script("Привет мир") is languages.Script.CYRILLIC
    assert languages.detect_script("Hello world") is languages.Script.LATIN
    assert languages.detect_script("مرحبا") is languages.Script.ARABIC
    assert languages.detect_script("こんにちは") is languages.Script.KANA


def test_line_text_is_rebuilt_when_the_engine_drops_the_spaces():
    """Some engines return a glued line string next to correct word fragments.

    `DON'TLETANYONETELL` is what the user sees; the engine still knew where the
    four words were. Nothing downstream can recover the boundaries once they
    are gone, so they are taken from the fragments here.
    """
    from picglot.providers.ocr.base import line_text_from_words

    assert (
        line_text_from_words("DON'TLETANYONETELL", ["DON'T", "LET", "ANYONE", "TELL"])
        == "DON'T LET ANYONE TELL"
    )
    # Already spaced: leave the engine's own spelling alone.
    assert line_text_from_words("YOU ARE AMAZING", ["YOU", "ARE", "AMAZING"]) == "YOU ARE AMAZING"
    # Differs by more than whitespace — a hyphen the engine split on. Forcing a
    # space here would corrupt a word that was never broken.
    assert line_text_from_words("well-known", ["well", "known"]) == "well-known"
    assert line_text_from_words("single", ["single"]) == "single"


def test_glued_words_withhold_the_top_confidence_band():
    """A badge reading "high confidence" over run-together words is worse than none."""
    from picglot.domain.enums import QualityBand
    from picglot.vision.quality import assess
    from picglot.vision.types import BoundingBox, PageResult, Region

    def page(text: str) -> PageResult:
        box = BoundingBox(0, 0, 100, 20)
        region = Region(
            id="reg_1",
            polygon=box.to_polygon(),
            bounding_box=box,
            text=text,
            confidence=0.97,
        )
        return PageResult(
            page_number=1,
            width=900,
            height=420,
            regions=[region],
            detected_language="en",
            confidence=0.97,
        )

    glued = assess([page("YOU ARE AMAZING, DON'TLETANYONETELL YOU OTHERWISE")])
    assert glued.band is not QualityBand.HIGH
    assert any(factor.key == "dropped_spaces" for factor in glued.factors)

    clean = assess([page("Emergency exit keep this door closed at all times")])
    assert clean.band is QualityBand.HIGH
    assert all(factor.key != "dropped_spaces" for factor in clean.factors)

    # Long compounds are normal in some languages; the threshold is relative to
    # the page so they must not be flagged.
    german = assess(
        [
            page(
                "Rechtsschutzversicherungsgesellschaften "
                "Bundesausbildungsfoerderungsgesetz Donaudampfschifffahrt"
            )
        ]
    )
    assert all(factor.key != "dropped_spaces" for factor in german.factors)
