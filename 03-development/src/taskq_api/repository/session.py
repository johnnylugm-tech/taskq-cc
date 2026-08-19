"""[FR-01] SQLAlchemy engine + session_scope context manager.

Citations: SPEC.md §3 FR-06 (transaction boundary); SAD.md §2.2 session.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from taskq_api.config import get_settings
from taskq_api.models.orm import Base

_engine: Engine | None = None
_insert_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None
_InsertSessionFactory: sessionmaker[Session] | None = None


def _build_engine() -> Engine:
    settings = get_settings()
    url = settings.db_url
    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(
        url,
        connect_args=connect_args,
        pool_size=settings.db_pool_size,
        pool_pre_ping=True,
        future=True,
    )
    return engine


def get_engine() -> Engine:
    """Return the process-wide SQLAlchemy engine, creating it on demand.

    Citations: SPEC.md §3 FR-06 (env-driven DB URL); test isolation —
    the engine is rebuilt when ``TASKQ_DB_URL`` changes so per-test
    ``tmp_path`` databases do not leak rows between cases.
    """
    global _engine, _SessionFactory
    current_url = get_settings().db_url
    if _engine is None or str(_engine.url) != current_url:
        if _engine is not None:
            _engine.dispose()
        _engine = _build_engine()
        # Ensure tables exist for the green TDD step. This is dev/test only;
        # production code uses Alembic migrations (FR-07).
        Base.metadata.create_all(_engine)
        # Drop the cached session factory — it was bound to the old engine.
        _SessionFactory = None
    return _engine


def get_insert_engine() -> Engine:
    """Separate engine used for inserts.

    Returns a distinct SQLAlchemy Engine instance so SQLAlchemy
    ``before_cursor_execute`` listeners attached to ``get_engine()`` (used
    in AC-1.7 to count the *list_paginated* SQL surface) do not capture
    write-side SQL. Both engines point at the same database file, so reads
    through ``get_engine()`` still observe freshly inserted rows.

    Citations: SPEC.md §3 FR-06; rebuilt together with the read engine
    when ``TASKQ_DB_URL`` changes (test isolation).
    """
    global _insert_engine, _InsertSessionFactory
    current_url = get_settings().db_url
    if _insert_engine is None or str(_insert_engine.url) != current_url:
        if _insert_engine is not None:
            _insert_engine.dispose()
        _insert_engine = _build_engine()
        Base.metadata.create_all(_insert_engine)
        _InsertSessionFactory = None
    return _insert_engine


def reset_engine() -> None:
    """Drop the cached engine so the next call rebuilds from current env."""
    global _engine, _insert_engine, _SessionFactory, _InsertSessionFactory
    if _engine is not None:
        _engine.dispose()
    if _insert_engine is not None:
        _insert_engine.dispose()
    _engine = None
    _insert_engine = None
    _SessionFactory = None
    _InsertSessionFactory = None


def _factory() -> sessionmaker[Session]:
    global _SessionFactory
    # Always probe ``get_engine()`` so a TASKQ_DB_URL change forces a
    # factory rebuild bound to the new engine (test isolation).
    engine = get_engine()
    if _SessionFactory is None or _SessionFactory.kw["bind"] is not engine:
        _SessionFactory = sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            future=True,
        )
    return _SessionFactory


def _insert_factory() -> sessionmaker[Session]:
    global _InsertSessionFactory
    # Mirror of ``_factory`` — bind to the current insert engine so a
    # URL change rebuilds the factory rather than reusing a stale bind.
    engine = get_insert_engine()
    if _InsertSessionFactory is None or _InsertSessionFactory.kw["bind"] is not engine:
        _InsertSessionFactory = sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            future=True,
        )
    return _InsertSessionFactory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Open a session, commit on success, rollback on error, always close."""
    session = _factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def insert_scope() -> Iterator[Session]:
    """Open an insert-only session on the private insert engine."""
    session = _insert_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = [
    "get_engine",
    "get_insert_engine",
    "reset_engine",
    "session_scope",
    "insert_scope",
]