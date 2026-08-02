"""Multimodal-LLM OCR adapter.

Used where classical engines struggle: handwriting, stylised posters, low
contrast photographs and mixed-script pages. The model returns line boxes in a
strict schema and its output is treated as untrusted data throughout.
"""

from __future__ import annotations

import time
from typing import Any

from lingoimage.core.errors import AppError, ErrorCode
from lingoimage.core.logging import get_logger
from lingoimage.providers.base import HealthState, ProviderHealth
from lingoimage.providers.llm import LlmMessage, get_llm
from lingoimage.providers.ocr.base import OcrProvider, OcrRequest, average_confidence, make_region
from lingoimage.vision.types import OcrOutcome, Region

log = get_logger(__name__)

OCR_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["lines", "detected_language"],
    "properties": {
        "detected_language": {
            "type": ["string", "null"],
            "description": "BCP-47 code of the dominant language, or null if unclear",
        },
        "lines": {
            "type": "array",
            "maxItems": 400,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "box", "confidence"],
                "properties": {
                    "text": {"type": "string", "maxLength": 2000},
                    "box": {
                        "type": "array",
                        "minItems": 4,
                        "maxItems": 4,
                        "items": {"type": "number", "minimum": 0, "maximum": 1},
                        "description": "[x, y, width, height] as fractions of the image size",
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "handwritten": {"type": "boolean"},
                },
            },
        },
    },
}

INSTRUCTION = (
    "Transcribe every visible line of text in the image. Preserve the original "
    "spelling, punctuation, casing and digits exactly — do not translate, correct "
    "or summarise. Give each line a bounding box as fractions of the image size and "
    "a confidence between 0 and 1. If a line is unreadable, omit it rather than "
    "guessing. Return no other keys."
)


class LlmOcrProvider(OcrProvider):
    name = "llm"
    local = False
    supports_handwriting = True
    returns_geometry = True

    def available(self) -> bool:
        return get_llm().available()

    def health(self) -> ProviderHealth:
        upstream = get_llm().health()
        if upstream.state is not HealthState.HEALTHY:
            return ProviderHealth(self.name, self.kind, upstream.state, upstream.detail)
        return super().health()

    def recognize(self, request: OcrRequest) -> OcrOutcome:
        started = time.perf_counter()
        width, height = request.image.size

        hint = ""
        if request.languages:
            hint = f"Expected language(s): {', '.join(request.languages)}. "
        if request.handwriting:
            hint += "The page contains handwriting. "

        result = get_llm().extract_json(
            instruction=INSTRUCTION + (f" {hint}" if hint else ""),
            schema=OCR_SCHEMA,
            message=LlmMessage(text="Transcribe the attached image.", images=[request.image]),
        )

        regions: list[Region] = []
        for line in result.data.get("lines", []):
            text = str(line.get("text", "")).strip()
            box = line.get("box") or []
            if not text or len(box) != 4:
                continue
            try:
                x, y, box_width, box_height = (float(value) for value in box)
            except (TypeError, ValueError):
                continue
            left, top = x * width, y * height
            right, bottom = left + box_width * width, top + box_height * height
            if right <= left or bottom <= top:
                continue
            regions.append(
                make_region(
                    [(left, top), (right, top), (right, bottom), (left, bottom)],
                    text,
                    _clamp(line.get("confidence")),
                    metadata={
                        "engine": "llm",
                        "model": result.model,
                        "handwritten": bool(line.get("handwritten")),
                    },
                )
            )

        if not regions:
            raise AppError(
                code=ErrorCode.NO_TEXT_DETECTED,
                details={"provider": self.name},
                internal="model returned no usable lines",
            )

        return OcrOutcome(
            regions=regions,
            provider=self.name,
            model=result.model,
            confidence=average_confidence(regions),
            duration_ms=int((time.perf_counter() - started) * 1000),
            detected_language=_normalize_language(result.data.get("detected_language")),
            cost_micro_usd=result.cost_micro_usd,
            metadata={
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "attempts": result.attempts,
            },
        )


def _clamp(value: Any) -> float | None:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _normalize_language(value: Any) -> str | None:
    from lingoimage.domain import languages

    if not isinstance(value, str):
        return None
    return languages.normalize(value)


__all__ = ["OCR_SCHEMA", "LlmOcrProvider"]
