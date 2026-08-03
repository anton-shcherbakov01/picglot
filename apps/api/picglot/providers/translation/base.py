"""Translation provider interface and placeholder protection.

Placeholder protection is the part that keeps translations usable: product
codes, URLs, emails, numbers and template variables are masked before the text
leaves for a provider and restored afterwards, so ``{count} items · SKU-4471``
does not come back as ``{счёт} штук · АРТ-4471``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from picglot.domain import languages
from picglot.domain.enums import TranslationSource
from picglot.providers.base import BaseProvider, ProviderKind


@dataclass(slots=True)
class TranslationRequest:
    texts: list[str]
    target_language: str
    source_language: str | None = None
    formality: str | None = None  # "formal" | "informal" | None
    glossary_terms: dict[str, str] = field(default_factory=dict)
    do_not_translate: set[str] = field(default_factory=set)
    context: str | None = None

    def __post_init__(self) -> None:
        self.target_language = languages.require(self.target_language).code
        if self.source_language:
            normalized = languages.normalize(self.source_language)
            self.source_language = normalized


@dataclass(slots=True)
class TranslationResult:
    texts: list[str]
    provider: str
    model: str | None = None
    source_language: str | None = None
    character_count: int = 0
    cost_micro_usd: int = 0
    duration_ms: int = 0
    source: TranslationSource = TranslationSource.PROVIDER
    alternatives: list[list[str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class TranslationProvider(BaseProvider):
    kind = ProviderKind.TRANSLATION
    supports_formality: bool = False
    supports_glossary: bool = False
    max_batch: int = 50

    def supports_pair(self, source: str | None, target: str) -> bool:
        return True

    def translate(self, request: TranslationRequest) -> TranslationResult:  # pragma: no cover
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Placeholder protection
# --------------------------------------------------------------------------- #
#: Ordered by specificity — the first pattern to match a span wins.
PROTECTED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("url", re.compile(r"https?://[^\s<>\"]+|www\.[^\s<>\"]+", re.IGNORECASE)),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")),
    (
        "template",
        re.compile(r"\{\{[^{}]{1,60}\}\}|\{[A-Za-z_][\w.]{0,40}\}|%\w{1,20}%|\$\{[^}]{1,40}\}"),
    ),
    ("html", re.compile(r"</?[A-Za-z][\w-]{0,20}(?:\s[^<>]{0,120})?/?>")),
    ("code", re.compile(r"\b[A-Z]{2,}[-_ ]?\d{2,}[A-Z0-9-]*\b")),
    ("phone", re.compile(r"\+?\d[\d\s().-]{6,17}\d")),
    ("money", re.compile(r"[$€£¥₽₴₸]\s?\d[\d\s.,]*|\d[\d\s.,]*\s?(?:USD|EUR|RUB|GBP|₽|\$)")),
)

_TOKEN = "⟦{index}⟧"
_TOKEN_RE = re.compile(r"⟦(\d{1,3})⟧")


@dataclass(slots=True)
class ProtectedText:
    masked: str
    values: list[str]

    def restore(self, translated: str) -> str:
        def _replace(match: re.Match[str]) -> str:
            index = int(match.group(1))
            return self.values[index] if 0 <= index < len(self.values) else match.group(0)

        restored = _TOKEN_RE.sub(_replace, translated)
        # Some providers mangle the token; fall back to appending what was lost.
        missing = [
            value
            for index, value in enumerate(self.values)
            if _TOKEN.format(index=index) in self.masked and value not in restored
        ]
        if missing and not _TOKEN_RE.search(restored):
            for value in missing:
                if value not in restored:
                    restored = f"{restored} {value}".strip()
        return restored


def protect(text: str, extra_terms: set[str] | None = None) -> ProtectedText:
    """Replace protected spans with opaque tokens."""
    spans: list[tuple[int, int, str]] = []
    for _kind, pattern in PROTECTED_PATTERNS:
        for match in pattern.finditer(text):
            if any(start < match.end() and match.start() < end for start, end, _ in spans):
                continue
            spans.append((match.start(), match.end(), match.group(0)))

    for term in sorted(extra_terms or set(), key=len, reverse=True):
        if not term:
            continue
        for match in re.finditer(re.escape(term), text):
            if any(start < match.end() and match.start() < end for start, end, _ in spans):
                continue
            spans.append((match.start(), match.end(), match.group(0)))

    if not spans:
        return ProtectedText(masked=text, values=[])

    spans.sort(key=lambda item: item[0])
    values: list[str] = []
    pieces: list[str] = []
    cursor = 0
    for start, end, value in spans:
        if start < cursor:
            continue
        pieces.append(text[cursor:start])
        pieces.append(_TOKEN.format(index=len(values)))
        values.append(value)
        cursor = end
    pieces.append(text[cursor:])
    return ProtectedText(masked="".join(pieces), values=values)


def apply_glossary(text: str, terms: dict[str, str], *, case_sensitive: bool = False) -> str:
    """Force glossary translations before the provider ever sees the text."""
    if not terms:
        return text
    flags = 0 if case_sensitive else re.IGNORECASE
    for source, target in sorted(terms.items(), key=lambda item: len(item[0]), reverse=True):
        if not source:
            continue
        text = re.sub(rf"\b{re.escape(source)}\b", target.replace("\\", r"\\"), text, flags=flags)
    return text


def split_long_text(text: str, max_chars: int) -> list[str]:
    """Split on sentence boundaries so a provider never truncates mid-sentence."""
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?。！？\n])\s+", text):
        if len(current) + len(sentence) + 1 > max_chars and current:
            chunks.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip() if current else sentence
    if current:
        chunks.append(current.strip())

    # A single sentence longer than the limit still has to be cut somewhere.
    result: list[str] = []
    for chunk in chunks:
        while len(chunk) > max_chars:
            cut = chunk.rfind(" ", 0, max_chars) or max_chars
            result.append(chunk[:cut])
            chunk = chunk[cut:].lstrip()
        if chunk:
            result.append(chunk)
    return result


def should_translate(text: str) -> bool:
    """Skip strings that are pure numbers, symbols or single codes."""
    stripped = text.strip()
    if len(stripped) < 2:
        return False
    letters = sum(1 for char in stripped if char.isalpha())
    return letters >= 2 and letters / len(stripped) > 0.25
