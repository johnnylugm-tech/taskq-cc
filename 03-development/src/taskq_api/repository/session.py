"""[FR-01, FR-06] SQLAlchemy engines + the transactional session boundary.

Two policies live here, once each, so no repository module can drift
from them:

* :func:`build_engine` applies the connection-pool policy —
  ``pool_size=TASKQ_DB_POOL_SIZE`` and ``pool_pre_ping=True``
  (FR-06 AC-6.5). Every engine in the project is built through it,
  including the rate-bucket engine in
  :mod:`taskq_api.repository.rate_repo`.
* :func:`transaction` is the single transaction boundary — commit on a
  clean exit, rollback on any exception, always close (FR-06 AC-6.2).
  :func:`session_scope` and :func:`insert_scope` are the two named
  entry points onto it; no repository hand-rolls the boundary.

Citations: SPEC.md §3 FR-06 (transaction boundary + pool config);
SAD.md §2.2 session; NFR-03 (transactional integrity).
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Callable, Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from taskq_api.config import get_settings
from taskq_api.models.orm import Base

# [FR-09 / SEC-T-05] SQLAlchemy's ``sqlalchemy.engine.create`` logger
# emits the raw connection URL at DEBUG level on every ``create_engine``
# call. When the URL carries a password (production Postgres,
# production MySQL, anything with userinfo) the password substring
# lands in every operator's DEBUG log — a textbook information-
# disclosure sink. Quiet that one logger to WARNING inside
# :func:`build_engine` so the URL is never stringified in a log line
# during the build window; the engine itself still receives the URL
# and connects normally.
_SA_CREATE_LOGGER = "sqlalchemy.engine.create"


def build_engine(url: str | None = None) -> Engine:
    """Create an engine carrying the project-wide connection-pool policy.

    ``pool_size`` is read from ``TASKQ_DB_POOL_SIZE`` and
    ``pool_pre_ping`` is always on, so a connection that went stale while
    idle in the pool is discarded rather than handed to a caller
    (FR-06 AC-6.5). ``url`` defaults to ``TASKQ_DB_URL``; callers pass it
    explicitly only when they have already resolved it.

    [FR-09] ``sqlalchemy.engine.create`` is briefly raised to WARNING so
    the URL it would otherwise log at DEBUG is not emitted; the original
    level is restored before returning.

    Citations: SPEC.md §3 FR-06 (pool_size + pool_pre_ping) + FR-09
    (DB URL password redaction in logs); SEC-T-05.
    """
    settings = get_settings()
    target = settings.db_url if url is None else url
    connect_args: dict = {}
    if target.startswith("sqlite"):
        # SQLite refuses cross-thread connection reuse by default, but the
        # pool hands a connection to whichever worker thread asks for it.
        connect_args["check_same_thread"] = False
    sa_logger = logging.getLogger(_SA_CREATE_LOGGER)
    original_level = sa_logger.level
    if sa_logger.level == logging.NOTSET or sa_logger.level < logging.WARNING:
        sa_logger.setLevel(logging.WARNING)
    try:
        return create_engine(
            target,
            connect_args=connect_args,
            pool_size=settings.db_pool_size,
            pool_pre_ping=True,
            future=True,
        )
    finally:
        sa_logger.setLevel(original_level)


@contextmanager
def transaction(new_session: Callable[[], Session]) -> Iterator[Session]:
    """Run one unit of work inside a single session with an explicit boundary.

    Commit on a clean exit; on any exception roll back and re-raise (the
    exception is never swallowed); close the session either way. This is
    the context manager FR-06 AC-6.2 requires the transaction boundary to
    be guaranteed by — repository entry points compose it rather than
    repeating ``try / commit / except rollback / finally close``.

    ``new_session`` is any zero-argument callable returning a
    :class:`~sqlalchemy.orm.Session`, which a
    :class:`~sqlalchemy.orm.sessionmaker` already is.

    Citations: SPEC.md §3 FR-06 ("成功 commit、例外 rollback"); NFR-03.
    """
    session = new_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class _EngineHandle:
    """A lazily built engine paired with the session factory bound to it.

    Keeping the two together is what makes a factory unable to outlive
    its engine: a ``TASKQ_DB_URL`` change (each test gets its own
    ``tmp_path`` database) rebuilds both in one step, so no factory is
    ever left pointing at a disposed engine.
    """

    def __init__(self) -> None:
        self._engine: Engine | None = None
        self._factory: sessionmaker[Session] | None = None

    def engine(self) -> Engine:
        """Return the engine, rebuilding it when the configured URL changed."""
        url = get_settings().db_url
        if self._engine is None or str(self._engine.url) != url:
            self.dispose()
            self._engine = build_engine(url)
            # Ensure tables exist for the green TDD step. This is dev/test
            # only; production schema is owned by Alembic migrations (FR-07).
            Base.metadata.create_all(self._engine)
        return self._engine

    def factory(self) -> sessionmaker[Session]:
        """Return a sessionmaker bound to the current engine."""
        engine = self.engine()
        if self._factory is None or self._factory.kw["bind"] is not engine:
            self._factory = sessionmaker(
                bind=engine,
                autoflush=False,
                autocommit=False,
                expire_on_commit=False,
                future=True,
            )
        return self._factory

    def dispose(self) -> None:
        """Drop the engine and its factory; the next access rebuilds both."""
        if self._engine is not None:
            self._engine.dispose()
        self._engine = None
        self._factory = None


# The shared read/write engine, and a private write-only engine (see
# ``get_insert_engine`` for why the second one exists).
_read = _EngineHandle()
_insert = _EngineHandle()


def get_engine() -> Engine:
    """Return the process-wide read/write engine, creating it on demand.

    Rebuilt whenever ``TASKQ_DB_URL`` changes so a per-test ``tmp_path``
    database cannot leak rows into the next case.

    Citations: SPEC.md §3 FR-06 (env-driven DB URL).
    """
    return _read.engine()


def get_insert_engine() -> Engine:
    """Return the private insert engine — a distinct :class:`Engine` instance.

    Writes run on their own engine so ``before_cursor_execute`` listeners
    attached to :func:`get_engine` — how FR-01 AC-1.7 and FR-06 AC-6.4
    measure the *list_paginated* statement count — never observe
    write-side traffic. Both engines address the same database, so reads
    still see freshly inserted rows.

    Citations: SPEC.md §3 FR-06; NFR-01 (N+1 guard measurement).
    """
    return _insert.engine()


def reset_engine() -> None:
    """Drop both cached engines so the next call rebuilds from current env."""
    _read.dispose()
    _insert.dispose()


# Test-only alias — kept under a leading-underscore name so production
# callers do not pick it up via tab-completion or ``from M import *``.
# The semantics are identical to ``reset_engine``; the alias exists
# only because a handful of FR-09 readiness tests historically
# targeted a per-test monkeypatched URL and want a stable name that
# reads as "test scaffolding" rather than "production reset".
_reset_engine_for_tests = reset_engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope on the shared read/write engine (FR-06 AC-6.2)."""
    with transaction(_read.factory()) as session:
        yield session


@contextmanager
def insert_scope() -> Iterator[Session]:
    """Transactional scope on the private insert engine (FR-06 AC-6.2)."""
    with transaction(_insert.factory()) as session:
        yield session


__all__ = [
    "build_engine",
    "transaction",
    "get_engine",
    "get_insert_engine",
    "reset_engine",
    "session_scope",
    "insert_scope",
]
