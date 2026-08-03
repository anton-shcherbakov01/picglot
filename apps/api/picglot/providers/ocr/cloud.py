"""Cloud OCR adapters: Google Vision, Azure AI Vision, AWS Textract, Yandex Vision.

All four are optional. Each is disabled unless both its credentials *and* its
``*_ENABLED`` flag are set, so an accidental key in the environment cannot start
sending documents off-machine.
"""

from __future__ import annotations

import base64
import time
from typing import Any

import httpx

from picglot.core.config import settings
from picglot.core.errors import AppError, ErrorCode
from picglot.core.logging import get_logger
from picglot.domain import languages as language_table
from picglot.providers.base import HealthState, ProviderHealth
from picglot.providers.ocr.base import OcrProvider, OcrRequest, average_confidence, make_region
from picglot.vision.types import OcrOutcome, Region

log = get_logger(__name__)


def _http_error(provider: str, exc: Exception) -> AppError:
    if isinstance(exc, httpx.TimeoutException):
        return AppError(
            code=ErrorCode.PROVIDER_TIMEOUT,
            details={"provider": provider},
            internal=str(exc)[:200],
        )
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in {400, 415, 422}:
        return AppError(
            code=ErrorCode.PROVIDER_REJECTED,
            details={"provider": provider, "status": status},
            internal=str(exc)[:300],
        )
    return AppError(
        code=ErrorCode.PROVIDER_UNAVAILABLE,
        details={"provider": provider, "status": status},
        internal=str(exc)[:300],
    )


class GoogleVisionProvider(OcrProvider):
    """Google Cloud Vision ``DOCUMENT_TEXT_DETECTION``."""

    name = "google_vision"
    local = False
    supports_handwriting = True
    cost_per_unit_micro_usd = 1500  # ≈ $1.50 / 1000 pages

    ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"

    def available(self) -> bool:
        return settings.google_vision_enabled and bool(settings.google_vision_credentials_json)

    def health(self) -> ProviderHealth:
        if not settings.google_vision_enabled:
            return ProviderHealth(
                self.name, self.kind, HealthState.NOT_CONFIGURED, "GOOGLE_VISION_ENABLED is false"
            )
        if not settings.google_vision_credentials_json:
            return ProviderHealth(
                self.name,
                self.kind,
                HealthState.NOT_CONFIGURED,
                "GOOGLE_VISION_CREDENTIALS_JSON is empty",
            )
        return super().health()

    def recognize(self, request: OcrRequest) -> OcrOutcome:
        from picglot.providers.google_auth import access_token

        started = time.perf_counter()
        hints = [
            code
            for code in (
                language_table.provider_code(language, "google_translate")
                for language in request.languages
            )
            if code
        ]
        body = {
            "requests": [
                {
                    "image": {"content": base64.b64encode(request.to_png_bytes()).decode("ascii")},
                    "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                    "imageContext": {"languageHints": hints} if hints else {},
                }
            ]
        }
        try:
            response = httpx.post(
                self.ENDPOINT,
                json=body,
                headers={"Authorization": f"Bearer {access_token()}"},
                timeout=settings.ocr_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise _http_error(self.name, exc) from exc

        responses = payload.get("responses") or [{}]
        first = responses[0]
        if "error" in first:
            raise AppError(
                code=ErrorCode.PROVIDER_REJECTED,
                details={"provider": self.name},
                internal=str(first["error"])[:300],
            )

        regions = _regions_from_google(first)
        self.charge(1)
        return OcrOutcome(
            regions=regions,
            provider=self.name,
            model="vision:document_text_detection",
            confidence=average_confidence(regions),
            duration_ms=int((time.perf_counter() - started) * 1000),
            detected_language=_google_language(first),
            cost_micro_usd=self.cost_per_unit_micro_usd,
        )


def _regions_from_google(payload: dict[str, Any]) -> list[Region]:
    annotation = payload.get("fullTextAnnotation") or {}
    regions: list[Region] = []
    for page in annotation.get("pages", []):
        for block in page.get("blocks", []):
            for paragraph in block.get("paragraphs", []):
                words: list[str] = []
                for word in paragraph.get("words", []):
                    symbols = "".join(symbol.get("text", "") for symbol in word.get("symbols", []))
                    words.append(symbols)
                    break_type = (
                        word.get("symbols", [{}])[-1]
                        .get("property", {})
                        .get("detectedBreak", {})
                        .get("type")
                    )
                    if break_type in {"SPACE", "EOL_SURE_SPACE"}:
                        words.append(" ")
                    elif break_type in {"LINE_BREAK", "SURE_SPACE"}:
                        words.append("\n")
                text = "".join(words).strip()
                if not text:
                    continue
                vertices = paragraph.get("boundingBox", {}).get("vertices", [])
                polygon = [(item.get("x", 0), item.get("y", 0)) for item in vertices]
                regions.append(
                    make_region(
                        polygon,
                        text,
                        paragraph.get("confidence"),
                        metadata={"engine": "google_vision"},
                    )
                )
    return regions


def _google_language(payload: dict[str, Any]) -> str | None:
    pages = (payload.get("fullTextAnnotation") or {}).get("pages", [])
    for page in pages:
        detected = page.get("property", {}).get("detectedLanguages", [])
        if detected:
            return language_table.normalize(detected[0].get("languageCode"))
    return None


class AzureVisionProvider(OcrProvider):
    """Azure AI Vision 4.0 Read (``imageanalysis:analyze?features=read``)."""

    name = "azure_vision"
    local = False
    supports_handwriting = True
    cost_per_unit_micro_usd = 1000

    def available(self) -> bool:
        return bool(
            settings.azure_vision_enabled
            and settings.azure_vision_endpoint
            and settings.azure_vision_key
        )

    def health(self) -> ProviderHealth:
        if not settings.azure_vision_enabled:
            return ProviderHealth(
                self.name, self.kind, HealthState.NOT_CONFIGURED, "AZURE_VISION_ENABLED is false"
            )
        if not (settings.azure_vision_endpoint and settings.azure_vision_key):
            return ProviderHealth(
                self.name,
                self.kind,
                HealthState.NOT_CONFIGURED,
                "AZURE_VISION_ENDPOINT / AZURE_VISION_KEY are required",
            )
        return super().health()

    def recognize(self, request: OcrRequest) -> OcrOutcome:
        started = time.perf_counter()
        endpoint = settings.azure_vision_endpoint.rstrip("/")
        url = f"{endpoint}/computervision/imageanalysis:analyze"
        params = {"api-version": "2024-02-01", "features": "read"}
        if request.languages:
            code = language_table.provider_code(request.languages[0], "azure_translator")
            if code:
                params["language"] = code

        try:
            response = httpx.post(
                url,
                params=params,
                content=request.to_png_bytes(),
                headers={
                    "Ocp-Apim-Subscription-Key": settings.azure_vision_key,
                    "Content-Type": "application/octet-stream",
                },
                timeout=settings.ocr_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise _http_error(self.name, exc) from exc

        regions: list[Region] = []
        for block in (payload.get("readResult") or {}).get("blocks", []):
            for line in block.get("lines", []):
                text = str(line.get("text", "")).strip()
                if not text:
                    continue
                polygon = [
                    (point.get("x", 0), point.get("y", 0))
                    for point in line.get("boundingPolygon", [])
                ]
                confidences = [
                    word.get("confidence")
                    for word in line.get("words", [])
                    if word.get("confidence") is not None
                ]
                regions.append(
                    make_region(
                        polygon,
                        text,
                        round(sum(confidences) / len(confidences), 4) if confidences else None,
                        metadata={"engine": "azure_vision"},
                    )
                )

        self.charge(1)
        return OcrOutcome(
            regions=regions,
            provider=self.name,
            model="azure-vision-4.0-read",
            confidence=average_confidence(regions),
            duration_ms=int((time.perf_counter() - started) * 1000),
            cost_micro_usd=self.cost_per_unit_micro_usd,
        )


class AwsTextractProvider(OcrProvider):
    """AWS Textract ``AnalyzeDocument`` with the TABLES feature."""

    name = "aws_textract"
    local = False
    supports_handwriting = True
    cost_per_unit_micro_usd = 1500

    def available(self) -> bool:
        return bool(
            settings.aws_textract_enabled
            and settings.aws_access_key_id
            and settings.aws_secret_access_key
        )

    def health(self) -> ProviderHealth:
        if not settings.aws_textract_enabled:
            return ProviderHealth(
                self.name, self.kind, HealthState.NOT_CONFIGURED, "AWS_TEXTRACT_ENABLED is false"
            )
        if not (settings.aws_access_key_id and settings.aws_secret_access_key):
            return ProviderHealth(
                self.name, self.kind, HealthState.NOT_CONFIGURED, "AWS credentials are required"
            )
        return super().health()

    def recognize(self, request: OcrRequest) -> OcrOutcome:
        import boto3

        started = time.perf_counter()
        width, height = request.image.size
        client = boto3.client(
            "textract",
            region_name=settings.aws_textract_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
        try:
            payload = client.analyze_document(
                Document={"Bytes": request.to_png_bytes()},
                FeatureTypes=["TABLES", "LAYOUT"],
            )
        except Exception as exc:
            raise _http_error(self.name, exc) from exc

        regions: list[Region] = []
        for block in payload.get("Blocks", []):
            if block.get("BlockType") != "LINE":
                continue
            text = str(block.get("Text", "")).strip()
            if not text:
                continue
            polygon = [
                (point.get("X", 0.0) * width, point.get("Y", 0.0) * height)
                for point in block.get("Geometry", {}).get("Polygon", [])
            ]
            regions.append(
                make_region(
                    polygon,
                    text,
                    block.get("Confidence", 0) / 100.0 if block.get("Confidence") else None,
                    metadata={"engine": "aws_textract", "block_id": block.get("Id")},
                )
            )

        self.charge(1)
        return OcrOutcome(
            regions=regions,
            provider=self.name,
            model="textract:analyze_document",
            confidence=average_confidence(regions),
            duration_ms=int((time.perf_counter() - started) * 1000),
            cost_micro_usd=self.cost_per_unit_micro_usd,
            metadata={"raw_blocks": len(payload.get("Blocks", []))},
        )


class YandexVisionProvider(OcrProvider):
    """Yandex Cloud Vision OCR — strong on Russian and other Cyrillic scripts."""

    name = "yandex_vision"
    local = False
    cost_per_unit_micro_usd = 1200

    ENDPOINT = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeText"

    def available(self) -> bool:
        return bool(
            settings.yandex_vision_enabled
            and settings.yandex_vision_api_key
            and settings.yandex_vision_folder_id
        )

    def health(self) -> ProviderHealth:
        if not settings.yandex_vision_enabled:
            return ProviderHealth(
                self.name, self.kind, HealthState.NOT_CONFIGURED, "YANDEX_VISION_ENABLED is false"
            )
        if not (settings.yandex_vision_api_key and settings.yandex_vision_folder_id):
            return ProviderHealth(
                self.name,
                self.kind,
                HealthState.NOT_CONFIGURED,
                "YANDEX_VISION_API_KEY / YANDEX_VISION_FOLDER_ID are required",
            )
        return super().health()

    def recognize(self, request: OcrRequest) -> OcrOutcome:
        started = time.perf_counter()
        codes = [
            language_table.provider_code(language, "yandex_translate")
            for language in request.languages
        ]
        body = {
            "mimeType": "image/png",
            "languageCodes": [code for code in codes if code] or ["*"],
            "model": "handwritten" if request.handwriting else "page",
            "content": base64.b64encode(request.to_png_bytes()).decode("ascii"),
        }
        try:
            response = httpx.post(
                self.ENDPOINT,
                json=body,
                headers={
                    "Authorization": f"Api-Key {settings.yandex_vision_api_key}",
                    "x-folder-id": settings.yandex_vision_folder_id,
                    "x-data-logging-enabled": "false",
                },
                timeout=settings.ocr_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise _http_error(self.name, exc) from exc

        regions: list[Region] = []
        result = payload.get("result", payload)
        for block in (result.get("textAnnotation") or {}).get("blocks", []):
            for line in block.get("lines", []):
                text = str(line.get("text", "")).strip()
                if not text:
                    continue
                vertices = line.get("boundingBox", {}).get("vertices", [])
                polygon = [(float(item.get("x", 0)), float(item.get("y", 0))) for item in vertices]
                confidences = [
                    word.get("confidence")
                    for word in line.get("words", [])
                    if word.get("confidence") is not None
                ]
                regions.append(
                    make_region(
                        polygon,
                        text,
                        round(sum(confidences) / len(confidences), 4) if confidences else None,
                        metadata={"engine": "yandex_vision"},
                    )
                )

        self.charge(1)
        return OcrOutcome(
            regions=regions,
            provider=self.name,
            model="yandex-ocr",
            confidence=average_confidence(regions),
            duration_ms=int((time.perf_counter() - started) * 1000),
            cost_micro_usd=self.cost_per_unit_micro_usd,
        )
