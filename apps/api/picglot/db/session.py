"""Engine and session management.

A single synchronous engine is shared by the API (FastAPI runs sync endpoints in
a thread pool) and the Celery workers. Sessions are always scoped: either by a
request via ``db_session`` or by an explicit ``session_scope()`` block.
"""

from __future__ import annotations

import threading
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, QueuePool

from picglot.core.config import settings
from picglot.core.logging import get_logger

log = get_logger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
#: Re-entrant on purpose: `get_session_factory` holds this lock while calling
#: `get_engine`, which takes it again. A plain Lock deadlocked the calling
#: thread against itself whenever the factory was built before the engine —
#: which is every CLI invocation, since the API creates its engine during
#: startup and then never reaches the slow path.
_lock = threading.RLock()


def _engine_kwargs() -> dict[str, Any]:
    if settings.database_url.startswith("sqlite"):
        return {
            "connect_args": {"check_same_thread": False},
            "poolclass": NullPool,
            "echo": settings.database_echo,
        }
    return {
        "poolclass": QueuePool,
        "pool_size": settings.database_pool_size,
        "max_overflow": settings.database_max_overflow,
        "pool_pre_ping": True,
        "pool_recycle": 1800,
        "echo": settings.database_echo,
        "connect_args": {
            "application_name": settings.otel_service_name,
            "connect_timeout": 10,
        },
    }


def get_engine() -> Engine:
    global _engine
    if _engine is not None:
        return _engine
    with _lock:
        if _engine is None:
            _engine = create_engine(settings.database_url, future=True, **_engine_kwargs())
            if settings.database_url.startswith("sqlite"):
                _install_sqlite_pragmas(_engine)
            log.info("db.engine_created", dialect=_engine.dialect.name)
    return _engine


def _install_sqlite_pragmas(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        with _lock:
            if _session_factory is None:
                _session_factory = sessionmaker(
                    bind=get_engine(),
                    autoflush=False,
                    autocommit=False,
                    expire_on_commit=False,
                    future=True,
                )
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commits on success, rolls back on any exception."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency. The route body owns the transaction boundary."""
    session = get_session_factory()()
    try:
        yield session
        if session.in_transaction():
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


#: Alias used in route signatures for readability.
db_session = get_session


def database_healthy() -> bool:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # pragma: no cover - environment dependent
        log.warning("db.health_failed", error=type(exc).__name__)
        return False


def dispose_engine() -> None:
    global _engine, _session_factory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _session_factory = None


def pool_stats() -> dict[str, int]:
    engine = get_engine()
    pool = engine.pool
    if not hasattr(pool, "size"):
        return {}
    return {
        "size": pool.size(),  # type: ignore[attr-defined]
        "checked_in": pool.checkedin(),  # type: ignore[attr-defined]
        "checked_out": pool.checkedout(),  # type: ignore[attr-defined]
        "overflow": pool.overflow(),  # type: ignore[attr-defined]
    }
