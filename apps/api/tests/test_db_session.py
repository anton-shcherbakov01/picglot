"""Engine and session-factory bootstrap."""

from __future__ import annotations

import threading


def test_session_factory_first_does_not_deadlock():
    """Building the factory before the engine must not block.

    `get_session_factory` holds the module lock while calling `get_engine`,
    which takes it again. With a non-reentrant lock the thread deadlocked
    against itself — invisible in the API, which builds its engine during
    startup, but every CLI command hung forever with no output.
    """
    from picglot.db import session as db_session

    # Cold start: the factory is the first thing anyone asks for.
    db_session._engine = None
    db_session._session_factory = None

    done = threading.Event()
    failure: list[BaseException] = []

    def build() -> None:
        try:
            db_session.get_session_factory()
        except BaseException as exc:
            failure.append(exc)
        finally:
            done.set()

    worker = threading.Thread(target=build, daemon=True)
    worker.start()
    assert done.wait(timeout=10), "get_session_factory deadlocked"
    assert not failure, failure[0]


def test_engine_is_created_once():
    from picglot.db import session as db_session

    db_session._engine = None
    db_session._session_factory = None
    assert db_session.get_engine() is db_session.get_engine()
