"""LLM translation adapter.

Useful where a phrase-based engine loses the plot: UI strings without context,
marketing copy, mixed-script pages, or when a glossary must be respected across
a whole document. Source text is passed as untrusted data (see providers/llm.py)
and the reply must match a strict schema.
"""

from __future__ import annotations

import time
from typing import Any

from lingoimage.core.errors import AppError, ErrorCode
from lingoimage.core.logging import get_logger
from lingoimage.core.metrics import translation_characters_total
from lingoimage.domain import languages
from lingoimage.providers.base import HealthState, ProviderHealth
from lingoimage.providers.llm import LlmMessage, get_llm
from lingoimage.providers.translation.base import (
    TranslationProvider,
    TranslationRequest,
    TranslationResult,
    protect,
)

log = get_logger(__name__)

TRANSLATION_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["translations"],
    "properties": {
        "detected_source_language": {"type": ["string", "null"]},
        "translations": {
            "type": "array",
            "maxItems": 200,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["index", "text"],
                "properties": {
                    "index": {"type": "integer", "minimum": 0},
                    "text": {"type": "string", "maxLength": 8000},
                    "alternative": {"type": ["string", "null"], "maxLength": 8000},
                },
            },
        },
    },
}


class LlmTranslationProvider(TranslationProvider):
    name = "llm"
    local = False
    supports_formality = True
    supports_glossary = True
    max_batch = 40
    cost_per_unit_micro_usd = 60

    def available(self) -> bool:
        return get_llm().available()

    def health(self) -> ProviderHealth:
        upstream = get_llm().health()
        if upstream.state is not HealthState.HEALTHY:
            return ProviderHealth(self.name, self.kind, upstream.state, upstream.detail)
        return super().health()

    def translate(self, request: TranslationRequest) -> TranslationResult:
        started = time.perf_counter()
        protections = [protect(text, request.do_not_translate) for text in request.texts]

        target = languages.require(request.target_language)
        source = languages.get(request.source_language) if request.source_language else None

        rules = [
            f"Translate each numbered segment into {target.name_en} ({target.code}).",
            "Return the same number of segments, matched by index.",
            "Preserve every ⟦n⟧ placeholder exactly as-is, in a natural position.",
            "Keep the original tone, capitalisation style and punctuation conventions.",
            "Do not add commentary, notes or explanations to the translation.",
            "If a segment is already in the target language, return it unchanged.",
        ]
        if source:
            rules.insert(0, f"The source language is {source.name_en} ({source.code}).")
        if request.formality == "formal":
            rules.append("Use a formal register (e.g. Sie / vous / Вы).")
        elif request.formality == "informal":
            rules.append("Use an informal register (e.g. du / tu / ты).")
        if request.glossary_terms:
            pairs = "; ".join(
                f"{source_term} → {target_term}"
                for source_term, target_term in list(request.glossary_terms.items())[:80]
            )
            rules.append(f"Apply this glossary exactly: {pairs}.")
        if request.do_not_translate:
            rules.append(
                "Leave these terms untranslated: "
                + ", ".join(sorted(request.do_not_translate)[:60])
            )
        if request.context:
            rules.append(f"Surrounding page context (for disambiguation only): {request.context}")

        numbered = "\n".join(
            f"{index}. {protection.masked}" for index, protection in enumerate(protections)
        )

        result = get_llm().extract_json(
            instruction=" ".join(rules),
            schema=TRANSLATION_SCHEMA,
            message=LlmMessage(text=numbered),
        )

        by_index: dict[int, dict[str, Any]] = {}
        for item in result.data.get("translations", []):
            try:
                by_index[int(item["index"])] = item
            except (KeyError, TypeError, ValueError):
                continue

        if not by_index:
            raise AppError(
                code=ErrorCode.PROVIDER_REJECTED,
                details={"provider": self.name},
                internal="model returned no translations",
            )

        texts: list[str] = []
        alternatives: list[list[str]] = []
        for index, protection in enumerate(protections):
            item = by_index.get(index)
            if item is None:
                # Never silently substitute the source text for a translation.
                raise AppError(
                    code=ErrorCode.PROVIDER_REJECTED,
                    details={"provider": self.name, "missing_segment": index},
                    internal="model skipped a segment",
                )
            texts.append(protection.restore(str(item.get("text", ""))))
            alternative = item.get("alternative")
            alternatives.append([protection.restore(str(alternative))] if alternative else [])

        characters = sum(len(text) for text in request.texts)
        translation_characters_total.labels(provider=self.name).inc(characters)

        return TranslationResult(
            texts=texts,
            provider=self.name,
            model=result.model,
            source_language=languages.normalize(result.data.get("detected_source_language"))
            or request.source_language,
            character_count=characters,
            cost_micro_usd=result.cost_micro_usd,
            duration_ms=int((time.perf_counter() - started) * 1000),
            alternatives=alternatives,
            metadata={
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "attempts": result.attempts,
            },
        )
