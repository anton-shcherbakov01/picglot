"""OCR post-processing.

Two guarantees:

1. The raw OCR string is preserved verbatim on the region (``text``); the
   cleaned string lives in ``normalized_text``. Nothing is lost.
2. A substitution is only applied when the surrounding context makes it
   near-certain (``0`` inside a word of letters, ``l`` between digits). Every
   change is recorded so the editor can list and revert it individually.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from picglot.domain.languages import Script, detect_script
from picglot.vision.types import Region

# Control characters, private-use glyphs, the replacement character and the
# zero-width junk OCR engines emit; none of it may reach an export.
_ARTEFACTS = re.compile(
    "[\\ue000-\\uf8ff\\ufffd\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f\\u200b\\u200c\\u200e\\u200f\\ufeff]"
)
_MULTISPACE = re.compile(r"[ \t ]{2,}")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?%)\]}])")
_SPACE_AFTER_OPEN = re.compile(r"([(\[{])\s+")
_REPEATED_PUNCT = re.compile(r"([,;:])\1{1,}")
_HYPHEN_BREAK = re.compile(r"(\w)[-‑–]\s*\n\s*(\w)")
_STRAY_LINE_BREAK = re.compile(r"(?<![.!?:;])\n(?![\n•·\-\d])")

_DIGIT_WORD = re.compile(r"(?<=[A-Za-zА-Яа-яЁё])[0О](?=[A-Za-zА-Яа-яЁё])")
_LETTER_IN_NUMBER = re.compile(r"(?<=\d)[lIoOSB](?=\d)")
_LEADING_LETTER_NUMBER = re.compile(r"\b[lI](?=\d{2,})")

_NUMBER_LOOKALIKES = {"l": "1", "I": "1", "o": "0", "O": "0", "S": "5", "B": "8"}
_LETTER_LOOKALIKES = {"0": "O", "О": "O", "1": "l", "5": "S", "8": "B"}

_URL_LIKE = re.compile(r"(https?://\S+|www\.\S+|\S+@\S+\.\S+)")


@dataclass(slots=True)
class Correction:
    kind: str
    before: str
    after: str
    position: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "before": self.before,
            "after": self.after,
            "position": self.position,
        }


@dataclass(slots=True)
class NormalizeOptions:
    fix_lookalikes: bool = True
    join_hyphenation: bool = True
    collapse_line_breaks: bool = False  # "keep line breaks" is the default
    fix_spacing: bool = True
    strip_artefacts: bool = True


@dataclass(slots=True)
class NormalizeResult:
    text: str
    corrections: list[Correction] = field(default_factory=list)
    low_confidence_spans: list[dict[str, Any]] = field(default_factory=list)


def normalize_text(
    raw: str,
    *,
    options: NormalizeOptions | None = None,
    script: Script | None = None,
) -> NormalizeResult:
    options = options or NormalizeOptions()
    if not raw:
        return NormalizeResult(text="")

    text = unicodedata.normalize("NFC", raw)
    corrections: list[Correction] = []

    if options.strip_artefacts:
        cleaned = _ARTEFACTS.sub("", text)
        if cleaned != text:
            corrections.append(Correction("artefact", "…", "", 0))
            text = cleaned

    protected = [match.span() for match in _URL_LIKE.finditer(text)]

    if options.join_hyphenation:
        joined = _HYPHEN_BREAK.sub(r"\1\2", text)
        if joined != text:
            corrections.append(Correction("hyphenation", "-\\n", "", 0))
            text = joined

    if options.collapse_line_breaks:
        collapsed = _STRAY_LINE_BREAK.sub(" ", text)
        if collapsed != text:
            corrections.append(Correction("line_break", "\\n", " ", 0))
            text = collapsed

    if options.fix_spacing:
        before = text
        text = _MULTISPACE.sub(" ", text)
        text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
        text = _SPACE_AFTER_OPEN.sub(r"\1", text)
        text = _REPEATED_PUNCT.sub(r"\1", text)
        text = "\n".join(line.strip() for line in text.split("\n"))
        if text != before:
            corrections.append(Correction("spacing", "  ", " ", 0))

    if options.fix_lookalikes and script is not Script.HAN_SIMPLIFIED:
        text, lookalike_fixes = _fix_lookalikes(text, protected)
        corrections.extend(lookalike_fixes)

    return NormalizeResult(text=text.strip(), corrections=corrections)


def _fix_lookalikes(text: str, protected: list[tuple[int, int]]) -> tuple[str, list[Correction]]:
    """Repair O/0 and l/1 confusions only where context is unambiguous."""
    corrections: list[Correction] = []

    def _guarded(match: re.Match[str], replacement: str, kind: str) -> str:
        start = match.start()
        if any(low <= start < high for low, high in protected):
            return match.group(0)
        original = match.group(0)
        if original == replacement:
            return original
        corrections.append(Correction(kind, original, replacement, start))
        return replacement

    def _digit_to_letter(match: re.Match[str]) -> str:
        letter = _LETTER_LOOKALIKES.get(match.group(0), match.group(0))
        # Match the case of the surrounding word: "t0tal" -> "total", not "tOtal".
        before = text[match.start() - 1] if match.start() else ""
        after = text[match.end()] if match.end() < len(text) else ""
        neighbours = f"{before}{after}"
        if neighbours and not any(char.isupper() for char in neighbours):
            letter = letter.lower()
        return _guarded(match, letter, "digit_in_word")

    text = _DIGIT_WORD.sub(_digit_to_letter, text)
    text = _LETTER_IN_NUMBER.sub(
        lambda match: _guarded(
            match, _NUMBER_LOOKALIKES.get(match.group(0), match.group(0)), "letter_in_number"
        ),
        text,
    )
    text = _LEADING_LETTER_NUMBER.sub(lambda match: _guarded(match, "1", "letter_in_number"), text)
    return text, corrections


def low_confidence_spans(
    text: str, confidence: float | None, *, threshold: float = 0.72
) -> list[dict[str, Any]]:
    """Flag characters worth a human glance.

    Providers rarely give per-character confidence, so we combine the region
    score with typographic risk markers (isolated symbols, mixed scripts).
    """
    spans: list[dict[str, Any]] = []
    if not text:
        return spans

    if confidence is not None and confidence < threshold:
        spans.append({"start": 0, "end": len(text), "reason": "low_region_confidence"})
        return spans

    for match in re.finditer(r"[|¦~^`¬]{1,}", text):
        spans.append({"start": match.start(), "end": match.end(), "reason": "unusual_symbol"})

    for match in re.finditer(r"\b\w*[А-Яа-я]+[A-Za-z]+\w*\b|\b\w*[A-Za-z]+[А-Яа-я]+\w*\b", text):
        spans.append({"start": match.start(), "end": match.end(), "reason": "mixed_script"})

    return spans[:20]


def normalize_region(region: Region, options: NormalizeOptions | None = None) -> Region:
    script = detect_script(region.text) if region.text else None
    result = normalize_text(region.text, options=options, script=script)
    region.normalized_text = result.text
    region.corrections = [correction.as_dict() for correction in result.corrections]
    region.low_confidence_spans = low_confidence_spans(result.text, region.confidence)
    return region


def normalize_regions(
    regions: list[Region], options: NormalizeOptions | None = None
) -> list[Region]:
    return [normalize_region(region, options) for region in regions]


def revert_correction(region: Region, index: int) -> Region:
    """Undo one automatic correction, keeping the rest applied."""
    if not (0 <= index < len(region.corrections)):
        return region
    correction = region.corrections.pop(index)
    text = region.normalized_text or ""
    position = int(correction.get("position", 0))
    after = str(correction.get("after", ""))
    before = str(correction.get("before", ""))
    if after and text[position : position + len(after)] == after:
        region.normalized_text = text[:position] + before + text[position + len(after) :]
    return region


def merge_paragraph_text(lines: list[str], *, keep_breaks: bool = True) -> str:
    if keep_breaks:
        return "\n".join(line.strip() for line in lines if line.strip())
    joined: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if joined and joined[-1].endswith(("-", "‑")):
            joined[-1] = joined[-1][:-1] + stripped
        else:
            joined.append(stripped)
    return " ".join(joined)


def confidence_band(confidence: float | None) -> str:
    if confidence is None:
        return "unknown"
    if confidence >= 0.9:
        return "high"
    if confidence >= 0.75:
        return "medium"
    if confidence >= 0.5:
        return "low"
    return "very_low"
