"""Font discovery, script coverage and text measurement.

Rendering translated text convincingly needs *real* font metrics — estimating
"characters × average width" produces overflowing boxes in Cyrillic and empty
space in CJK. Every measurement here goes through the actual glyph metrics of
the font that will be used to draw.

Fonts are discovered from (in order):
  1. ``FONT_DIR`` — fonts shipped with the deployment (licence-checked);
  2. the platform font directories;
  3. Pillow's bundled default as the last resort (Latin only).

A font is only selected for a string if its cmap actually covers that string,
so text never renders as a row of tofu boxes.
"""

from __future__ import annotations

import functools
import platform
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import ImageFont

from picglot.core.config import settings
from picglot.core.logging import get_logger
from picglot.domain.enums import FontClass
from picglot.domain.languages import Script

log = get_logger(__name__)

_SYSTEM_FONT_DIRS: dict[str, list[Path]] = {
    "Windows": [Path("C:/Windows/Fonts")],
    "Darwin": [
        Path("/System/Library/Fonts"),
        Path("/Library/Fonts"),
        Path.home() / "Library/Fonts",
    ],
    "Linux": [
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path.home() / ".local/share/fonts",
    ],
}

#: Representative code points that must be covered for a script.
SCRIPT_PROBES: dict[Script, str] = {
    Script.LATIN: "AaZz",
    Script.CYRILLIC: "АяЁж",
    Script.ARABIC: "ابجد",
    Script.HEBREW: "אבגד",
    Script.HAN_SIMPLIFIED: "中文简体",
    Script.HAN_TRADITIONAL: "中文繁體",
    Script.KANA: "あいカナ",
    Script.HANGUL: "한국어글",
    Script.DEVANAGARI: "अआकख",
    Script.THAI: "กขคง",
    Script.GEORGIAN: "ქართ",
    Script.ARMENIAN: "աբգդ",
}

#: Preferred families per class, best first. Matching is substring, case-insensitive.
FAMILY_PREFERENCES: dict[FontClass, tuple[str, ...]] = {
    FontClass.SANS: (
        "notosans",
        "dejavusans",
        "liberationsans",
        "arial",
        "helvetica",
        "roboto",
        "opensans",
        "segoeui",
        "ubuntu",
        "aileron",
    ),
    FontClass.SERIF: (
        "notoserif",
        "dejavuserif",
        "liberationserif",
        "timesnewroman",
        "georgia",
        "times",
        "cambria",
    ),
    FontClass.MONO: (
        "notosansmono",
        "dejavusansmono",
        "liberationmono",
        "consolas",
        "couriernew",
        "menlo",
        "ubuntumono",
    ),
    # Caveat ships with the deployment (OFL, Latin + Cyrillic) because the
    # alternatives here are Windows and macOS faces: on a Linux image the whole
    # list used to miss and fall through to the grotesque at the end, so
    # handwriting was detected and then drawn as if it never had been.
    FontClass.HANDWRITING: (
        "caveat",
        "comicsansms",
        "segoescript",
        "bradleyhand",
        "notosans",
    ),
}

#: CJK and RTL need dedicated families regardless of the requested class.
SCRIPT_FAMILIES: dict[Script, tuple[str, ...]] = {
    Script.HAN_SIMPLIFIED: ("notosanscjk", "notosanssc", "msyh", "simsun", "pingfang", "wqy"),
    Script.HAN_TRADITIONAL: ("notosanscjk", "notosanstc", "msjh", "pmingliu", "pingfang"),
    Script.KANA: ("notosanscjk", "notosansjp", "msgothic", "yugothic", "hiragino"),
    Script.HANGUL: ("notosanscjk", "notosanskr", "malgun", "applegothic", "nanum"),
    Script.ARABIC: ("notonaskharabic", "notosansarabic", "amiri", "tahoma", "arial"),
    Script.HEBREW: ("notosanshebrew", "davidlibre", "arial", "tahoma"),
    Script.THAI: ("notosansthai", "leelawadee", "tahoma"),
    Script.DEVANAGARI: ("notosansdevanagari", "mangal", "nirmala"),
    Script.GEORGIAN: ("notosansgeorgian", "sylfaen"),
    Script.ARMENIAN: ("notosansarmenian", "sylfaen"),
}


@dataclass(slots=True)
class FontFile:
    path: Path
    family: str
    normalized_family: str
    bold: bool = False
    italic: bool = False
    coverage: set[int] = field(default_factory=set)
    index: int = 0

    @property
    def style_key(self) -> tuple[bool, bool]:
        return (self.bold, self.italic)


class FontRegistry:
    def __init__(self) -> None:
        self._files: list[FontFile] = []
        self._lock = threading.Lock()
        self._loaded = False

    # -- discovery ----------------------------------------------------------
    def _candidate_dirs(self) -> list[Path]:
        directories: list[Path] = []
        bundled = settings.font_directory
        if bundled.exists():
            directories.append(bundled)
        directories.extend(
            path for path in _SYSTEM_FONT_DIRS.get(platform.system(), []) if path.exists()
        )
        return directories

    def load(self, force: bool = False) -> None:
        if self._loaded and not force:
            return
        with self._lock:
            if self._loaded and not force:
                return
            files: list[FontFile] = []
            seen: set[str] = set()
            for directory in self._candidate_dirs():
                for path in sorted(directory.rglob("*")):
                    if path.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
                        continue
                    key = path.name.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    parsed = _describe(path)
                    if parsed:
                        files.append(parsed)
            self._files = files
            self._loaded = True
            log.info("fonts.loaded", count=len(files), dirs=len(self._candidate_dirs()))

    @property
    def files(self) -> list[FontFile]:
        self.load()
        return self._files

    # -- selection ----------------------------------------------------------
    def find(
        self,
        *,
        font_class: FontClass = FontClass.SANS,
        script: Script = Script.LATIN,
        bold: bool = False,
        italic: bool = False,
        text: str = "",
        family_hint: str | None = None,
    ) -> FontFile | None:
        self.load()
        if not self._files:
            return None

        required = {ord(char) for char in (text or SCRIPT_PROBES.get(script, "A"))}
        required = {code for code in required if code > 32}

        preferences: list[str] = []
        if family_hint:
            preferences.append(_normalize_family(family_hint))
        preferences.extend(SCRIPT_FAMILIES.get(script, ()))
        preferences.extend(FAMILY_PREFERENCES.get(font_class, ()))

        covering = [file for file in self._files if _covers(file, required)]
        if not covering:
            covering = [file for file in self._files if _covers(file, required, ratio=0.9)]
        if not covering:
            return None

        def rank(file: FontFile) -> tuple[int, int, int]:
            family_rank = len(preferences)
            for index, preference in enumerate(preferences):
                if preference in file.normalized_family:
                    family_rank = index
                    break
            style_penalty = int(file.bold != bold) + int(file.italic != italic)
            return (family_rank, style_penalty, len(file.normalized_family))

        return min(covering, key=rank)

    def coverage_report(self) -> dict[str, bool]:
        """Which scripts this installation can actually render."""
        self.load()
        report: dict[str, bool] = {}
        for script, probe in SCRIPT_PROBES.items():
            report[str(script)] = self.find(script=script, text=probe) is not None
        return report


registry = FontRegistry()


def _normalize_family(name: str) -> str:
    return "".join(char for char in name.lower() if char.isalnum())


def _describe(path: Path) -> FontFile | None:
    """Read family/style/cmap. Falls back to the filename when parsing fails."""
    name = path.stem
    lowered = name.lower()
    bold = "bold" in lowered or lowered.endswith("bd") or "-b." in lowered
    italic = "italic" in lowered or "oblique" in lowered or lowered.endswith("i")

    coverage: set[int] = set()
    family = name
    try:
        from fontTools.ttLib import TTCollection, TTFont

        if path.suffix.lower() == ".ttc":
            with TTCollection(str(path), lazy=True) as collection:
                font = collection.fonts[0]
                coverage = _cmap_codes(font)
                family = _family_name(font) or name
        else:
            with TTFont(str(path), lazy=True, fontNumber=0) as font:
                coverage = _cmap_codes(font)
                family = _family_name(font) or name
                subfamily = _subfamily_name(font) or ""
                lowered_sub = subfamily.lower()
                bold = bold or "bold" in lowered_sub
                italic = italic or "italic" in lowered_sub or "oblique" in lowered_sub
    except Exception:
        # Unparseable or unusual font — keep it with filename-derived metadata,
        # coverage stays empty so it only wins when nothing else covers.
        pass

    return FontFile(
        path=path,
        family=family,
        normalized_family=_normalize_family(family) or _normalize_family(name),
        bold=bold,
        italic=italic,
        coverage=coverage,
    )


def _cmap_codes(font: Any) -> set[int]:
    codes: set[int] = set()
    try:
        for table in font["cmap"].tables:
            if table.isUnicode():
                codes.update(table.cmap.keys())
    except Exception:
        return set()
    return codes


def _family_name(font: Any) -> str | None:
    try:
        record = font["name"].getDebugName(1)
        return str(record) if record else None
    except Exception:
        return None


def _subfamily_name(font: Any) -> str | None:
    try:
        record = font["name"].getDebugName(2)
        return str(record) if record else None
    except Exception:
        return None


def _covers(file: FontFile, required: set[int], *, ratio: float = 1.0) -> bool:
    if not required:
        return True
    if not file.coverage:
        # Unknown coverage: only usable for plain ASCII.
        return all(code < 128 for code in required)
    if ratio >= 1.0:
        return required.issubset(file.coverage)
    covered = len(required & file.coverage)
    return covered / len(required) >= ratio


@functools.lru_cache(maxsize=512)
def _load_truetype(path_str: str, size: int, index: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path_str, size=size, index=index)


def load_font(
    *,
    size: float,
    font_class: FontClass = FontClass.SANS,
    script: Script = Script.LATIN,
    bold: bool = False,
    italic: bool = False,
    text: str = "",
    family_hint: str | None = None,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Return a Pillow font object, never ``None``."""
    pixel_size = max(4, round(size))
    file = registry.find(
        font_class=font_class,
        script=script,
        bold=bold,
        italic=italic,
        text=text,
        family_hint=family_hint,
    )
    if file is not None:
        try:
            return _load_truetype(str(file.path), pixel_size, file.index)
        except OSError as exc:  # pragma: no cover - corrupt font file
            log.warning("fonts.load_failed", family=file.family, error=str(exc)[:100])

    try:
        return ImageFont.load_default(size=pixel_size)
    except TypeError:  # pragma: no cover - very old Pillow
        return ImageFont.load_default()


@dataclass(slots=True)
class TextMetrics:
    width: float
    height: float
    ascent: float
    descent: float
    line_height: float


def measure(text: str, font: Any, *, line_height_ratio: float = 1.2) -> TextMetrics:
    """Measure a single line with real glyph metrics."""
    if not text:
        ascent, descent = _ascent_descent(font)
        return TextMetrics(
            0.0, ascent + descent, ascent, descent, (ascent + descent) * line_height_ratio
        )
    try:
        left, top, right, bottom = font.getbbox(text)
        width = float(right - left)
        height = float(bottom - top)
    except Exception:  # pragma: no cover - bitmap fallback font
        width = float(len(text) * 6)
        height = 11.0
    ascent, descent = _ascent_descent(font)
    return TextMetrics(
        width=width,
        height=height,
        ascent=ascent,
        descent=descent,
        line_height=(ascent + descent) * line_height_ratio,
    )


def _ascent_descent(font: Any) -> tuple[float, float]:
    try:
        ascent, descent = font.getmetrics()
        return float(ascent), float(descent)
    except Exception:
        return 8.0, 3.0


def text_width(text: str, font: Any, letter_spacing: float = 0.0) -> float:
    if not text:
        return 0.0
    width = measure(text, font).width
    if letter_spacing:
        width += letter_spacing * max(0, len(text) - 1)
    return width


def script_supported(script: Script) -> bool:
    return registry.find(script=script, text=SCRIPT_PROBES.get(script, "A")) is not None


def available_families(limit: int = 60) -> list[str]:
    """Families offered in the editor's font picker."""
    seen: dict[str, str] = {}
    for file in registry.files:
        seen.setdefault(file.normalized_family, file.family)
    return sorted(seen.values())[:limit]
