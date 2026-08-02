"""Local OCR engines: RapidOCR (ONNX) and Tesseract.

These are the default chain — no API key, no data leaving the machine, and the
only providers permitted when ``LOCAL_ONLY_PROCESSING`` is on.
"""

from __future__ import annotations

import shutil
import threading
import time
from typing import Any

import numpy as np

from lingoimage.core.config import settings
from lingoimage.core.errors import AppError, ErrorCode
from lingoimage.core.logging import get_logger
from lingoimage.domain import languages as language_table
from lingoimage.providers.base import HealthState, ProviderHealth
from lingoimage.providers.ocr.base import OcrProvider, OcrRequest, average_confidence, make_region
from lingoimage.vision.types import OcrOutcome, Region

log = get_logger(__name__)


class RapidOcrProvider(OcrProvider):
    """RapidOCR — PP-OCR models via onnxruntime. Strong on photos and screenshots.

    Ships its own models with the wheel, so a fresh install works offline with
    no download step.
    """

    name = "rapidocr"
    local = True
    returns_geometry = True

    _engine: Any = None
    _engine_lock = threading.Lock()

    def available(self) -> bool:
        try:
            import rapidocr_onnxruntime  # noqa: F401
        except ImportError:
            return False
        return True

    def _get_engine(self) -> Any:
        if RapidOcrProvider._engine is not None:
            return RapidOcrProvider._engine
        with RapidOcrProvider._engine_lock:
            if RapidOcrProvider._engine is None:
                from rapidocr_onnxruntime import RapidOCR

                RapidOcrProvider._engine = RapidOCR()
                log.info("ocr.rapidocr_loaded")
        return RapidOcrProvider._engine

    def health(self) -> ProviderHealth:
        if not self.available():
            return ProviderHealth(
                self.name,
                self.kind,
                HealthState.NOT_CONFIGURED,
                "rapidocr-onnxruntime is not installed (pip install 'lingoimage[ocr]')",
            )
        return super().health()

    def recognize(self, request: OcrRequest) -> OcrOutcome:
        started = time.perf_counter()
        engine = self._get_engine()
        array = np.array(request.image.convert("RGB"))

        try:
            result, _elapsed = engine(array)
        except Exception as exc:
            raise AppError(
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                details={"provider": self.name},
                internal=f"rapidocr failed: {exc}",
            ) from exc

        regions: list[Region] = []
        for item in result or []:
            try:
                polygon, text, score = item[0], item[1], item[2]
            except (IndexError, TypeError):
                continue
            if not str(text).strip():
                continue
            regions.append(
                make_region(
                    polygon,
                    str(text),
                    float(score) if score is not None else None,
                    metadata={"engine": "rapidocr"},
                )
            )

        return OcrOutcome(
            regions=regions,
            provider=self.name,
            model="PP-OCRv4",
            confidence=average_confidence(regions),
            duration_ms=int((time.perf_counter() - started) * 1000),
            detected_language=_guess_language(regions, request.languages),
            metadata={"region_count": len(regions)},
        )


class TesseractProvider(OcrProvider):
    """Tesseract 5 — broadest language coverage, best on clean scans.

    Uses ``image_to_data`` so we get per-word geometry and confidence rather
    than a flat string.
    """

    name = "tesseract"
    local = True
    returns_geometry = True
    supports_handwriting = True  # poorly, but it is a last resort

    def available(self) -> bool:
        try:
            import pytesseract  # noqa: F401
        except ImportError:
            return False
        return self._binary_path() is not None

    def _binary_path(self) -> str | None:
        if settings.tesseract_cmd:
            return settings.tesseract_cmd
        return shutil.which("tesseract")

    def health(self) -> ProviderHealth:
        try:
            import pytesseract
        except ImportError:
            return ProviderHealth(
                self.name, self.kind, HealthState.NOT_CONFIGURED, "pytesseract is not installed"
            )
        binary = self._binary_path()
        if not binary:
            return ProviderHealth(
                self.name,
                self.kind,
                HealthState.NOT_CONFIGURED,
                "the tesseract binary is not on PATH (set TESSERACT_CMD)",
            )
        try:
            pytesseract.pytesseract.tesseract_cmd = binary
            version = str(pytesseract.get_tesseract_version())
            return ProviderHealth(self.name, self.kind, HealthState.HEALTHY, f"tesseract {version}")
        except Exception as exc:  # pragma: no cover
            return ProviderHealth(self.name, self.kind, HealthState.UNAVAILABLE, type(exc).__name__)

    def recognize(self, request: OcrRequest) -> OcrOutcome:
        import pytesseract

        started = time.perf_counter()
        binary = self._binary_path()
        if binary:
            pytesseract.pytesseract.tesseract_cmd = binary
        if settings.tessdata_prefix:
            import os

            os.environ.setdefault("TESSDATA_PREFIX", settings.tessdata_prefix)

        language_arg = language_table.tesseract_codes(request.languages)
        # PSM 6 (uniform block) is the safest default; sparse text for screenshots.
        psm = 11 if request.hints.get("sparse_text") else 6
        config = f"--oem 1 --psm {psm}"

        try:
            data = pytesseract.image_to_data(
                request.image,
                lang=language_arg,
                config=config,
                output_type=pytesseract.Output.DICT,
            )
        except pytesseract.TesseractError as exc:
            missing = "Failed loading language" in str(exc)
            raise AppError(
                code=ErrorCode.LANGUAGE_NOT_SUPPORTED
                if missing
                else ErrorCode.PROVIDER_UNAVAILABLE,
                details={"provider": self.name, "languages": language_arg},
                internal=str(exc)[:300],
            ) from exc
        except Exception as exc:
            raise AppError(
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                details={"provider": self.name},
                internal=str(exc)[:300],
            ) from exc

        regions = _regions_from_tesseract(data)
        return OcrOutcome(
            regions=regions,
            provider=self.name,
            model=f"tesseract:{language_arg}",
            confidence=average_confidence(regions),
            duration_ms=int((time.perf_counter() - started) * 1000),
            detected_language=_guess_language(regions, request.languages),
            metadata={"psm": psm, "languages": language_arg},
        )


def _regions_from_tesseract(data: dict[str, list[Any]]) -> list[Region]:
    """Group word boxes into line regions using Tesseract's own line indices."""
    count = len(data.get("text", []))
    lines: dict[tuple[int, int, int, int], dict[str, Any]] = {}

    for index in range(count):
        text = str(data["text"][index]).strip()
        if not text:
            continue
        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            confidence = -1.0
        if confidence < 0:
            continue

        key = (
            int(data["page_num"][index]),
            int(data["block_num"][index]),
            int(data["par_num"][index]),
            int(data["line_num"][index]),
        )
        left, top = int(data["left"][index]), int(data["top"][index])
        width, height = int(data["width"][index]), int(data["height"][index])

        entry = lines.setdefault(
            key,
            {
                "words": [],
                "confidences": [],
                "x1": left,
                "y1": top,
                "x2": left + width,
                "y2": top + height,
            },
        )
        entry["words"].append(text)
        entry["confidences"].append(confidence / 100.0)
        entry["x1"] = min(entry["x1"], left)
        entry["y1"] = min(entry["y1"], top)
        entry["x2"] = max(entry["x2"], left + width)
        entry["y2"] = max(entry["y2"], top + height)

    regions: list[Region] = []
    for key, entry in lines.items():
        polygon = [
            (entry["x1"], entry["y1"]),
            (entry["x2"], entry["y1"]),
            (entry["x2"], entry["y2"]),
            (entry["x1"], entry["y2"]),
        ]
        confidences = entry["confidences"]
        regions.append(
            make_region(
                polygon,
                " ".join(entry["words"]),
                round(sum(confidences) / len(confidences), 4) if confidences else None,
                metadata={"engine": "tesseract", "block": key[1], "paragraph": key[2]},
            )
        )
    return regions


def _guess_language(regions: list[Region], hints: list[str]) -> str | None:
    if not regions:
        return None
    sample = " ".join(region.text for region in regions[:25])[:2000]
    return language_table.guess_language(sample, hints) or (hints[0] if hints else None)
