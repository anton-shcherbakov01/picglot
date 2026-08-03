"""Queue abstraction.

``celery`` is the production backend. ``inline`` runs the same task function in
a bounded thread pool inside the current process — this is what makes local
development and the end-to-end test suite work without a broker, while
executing exactly the same code path.
"""

from __future__ import annotations

import atexit
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from picglot.core.config import settings
from picglot.core.logging import get_logger

log = get_logger(__name__)

_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()
_futures: dict[str, Future[Any]] = {}


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        with _lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(
                    max_workers=max(2, settings.max_concurrent_jobs_per_user * 2),
                    thread_name_prefix="picglot-inline",
                )
                atexit.register(shutdown)
    return _executor


def enqueue(job_id: str, *, queue: str = "cpu", priority: int = 1) -> None:
    if settings.queue_backend == "inline":
        _enqueue_inline(job_id)
        return
    _enqueue_celery(job_id, queue=queue, priority=priority)


def _enqueue_inline(job_id: str) -> None:
    from picglot.workers.tasks import execute_job

    def _run() -> None:
        try:
            execute_job(job_id)
        except Exception:  # pragma: no cover - the task records its own failure
            log.exception("inline.job_crashed", job_id=job_id)
        finally:
            _futures.pop(job_id, None)

    _futures[job_id] = _get_executor().submit(_run)
    log.info("queue.enqueued", job_id=job_id, backend="inline")


def _enqueue_celery(job_id: str, *, queue: str, priority: int) -> None:
    from picglot.workers.celery_app import celery_app

    celery_app.send_task(
        "picglot.execute_job",
        args=[job_id],
        queue=queue,
        priority=max(0, min(9, priority)),
    )
    log.info("queue.enqueued", job_id=job_id, backend="celery", queue=queue)


def wait_for(job_id: str, timeout: float = 300.0) -> bool:
    """Block until an inline job finishes. Used by tests, never in production."""
    future = _futures.get(job_id)
    if future is None:
        return True
    try:
        future.result(timeout=timeout)
        return True
    except Exception:
        return False


def pending_count() -> int:
    return len([future for future in _futures.values() if not future.done()])


def shutdown(wait: bool = True) -> None:
    global _executor
    with _lock:
        if _executor is not None:
            _executor.shutdown(wait=wait, cancel_futures=not wait)
            _executor = None
