"""Translation provider registry, cache, glossary and translation-memory pipeline.

Order of resolution for every segment (the editor shows which one was used):

    1. glossary exact match / do-not-translate
    2. translation memory exact match
    3. translation memory fuzzy match above threshold
    4. provider cache (same text, same pair, same glossary version)
    5. provider call
"""

from __future__ import annotations

import functools
from typing import Any

from picglot.core.config import settings
from picglot.core.errors import AppError, ErrorCode
from picglot.core.logging import get_logger
from picglot.providers.base import ChainResult, ProviderKind, run_chain
from picglot.providers.translation.base import (
    TranslationProvider,
    TranslationRequest,
    TranslationResult,
)
from picglot.providers.translation.cloud import (
    AzureTranslatorProvider,
    DeeplProvider,
    GoogleTranslateProvider,
    YandexTranslateProvider,
)
from picglot.providers.translation.llm import LlmTranslationProvider
from picglot.providers.translation.local import ArgosProvider, EchoProvider

log = get_logger(__name__)

_REGISTRY: dict[str, type[TranslationProvider]] = {
    "deepl": DeeplProvider,
    "google_translate": GoogleTranslateProvider,
    "yandex_translate": YandexTranslateProvider,
    "azure_translator": AzureTranslatorProvider,
    "llm": LlmTranslationProvider,
    "argos": ArgosProvider,
    "echo": EchoProvider,
}


@functools.cache
def get_provider(name: str) -> TranslationProvider:
    provider_class = _REGISTRY.get(name)
    if provider_class is None:
        raise AppError(
            code=ErrorCode.VALIDATION_FAILED,
            details={"provider": name, "known": sorted(_REGISTRY)},
            internal=f"unknown translation provider {name!r}",
        )
    return provider_class()


def reset_providers() -> None:
    get_provider.cache_clear()


def available_providers() -> list[TranslationProvider]:
    providers: list[TranslationProvider] = []
    for name in _REGISTRY:
        if name == "echo" and not settings.is_test:
            continue
        try:
            providers.append(get_provider(name))
        except AppError:  # pragma: no cover
            continue
    return providers


def chain(source: str | None, target: str) -> list[TranslationProvider]:
    providers: list[TranslationProvider] = []
    for name in settings.translation_provider_priority:
        try:
            provider = get_provider(name)
        except AppError:
            log.warning("translation.unknown_provider_configured", provider=name)
            continue
        if provider.available() and provider.supports_pair(source, target):
            providers.append(provider)
    return providers


def translate(request: TranslationRequest) -> ChainResult:
    providers = chain(request.source_language, request.target_language)
    if not providers:
        raise AppError(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            details={
                "source": request.source_language,
                "target": request.target_language,
                "configured": settings.translation_provider_priority,
            },
            internal=(
                "no translation provider is configured for this pair — set DEEPL_API_KEY, "
                "GOOGLE_TRANSLATE_API_KEY, YANDEX_TRANSLATE_API_KEY, AZURE_TRANSLATOR_KEY, "
                "an LLM key, or install the [translate-local] extra"
            ),
        )
    return run_chain(
        providers,  # type: ignore[arg-type]
        operation="translate",
        call=lambda provider: provider.translate(request),
        kind=ProviderKind.TRANSLATION,
    )


def health_report() -> list[dict[str, Any]]:
    return [provider.health().as_dict() for provider in available_providers()]


def any_provider_configured() -> bool:
    """Drives the honest "translation needs configuration" notice in the UI."""
    return any(provider.available() for provider in available_providers())


__all__ = [
    "TranslationProvider",
    "TranslationRequest",
    "TranslationResult",
    "any_provider_configured",
    "available_providers",
    "chain",
    "get_provider",
    "health_report",
    "reset_providers",
    "translate",
]
