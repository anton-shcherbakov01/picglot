"""Fixed-window rate limiting with a Redis backend.

Sliding windows are nicer but a fixed window with a short period is enough for
abuse control here and costs one round trip. Every limiter returns the full
decision so the API can emit ``X-RateLimit-*`` and ``Retry-After`` headers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from lingoimage.core.config import settings
from lingoimage.core.errors import AppError, ErrorCode
from lingoimage.core.metrics import rate_limit_hits_total
from lingoimage.core.redis import get_redis


@dataclass(slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_after: int
    scope: str

    def headers(self) -> dict[str, str]:
        headers = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(0, self.remaining)),
            "X-RateLimit-Reset": str(self.reset_after),
        }
        if not self.allowed:
            headers["Retry-After"] = str(self.reset_after)
        return headers

    def raise_if_blocked(self) -> None:
        if not self.allowed:
            raise AppError(
                code=ErrorCode.RATE_LIMIT_EXCEEDED,
                details={"retry_after_seconds": self.reset_after, "scope": self.scope},
            )


def check(scope: str, identifier: str, limit: int, window_seconds: int) -> RateLimitDecision:
    """Consume one unit from ``scope:identifier``."""
    if not settings.rate_limit_enabled or limit <= 0:
        return RateLimitDecision(True, limit, limit, 0, scope)

    now = int(time.time())
    window_start = now - (now % window_seconds)
    key = f"rl:{scope}:{identifier}:{window_start}"
    client = get_redis()

    try:
        pipe = client.pipeline()
        pipe.incr(key, 1)
        pipe.expire(key, window_seconds + 1)
        used = int(pipe.execute()[0])
    except Exception:  # pragma: no cover - never fail closed on limiter errors
        return RateLimitDecision(True, limit, limit, 0, scope)

    reset_after = window_start + window_seconds - now
    allowed = used <= limit
    if not allowed:
        rate_limit_hits_total.labels(scope=scope).inc()
    return RateLimitDecision(allowed, limit, limit - used, max(reset_after, 1), scope)


def peek(scope: str, identifier: str, limit: int, window_seconds: int) -> RateLimitDecision:
    """Read the current usage without consuming a unit."""
    if not settings.rate_limit_enabled or limit <= 0:
        return RateLimitDecision(True, limit, limit, 0, scope)
    now = int(time.time())
    window_start = now - (now % window_seconds)
    key = f"rl:{scope}:{identifier}:{window_start}"
    try:
        used = int(get_redis().get(key) or 0)
    except Exception:  # pragma: no cover
        used = 0
    reset_after = window_start + window_seconds - now
    return RateLimitDecision(used < limit, limit, limit - used, max(reset_after, 1), scope)


def reset(scope: str, identifier: str) -> None:
    client = get_redis()
    for key in client.scan_iter(match=f"rl:{scope}:{identifier}:*"):
        client.delete(key)


# --------------------------------------------------------------------------- #
# Named limiters used across the app
# --------------------------------------------------------------------------- #
def anonymous_requests(ip_hash: str) -> RateLimitDecision:
    return check("anon", ip_hash, settings.rate_limit_anon_per_minute, 60)


def user_requests(user_id: str) -> RateLimitDecision:
    return check("user", user_id, settings.rate_limit_user_per_minute, 60)


def login_attempts(identifier: str) -> RateLimitDecision:
    return check("login", identifier, settings.rate_limit_login_per_15min, 900)


def uploads(identifier: str) -> RateLimitDecision:
    return check("upload", identifier, settings.rate_limit_upload_per_hour, 3600)


def api_requests(api_key_id: str) -> RateLimitDecision:
    return check("api", api_key_id, settings.rate_limit_api_per_minute, 60)


def share_views(token_hash: str) -> RateLimitDecision:
    return check("share", token_hash, settings.rate_limit_share_per_minute, 60)


class CircuitBreaker:
    """Trip after N consecutive failures; half-open after ``recovery`` seconds."""

    def __init__(self, name: str, threshold: int = 5, recovery_seconds: int = 60) -> None:
        self.name = name
        self.threshold = threshold
        self.recovery_seconds = recovery_seconds

    @property
    def _fail_key(self) -> str:
        return f"cb:{self.name}:failures"

    @property
    def _open_key(self) -> str:
        return f"cb:{self.name}:open"

    def is_open(self) -> bool:
        try:
            return bool(get_redis().exists(self._open_key))
        except Exception:  # pragma: no cover
            return False

    def record_success(self) -> None:
        client = get_redis()
        client.delete(self._fail_key, self._open_key)

    def record_failure(self) -> None:
        client = get_redis()
        failures = int(client.incr(self._fail_key, 1))
        client.expire(self._fail_key, self.recovery_seconds * 4)
        if failures >= self.threshold:
            client.setex(self._open_key, self.recovery_seconds, "1")

    def seconds_until_retry(self) -> int:
        try:
            ttl = int(get_redis().ttl(self._open_key))
        except Exception:  # pragma: no cover
            return 0
        return max(ttl, 0)
