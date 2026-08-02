"""Redis access with a graceful in-memory fallback.

Redis backs rate limiting, caches, distributed locks and the job event stream.
When it is unavailable (local dev without Docker, unit tests) we degrade to an
in-process implementation instead of failing requests. The fallback is
explicitly *not* safe across processes and says so in ``is_distributed``.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any

from lingoimage.core.config import settings
from lingoimage.core.logging import get_logger

log = get_logger(__name__)

_client: Any = None
_client_lock = threading.Lock()
_probe_failed_at: float = 0.0
_PROBE_BACKOFF_SECONDS = 15.0


class InMemoryRedis:
    """Minimal subset of the redis API used by this codebase."""

    is_distributed = False

    def __init__(self) -> None:
        self._values: dict[str, tuple[str, float | None]] = {}
        self._lists: dict[str, list[str]] = defaultdict(list)
        self._lock = threading.RLock()

    # -- keys ---------------------------------------------------------------
    def _alive(self, key: str) -> bool:
        item = self._values.get(key)
        if item is None:
            return False
        _, expires = item
        if expires is not None and expires < time.time():
            self._values.pop(key, None)
            return False
        return True

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._values[key][0] if self._alive(key) else None

    def set(self, key: str, value: Any, ex: int | None = None, nx: bool = False) -> bool:
        with self._lock:
            if nx and self._alive(key):
                return False
            self._values[key] = (str(value), time.time() + ex if ex else None)
            return True

    def setex(self, key: str, ttl: int, value: Any) -> bool:
        return self.set(key, value, ex=ttl)

    def delete(self, *keys: str) -> int:
        with self._lock:
            removed = 0
            for key in keys:
                removed += 1 if self._values.pop(key, None) is not None else 0
                self._lists.pop(key, None)
            return removed

    def exists(self, key: str) -> int:
        with self._lock:
            return 1 if self._alive(key) else 0

    def incr(self, key: str, amount: int = 1) -> int:
        with self._lock:
            current = int(self._values[key][0]) if self._alive(key) else 0
            expires = self._values[key][1] if self._alive(key) else None
            current += amount
            self._values[key] = (str(current), expires)
            return current

    def expire(self, key: str, ttl: int) -> bool:
        with self._lock:
            if not self._alive(key):
                return False
            value, _ = self._values[key]
            self._values[key] = (value, time.time() + ttl)
            return True

    def ttl(self, key: str) -> int:
        with self._lock:
            if not self._alive(key):
                return -2
            expires = self._values[key][1]
            return -1 if expires is None else max(0, int(expires - time.time()))

    def scan_iter(self, match: str = "*", count: int = 100):
        prefix = match.rstrip("*")
        with self._lock:
            for key in list(self._values):
                if key.startswith(prefix) and self._alive(key):
                    yield key

    # -- lists (job event stream) ------------------------------------------
    def rpush(self, key: str, *values: str) -> int:
        with self._lock:
            self._lists[key].extend(str(v) for v in values)
            return len(self._lists[key])

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        with self._lock:
            items = self._lists.get(key, [])
            return items[start:] if end == -1 else items[start : end + 1]

    def ltrim(self, key: str, start: int, end: int) -> bool:
        with self._lock:
            items = self._lists.get(key, [])
            self._lists[key] = items[start:] if end == -1 else items[start : end + 1]
            return True

    def llen(self, key: str) -> int:
        with self._lock:
            return len(self._lists.get(key, []))

    def ping(self) -> bool:
        return True

    def pipeline(self, transaction: bool = True):
        return _InMemoryPipeline(self)

    def close(self) -> None:
        return None

    def flushdb(self) -> None:
        with self._lock:
            self._values.clear()
            self._lists.clear()


class _InMemoryPipeline:
    def __init__(self, backing: InMemoryRedis) -> None:
        self._backing = backing
        self._ops: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str):
        def record(*args: Any, **kwargs: Any) -> _InMemoryPipeline:
            self._ops.append((name, args, kwargs))
            return self

        return record

    def execute(self) -> list[Any]:
        results = []
        for name, args, kwargs in self._ops:
            results.append(getattr(self._backing, name)(*args, **kwargs))
        self._ops.clear()
        return results

    def __enter__(self) -> _InMemoryPipeline:
        return self

    def __exit__(self, *exc: Any) -> None:
        self._ops.clear()


_memory_fallback = InMemoryRedis()


def get_redis() -> Any:
    """Return a live Redis client, or the in-memory fallback."""
    global _client, _probe_failed_at

    if _client is not None:
        return _client
    if time.monotonic() - _probe_failed_at < _PROBE_BACKOFF_SECONDS:
        return _memory_fallback

    with _client_lock:
        if _client is not None:
            return _client
        try:
            import redis as redis_lib

            client = redis_lib.Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=5,
                health_check_interval=30,
                retry_on_timeout=True,
            )
            client.ping()
            client.is_distributed = True  # type: ignore[attr-defined]
            _client = client
            log.info("redis.connected", url=_safe_url(settings.redis_url))
            return _client
        except Exception as exc:  # pragma: no cover - depends on environment
            _probe_failed_at = time.monotonic()
            log.warning(
                "redis.unavailable",
                error=type(exc).__name__,
                fallback="in-memory (single process only)",
            )
            return _memory_fallback


def redis_healthy() -> bool:
    client = get_redis()
    if isinstance(client, InMemoryRedis):
        return False
    try:
        return bool(client.ping())
    except Exception:  # pragma: no cover
        return False


def reset_redis_client() -> None:
    """Used by tests to force re-detection."""
    global _client, _probe_failed_at
    with _client_lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:  # pragma: no cover
                pass
        _client = None
        _probe_failed_at = 0.0
        _memory_fallback.flushdb()


def _safe_url(url: str) -> str:
    if "@" in url:
        scheme, _, rest = url.partition("://")
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return url


class DistributedLock:
    """Best-effort mutual exclusion, e.g. one credit debit per job at a time."""

    def __init__(self, name: str, ttl: int = 60) -> None:
        self.key = f"lock:{name}"
        self.ttl = ttl
        self._token = str(time.time_ns())
        self._acquired = False

    def acquire(self, blocking: bool = False, timeout: float = 5.0) -> bool:
        client = get_redis()
        deadline = time.monotonic() + timeout
        while True:
            if client.set(self.key, self._token, ex=self.ttl, nx=True):
                self._acquired = True
                return True
            if not blocking or time.monotonic() > deadline:
                return False
            time.sleep(0.05)

    def release(self) -> None:
        if not self._acquired:
            return
        client = get_redis()
        try:
            if client.get(self.key) == self._token:
                client.delete(self.key)
        finally:
            self._acquired = False

    def __enter__(self) -> DistributedLock:
        self.acquire(blocking=True)
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()
