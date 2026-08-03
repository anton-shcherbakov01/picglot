"""Language catalogue.

One table drives OCR model selection, translation provider codes, font choice,
text direction and the public "supported languages" page. Adding a language is
a single entry here plus a font that covers the script.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Script(StrEnum):
    LATIN = "latin"
    CYRILLIC = "cyrillic"
    ARABIC = "arabic"
    HEBREW = "hebrew"
    HAN_SIMPLIFIED = "han_simplified"
    HAN_TRADITIONAL = "han_traditional"
    KANA = "kana"
    HANGUL = "hangul"
    DEVANAGARI = "devanagari"
    THAI = "thai"
    GEORGIAN = "georgian"
    ARMENIAN = "armenian"


class Direction(StrEnum):
    LTR = "ltr"
    RTL = "rtl"


@dataclass(frozen=True, slots=True)
class Language:
    code: str  # BCP-47 base used across our API
    name_en: str
    name_native: str
    script: Script
    direction: Direction = Direction.LTR
    #: Vertical writing is *possible* (CJK); never assumed by default.
    supports_vertical: bool = False
    tesseract: str | None = None
    rapidocr: str | None = None
    deepl_source: str | None = None
    deepl_target: str | None = None
    google: str | None = None
    yandex: str | None = None
    azure: str | None = None
    argos: str | None = None
    #: Rough characters-per-word ratio, used to pre-size text boxes.
    density: float = 1.0
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_rtl(self) -> bool:
        return self.direction is Direction.RTL

    @property
    def is_cjk(self) -> bool:
        return self.script in {
            Script.HAN_SIMPLIFIED,
            Script.HAN_TRADITIONAL,
            Script.KANA,
            Script.HANGUL,
        }


LANGUAGES: tuple[Language, ...] = (
    Language(
        "en",
        "English",
        "English",
        Script.LATIN,
        tesseract="eng",
        rapidocr="en",
        deepl_source="EN",
        deepl_target="EN-GB",
        google="en",
        yandex="en",
        azure="en",
        argos="en",
        density=1.0,
    ),
    Language(
        "ru",
        "Russian",
        "Русский",
        Script.CYRILLIC,
        tesseract="rus",
        rapidocr="ru",
        deepl_source="RU",
        deepl_target="RU",
        google="ru",
        yandex="ru",
        azure="ru",
        argos="ru",
        density=1.15,
    ),
    Language(
        "uk",
        "Ukrainian",
        "Українська",
        Script.CYRILLIC,
        tesseract="ukr",
        rapidocr="ru",
        deepl_source="UK",
        deepl_target="UK",
        google="uk",
        yandex="uk",
        azure="uk",
        argos="uk",
        density=1.15,
    ),
    Language(
        "be",
        "Belarusian",
        "Беларуская",
        Script.CYRILLIC,
        tesseract="bel",
        rapidocr="ru",
        google="be",
        yandex="be",
        azure="be",
        density=1.15,
    ),
    Language(
        "de",
        "German",
        "Deutsch",
        Script.LATIN,
        tesseract="deu",
        rapidocr="en",
        deepl_source="DE",
        deepl_target="DE",
        google="de",
        yandex="de",
        azure="de",
        argos="de",
        density=1.25,
    ),
    Language(
        "fr",
        "French",
        "Français",
        Script.LATIN,
        tesseract="fra",
        rapidocr="en",
        deepl_source="FR",
        deepl_target="FR",
        google="fr",
        yandex="fr",
        azure="fr",
        argos="fr",
        density=1.2,
    ),
    Language(
        "es",
        "Spanish",
        "Español",
        Script.LATIN,
        tesseract="spa",
        rapidocr="en",
        deepl_source="ES",
        deepl_target="ES",
        google="es",
        yandex="es",
        azure="es",
        argos="es",
        density=1.15,
    ),
    Language(
        "pt",
        "Portuguese",
        "Português",
        Script.LATIN,
        tesseract="por",
        rapidocr="en",
        deepl_source="PT",
        deepl_target="PT-PT",
        google="pt",
        yandex="pt",
        azure="pt",
        argos="pt",
        density=1.15,
    ),
    Language(
        "it",
        "Italian",
        "Italiano",
        Script.LATIN,
        tesseract="ita",
        rapidocr="en",
        deepl_source="IT",
        deepl_target="IT",
        google="it",
        yandex="it",
        azure="it",
        argos="it",
        density=1.15,
    ),
    Language(
        "pl",
        "Polish",
        "Polski",
        Script.LATIN,
        tesseract="pol",
        rapidocr="en",
        deepl_source="PL",
        deepl_target="PL",
        google="pl",
        yandex="pl",
        azure="pl",
        argos="pl",
        density=1.2,
    ),
    Language(
        "cs",
        "Czech",
        "Čeština",
        Script.LATIN,
        tesseract="ces",
        rapidocr="en",
        deepl_source="CS",
        deepl_target="CS",
        google="cs",
        yandex="cs",
        azure="cs",
        argos="cs",
        density=1.1,
    ),
    Language(
        "tr",
        "Turkish",
        "Türkçe",
        Script.LATIN,
        tesseract="tur",
        rapidocr="en",
        deepl_source="TR",
        deepl_target="TR",
        google="tr",
        yandex="tr",
        azure="tr",
        argos="tr",
        density=1.2,
    ),
    Language(
        "ar",
        "Arabic",
        "العربية",
        Script.ARABIC,
        direction=Direction.RTL,
        tesseract="ara",
        rapidocr="ar",
        deepl_source="AR",
        deepl_target="AR",
        google="ar",
        yandex="ar",
        azure="ar",
        argos="ar",
        density=0.9,
    ),
    Language(
        "he",
        "Hebrew",
        "עברית",
        Script.HEBREW,
        direction=Direction.RTL,
        tesseract="heb",
        google="iw",
        yandex="he",
        azure="he",
        argos="he",
        density=0.85,
    ),
    Language(
        "zh-Hans",
        "Chinese (Simplified)",
        "简体中文",
        Script.HAN_SIMPLIFIED,
        supports_vertical=True,
        tesseract="chi_sim",
        rapidocr="ch",
        deepl_source="ZH",
        deepl_target="ZH-HANS",
        google="zh-CN",
        yandex="zh",
        azure="zh-Hans",
        argos="zh",
        density=0.55,
        aliases=("zh", "zh-CN"),
    ),
    Language(
        "zh-Hant",
        "Chinese (Traditional)",
        "繁體中文",
        Script.HAN_TRADITIONAL,
        supports_vertical=True,
        tesseract="chi_tra",
        rapidocr="chinese_cht",
        deepl_source="ZH",
        deepl_target="ZH-HANT",
        google="zh-TW",
        yandex="zh",
        azure="zh-Hant",
        density=0.55,
        aliases=("zh-TW", "zh-HK"),
    ),
    Language(
        "ja",
        "Japanese",
        "日本語",
        Script.KANA,
        supports_vertical=True,
        tesseract="jpn",
        rapidocr="japan",
        deepl_source="JA",
        deepl_target="JA",
        google="ja",
        yandex="ja",
        azure="ja",
        argos="ja",
        density=0.6,
    ),
    Language(
        "ko",
        "Korean",
        "한국어",
        Script.HANGUL,
        supports_vertical=True,
        tesseract="kor",
        rapidocr="korean",
        deepl_source="KO",
        deepl_target="KO",
        google="ko",
        yandex="ko",
        azure="ko",
        argos="ko",
        density=0.7,
    ),
    Language(
        "hi",
        "Hindi",
        "हिन्दी",
        Script.DEVANAGARI,
        tesseract="hin",
        rapidocr="devanagari",
        google="hi",
        yandex="hi",
        azure="hi",
        argos="hi",
        density=1.0,
    ),
    Language(
        "id",
        "Indonesian",
        "Bahasa Indonesia",
        Script.LATIN,
        tesseract="ind",
        rapidocr="en",
        deepl_source="ID",
        deepl_target="ID",
        google="id",
        yandex="id",
        azure="id",
        argos="id",
        density=1.2,
    ),
    Language(
        "vi",
        "Vietnamese",
        "Tiếng Việt",
        Script.LATIN,
        tesseract="vie",
        rapidocr="en",
        google="vi",
        yandex="vi",
        azure="vi",
        density=1.15,
    ),
    Language(
        "th",
        "Thai",
        "ไทย",
        Script.THAI,
        tesseract="tha",
        google="th",
        yandex="th",
        azure="th",
        density=0.95,
    ),
    Language(
        "kk",
        "Kazakh",
        "Қазақша",
        Script.CYRILLIC,
        tesseract="kaz",
        rapidocr="ru",
        google="kk",
        yandex="kk",
        azure="kk",
        density=1.2,
    ),
    Language(
        "uz",
        "Uzbek",
        "Oʻzbekcha",
        Script.LATIN,
        tesseract="uzb",
        rapidocr="en",
        google="uz",
        yandex="uz",
        azure="uz",
        density=1.2,
    ),
    Language(
        "ka",
        "Georgian",
        "ქართული",
        Script.GEORGIAN,
        tesseract="kat",
        google="ka",
        azure="ka",
        density=1.1,
    ),
    Language(
        "hy",
        "Armenian",
        "Հայերեն",
        Script.ARMENIAN,
        tesseract="hye",
        google="hy",
        azure="hy",
        density=1.1,
    ),
)

BY_CODE: dict[str, Language] = {}
for _language in LANGUAGES:
    BY_CODE[_language.code.lower()] = _language
    for _alias in _language.aliases:
        BY_CODE.setdefault(_alias.lower(), _language)

#: The interface itself ships in these locales.
UI_LOCALES: tuple[str, ...] = ("en", "ru", "es", "de", "fr", "pt", "tr", "id", "pl", "uk")

AUTO = "auto"


def get(code: str | None) -> Language | None:
    if not code:
        return None
    normalized = code.strip().lower().replace("_", "-")
    if normalized in {AUTO, ""}:
        return None
    if normalized in BY_CODE:
        return BY_CODE[normalized]
    # "en-US" -> "en"
    base = normalized.split("-")[0]
    return BY_CODE.get(base)


def require(code: str) -> Language:
    language = get(code)
    if language is None:
        from picglot.core.errors import AppError, ErrorCode

        raise AppError(
            code=ErrorCode.LANGUAGE_NOT_SUPPORTED,
            details={"language": code, "supported": [item.code for item in LANGUAGES]},
        )
    return language


def normalize(code: str | None) -> str | None:
    language = get(code)
    return language.code if language else None


def is_supported(code: str | None) -> bool:
    return get(code) is not None


def tesseract_codes(codes: list[str]) -> str:
    """Build a Tesseract ``-l`` argument, always keeping English as a fallback."""
    resolved: list[str] = []
    for code in codes:
        language = get(code)
        if language and language.tesseract and language.tesseract not in resolved:
            resolved.append(language.tesseract)
    if not resolved:
        resolved = ["eng"]
    elif "eng" not in resolved:
        resolved.append("eng")
    return "+".join(resolved)


def provider_code(code: str, provider: str, *, target: bool = False) -> str | None:
    language = get(code)
    if language is None:
        return None
    return {
        "deepl": language.deepl_target if target else language.deepl_source,
        "google_translate": language.google,
        "yandex_translate": language.yandex,
        "azure_translator": language.azure,
        "argos": language.argos,
        "rapidocr": language.rapidocr,
        "tesseract": language.tesseract,
    }.get(provider)


def supported_pairs(limit: int | None = None) -> list[tuple[str, str]]:
    """Language pairs advertised on SEO pages (any supported source to any target)."""
    pairs = [
        (source.code, target.code)
        for source in LANGUAGES
        for target in LANGUAGES
        if source.code != target.code
    ]
    return pairs[:limit] if limit else pairs


def as_dicts() -> list[dict[str, object]]:
    return [
        {
            "code": language.code,
            "name_en": language.name_en,
            "name_native": language.name_native,
            "script": str(language.script),
            "direction": str(language.direction),
            "rtl": language.is_rtl,
            "cjk": language.is_cjk,
            "supports_vertical": language.supports_vertical,
            "ocr": bool(language.tesseract or language.rapidocr),
            "translation": bool(
                language.deepl_target or language.google or language.yandex or language.azure
            ),
            "offline_translation": bool(language.argos),
        }
        for language in LANGUAGES
    ]


#: Unicode ranges used for script detection when no OCR language hint exists.
SCRIPT_RANGES: tuple[tuple[Script, tuple[int, int]], ...] = (
    (Script.CYRILLIC, (0x0400, 0x04FF)),
    (Script.ARABIC, (0x0600, 0x06FF)),
    (Script.HEBREW, (0x0590, 0x05FF)),
    (Script.DEVANAGARI, (0x0900, 0x097F)),
    (Script.THAI, (0x0E00, 0x0E7F)),
    (Script.GEORGIAN, (0x10A0, 0x10FF)),
    (Script.ARMENIAN, (0x0530, 0x058F)),
    (Script.HANGUL, (0xAC00, 0xD7AF)),
    (Script.KANA, (0x3040, 0x30FF)),
    (Script.HAN_SIMPLIFIED, (0x4E00, 0x9FFF)),
)


def detect_script(text: str) -> Script:
    """Majority script of a string; Latin when nothing else dominates."""
    counts: dict[Script, int] = {}
    latin = 0
    for char in text:
        point = ord(char)
        if (0x41 <= point <= 0x5A) or (0x61 <= point <= 0x7A) or (0x00C0 <= point <= 0x024F):
            latin += 1
            continue
        for script, (low, high) in SCRIPT_RANGES:
            if low <= point <= high:
                counts[script] = counts.get(script, 0) + 1
                break
    if not counts:
        return Script.LATIN
    best_script, best_count = max(counts.items(), key=lambda item: item[1])
    return best_script if best_count >= latin else Script.LATIN


def guess_language(text: str, hints: list[str] | None = None) -> str | None:
    """Cheap script-based guess. Returns ``None`` when the script is ambiguous."""
    if not text or not text.strip():
        return None
    script = detect_script(text)
    candidates = [language for language in LANGUAGES if language.script is script]
    if not candidates:
        return None
    if hints:
        for hint in hints:
            hinted = get(hint)
            if hinted and hinted.script is script:
                return hinted.code
    # Latin is shared by too many languages to guess from the script alone.
    if script is Script.LATIN:
        return None
    return candidates[0].code
