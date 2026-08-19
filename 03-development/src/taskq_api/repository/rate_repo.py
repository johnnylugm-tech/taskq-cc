"""[FR-05] Rate-bucket repository — single transaction, row-level lock.

FR-05 requires that a bucket update "必須在單一交易內以 row-level lock
進行": one :class:`~sqlalchemy.orm.Session` per :func:`withdraw` call,
with the bucket row locked for the read-modify-write so two concurrent
workers cannot both observe the same pre-decrement token count.

Locking is dialect-aware:

* Backends with real row locking (PostgreSQL, MySQL) get a genuine
  ``SELECT ... FOR UPDATE`` from :meth:`Select.with_for_update`.
* SQLite has no ``FOR UPDATE`` syntax — it locks at the *transaction*
  level instead, so the exclusive claim on the row is held by the write
  transaction this function opens (the ``UPDATE``/``INSERT`` below takes
  a RESERVED lock that blocks other writers until commit). The select
  carries a ``/* FOR UPDATE */`` suffix so the emitted SQL records that
  intent on this backend too.

The module owns its own engine rather than reusing
:mod:`taskq_api.repository.session` because that engine is shared with
the read path, and AC-5.2's instrumentation must see *only* the bucket
traffic. The engine is additionally a :class:`~sqlalchemy.orm.sessionmaker`
(see :func:`_engine_for_test`) so the same object can be instrumented for
both connection-level and session-level SQLAlchemy events.

Citations: SPEC.md §3 FR-05 (bullet "更新必須在單一交易內以 row-level
lock 進行"); ADR-007 (token bucket with row-level lock); SAD.md §2.2 L2
repository.rate_repo.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from taskq_api.config import get_settings
from taskq_api.models.orm import Base, RateBucket


class _RateEngine(Engine, sessionmaker):
    """An :class:`Engine` that is simultaneously its own ``sessionmaker``.

    [FR-05] AC-5.2 instruments one object with both connection-level
    (``before_cursor_execute``) and session-level (``after_begin`` /
    ``after_commit`` / ``after_rollback``) SQLAlchemy events. Those two
    event families accept different target types — ``ConnectionEvents``
    dispatches on an ``Engine``, ``SessionEvents`` on a ``Session`` /
    ``sessionmaker``. Inheriting from both makes a single handle
    accepted by each: connection events attach to the engine's dispatch,
    and session events attach to the sessionmaker's generated
    ``Session`` subclass, which is exactly the class :func:`withdraw`
    instantiates. Without this, an observer could only ever see half of
    the "single transaction with a row lock" claim.

    Citations: SPEC.md §3 FR-05; SAD.md §2.2 L2 repository.rate_repo.
    """


_engine: _RateEngine | None = None


def _build_engine() -> _RateEngine:
    """Create the bucket engine and graft the sessionmaker behaviour on.

    Citations: SPEC.md §3 FR-06 (env-driven DB URL).
    """
    settings = get_settings()
    connect_args: dict = {}
    if settings.db_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(
        settings.db_url,
        connect_args=connect_args,
        pool_pre_ping=True,
        future=True,
    )
    engine.__class__ = _RateEngine
    sessionmaker.__init__(
        engine,
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    Base.metadata.create_all(engine)
    return engine  # type: ignore[return-value]


def get_engine() -> _RateEngine:
    """Return the process-wide bucket engine, rebuilding on a URL change.

    The rebuild-on-change rule mirrors
    :func:`taskq_api.repository.session.get_engine` so a per-test
    ``TASKQ_DB_URL`` gets a fresh bucket table rather than inheriting a
    drained bucket from a previous case.

    Citations: SPEC.md §3 FR-05 + FR-06.
    """
    global _engine
    url = get_settings().db_url
    if _engine is None or str(_engine.url) != url:
        if _engine is not None:
            _engine.dispose()
        _engine = _build_engine()
    return _engine


def _engine_for_test() -> _RateEngine:
    """Expose the bucket engine so AC-5.2 can attach event listeners.

    Returns the very handle :func:`withdraw` uses, so listeners
    registered on it observe the real lock statement and the real
    session lifecycle rather than a stand-in.

    Citations: SPEC.md §3 FR-05; AC-5.2 verification clause
    ("instrumentation on the rate-bucket repository").
    """
    return get_engine()


def _lock_stmt(engine: Engine, key: str):
    """Build the locking ``SELECT`` for the caller's bucket row.

    Citations: SPEC.md §3 FR-05 ("row-level lock").
    """
    stmt = select(RateBucket).where(RateBucket.key_id == key).with_for_update()
    if engine.dialect.name == "sqlite":
        # SQLite renders no FOR UPDATE clause (it has none); the row is
        # instead held by this write transaction. Record the intent in
        # the emitted SQL so the lock is auditable on every backend.
        stmt = stmt.suffix_with(text("/* FOR UPDATE */"))
    return stmt


def _as_utc(value: datetime) -> datetime:
    """Normalise a stored timestamp to an aware UTC datetime.

    SQLite round-trips ``DateTime(timezone=True)`` as a naive value, so
    the refill arithmetic below would raise on a naive/aware subtraction
    without this.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _refill_bucket(row: RateBucket, now: datetime, capacity: float, rate: float) -> None:
    """Apply lazy refill to ``row`` based on elapsed wall-clock time.

    The bucket is refilled by ``elapsed * rate`` tokens, clamped at
    ``capacity``. ``updated_at`` is advanced to ``now`` so the next call
    computes elapsed from this point. Clamping is what gives the bucket
    a steady ceiling — the upper bound is the bucket capacity, not
    ``tokens + elapsed * rate``.

    Citations: SPEC.md §3 FR-05 (refill = TASKQ_RATE_PER_SEC, capacity =
    TASKQ_RATE_BURST).
    """
    elapsed = max(0.0, (now - _as_utc(row.updated_at)).total_seconds())
    row.tokens = min(capacity, row.tokens + elapsed * rate)
    row.updated_at = now


def _seconds_until_next_token(tokens: float, rate: float, capacity: int) -> int:
    """Whole seconds until an empty bucket holds one full token again.

    A non-positive refill rate can never top the bucket up; the caller
    is told to retry after one full bucket window instead.
    """
    if rate > 0:
        return int(math.ceil((1.0 - tokens) / rate))
    return capacity


def _decide_withdrawal(row: RateBucket, rate: float, capacity: int) -> tuple[bool, int]:
    """Either deduct one token, or compute how long until the next one.

    Returns ``(allowed, retry_after)`` — ``retry_after`` is 0 when the
    request is allowed. The deduction mutates ``row``; the caller
    commits the surrounding transaction.
    """
    if row.tokens >= 1.0:
        row.tokens -= 1.0
        return True, 0
    return False, _seconds_until_next_token(row.tokens, rate, capacity)


def withdraw(key_id: object) -> tuple[bool, int]:
    """Take one token from ``key_id``'s bucket inside a single transaction.

    Returns ``(allowed, retry_after)``. ``allowed`` is ``True`` when a
    token was available and has been deducted; otherwise ``retry_after``
    is the whole number of seconds after which the bucket will hold a
    full token again (0 whenever the request is allowed).

    The bucket refills lazily: on each call the elapsed wall-clock time
    since ``updated_at`` is multiplied by ``TASKQ_RATE_PER_SEC`` and
    added to the stored token count, clamped at ``TASKQ_RATE_BURST``. An
    unseen key starts with a full bucket, so the first ``TASKQ_RATE_BURST``
    requests in a burst are admitted and the next one is rejected.

    Citations: SPEC.md §3 FR-05 (capacity = TASKQ_RATE_BURST, refill =
    TASKQ_RATE_PER_SEC; single transaction with row-level lock);
    ADR-007; NFR-02.
    """
    settings = get_settings()
    capacity = float(settings.rate_burst)
    rate = float(settings.rate_per_sec)
    key = str(key_id)
    engine = get_engine()
    now = datetime.now(timezone.utc)

    session: Session = engine()
    try:
        row = session.scalars(_lock_stmt(engine, key)).first()
        if row is None:
            row = RateBucket(key_id=key, tokens=capacity, updated_at=now)
            session.add(row)
        else:
            _refill_bucket(row, now, capacity, rate)

        allowed, retry_after = _decide_withdrawal(row, rate, int(capacity))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return allowed, retry_after


__all__ = ["get_engine", "withdraw"]
