"""Cloud translation adapters: DeepL, Google, Yandex, Azure."""

from __future__ import annotations

import time
from typing import Any

import httpx

from picglot.core.config import settings
from picglot.core.errors import AppError, ErrorCode
from picglot.core.logging import get_logger
from picglot.core.metrics import translation_characters_total
from picglot.domain import languages
from picglot.providers.base import HealthState, ProviderHealth
from picglot.providers.translation.base import (
    TranslationProvider,
    TranslationRequest,
    TranslationResult,
    protect,
)

log = get_logger(__name__)


def _http_error(provider: str, exc: Exception) -> AppError:
    if isinstance(exc, httpx.TimeoutException):
        return AppError(code=ErrorCode.PROVIDER_TIMEOUT, details={"provider": provider})
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 429:
        return AppError(
            code=ErrorCode.RATE_LIMIT_EXCEEDED,
            details={"provider": provider},
            internal="provider rate limit",
        )
    if status in {400, 422}:
        return AppError(
            code=ErrorCode.PROVIDER_REJECTED,
            details={"provider": provider, "status": status},
            internal=str(exc)[:250],
        )
    return AppError(
        code=ErrorCode.PROVIDER_UNAVAILABLE,
        details={"provider": provider, "status": status},
        internal=str(exc)[:250],
    )


class _HttpTranslationProvider(TranslationProvider):
    """Shared placeholder masking and accounting for the HTTP adapters."""

    def _prepare(self, request: TranslationRequest) -> tuple[list[str], list[Any]]:
        protections = [protect(text, request.do_not_translate) for text in request.texts]
        return [item.masked for item in protections], protections

    def _finalise(
        self,
        translated: list[str],
        protections: list[Any],
        request: TranslationRequest,
        *,
        model: str,
        started: float,
        source_language: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TranslationResult:
        restored = [
            protection.restore(text)
            for text, protection in zip(translated, protections, strict=False)
        ]
        characters = sum(len(text) for text in request.texts)
        translation_characters_total.labels(provider=self.name).inc(characters)
        cost = self.charge(characters)
        return TranslationResult(
            texts=restored,
            provider=self.name,
            model=model,
            source_language=source_language or request.source_language,
            character_count=characters,
            cost_micro_usd=cost,
            duration_ms=int((time.perf_counter() - started) * 1000),
            metadata=metadata or {},
        )


class DeeplProvider(_HttpTranslationProvider):
    name = "deepl"
    local = False
    supports_formality = True
    supports_glossary = True
    max_batch = 50
    cost_per_unit_micro_usd = 20  # ≈ $20 per million characters

    def available(self) -> bool:
        return bool(settings.deepl_api_key)

    def supports_pair(self, source: str | None, target: str) -> bool:
        return languages.provider_code(target, "deepl", target=True) is not None

    def health(self) -> ProviderHealth:
        if not settings.deepl_api_key:
            return ProviderHealth(
                self.name, self.kind, HealthState.NOT_CONFIGURED, "DEEPL_API_KEY is empty"
            )
        return super().health()

    def translate(self, request: TranslationRequest) -> TranslationResult:
        started = time.perf_counter()
        masked, protections = self._prepare(request)

        payload: dict[str, Any] = {
            "text": masked,
            "target_lang": languages.provider_code(request.target_language, "deepl", target=True),
            "tag_handling": "xml",
            "ignore_tags": ["x"],
        }
        if request.source_language:
            source = languages.provider_code(request.source_language, "deepl")
            if source:
                payload["source_lang"] = source
        if request.formality in {"formal", "informal"}:
            payload["formality"] = "prefer_more" if request.formality == "formal" else "prefer_less"

        try:
            response = httpx.post(
                f"{settings.deepl_api_url.rstrip('/')}/translate",
                json=payload,
                headers={"Authorization": f"DeepL-Auth-Key {settings.deepl_api_key}"},
                timeout=settings.translation_timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise _http_error(self.name, exc) from exc

        translations = data.get("translations", [])
        texts = [item.get("text", "") for item in translations]
        detected = translations[0].get("detected_source_language") if translations else None

        return self._finalise(
            texts,
            protections,
            request,
            model="deepl",
            started=started,
            source_language=languages.normalize(detected) if detected else None,
        )


class GoogleTranslateProvider(_HttpTranslationProvider):
    name = "google_translate"
    local = False
    max_batch = 100
    cost_per_unit_micro_usd = 20

    ENDPOINT = "https://translation.googleapis.com/language/translate/v2"

    def available(self) -> bool:
        return bool(settings.google_translate_api_key or settings.google_vision_credentials_json)

    def supports_pair(self, source: str | None, target: str) -> bool:
        return languages.provider_code(target, "google_translate") is not None

    def health(self) -> ProviderHealth:
        if not self.available():
            return ProviderHealth(
                self.name,
                self.kind,
                HealthState.NOT_CONFIGURED,
                "GOOGLE_TRANSLATE_API_KEY or a service account is required",
            )
        return super().health()

    def translate(self, request: TranslationRequest) -> TranslationResult:
        started = time.perf_counter()
        masked, protections = self._prepare(request)

        params: dict[str, Any] = {}
        headers: dict[str, str] = {}
        if settings.google_translate_api_key:
            params["key"] = settings.google_translate_api_key
        else:
            from picglot.providers.google_auth import access_token

            headers["Authorization"] = f"Bearer {access_token()}"

        body: dict[str, Any] = {
            "q": masked,
            "target": languages.provider_code(request.target_language, "google_translate"),
            "format": "text",
        }
        if request.source_language:
            source = languages.provider_code(request.source_language, "google_translate")
            if source:
                body["source"] = source

        try:
            response = httpx.post(
                self.ENDPOINT,
                params=params,
                headers=headers,
                json=body,
                timeout=settings.translation_timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise _http_error(self.name, exc) from exc

        items = data.get("data", {}).get("translations", [])
        texts = [item.get("translatedText", "") for item in items]
        detected = items[0].get("detectedSourceLanguage") if items else None

        return self._finalise(
            texts,
            protections,
            request,
            model="google-translate-v2",
            started=started,
            source_language=languages.normalize(detected) if detected else None,
        )


class YandexTranslateProvider(_HttpTranslationProvider):
    name = "yandex_translate"
    local = False
    max_batch = 100
    cost_per_unit_micro_usd = 15

    ENDPOINT = "https://translate.api.cloud.yandex.net/translate/v2/translate"

    def available(self) -> bool:
        return bool(settings.yandex_translate_api_key and settings.yandex_translate_folder_id)

    def supports_pair(self, source: str | None, target: str) -> bool:
        return languages.provider_code(target, "yandex_translate") is not None

    def health(self) -> ProviderHealth:
        if not self.available():
            return ProviderHealth(
                self.name,
                self.kind,
                HealthState.NOT_CONFIGURED,
                "YANDEX_TRANSLATE_API_KEY / YANDEX_TRANSLATE_FOLDER_ID are required",
            )
        return super().health()

    def translate(self, request: TranslationRequest) -> TranslationResult:
        started = time.perf_counter()
        masked, protections = self._prepare(request)

        body: dict[str, Any] = {
            "folderId": settings.yandex_translate_folder_id,
            "texts": masked,
            "targetLanguageCode": languages.provider_code(
                request.target_language, "yandex_translate"
            ),
            "format": "PLAIN_TEXT",
        }
        if request.source_language:
            source = languages.provider_code(request.source_language, "yandex_translate")
            if source:
                body["sourceLanguageCode"] = source

        try:
            response = httpx.post(
                self.ENDPOINT,
                json=body,
                headers={"Authorization": f"Api-Key {settings.yandex_translate_api_key}"},
                timeout=settings.translation_timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise _http_error(self.name, exc) from exc

        items = data.get("translations", [])
        texts = [item.get("text", "") for item in items]
        detected = items[0].get("detectedLanguageCode") if items else None

        return self._finalise(
            texts,
            protections,
            request,
            model="yandex-translate-v2",
            started=started,
            source_language=languages.normalize(detected) if detected else None,
        )


class AzureTranslatorProvider(_HttpTranslationProvider):
    name = "azure_translator"
    local = False
    max_batch = 100
    cost_per_unit_micro_usd = 10

    def available(self) -> bool:
        return bool(settings.azure_translator_key)

    def supports_pair(self, source: str | None, target: str) -> bool:
        return languages.provider_code(target, "azure_translator") is not None

    def health(self) -> ProviderHealth:
        if not settings.azure_translator_key:
            return ProviderHealth(
                self.name, self.kind, HealthState.NOT_CONFIGURED, "AZURE_TRANSLATOR_KEY is empty"
            )
        return super().health()

    def translate(self, request: TranslationRequest) -> TranslationResult:
        started = time.perf_counter()
        masked, protections = self._prepare(request)

        params: dict[str, Any] = {
            "api-version": "3.0",
            "to": languages.provider_code(request.target_language, "azure_translator"),
            "textType": "plain",
        }
        if request.source_language:
            source = languages.provider_code(request.source_language, "azure_translator")
            if source:
                params["from"] = source

        headers = {"Ocp-Apim-Subscription-Key": settings.azure_translator_key}
        if settings.azure_translator_region:
            headers["Ocp-Apim-Subscription-Region"] = settings.azure_translator_region

        try:
            response = httpx.post(
                f"{settings.azure_translator_endpoint.rstrip('/')}/translate",
                params=params,
                headers=headers,
                json=[{"Text": text} for text in masked],
                timeout=settings.translation_timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise _http_error(self.name, exc) from exc

        texts = [item.get("translations", [{}])[0].get("text", "") for item in data]
        detected = data[0].get("detectedLanguage", {}).get("language") if data else None

        return self._finalise(
            texts,
            protections,
            request,
            model="azure-translator-3.0",
            started=started,
            source_language=languages.normalize(detected) if detected else None,
        )
