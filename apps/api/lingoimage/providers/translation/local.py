"""Local translation: Argos Translate (offline neural MT) and a test echo provider.

Argos is optional — it downloads ~100 MB per language pair. When it is not
installed the provider reports ``not_configured`` and the UI tells the user
plainly that translation needs either the offline models or a provider key,
rather than silently returning the source text.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from lingoimage.core.config import settings
from lingoimage.core.errors import AppError, ErrorCode
from lingoimage.core.logging import get_logger
from lingoimage.domain import languages
from lingoimage.domain.enums import TranslationSource
from lingoimage.providers.base import HealthState, ProviderHealth
from lingoimage.providers.translation.base import (
    TranslationProvider,
    TranslationRequest,
    TranslationResult,
    protect,
)

log = get_logger(__name__)


class ArgosProvider(TranslationProvider):
    """Offline translation via argostranslate. No data leaves the machine."""

    name = "argos"
    local = True
    max_batch = 20
    cost_per_unit_micro_usd = 0

    _lock = threading.Lock()
    _installed_pairs: set[tuple[str, str]] | None = None

    def available(self) -> bool:
        try:
            import argostranslate.translate  # noqa: F401
        except ImportError:
            return False
        return bool(self._pairs())

    def _pairs(self) -> set[tuple[str, str]]:
        if ArgosProvider._installed_pairs is not None:
            return ArgosProvider._installed_pairs
        with ArgosProvider._lock:
            if ArgosProvider._installed_pairs is None:
                try:
                    import argostranslate.translate as argos

                    pairs: set[tuple[str, str]] = set()
                    for language in argos.get_installed_languages():
                        for translation in getattr(language, "translations_from", []):
                            pairs.add((language.code, translation.to_lang.code))
                    ArgosProvider._installed_pairs = pairs
                except Exception as exc:  # pragma: no cover - optional dependency
                    log.info("translation.argos_unavailable", error=type(exc).__name__)
                    ArgosProvider._installed_pairs = set()
        return ArgosProvider._installed_pairs or set()

    def supports_pair(self, source: str | None, target: str) -> bool:
        target_code = languages.provider_code(target, "argos")
        if not target_code:
            return False
        pairs = self._pairs()
        if source:
            source_code = languages.provider_code(source, "argos")
            return bool(source_code) and (source_code, target_code) in pairs
        # Without a known source we need at least one pair into the target.
        return any(to_code == target_code for _from, to_code in pairs)

    def health(self) -> ProviderHealth:
        try:
            import argostranslate.translate  # noqa: F401
        except ImportError:
            return ProviderHealth(
                self.name,
                self.kind,
                HealthState.NOT_CONFIGURED,
                "argostranslate is not installed (pip install 'lingoimage[translate-local]')",
            )
        pairs = self._pairs()
        if not pairs:
            return ProviderHealth(
                self.name,
                self.kind,
                HealthState.NOT_CONFIGURED,
                "no offline language packages installed — run: python -m lingoimage.cli "
                "install-language-pack <source> <target>",
            )
        return ProviderHealth(
            self.name, self.kind, HealthState.HEALTHY, f"{len(pairs)} offline pairs installed"
        )

    def translate(self, request: TranslationRequest) -> TranslationResult:
        import argostranslate.translate as argos

        started = time.perf_counter()
        target_code = languages.provider_code(request.target_language, "argos")
        source_code = (
            languages.provider_code(request.source_language, "argos")
            if request.source_language
            else None
        )
        if not source_code:
            source_code = self._infer_source(request, target_code or "en")
        if not (source_code and target_code):
            raise AppError(
                code=ErrorCode.LANGUAGE_NOT_SUPPORTED,
                details={"source": request.source_language, "target": request.target_language},
            )

        outputs: list[str] = []
        for text in request.texts:
            protection = protect(text, request.do_not_translate)
            try:
                translated = argos.translate(protection.masked, source_code, target_code)
            except Exception as exc:
                raise AppError(
                    code=ErrorCode.PROVIDER_UNAVAILABLE,
                    details={"provider": self.name},
                    internal=str(exc)[:250],
                ) from exc
            outputs.append(protection.restore(translated))

        return TranslationResult(
            texts=outputs,
            provider=self.name,
            model=f"argos:{source_code}->{target_code}",
            source_language=languages.normalize(source_code),
            character_count=sum(len(text) for text in request.texts),
            cost_micro_usd=0,
            duration_ms=int((time.perf_counter() - started) * 1000),
            metadata={"offline": True},
        )

    def _infer_source(self, request: TranslationRequest, target_code: str) -> str | None:
        sample = " ".join(request.texts)[:500]
        guessed = languages.guess_language(sample)
        if guessed:
            code = languages.provider_code(guessed, "argos")
            if code and (code, target_code) in self._pairs():
                return code
        for source, target in sorted(self._pairs()):
            if target == target_code and source == "en":
                return source
        return None

    @classmethod
    def reset_cache(cls) -> None:
        with cls._lock:
            cls._installed_pairs = None


class EchoProvider(TranslationProvider):
    """Deterministic provider used only by the test suite.

    Refuses to run outside ``ENVIRONMENT=test`` so a misconfiguration can never
    hand a user untranslated text and call it a translation.
    """

    name = "echo"
    local = True
    cost_per_unit_micro_usd = 0

    def available(self) -> bool:
        return settings.is_test

    def health(self) -> ProviderHealth:
        if not settings.is_test:
            return ProviderHealth(
                self.name, self.kind, HealthState.NOT_CONFIGURED, "test environment only"
            )
        return ProviderHealth(self.name, self.kind, HealthState.HEALTHY, "deterministic test stub")

    def translate(self, request: TranslationRequest) -> TranslationResult:
        if not settings.is_test:
            raise AppError(
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                details={"provider": self.name},
                internal="echo provider is only permitted in the test environment",
            )
        target = request.target_language
        outputs: list[str] = []
        for text in request.texts:
            protection = protect(text, request.do_not_translate)
            outputs.append(protection.restore(f"[{target}] {protection.masked}"))
        return TranslationResult(
            texts=outputs,
            provider=self.name,
            model="echo",
            source_language=request.source_language,
            character_count=sum(len(text) for text in request.texts),
            source=TranslationSource.PROVIDER,
            metadata={"deterministic": True},
        )


def install_language_pack(source: str, target: str) -> dict[str, Any]:
    """Download and install one offline Argos pair (used by the CLI)."""
    try:
        import argostranslate.package
    except ImportError as exc:
        raise AppError(
            code=ErrorCode.FEATURE_DISABLED,
            internal="argostranslate is not installed",
        ) from exc

    source_code = languages.provider_code(source, "argos")
    target_code = languages.provider_code(target, "argos")
    if not (source_code and target_code):
        raise AppError(
            code=ErrorCode.LANGUAGE_NOT_SUPPORTED, details={"source": source, "target": target}
        )

    argostranslate.package.update_package_index()
    for package in argostranslate.package.get_available_packages():
        if package.from_code == source_code and package.to_code == target_code:
            path = package.download()
            argostranslate.package.install_from_path(path)
            ArgosProvider.reset_cache()
            return {"installed": True, "from": source_code, "to": target_code}

    raise AppError(
        code=ErrorCode.LANGUAGE_NOT_SUPPORTED,
        details={"source": source_code, "target": target_code},
        internal="no Argos package published for this pair",
    )
