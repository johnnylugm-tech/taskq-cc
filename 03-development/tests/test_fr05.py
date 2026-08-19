"""FR-05: 流量控制 — TDD-RED failing tests.

Realises the 4 test cases of ``02-architecture/TEST_SPEC.md`` FR-05, plus
seven coverage-only unit tests added in the COVERAGE-FIX dispatch to
exercise the residual uncovered lines in ``deps.py``, ``rate_repo.py``,
and ``orm.py`` so the per-FR Gate 1 ``test_coverage`` dimension hits
100% (the project's Gate 1 audit threshold — see
``.methodology/lessons/bdd40d6652e9.md``).

Per [SAB — BINDING MODULE PATHS] the dotted names imported here are the
ones ``.methodology/SAB.json`` declares for FR-05:

  * ``taskq_api.api.deps``
  * ``taskq_api.service.ratelimit``
  * ``taskq_api.repository.rate_repo``
  * ``taskq_api.models.orm``

Sub-assertion predicates taken verbatim from the TEST_SPEC table:

  FR05-429                  result["status"] == 429                       (1)
  FR05-retry-after          result["retry_after"] >= 0                    (1)
  FR05-retry-after-int      result["retry_after_header"] == str(retry)    (1)
  FR05-row-lock             result["lock_event"] == "FOR UPDATE"          (2)
  FR05-single-session       result["session_count"] == 1                  (2)
  FR05-healthz-never-429    result["never_429"] == True                   (3)

In-process vs out-of-process (per [INTEGRATION FR GUIDELINES]):
* AC-5.1 / AC-5.3 / SEC-T02 fire IN-PROCESS through ``httpx.ASGITransport``
  so pytest-cov can measure deps / service / route code under the burst.
* AC-5.2 is an IN-PROCESS unit test driving
  ``taskq_api.repository.rate_repo.withdraw`` directly with a SQLAlchemy
  ``before_cursor_execute`` event listener that records lock statements
  and session lifecycles.

Citations: SPEC.md §3 FR-05 + §7 row 429 + §8 #9; ADR-007 (token bucket
with row-level lock); NFR-02 (rate-limit 429).
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

# Standard top-level imports — RED state. ``taskq_api.service.ratelimit``
# and ``taskq_api.repository.rate_repo`` do not exist on disk yet;
# pytest will report Exit Code 2 (Collection Error) which IS the
# expected RED state per the task brief.
from taskq_api.api import deps  # noqa: F401
from taskq_api.app import create_app
from taskq_api.errors import Problem
from taskq_api.models.orm import Base, RateBucket, _utcnow
from taskq_api.repository import rate_repo  # noqa: F401
from taskq_api.service import ratelimit  # noqa: F401
from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Test isolation — each test gets its own SQLite file so bucket state from
# the burst tests cannot leak across cases (per [INTEGRATION FR GUIDELINES]).
# ---------------------------------------------------------------------------

_SRC_ROOT = Path(__file__).resolve().parent.parent / "src"


@pytest.fixture(autouse=True)
def _isolated_bucket_db(tmp_path, monkeypatch):
    """Per-test TASKQ_DB_URL + TASKQ_HOME so the rate-bucket table is fresh.

    The burst tests (AC-5.1, SEC-T02) hammer the bucket with N+1 requests
    and rely on the first N succeeding; an unsanitised DB would carry a
    near-empty bucket forward from the previous test and the (N+1)th
    request would never observe 429.
    """
    db_path = tmp_path / "fr05_test.db"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("TASKQ_HOME", str(tmp_path))
    # A small burst so the (N+1)th request is over capacity within a
    # single test run; the burst tests override these.
    monkeypatch.setenv("TASKQ_RATE_BURST", "10")
    monkeypatch.setenv("TASKQ_RATE_PER_SEC", "0.1")


def _request(method: str, path: str, api_key: str) -> httpx.Response:
    """Issue one in-process request against the ASGI app."""
    app = create_app()

    async def _go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.request(
                method, path, headers={"X-API-Key": api_key}
            )

    return asyncio.run(_go())


# ---------------------------------------------------------------------------
# Test isolation — the rate limiter must key off the resolved API key, so
# the burst tests bind ``read_key`` to a known key_id via a stub.
# Without this, ``deps.require_api_key`` would reach into the real
# ``key_repo`` (which itself does not yet exist) and the tests would
# fail on infrastructure rather than on FR-05.
# ---------------------------------------------------------------------------

_READ_KEY_ID = "key-read"


@pytest.fixture(autouse=True)
def _stub_key_resolution(monkeypatch):
    """Bind ``read_key`` to a ``read``-scope key for the burst tests.

    The autouse fixture runs after ``_isolated_bucket_db`` so the DB
    path is already pointing at the per-test SQLite file.
    """
    def _resolve(plaintext: str):
        if plaintext == "read_key":
            return (_READ_KEY_ID, "read")
        return None

    monkeypatch.setattr(deps.auth, "resolve_api_key", _resolve)
    # The per-route closure imports ``auth`` via ``from taskq_api.service
    # import auth`` and resolves the symbol at call time; both bindings
    # must be patched so the closure sees the stub.
    monkeypatch.setattr("taskq_api.api.deps.auth.resolve_api_key", _resolve, raising=False)


# ---------------------------------------------------------------------------
# FR-05 cases
# ---------------------------------------------------------------------------


def test_ac_5_1_burst_over_capacity_returns_429_with_retry_after():  # NFR-02 (NP-03 — rate-limit 429 on burst over capacity), NFR-09 (zero-skip), NFR-10 (integration)
    """AC-5.1 — bursting beyond TASKQ_RATE_BURST against the same key returns
    429 + problem+json + a ``Retry-After`` header carrying a non-negative
    integer. Covers TEST_SPEC FR-05 row 1 (burst=20, capacity=10).
    """
    # Set a small bucket so the (N+1)th request is rejected without
    # depending on real-world timing.
    os.environ["TASKQ_RATE_BURST"] = "10"
    os.environ["TASKQ_RATE_PER_SEC"] = "0.01"

    burst = 20
    capacity = 10

    responses = [
        _request("GET", "/v1/tasks/1", "read_key")
        for _ in range(burst)
    ]
    statuses = [r.status_code for r in responses]

    # The (N+1)th request (index ``capacity``) is the first rejection.
    rejected = responses[capacity]
    retry_after_header = rejected.headers.get("Retry-After", "")

    # FR05-429 (applies_to 1)
    result_status = rejected.status_code
    assert result_status == 429
    # problem+json per FR-10
    assert "problem+json" in rejected.headers.get("content-type", "")
    # The first ``capacity`` requests must all have been admitted.
    assert all(s != 429 for s in statuses[:capacity])

    # FR05-retry-after (applies_to 1) — header parses to a non-negative int.
    retry_after = int(retry_after_header)
    assert retry_after >= 0

    # FR05-retry-after-int (applies_to 1) — header is the stringified int.
    result = {
        "status": result_status,
        "retry_after": retry_after,
        "retry_after_header": retry_after_header,
    }
    assert result["retry_after_header"] == str(result["retry_after"])


def test_ac_5_2_bucket_update_uses_row_level_lock_single_session():  # NFR-06 (repository — single transaction with row-level lock), NFR-09 (zero-skip)
    """AC-5.2 — ``taskq_api.repository.rate_repo.withdraw`` takes a row-level
    lock and runs inside a single ``Session`` per call.

    Covers TEST_SPEC FR-05 row 2. Verification: a SQLAlchemy
    ``before_cursor_execute`` event listener records the
    ``SELECT ... FOR UPDATE`` statement and a session-lifecycle counter
    asserts exactly one ``Session`` was opened for the call.
    """
    # The list is captured in the same scope as the call below so
    # ``monkeypatch`` cleanup restores both the listener and the
    # ``withdraw`` binding afterwards.
    sql_events: list[str] = []
    session_lifecycles: list[str] = []

    engine = rate_repo._engine_for_test()  # GREEN TODO: rate_repo must expose _engine_for_test() returning a SQLAlchemy Engine.

    from sqlalchemy import event  # local import — the listener API lives here, not in the SAB modules.

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001 — SQLAlchemy event signature
        sql_events.append(statement)
        if "FOR UPDATE" in statement.upper():
            sql_events.append("FOR UPDATE")

    @event.listens_for(engine, "after_begin")
    def _session_begin(session, transaction, connection):  # noqa: ANN001
        session_lifecycles.append("begin:" + str(id(session)))

    @event.listens_for(engine, "after_commit")
    def _session_commit(session):  # noqa: ANN001
        session_lifecycles.append("commit:" + str(id(session)))

    @event.listens_for(engine, "after_rollback")
    def _session_rollback(session):  # noqa: ANN001
        session_lifecycles.append("rollback:" + str(id(session)))

    # GREEN TODO: rate_repo.withdraw(key_id: int) -> (allowed: bool, retry_after: int)
    # must open a single Session, SELECT ... FOR UPDATE the rate_buckets row,
    # refill + decrement, and commit.
    allowed, retry_after = rate_repo.withdraw(key_id=42)

    result = {
        "lock_event": "FOR UPDATE" if any("FOR UPDATE" in s.upper() for s in sql_events) else "",
        "session_count": sum(1 for ev in session_lifecycles if ev.startswith("begin:")),
    }
    # FR05-row-lock (applies_to 2)
    assert result["lock_event"] == "FOR UPDATE"
    # FR05-single-session (applies_to 2)
    assert result["session_count"] == 1

    # Sanity — withdraw must return a tuple shaped (allowed, retry_after).
    assert isinstance(allowed, bool)
    assert isinstance(retry_after, int)
    assert retry_after >= 0


def test_ac_5_3_healthz_readyz_exempt_from_rate_limit():  # NFR-09 (zero-skip), NFR-10 (integration)
    """AC-5.3 — ``/healthz`` and ``/readyz`` are not counted against the bucket.

    Covers TEST_SPEC FR-05 row 3 (burst=100, capacity=2). 100 health
    requests with a low-burst bucket must not return a single 429.
    """
    # Pin the bucket to a tight capacity so any accidental counting
    # would surface as a 429.
    os.environ["TASKQ_RATE_BURST"] = "2"
    os.environ["TASKQ_RATE_PER_SEC"] = "0.01"

    burst = 100
    capacity = 2
    assert burst > capacity, "test invariant: burst must exceed capacity"

    never_429 = True
    for path in ("/healthz", "/readyz"):
        for _ in range(burst):
            response = _request("GET", path, "")
            if response.status_code == 429:
                never_429 = False
                break

    result = {"never_429": never_429}
    # FR05-healthz-never-429 (applies_to 3)
    assert result["never_429"] is True


def test_sec_t02_rate_limit_returns_429_with_retry_after():  # NFR-02 (NP-03 — security control: DoS mitigation), NFR-09 (zero-skip), NFR-10 (integration)
    """SEC-T-02 — a burst against ``/v1/tasks`` (capacity 5) returns 429 + ``Retry-After``.

    Covers TEST_SPEC FR-05 row 4 (burst=20, capacity=5). Independent of
    AC-5.1 so the security-control assertion survives even if the route
    or capacity in AC-5.1 changes.
    """
    os.environ["TASKQ_RATE_BURST"] = "5"
    os.environ["TASKQ_RATE_PER_SEC"] = "0.01"

    burst = 20
    capacity = 5

    responses = [
        _request("GET", "/v1/tasks", "read_key")
        for _ in range(burst)
    ]
    rejected = responses[capacity]
    retry_after_header = rejected.headers.get("Retry-After", "")

    result = {
        "status": rejected.status_code,
        "retry_after_header": retry_after_header,
    }
    # FR05-429 (applies_to 1 — same predicate reused for the security case)
    assert result["status"] == 429
    assert "problem+json" in rejected.headers.get("content-type", "")
    # FR05-retry-after — header parses to a non-negative int.
    retry_after = int(retry_after_header)
    assert retry_after >= 0
    # FR05-retry-after-int
    assert result["retry_after_header"] == str(retry_after)
    # The first ``capacity`` requests must all have been admitted.
    assert all(r.status_code != 429 for r in responses[:capacity])


# ---------------------------------------------------------------------------
# Coverage-only tests — added in the COVERAGE-FIX dispatch to exercise the
# residual uncovered lines so per-FR Gate 1 test_coverage reaches 100%.
# These tests do NOT change any FR-05 behaviour; they only call internal
# helpers that the burst tests reach transitively but which coverage reports
# as uncovered for one of three reasons:
#   1. The autouse stub short-circuits the resolution (deps.py:74).
#   2. A helper is only exercised via direct unit-test access
#      (rate_repo._as_utc, rate_repo._seconds_until_next_token).
#   3. A row default is never invoked because the producer passes the
#      column explicitly (orm._utcnow via RateBucket default).
# Each test targets exactly one previously-Miss'd line and asserts a
# minimal invariant on the result.
# ---------------------------------------------------------------------------


def test_orm_utcnow_used_as_ratebucket_default_when_column_not_provided():
    """Coverage for orm.py:17 — ``_utcnow`` is invoked when ``RateBucket`` is
    inserted without an explicit ``updated_at`` (the column default).
    """
    # Schema is created on first access of the engine by the autouse
    # ``_isolated_bucket_db`` fixture's TASKQ_DB_URL.
    engine = rate_repo.get_engine()
    # The bucket engine also sees the orm.Base metadata via
    # ``Base.metadata.create_all(engine)`` inside ``_build_engine``.
    assert Base.metadata.tables["rate_buckets"] in Base.metadata.tables.values()

    # ``_utcnow`` returns an aware UTC datetime; SQLite stores datetimes
    # as ISO strings without tz info, so on read the column comes back
    # naive. The local-clock window below is in UTC for direct comparison.
    before_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    with Session(engine) as session:
        row = RateBucket(key_id="default-utcnow-key", tokens=1.0)
        # Do NOT pass ``updated_at`` — the column default must fire.
        session.add(row)
        session.commit()
        # The default invoked ``_utcnow`` (orm.py:17) and populated the
        # column. We assert the value is within the call window — that
        # proves the default ran (and did not silently use None).
        assert row.updated_at is not None
    after_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    assert before_utc <= row.updated_at <= after_utc


def test_orm_utcnow_direct_returns_aware_utc():
    """Coverage for orm.py:17 — direct invocation of the ``_utcnow`` helper.

    Belt-and-braces: the column-default path above already hits the line,
    but a direct call guards against future refactors that move the
    default to a server-side construct while keeping the helper alive.
    """
    result = _utcnow()
    assert isinstance(result, datetime)
    assert result.tzinfo is not None


def test_rate_repo_as_utc_handles_naive_datetime():
    """Coverage for rate_repo.py:146 — ``_as_utc`` returns an aware
    datetime when given a naive one (the SQLite round-trip case).
    """
    naive = datetime(2026, 1, 1, 12, 0, 0)  # no tzinfo
    assert naive.tzinfo is None
    result = rate_repo._as_utc(naive)
    assert result.tzinfo is not None
    # Round-trip preserves wall-clock time.
    assert result.replace(tzinfo=None) == naive


def test_rate_repo_as_utc_handles_aware_datetime():
    """Coverage for rate_repo.py:147 — ``_as_utc`` normalises an aware
    non-UTC datetime to UTC (the ``astimezone`` branch).
    """
    from datetime import timedelta

    # Build an aware datetime in a non-UTC offset so the
    # ``astimezone(timezone.utc)`` branch is exercised.
    tz_plus_eight = timezone(timedelta(hours=8))
    aware = datetime(2026, 1, 1, 20, 0, 0, tzinfo=tz_plus_eight)
    result = rate_repo._as_utc(aware)
    assert result.tzinfo == timezone.utc
    # 20:00 +08:00 == 12:00 UTC.
    assert result == datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_rate_repo_seconds_until_next_token_returns_capacity_when_rate_zero():
    """Coverage for rate_repo.py:175 — ``_seconds_until_next_token``
    returns ``capacity`` when ``rate <= 0`` (the "non-positive refill
    rate can never top the bucket up" branch).
    """
    # rate=0.0 triggers the ``else`` arm.
    assert rate_repo._seconds_until_next_token(tokens=0.0, rate=0.0, capacity=10) == 10
    # Negative rate also falls through to capacity (defensive branch).
    assert rate_repo._seconds_until_next_token(tokens=0.5, rate=-1.0, capacity=5) == 5


def test_rate_repo_withdraw_rolls_back_on_exception(monkeypatch):
    """Coverage for rate_repo.py:227-229 — ``withdraw``'s ``except
    Exception`` arm rolls the session back and re-raises.

    The branch is hit by making ``_decide_withdrawal`` raise. We assert
    that ``withdraw`` propagates the original exception (the rollback
    path does NOT swallow it) and that a follow-up clean ``withdraw`` on
    the same key still succeeds — proving the prior transaction was
    actually rolled back (no partial state left on the row).
    """
    # Capture the real helper BEFORE we replace it so we can restore it
    # for the follow-up clean call.
    original_decide = rate_repo._decide_withdrawal

    def _raise(*_args, **_kwargs):
        raise RuntimeError("simulated decide failure")

    monkeypatch.setattr(rate_repo, "_decide_withdrawal", _raise)

    with pytest.raises(RuntimeError, match="simulated decide failure"):
        rate_repo.withdraw(key_id="rollback-key")

    # Restore the real helper and confirm the bucket is in a clean state
    # — the rollback prevented the half-written row from leaking out.
    monkeypatch.setattr(rate_repo, "_decide_withdrawal", original_decide)
    allowed, retry_after = rate_repo.withdraw(key_id="rollback-key")
    assert allowed is True
    assert retry_after == 0


def test_deps_resolve_or_raise_401_on_invalid_key():
    """Coverage for deps.py:74 — ``_resolve_or_raise`` raises a 401
    ``Problem`` when ``resolve_api_key`` returns ``None`` (no row / empty
    header). The autouse ``_stub_key_resolution`` fixture maps any
    plaintext other than ``"read_key"`` to ``None``.
    """
    with pytest.raises(Problem) as excinfo:
        deps._resolve_or_raise("definitely-not-a-key")
    assert excinfo.value.status == 401
    assert excinfo.value.title == "Unauthorized"
    assert excinfo.value.type_uri == "/errors/unauthorized"


def test_deps_require_api_key_returns_resolved_tuple():
    """Coverage for deps.py:97 — the standalone ``require_api_key``
    dependency returns the resolved ``(key_id, scope)`` tuple when the
    header is valid. The per-route closure
    (``require_api_key_with_scope``) is what production routes use, but
    AC-4.3's static introspection still walks this function, so its body
    must stay covered.
    """
    # The autouse stub binds ``read_key`` -> (``"key-read"``, ``"read"``).
    result = deps.require_api_key(x_api_key="read_key")
    assert result == (_READ_KEY_ID, "read")


def test_deps_enforce_scope_403_on_insufficient_scope():
    """Coverage for deps.py:113 — ``enforce_scope`` raises a 403
    ``Problem`` when the held scope does not cover the required scope.
    """
    with pytest.raises(Problem) as excinfo:
        deps.enforce_scope(api_key=(_READ_KEY_ID, "read"), required="admin")
    assert excinfo.value.status == 403
    assert excinfo.value.title == "Forbidden"
    assert excinfo.value.type_uri == "/errors/forbidden"
