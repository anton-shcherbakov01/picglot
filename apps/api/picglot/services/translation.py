"""Translation orchestration: glossary, translation memory, cache, dedup, providers."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from picglot.core.config import settings
from picglot.core.errors import AppError, ErrorCode
from picglot.core.logging import get_logger
from picglot.core.metrics import cache_hits_total, cache_misses_total
from picglot.core.redis import get_redis
from picglot.db.models import Glossary, GlossaryTerm, Job, ProviderUsage, TranslationMemoryEntry
from picglot.domain.enums import TranslationSource
from picglot.providers import translation as translation_providers
from picglot.providers.translation.base import TranslationRequest, should_translate
from picglot.vision.types import Region

log = get_logger(__name__)

FUZZY_THRESHOLD = 0.92

#: Per-run provenance so the editor can show where each translation came from.
_provenance: threading.local = threading.local()


def _set_provenance(region_id: str, provider: str, source: TranslationSource) -> None:
    if not hasattr(_provenance, "map"):
        _provenance.map = {}
    _provenance.map[region_id] = (provider, source)


def last_provider_for(region_id: str) -> str | None:
    entry = getattr(_provenance, "map", {}).get(region_id)
    return entry[0] if entry else None


def last_source_for(region_id: str) -> TranslationSource | None:
    entry = getattr(_provenance, "map", {}).get(region_id)
    return entry[1] if entry else None


def reset_provenance() -> None:
    _provenance.map = {}


@dataclass(slots=True)
class GlossaryBundle:
    terms: dict[str, str] = field(default_factory=dict)
    do_not_translate: set[str] = field(default_factory=set)
    glossary_id: str | None = None
    version: int = 0

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            {"t": sorted(self.terms.items()), "d": sorted(self.do_not_translate)},
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def load_glossary(
    session: Session,
    *,
    glossary_id: str | None,
    user_id: str | None,
    workspace_id: str | None,
    source_language: str | None,
    target_language: str,
) -> GlossaryBundle:
    statement = select(Glossary).where(Glossary.deleted_at.is_(None))
    if glossary_id:
        statement = statement.where(Glossary.id == glossary_id)
    else:
        statement = statement.where(
            Glossary.is_default.is_(True), Glossary.target_language == target_language
        )
        if workspace_id:
            statement = statement.where(Glossary.workspace_id == workspace_id)
        elif user_id:
            statement = statement.where(Glossary.user_id == user_id)
        else:
            return GlossaryBundle()
        if source_language:
            statement = statement.where(Glossary.source_language == source_language)

    glossary = session.execute(statement).scalars().first()
    if glossary is None:
        return GlossaryBundle()

    bundle = GlossaryBundle(glossary_id=glossary.id, version=int(glossary.version))
    for term in session.execute(
        select(GlossaryTerm).where(GlossaryTerm.glossary_id == glossary.id)
    ).scalars():
        if term.do_not_translate:
            bundle.do_not_translate.add(term.source_term)
        else:
            bundle.terms[term.source_term] = term.target_term
    return bundle


# --------------------------------------------------------------------------- #
# Translation memory
# --------------------------------------------------------------------------- #
def _segment_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).lower().encode("utf-8")).hexdigest()


def tm_lookup(
    session: Session,
    text: str,
    *,
    source_language: str | None,
    target_language: str,
    user_id: str | None,
    workspace_id: str | None,
) -> tuple[str, TranslationSource] | None:
    if not (user_id or workspace_id):
        return None

    statement = select(TranslationMemoryEntry).where(
        TranslationMemoryEntry.target_language == target_language
    )
    if workspace_id:
        statement = statement.where(TranslationMemoryEntry.workspace_id == workspace_id)
    else:
        statement = statement.where(TranslationMemoryEntry.user_id == user_id)
    if source_language:
        statement = statement.where(TranslationMemoryEntry.source_language == source_language)

    exact = (
        session.execute(statement.where(TranslationMemoryEntry.source_hash == _segment_hash(text)))
        .scalars()
        .first()
    )
    if exact is not None:
        exact.frequency = int(exact.frequency) + 1
        exact.last_used_at = datetime.now(UTC)
        return exact.target_text, TranslationSource.TRANSLATION_MEMORY

    # Fuzzy: only worth attempting on short segments, and only against a slice.
    if len(text) > 220:
        return None
    candidates = list(
        session.execute(
            statement.order_by(TranslationMemoryEntry.frequency.desc()).limit(200)
        ).scalars()
    )
    best: TranslationMemoryEntry | None = None
    best_ratio = 0.0
    for candidate in candidates:
        ratio = SequenceMatcher(None, text.lower(), candidate.source_text.lower()).ratio()
        if ratio > best_ratio:
            best, best_ratio = candidate, ratio
    if best is not None and best_ratio >= FUZZY_THRESHOLD:
        best.frequency = int(best.frequency) + 1
        best.last_used_at = datetime.now(UTC)
        return best.target_text, TranslationSource.FUZZY_MATCH
    return None


def tm_store(
    session: Session,
    *,
    source_text: str,
    target_text: str,
    source_language: str | None,
    target_language: str,
    user_id: str | None,
    workspace_id: str | None,
    confirmed: bool = False,
    quality_score: float = 0.8,
) -> None:
    if not (user_id or workspace_id) or not source_text.strip() or not target_text.strip():
        return
    source_hash = _segment_hash(source_text)
    statement = select(TranslationMemoryEntry).where(
        TranslationMemoryEntry.source_hash == source_hash,
        TranslationMemoryEntry.target_language == target_language,
    )
    statement = (
        statement.where(TranslationMemoryEntry.workspace_id == workspace_id)
        if workspace_id
        else statement.where(TranslationMemoryEntry.user_id == user_id)
    )
    existing = session.execute(statement).scalars().first()
    if existing is not None:
        if confirmed or not existing.confirmed_by_user:
            existing.target_text = target_text
            existing.confirmed_by_user = existing.confirmed_by_user or confirmed
            existing.quality = max(existing.quality, quality_score)
        existing.frequency = int(existing.frequency) + 1
        existing.last_used_at = datetime.now(UTC)
        return

    session.add(
        TranslationMemoryEntry(
            user_id=None if workspace_id else user_id,
            workspace_id=workspace_id,
            source_language=source_language or "auto",
            target_language=target_language,
            source_hash=source_hash,
            source_text=source_text[:8000],
            target_text=target_text[:8000],
            quality=quality_score,
            confirmed_by_user=confirmed,
            last_used_at=datetime.now(UTC),
        )
    )


# --------------------------------------------------------------------------- #
# Provider cache
# --------------------------------------------------------------------------- #
def _cache_key(text: str, source: str | None, target: str, fingerprint: str) -> str:
    digest = hashlib.sha256(
        f"{source or 'auto'}|{target}|{fingerprint}|{text}".encode()
    ).hexdigest()
    return f"tr:{digest}"


def cache_get(key: str) -> str | None:
    try:
        value = get_redis().get(key)
    except Exception:  # pragma: no cover
        return None
    if value:
        cache_hits_total.labels(cache="translation").inc()
        return str(value)
    cache_misses_total.labels(cache="translation").inc()
    return None


def cache_put(key: str, value: str) -> None:
    try:
        get_redis().setex(key, settings.translation_cache_ttl_seconds, value)
    except Exception:  # pragma: no cover
        pass


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def translate_regions(
    session: Session,
    *,
    regions: list[Region],
    source_language: str | None,
    target_language: str,
    user_id: str | None = None,
    workspace_id: str | None = None,
    glossary_id: str | None = None,
    formality: str | None = None,
    job: Job | None = None,
    store_memory: bool = True,
) -> dict[str, str]:
    """Translate every eligible region. Returns ``{region_id: translated_text}``."""
    reset_provenance()
    bundle = load_glossary(
        session,
        glossary_id=glossary_id,
        user_id=user_id,
        workspace_id=workspace_id,
        source_language=source_language,
        target_language=target_language,
    )

    pending: dict[str, list[str]] = {}  # unique text -> region ids
    results: dict[str, str] = {}

    for region in regions:
        text = region.effective_text.strip()
        if region.skip_translation or not text:
            continue
        if not should_translate(text):
            # Pure numbers/codes: keep them verbatim rather than mangling them.
            results[region.id] = text
            _set_provenance(region.id, "", TranslationSource.UNTRANSLATED)
            continue
        if text in bundle.do_not_translate:
            results[region.id] = text
            _set_provenance(region.id, "glossary", TranslationSource.GLOSSARY)
            continue
        if bundle.terms.get(text):
            results[region.id] = bundle.terms[text]
            _set_provenance(region.id, "glossary", TranslationSource.GLOSSARY)
            continue

        remembered = tm_lookup(
            session,
            text,
            source_language=source_language,
            target_language=target_language,
            user_id=user_id,
            workspace_id=workspace_id,
        )
        if remembered is not None:
            results[region.id] = remembered[0]
            _set_provenance(region.id, "translation_memory", remembered[1])
            continue

        cached = cache_get(_cache_key(text, source_language, target_language, bundle.fingerprint))
        if cached is not None:
            results[region.id] = cached
            _set_provenance(region.id, "cache", TranslationSource.CACHE)
            continue

        pending.setdefault(text, []).append(region.id)

    if not pending:
        return results

    unique_texts = list(pending)
    log.info(
        "translation.batch",
        segments=len(unique_texts),
        regions=sum(len(ids) for ids in pending.values()),
        target=target_language,
    )

    provider_name = ""
    for chunk in _chunks(unique_texts, settings.translation_max_chars_per_request):
        request = TranslationRequest(
            texts=chunk,
            source_language=source_language,
            target_language=target_language,
            formality=formality,
            glossary_terms=bundle.terms,
            do_not_translate=bundle.do_not_translate,
        )
        chain = translation_providers.translate(request)
        outcome = chain.value
        provider_name = chain.provider

        if job is not None:
            session.add(
                ProviderUsage(
                    kind="translation",
                    provider=chain.provider,
                    model=outcome.model,
                    job_id=job.id,
                    units=outcome.character_count,
                    unit_kind="character",
                    cost_micro_usd=outcome.cost_micro_usd,
                    latency_ms=outcome.duration_ms,
                    success=True,
                )
            )

        for source_text, translated in zip(chunk, outcome.texts, strict=False):
            for region_id in pending.get(source_text, []):
                results[region_id] = translated
                _set_provenance(region_id, chain.provider, TranslationSource.PROVIDER)
            cache_put(
                _cache_key(source_text, source_language, target_language, bundle.fingerprint),
                translated,
            )
            if store_memory:
                tm_store(
                    session,
                    source_text=source_text,
                    target_text=translated,
                    source_language=outcome.source_language or source_language,
                    target_language=target_language,
                    user_id=user_id,
                    workspace_id=workspace_id,
                )

    missing = [
        region.id
        for region in regions
        if not region.skip_translation
        and region.effective_text.strip()
        and region.id not in results
    ]
    if missing:
        log.warning("translation.incomplete", missing=len(missing), provider=provider_name)
    return results


def _chunks(texts: list[str], max_chars: int) -> list[list[str]]:
    """Group segments into requests that respect the provider character limit."""
    chunks: list[list[str]] = []
    current: list[str] = []
    size = 0
    for text in texts:
        length = len(text)
        if current and (size + length > max_chars or len(current) >= 50):
            chunks.append(current)
            current, size = [], 0
        current.append(text)
        size += length
    if current:
        chunks.append(current)
    return chunks


def retranslate_one(
    session: Session,
    *,
    text: str,
    source_language: str | None,
    target_language: str,
    provider: str | None = None,
    formality: str | None = None,
    user_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Editor action: translate one block again, optionally with a named provider."""
    request = TranslationRequest(
        texts=[text],
        source_language=source_language,
        target_language=target_language,
        formality=formality,
    )
    if provider:
        adapter = translation_providers.get_provider(provider)
        if not adapter.available():
            raise AppError(code=ErrorCode.PROVIDER_UNAVAILABLE, details={"provider": provider})
        adapter.guard()
        outcome = adapter.translate(request)
        used = provider
    else:
        chain = translation_providers.translate(request)
        outcome = chain.value
        used = chain.provider

    translated = outcome.texts[0] if outcome.texts else ""
    if user_id or workspace_id:
        tm_store(
            session,
            source_text=text,
            target_text=translated,
            source_language=outcome.source_language or source_language,
            target_language=target_language,
            user_id=user_id,
            workspace_id=workspace_id,
        )
    return {
        "text": translated,
        "provider": used,
        "model": outcome.model,
        "alternatives": outcome.alternatives[0] if outcome.alternatives else [],
        "source_language": outcome.source_language,
    }


def alternatives_for(
    session: Session,
    *,
    text: str,
    source_language: str | None,
    target_language: str,
    limit: int = 3,
) -> list[dict[str, str]]:
    """Run the text through several configured providers for comparison."""
    results: list[dict[str, str]] = []
    for adapter in translation_providers.chain(source_language, target_language)[:limit]:
        try:
            adapter.guard()
            outcome = adapter.translate(
                TranslationRequest(
                    texts=[text],
                    source_language=source_language,
                    target_language=target_language,
                )
            )
        except AppError:
            continue
        if outcome.texts:
            results.append({"provider": adapter.name, "text": outcome.texts[0]})
    return results
