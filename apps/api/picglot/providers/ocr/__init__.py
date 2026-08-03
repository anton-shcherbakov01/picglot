"""OCR provider registry and selection.

Selection is: take the configured priority order, keep the providers that are
available and permitted, and run the chain until one succeeds. Handwriting and
table extraction have their own orders because the best engine differs.
"""

from __future__ import annotations

import functools
from typing import Any

from PIL import Image

from picglot.core.config import settings
from picglot.core.errors import AppError, ErrorCode
from picglot.core.logging import get_logger
from picglot.providers.base import ChainResult, ProviderKind, run_chain
from picglot.providers.ocr.base import OcrProvider, OcrRequest
from picglot.providers.ocr.cloud import (
    AwsTextractProvider,
    AzureVisionProvider,
    GoogleVisionProvider,
    YandexVisionProvider,
)
from picglot.providers.ocr.llm import LlmOcrProvider
from picglot.providers.ocr.local import RapidOcrProvider, TesseractProvider
from picglot.vision.types import OcrOutcome

log = get_logger(__name__)

_REGISTRY: dict[str, type[OcrProvider]] = {
    "rapidocr": RapidOcrProvider,
    "tesseract": TesseractProvider,
    "google_vision": GoogleVisionProvider,
    "azure_vision": AzureVisionProvider,
    "aws_textract": AwsTextractProvider,
    "yandex_vision": YandexVisionProvider,
    "llm": LlmOcrProvider,
}


@functools.cache
def get_provider(name: str) -> OcrProvider:
    provider_class = _REGISTRY.get(name)
    if provider_class is None:
        raise AppError(
            code=ErrorCode.VALIDATION_FAILED,
            details={"provider": name, "known": sorted(_REGISTRY)},
            internal=f"unknown OCR provider {name!r}",
        )
    return provider_class()


def reset_providers() -> None:
    get_provider.cache_clear()


def available_providers() -> list[OcrProvider]:
    providers = []
    for name in _REGISTRY:
        try:
            provider = get_provider(name)
        except AppError:  # pragma: no cover
            continue
        providers.append(provider)
    return providers


def chain_for(purpose: str = "text") -> list[OcrProvider]:
    """Providers to try for a purpose, best first.

    ``builtin`` in the table priority list is not a provider — it means "read
    the cells with the ordinary text chain and recover the grid ourselves"
    (see ``vision/tables.py``). It therefore expands to the text chain rather
    than being skipped, which would otherwise leave the list empty on an
    installation with no cloud table provider configured.
    """
    order = {
        "text": settings.ocr_provider_priority,
        "handwriting": settings.ocr_handwriting_provider_priority,
        "table": settings.ocr_table_provider_priority,
    }.get(purpose, settings.ocr_provider_priority)

    names: list[str] = []
    for name in order:
        if name == "builtin":
            names.extend(item for item in settings.ocr_provider_priority if item not in names)
        elif name not in names:
            names.append(name)

    providers: list[OcrProvider] = []
    for name in names:
        try:
            provider = get_provider(name)
        except AppError:
            log.warning("ocr.unknown_provider_configured", provider=name)
            continue
        if provider.available():
            providers.append(provider)

    # A purpose-specific list that ends up empty falls back to the text chain
    # rather than failing the job outright.
    if not providers and purpose != "text":
        return chain_for("text")
    return providers


def recognize(
    image: Image.Image,
    *,
    languages: list[str] | None = None,
    purpose: str = "text",
    handwriting: bool = False,
    hints: dict[str, Any] | None = None,
) -> ChainResult:
    """Run OCR through the configured provider chain."""
    request = OcrRequest(
        image=image,
        languages=languages or [],
        handwriting=handwriting or purpose == "handwriting",
        hints=hints or {},
    )
    providers = chain_for("handwriting" if request.handwriting else purpose)
    if not providers:
        raise AppError(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            details={"purpose": purpose},
            internal=(
                "no OCR provider is available; install the [ocr] extra or configure a cloud "
                "provider (see docs/operations/configuration.md)"
            ),
        )
    return run_chain(
        providers,  # type: ignore[arg-type]
        operation=f"ocr:{purpose}",
        call=lambda provider: provider.recognize(request),
        kind=ProviderKind.OCR,
    )


def health_report() -> list[dict[str, Any]]:
    return [provider.health().as_dict() for provider in available_providers()]


__all__ = [
    "OcrOutcome",
    "OcrProvider",
    "OcrRequest",
    "available_providers",
    "chain_for",
    "get_provider",
    "health_report",
    "recognize",
    "reset_providers",
]
