"""FR-05: 流量控制 — TDD-RED failing tests.

Realises the 4 test cases of ``02-architecture/TEST_SPEC.md`` FR-05.

Per [SAB — BINDING MODULE PATHS] the dotted names imported here are the
ones ``.methodology/SAB.json`` declares for FR-05:

  * ``taskq_api.api.deps``
  * ``taskq_api.service.ratelimit``
  * ``taskq_api.repository.rate_repo``
  * ``taskq_api.models.orm``

None of ``service.ratelimit`` and ``repository.rate_repo`` exist on
disk yet — the Gate 1 phantom-module check would BLOCK if GREEN created
a different name. RED, by contrast, fails cleanly at import time and
that is a valid RED state per the brief.

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
import sqlite3
from pathlib import Path

import httpx
import pytest

# Standard top-level imports — RED state. ``taskq_api.service.ratelimit``
# and ``taskq_api.repository.rate_repo`` do not exist on disk yet;
# pytest will report Exit Code 2 (Collection Error) which IS the
# expected RED state per the task brief.
from taskq_api.api import deps  # noqa: F401
from taskq_api.app import create_app
from taskq_api.repository import rate_repo  # noqa: F401
from taskq_api.service import ratelimit  # noqa: F401

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
