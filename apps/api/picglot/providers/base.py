"""Shared provider machinery: health, circuit breaking, retries, cost accounting.

Every adapter — OCR, translation, LLM — is wrapped by the same policy so that
a misbehaving vendor degrades into a fallback instead of taking down a job, and
so that spend is capped without editing adapter code.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, TypeVar

from picglot.core.config import LOCAL_OCR_PROVIDERS, LOCAL_TRANSLATION_PROVIDERS, settings
from picglot.core.errors import AppError, ErrorCode
from picglot.core.logging import get_logger
from picglot.core.metrics import (
    provider_calls_total,
    provider_circuit_open,
    provider_cost_usd,
    provider_daily_cost_usd,
    provider_latency,
)
from picglot.core.ratelimit import CircuitBreaker
from picglot.core.redis import get_redis

log = get_logger(__name__)

T = TypeVar("T")


class ProviderKind(StrEnum):
    OCR = "ocr"
    TRANSLATION = "translation"
    LLM = "llm"
    PAYMENT = "payment"
    EMAIL = "email"
    ANALYTICS = "analytics"


class HealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    NOT_CONFIGURED = "not_configured"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class ProviderHealth:
    name: str
    kind: ProviderKind
    state: HealthState
    detail: str = ""
    latency_ms: int | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def usable(self) -> bool:
        return self.state in {HealthState.HEALTHY, HealthState.DEGRADED}

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": str(self.kind),
            "state": str(self.state),
            "detail": self.detail,
            "latency_ms": self.latency_ms,
            "checked_at": self.checked_at.isoformat(),
        }


class Provider(Protocol):
    name: str
    kind: ProviderKind
    #: Runs entirely on our own infrastructure — allowed in local-only mode.
    local: bool

    def available(self) -> bool: ...
    def health(self) -> ProviderHealth: ...


class BaseProvider:
    name: str = "base"
    kind: ProviderKind = ProviderKind.OCR
    local: bool = False
    #: Estimated cost per unit in micro-USD (1e-6 USD); 0 for local providers.
    cost_per_unit_micro_usd: int = 0
    failure_threshold: int = 5
    recovery_seconds: int = 60

    def __init__(self) -> None:
        self._breaker = CircuitBreaker(
            f"{self.kind}:{self.name}",
            threshold=self.failure_threshold,
            recovery_seconds=self.recovery_seconds,
        )

    # -- capability ---------------------------------------------------------
    def available(self) -> bool:
        """Whether this provider is configured well enough to be attempted."""
        return True

    def allowed(self) -> bool:
        """Local-only mode blocks every provider that leaves our infrastructure."""
        if not settings.local_only_processing:
            return True
        return self.local

    def health(self) -> ProviderHealth:
        if not self.available():
            return ProviderHealth(
                self.name, self.kind, HealthState.NOT_CONFIGURED, "missing credentials or runtime"
            )
        if self.circuit_open:
            return ProviderHealth(
                self.name,
                self.kind,
                HealthState.UNAVAILABLE,
                f"circuit open for {self._breaker.seconds_until_retry()}s",
            )
        return ProviderHealth(self.name, self.kind, HealthState.HEALTHY)

    # -- circuit breaker ----------------------------------------------------
    @property
    def circuit_open(self) -> bool:
        is_open = self._breaker.is_open()
        provider_circuit_open.labels(kind=str(self.kind), provider=self.name).set(
            1 if is_open else 0
        )
        return is_open

    def record_success(self) -> None:
        self._breaker.record_success()
        provider_circuit_open.labels(kind=str(self.kind), provider=self.name).set(0)

    def record_failure(self) -> None:
        self._breaker.record_failure()

    # -- spend --------------------------------------------------------------
    @property
    def _cost_key(self) -> str:
        today = datetime.now(UTC).strftime("%Y%m%d")
        return f"cost:{self.kind}:{self.name}:{today}"

    def daily_cost_usd(self) -> float:
        try:
            return float(get_redis().get(self._cost_key) or 0.0)
        except Exception:  # pragma: no cover
            return 0.0

    def daily_limit_usd(self) -> float | None:
        if self.kind is ProviderKind.LLM:
            return settings.llm_daily_cost_limit_usd
        return None

    def within_budget(self) -> bool:
        limit = self.daily_limit_usd()
        return limit is None or self.daily_cost_usd() < limit

    def charge(self, units: int) -> int:
        """Record estimated spend; returns the amount in micro-USD."""
        micro = units * self.cost_per_unit_micro_usd
        if micro <= 0:
            return 0
        usd = micro / 1_000_000
        try:
            client = get_redis()
            current = float(client.get(self._cost_key) or 0.0) + usd
            client.setex(self._cost_key, 172800, f"{current:.6f}")
            provider_daily_cost_usd.labels(kind=str(self.kind), provider=self.name).set(current)
        except Exception:  # pragma: no cover
            pass
        provider_cost_usd.labels(kind=str(self.kind), provider=self.name).inc(usd)
        return micro

    # -- invocation ---------------------------------------------------------
    def guard(self) -> None:
        """Raise if this provider must not be called right now."""
        if not self.allowed():
            raise AppError(
                code=ErrorCode.LOCAL_ONLY_BLOCKED,
                details={"provider": self.name},
                internal="local_only_processing blocks external providers",
            )
        if not self.available():
            raise AppError(
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                details={"provider": self.name, "reason": "not_configured"},
            )
        if self.circuit_open:
            raise AppError(
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                details={
                    "provider": self.name,
                    "retry_after_seconds": self._breaker.seconds_until_retry(),
                },
            )
        if not self.within_budget():
            raise AppError(
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                details={"provider": self.name, "reason": "daily_cost_limit"},
                internal=f"{self.name} exceeded its daily spend cap",
            )


@dataclass(slots=True)
class ProviderAttempt:
    provider: str
    ok: bool
    error_code: str | None = None
    duration_ms: int = 0


@dataclass(slots=True)
class ChainResult:
    """The outcome of running a provider chain, including what was tried."""

    value: Any
    provider: str
    attempts: list[ProviderAttempt] = field(default_factory=list)
    duration_ms: int = 0


def run_chain(
    providers: list[BaseProvider],
    operation: str,
    call: Any,
    *,
    kind: ProviderKind,
) -> ChainResult:
    """Try each provider in order; return the first success.

    ``call`` receives the provider and returns the result. Errors that are
    clearly the caller's fault (unsupported language, bad input) abort the chain
    immediately — retrying a different vendor cannot help.
    """
    attempts: list[ProviderAttempt] = []
    last_error: AppError | None = None
    chain_started = time.perf_counter()

    usable = [provider for provider in providers if provider.allowed()]
    if not usable:
        raise AppError(
            code=ErrorCode.LOCAL_ONLY_BLOCKED
            if settings.local_only_processing
            else ErrorCode.PROVIDER_UNAVAILABLE,
            details={"operation": operation},
            internal=f"no usable {kind} provider among {[p.name for p in providers]}",
        )

    for provider in usable:
        started = time.perf_counter()
        try:
            provider.guard()
            value = call(provider)
        except AppError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            attempts.append(ProviderAttempt(provider.name, False, str(exc.code), elapsed))
            provider_calls_total.labels(
                kind=str(kind), provider=provider.name, status="error"
            ).inc()
            if exc.code in _FATAL_CODES:
                raise
            if exc.code not in {ErrorCode.LOCAL_ONLY_BLOCKED}:
                provider.record_failure()
            last_error = exc
            log.warning(
                "provider.attempt_failed",
                provider=provider.name,
                operation=operation,
                code=str(exc.code),
            )
            continue
        except Exception as exc:  # unexpected adapter bug — treat as unavailable
            elapsed = int((time.perf_counter() - started) * 1000)
            attempts.append(ProviderAttempt(provider.name, False, "internal_error", elapsed))
            provider.record_failure()
            provider_calls_total.labels(
                kind=str(kind), provider=provider.name, status="error"
            ).inc()
            last_error = AppError(
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                details={"provider": provider.name},
                internal=f"{type(exc).__name__}: {exc}",
            )
            log.exception("provider.attempt_crashed", provider=provider.name, operation=operation)
            continue

        elapsed = int((time.perf_counter() - started) * 1000)
        provider.record_success()
        provider_latency.labels(kind=str(kind), provider=provider.name).observe(elapsed / 1000)
        provider_calls_total.labels(kind=str(kind), provider=provider.name, status="ok").inc()
        attempts.append(ProviderAttempt(provider.name, True, None, elapsed))
        return ChainResult(
            value=value,
            provider=provider.name,
            attempts=attempts,
            duration_ms=int((time.perf_counter() - chain_started) * 1000),
        )

    raise last_error or AppError(
        code=ErrorCode.PROVIDER_UNAVAILABLE, details={"operation": operation}
    )


#: Errors where trying another provider cannot possibly help.
_FATAL_CODES = frozenset(
    {
        ErrorCode.UNSUPPORTED_FILE_TYPE,
        ErrorCode.FILE_TOO_LARGE,
        ErrorCode.IMAGE_TOO_LARGE,
        ErrorCode.CORRUPTED_FILE,
        ErrorCode.ENCRYPTED_PDF,
        ErrorCode.CANCELLED,
        ErrorCode.INSUFFICIENT_CREDITS,
    }
)


def is_local(kind: ProviderKind, name: str) -> bool:
    if kind is ProviderKind.OCR:
        return name in LOCAL_OCR_PROVIDERS
    if kind is ProviderKind.TRANSLATION:
        return name in LOCAL_TRANSLATION_PROVIDERS
    return False


def retry_with_backoff(
    call: Any, *, attempts: int = 3, base_delay: float = 0.5, max_delay: float = 8.0
) -> Any:
    """Exponential backoff for transient vendor errors."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return call()
        except AppError as exc:
            last = exc
            if not exc.retryable or attempt == attempts - 1:
                raise
        except Exception as exc:
            last = exc
            if attempt == attempts - 1:
                raise
        time.sleep(min(base_delay * (2**attempt), max_delay))
    raise last  # type: ignore[misc]
