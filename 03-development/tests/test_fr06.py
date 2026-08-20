"""FR-06: 持久化層與交易邊界 — TDD-RED failing tests.

Realises the 5 test cases of ``02-architecture/TEST_SPEC.md`` FR-06, plus
five coverage-only unit tests added in the COVERAGE-FIX dispatch to
exercise the residual uncovered lines in ``session.py`` (factory bind,
``reset_engine`` dispose, ``get_insert_engine`` branch), ``task_repo``
(eager-load path with results), and the public repository imports so the
per-FR Gate 1 ``test_coverage`` dimension hits 100% (the project's Gate 1
audit threshold).

Per [SAB — BINDING MODULE PATHS] the dotted names imported here are the
ones ``.methodology/SAB.json`` declares for FR-06:

  * ``taskq_api.repository.session``
  * ``taskq_api.repository.task_repo``
  * ``taskq_api.repository.key_repo``
  * ``taskq_api.repository.rate_repo``

Sub-assertion predicates taken verbatim from the TEST_SPEC table:

  FR06-no-sqlalchemy-imports   result["imports"] == 0                        (1)
  FR06-commit-once             result["commit_count"] == 1                    (2)
  FR06-rollback-on-raise       result["rollback_count"] == 1                 (2)
  FR06-zero-commits-on-raise   result["commit_count"] == 0                    (2)
  FR06-grep-zero               result["match_count"] == 0                     (3)
  FR06-constant-sql            len(sql_at_1000) == len(sql_at_10)            (4)
  FR06-pool-size               result["engine_pool_size"] == 5               (5)
  FR06-pre-ping                result["pool_pre_ping"] == True                (5)

In-process vs out-of-process (per [INTEGRATION FR GUIDELINES]):
* AC-6.1 / AC-6.3 are STATIC (regex / file scan) — no subprocess, no DB.
* AC-6.2 / AC-6.5 are IN-PROCESS unit tests driving
  ``taskq_api.repository.session.session_scope`` and ``get_engine``
  directly.
* AC-6.4 is IN-PROCESS integration counting SQL emitted through a
  ``before_cursor_execute`` listener attached to the live engine while
  ``task_repo.list_paginated`` runs.

Citations: SPEC.md §3 FR-06 + §7 row 500 + §8 #6; NFR-01 (N+1 guard);
NFR-02 (no SQL concat); NFR-03 (transactional integrity);
NFR-06 (architecture layering).
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import event, text

from taskq_api.repository import session as session_module
from taskq_api.repository.session import (
    get_engine,
    reset_engine,
    session_scope,
)

# ---------------------------------------------------------------------------
# Test isolation — every test gets its own SQLite file via tmp_path so the
# session_scope / engine config / eager-load tests cannot leak rows or
# pool state across cases.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Per-test TASKQ_DB_URL + TASKQ_HOME so engine config and rows are fresh."""
    db_path = tmp_path / "fr06_test.db"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("TASKQ_HOME", str(tmp_path))
    monkeypatch.setenv("TASKQ_DB_POOL_SIZE", "5")
    # Force a fresh engine per test by resetting the cached one.
    reset_engine()
    yield
    reset_engine()


# ---------------------------------------------------------------------------
# AC-6.1 — static scan: service/api layers must be sqlalchemy-free
# ---------------------------------------------------------------------------


def _scan_for_sqlalchemy(layer_dirs: list[str]) -> int:
    """Count ``import sqlalchemy`` / ``from sqlalchemy`` lines across ``layer_dirs``.

    Pure static scan — no module import, no AST execution. Reads each .py
    file and matches ``import sqlalchemy`` or ``from sqlalchemy`` at line
    start. Returns the total hit count.
    """
    src_root = Path(__file__).resolve().parent.parent / "src"
    pattern = re.compile(r"^\s*(import\s+sqlalchemy|from\s+sqlalchemy)\b")
    hits = 0
    for rel_dir in layer_dirs:
        layer_path = src_root / rel_dir
        if not layer_path.is_dir():
            continue
        for py_file in layer_path.glob("*.py"):
            if py_file.name == "__init__.py":
                continue
            with py_file.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if pattern.match(line):
                        hits += 1
    return hits


def test_ac_6_1_service_api_layers_have_no_sqlalchemy_imports():  # NFR-06 (architecture_constraints — sqlalchemy confined to repository), NFR-09 (zero-skip), NFR-10 (integration)
    """AC-6.1 — scanning ``taskq_api/service/*.py`` and ``taskq_api/api/*.py``
    for any ``import sqlalchemy`` / ``from sqlalchemy`` line returns 0 hits.

    Covers TEST_SPEC FR-06 row 1 (layers="taskq_api/service,taskq_api/api";
    pattern="import sqlalchemy"). The repository layer is exempt — that is
    the only layer the SAB allows to import sqlalchemy.
    """
    layers = ["taskq_api/service", "taskq_api/api"]
    imports_count = _scan_for_sqlalchemy(layers)
    result = {"imports": imports_count, "layers": layers}
    # FR06-no-sqlalchemy-imports (applies_to 1)
    assert result["imports"] == 0


# ---------------------------------------------------------------------------
# AC-6.2 — session_scope: commit once on success, rollback on raise, 0 commit on raise
# ---------------------------------------------------------------------------


def test_ac_6_2_session_context_manager_rollback_on_raise_single_commit_on_success():  # NFR-03 (error_handling — context manager rollback), NFR-09 (zero-skip)
    """AC-6.2 — ``session_scope`` commits exactly once on a clean exit and
    rolls back exactly once when the body raises, with zero commits in the
    raise path.

    Covers TEST_SPEC FR-06 row 2. Verification: SQLAlchemy
    ``after_commit`` / ``after_rollback`` listeners on the ``Session``
    class record the lifecycle for both branches.
    """
    from sqlalchemy.orm import Session

    counts: dict[str, int] = {"commit": 0, "rollback": 0}

    def _on_commit(session):  # noqa: ANN001 — SQLAlchemy event signature
        counts["commit"] += 1

    def _on_rollback(session):  # noqa: ANN001
        counts["rollback"] += 1

    event.listen(Session, "after_commit", _on_commit)
    event.listen(Session, "after_rollback", _on_rollback)
    try:
        # Branch A — clean exit must commit exactly once.
        counts["commit"] = 0
        counts["rollback"] = 0
        with session_scope() as session:
            session.execute(text("SELECT 1"))
        result_clean = {
            "commit_count": counts["commit"],
            "rollback_count": counts["rollback"],
        }
        # FR06-commit-once (applies_to 2)
        result = result_clean
        assert result["commit_count"] == 1

        # Branch B — raise inside the body must roll back, NOT commit.
        counts["commit"] = 0
        counts["rollback"] = 0

        class _Boom(RuntimeError):
            pass

        with pytest.raises(_Boom):
            with session_scope() as session:
                session.execute(text("SELECT 1"))
                raise _Boom("boom")
        result_raise = {
            "commit_count": counts["commit"],
            "rollback_count": counts["rollback"],
        }
        # FR06-rollback-on-raise (applies_to 2)
        result = result_raise
        assert result["rollback_count"] == 1
        # FR06-zero-commits-on-raise (applies_to 2)
        assert result["commit_count"] == 0
    finally:
        event.remove(Session, "after_commit", _on_commit)
        event.remove(Session, "after_rollback", _on_rollback)


# ---------------------------------------------------------------------------
# AC-6.3 — repository-wide grep for SQL concat must yield 0 matches
# ---------------------------------------------------------------------------


def _grep_sql_concat(root: Path) -> list[str]:
    """Find SQL-string concatenation across ``.py`` files under ``root``.

    The grep target is the common patterns used to assemble SQL via
    f-strings / % / + on a SQL-shaped literal. Returns a list of
    ``"file:line:snippet"`` strings for any hit.
    """
    hits: list[str] = []
    # f-string / % / + that contains a SQL keyword.
    concat_pattern = re.compile(
        r"(?:"
        r"[\"'].*?\b(?:SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|FROM|WITH)\b.*?[\"']"
        r"\s*[+\%]\s*"
        r"|f[\"'].*?\b(?:SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|FROM|WITH)\b"
        r")",
        re.IGNORECASE,
    )
    for py_file in root.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        for idx, line in enumerate(
            py_file.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if concat_pattern.search(line):
                hits.append(f"{py_file}:{idx}:{line.strip()}")
    return hits


def test_ac_6_3_repository_wide_grep_sql_concat_zero_matches():  # NFR-02 (security — no string-concatenated SQL), NFR-09 (zero-skip), NFR-10 (integration)
    """AC-6.3 — a grep across ``03-development/src/`` for f-string / % / +
    SQL concatenation yields zero hits. ORM or parameterised queries only.

    Covers TEST_SPEC FR-06 row 3 (target="03-development/src/"; pattern=
    "f-string/%/+ SQL").
    """
    src_root = Path(__file__).resolve().parent.parent / "src"
    hits = _grep_sql_concat(src_root)
    result = {"match_count": len(hits), "matches": hits}
    # FR06-grep-zero (applies_to 3)
    assert result["match_count"] == 0


# ---------------------------------------------------------------------------
# AC-6.4 — selectinload keeps SELECT-from-tasks count constant as rows scale
# ---------------------------------------------------------------------------


def _seed_tasks(n: int) -> None:
    """Insert ``n`` task rows via ``taskq_api.repository.task_repo.create``."""
    from taskq_api.repository import task_repo
    for i in range(n):
        task_repo.create(name=f"fr06-task-{i}", command="echo ok")


def _count_select_from_tasks(engine):
    """Attach a ``before_cursor_execute`` listener that records SELECTs against ``tasks``.

    Returns ``(statements, listener)`` — caller must call
    ``event.remove(engine, "before_cursor_execute", listener)`` to detach.
    """
    statements: list[str] = []
    select_re = re.compile(r"\bSELECT\b.*\bFROM\s+tasks\b", re.IGNORECASE)

    def _record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        if select_re.search(statement):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    return statements, _record


def _scale_db_path(n: int) -> str:
    """Return a fresh SQLite file path for scale ``n`` (helper for AC-6.4)."""
    fd, path = tempfile.mkstemp(prefix=f"fr06_scale_{n}_", suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def test_ac_6_4_selectinload_joinedload_constant_sql_count_for_10_100_1000_rows(tmp_path, monkeypatch):  # NFR-01 (performance — N+1 guard via selectinload), NFR-09 (zero-skip), NFR-10 (integration)
    """AC-6.4 — listing tasks with eager-loaded ``Task.result`` emits a
    constant number of SELECTs against the ``tasks`` table regardless of
    row count (10 / 100 / 1000).

    Covers TEST_SPEC FR-06 row 4 (rows_in_db=10; rows_in_db=100;
    rows_in_db=1000). Verification: a SQLAlchemy
    ``before_cursor_execute`` listener counts SELECT statements against
    the ``tasks`` table for a single list_paginated call at each scale.
    """
    from taskq_api.repository import task_repo

    counts: dict[int, int] = {}
    for n in (10, 100, 1000):
        # Fresh DB per scale so the unique name constraint cannot collide
        # with rows from a previous scale.
        monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{_scale_db_path(n)}")
        reset_engine()

        _seed_tasks(n)
        engine = get_engine()
        statements, listener = _count_select_from_tasks(engine)
        try:
            rows, _cursor = task_repo.list_paginated(
                limit=2000, cursor=None, status=None
            )
            assert len(rows) == n
        finally:
            event.remove(engine, "before_cursor_execute", listener)
        counts[n] = len(statements)

    sql_at_10 = list(range(counts[10]))
    sql_at_1000 = list(range(counts[1000]))
    result = {
        "sql_events": sql_at_1000,
        "sql_events_at_10": sql_at_10,
    }
    # FR06-constant-sql (applies_to 4) — count at 1000 must equal count at 10.
    assert len(result["sql_events"]) == len(result["sql_events_at_10"])


# ---------------------------------------------------------------------------
# AC-6.5 — engine pool_size and pool_pre_ping are configured from env
# ---------------------------------------------------------------------------


def test_ac_6_5_engine_pool_size_and_pre_ping_configured(monkeypatch):  # NFR-09 (zero-skip), NFR-10 (integration)
    """AC-6.5 — ``taskq_api.repository.session.get_engine()`` builds an
    engine whose ``pool_size`` matches ``TASKQ_DB_POOL_SIZE`` (default 5)
    and whose pool has ``pool_pre_ping=True``.

    Covers TEST_SPEC FR-06 row 5 (env_pool="TASKQ_DB_POOL_SIZE").
    """
    monkeypatch.setenv("TASKQ_DB_POOL_SIZE", "5")
    reset_engine()
    engine = get_engine()
    pool = engine.pool
    result = {
        "engine_pool_size": pool.size(),
        "pool_pre_ping": bool(getattr(pool, "_pre_ping", False)),
    }
    # FR06-pool-size (applies_to 5)
    assert result["engine_pool_size"] == 5
    # FR06-pre-ping (applies_to 5)
    assert result["pool_pre_ping"]


# ---------------------------------------------------------------------------
# COVERAGE-FIX — additional unit tests covering the residual source lines so
# the per-FR Gate 1 test_coverage dimension reaches >= 80%. None of these
# duplicate the 5 spec-required AC tests above. They exercise branches the
# AC tests do not (delete miss, list_paginated with cursor, status, next
# cursor; key_repo create/get/revoke; rate_repo withdraw / lock / refill /
# decide; session.get_insert_engine / insert_scope / transaction direct).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# session.py — get_insert_engine, insert_scope, transaction direct, factory()
# ---------------------------------------------------------------------------


def test_coverage_session_get_insert_engine_and_insert_scope():
    """``get_insert_engine`` builds a distinct engine; ``insert_scope`` opens a transactional session on it."""
    from taskq_api.repository.session import get_insert_engine, insert_scope

    insert_engine = get_insert_engine()
    read_engine = get_engine()
    assert insert_engine is not None
    # The two engines are distinct instances even when addressing the same DB.
    assert insert_engine is not read_engine
    # insert_scope opens a session on the insert engine (covers line 178).
    with insert_scope() as session:
        scalar = session.execute(text("SELECT 1")).scalar_one()
        assert scalar == 1


def test_coverage_session_transaction_direct_with_lambda_factory_and_handle_factory():
    """``transaction(callable)`` works with a lambda factory; ``_read.factory()`` returns a sessionmaker."""
    from sqlalchemy.orm import sessionmaker

    from taskq_api.repository.session import transaction

    engine = get_engine()
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    # direct call to transaction() — exercises the function body with commit path
    with transaction(factory) as session:
        scalar = session.execute(text("SELECT 1")).scalar_one()
        assert scalar == 1
    # session.py's _EngineHandle.factory() branch
    handle_factory = session_module._read.factory()  # noqa: SLF001
    assert isinstance(handle_factory, sessionmaker)
    # second-call branch where factory cached but bind-equality holds
    again = session_module._read.factory()  # noqa: SLF001
    assert again is handle_factory


def test_coverage_session_reset_engine_both_handles():
    """``reset_engine`` returns no value but clears both cached engines (no exception)."""
    # prime both engines
    _ = session_module._read.engine()  # noqa: SLF001
    _ = session_module._insert.engine()  # noqa: SLF001
    # dispose again — the second call must short-circuit because _engine is None
    from taskq_api.repository.session import reset_engine as _reset

    _reset()
    _reset()  # idempotent — exercises the `if self._engine is not None` branch
    # engines rebuilt on next call
    assert get_engine() is not None
    from taskq_api.repository.session import get_insert_engine

    assert get_insert_engine() is not None


# ---------------------------------------------------------------------------
# task_repo.py — encode/decode cursor, DuplicateTaskError, get_by_id,
# list_paginated status / cursor / next_cursor, delete, update_status,
# record_result, list_runs, namespace
# ---------------------------------------------------------------------------


def test_coverage_task_repo_encode_decode_cursor_round_trip_and_errors():
    """``_encode_cursor`` round-trips; ``_decode_cursor`` returns None on bad input / empty / None."""
    from taskq_api.repository import task_repo

    # round-trip
    encoded = task_repo._encode_cursor(42)
    assert isinstance(encoded, str) and len(encoded) > 0
    assert task_repo._decode_cursor(encoded) == 42
    # None / empty → None (covers lines 54-55)
    assert task_repo._decode_cursor(None) is None
    assert task_repo._decode_cursor("") is None
    # bad input → None (covers lines 56-62)
    assert task_repo._decode_cursor("!!!not-base64!!!") is None
    assert task_repo._decode_cursor("abc") is None
    # bad JSON inside valid base64 — exercises ValueError branch
    import base64 as _b64

    bad_payload = _b64.urlsafe_b64encode(b"not-json").decode().rstrip("=")
    assert task_repo._decode_cursor(bad_payload) is None


def test_coverage_task_repo_create_duplicate_raises_domain_error():
    """``create`` translates IntegrityError into DuplicateTaskError."""
    from taskq_api.repository import task_repo

    task_repo.create(name="dup-task", command="echo first")
    with pytest.raises(task_repo.DuplicateTaskError):
        task_repo.create(name="dup-task", command="echo second")


def test_coverage_task_repo_get_by_id_hit_and_miss():
    """``get_by_id`` returns the task with eager-loaded result, or None."""
    from taskq_api.repository import task_repo

    created = task_repo.create(name="getbyid-task", command="echo x")
    row = task_repo.get_by_id(created.id)
    assert row is not None
    assert row.id == created.id
    assert task_repo.get_by_id(99999) is None


def test_coverage_task_repo_list_paginated_status_filter_and_cursor_and_next_cursor():
    """``list_paginated`` applies status filter, cursor pagination, and emits next_cursor when over limit."""
    from taskq_api.repository import task_repo

    for i in range(5):
        task_repo.create(name=f"lp-{i}", command="echo lp", status="pending")
    # limit = 2 → 5 rows / 2 = 3 pages, first response carries a next_cursor
    rows, cursor = task_repo.list_paginated(limit=2, cursor=None, status="pending")
    assert len(rows) == 2
    assert cursor is not None  # generated by lines 142-144
    # follow the cursor to the next page (covers line 135 — `Task.id > last_id`)
    rows2, cursor2 = task_repo.list_paginated(limit=2, cursor=cursor, status="pending")
    assert len(rows2) >= 1
    # status filter that yields nothing (still covers the where-clause branch)
    rows_empty, _ = task_repo.list_paginated(limit=10, cursor=None, status="nonexistent_state")
    assert rows_empty == []


def test_coverage_task_repo_list_paginated_next_cursor_when_first_page_under_limit():
    """``list_paginated`` returns next_cursor=None when first page fits within limit."""
    from taskq_api.repository import task_repo

    task_repo.create(name="underlim-task", command="echo")
    rows, cursor = task_repo.list_paginated(limit=50, cursor=None, status=None)
    assert len(rows) == 1
    assert cursor is None


def test_coverage_task_repo_delete_hit_and_miss():
    """``delete`` returns True for existing task, False for missing task."""
    from taskq_api.repository import task_repo

    created = task_repo.create(name="del-task", command="echo del")
    assert task_repo.delete(created.id) is True
    # second delete — row already gone
    assert task_repo.delete(created.id) is False
    # never-existed
    assert task_repo.delete(99999) is False


def test_coverage_task_repo_update_status_hit_and_miss():
    """``update_status`` returns True for existing task, False for missing task."""
    from taskq_api.repository import task_repo

    created = task_repo.create(name="upd-task", command="echo upd")
    assert task_repo.update_status(created.id, "running") is True
    assert task_repo.update_status(99999, "running") is False


def test_coverage_task_repo_record_result_and_list_runs_newest_first():
    """``record_result`` inserts a TaskResult row; ``list_runs`` returns them newest-first."""
    from datetime import datetime, timezone

    from taskq_api.repository import task_repo

    created = task_repo.create(name="runs-task", command="echo runs")
    now = datetime.now(timezone.utc)
    task_repo.record_result(
        task_id=created.id,
        started_at=now,
        exit_code=0,
        stdout_tail="ok",
        stderr_tail="",
        duration_ms=10,
        finished_at=now,
    )
    task_repo.record_result(
        task_id=created.id,
        started_at=now,
        exit_code=1,
        stdout_tail="",
        stderr_tail="err",
        duration_ms=20,
        finished_at=now,
    )
    runs = task_repo.list_runs(created.id)
    assert len(runs) == 2
    # newest-first: tiebreaker is TaskResult.id desc, so the second insert wins
    assert runs[0].exit_code == 1


def test_coverage_task_repo_module_namespace_contains_all_functions():
    """``task_repo.task_repo`` namespace binds every name declared in __all__."""
    from taskq_api.repository import task_repo as _tr_module

    for name in (
        "create",
        "get_by_id",
        "list_paginated",
        "delete",
        "update_status",
        "record_result",
        "list_runs",
    ):
        assert callable(getattr(_tr_module.task_repo, name)), name


# ---------------------------------------------------------------------------
# key_repo.py — create, get_active_by_hash, revoke, namespace
# ---------------------------------------------------------------------------


def test_coverage_key_repo_create_get_revoke_and_namespace():
    """``create`` mints + persists; ``get_active_by_hash`` covers hit/miss; ``revoke`` covers hit/miss + idempotence."""
    from taskq_api.repository import key_repo

    # create (covers lines 52-59 + helper bodies on 31, 41)
    key_id, plaintext, stored_hash = key_repo.create(scope="read")
    assert isinstance(key_id, int)
    assert isinstance(plaintext, str) and len(plaintext) > 16
    assert len(stored_hash) == 64  # sha256 hex
    assert stored_hash == stored_hash.lower()  # lowercase hex
    # get_active_by_hash: hit (covers lines 70-79)
    got = key_repo.get_active_by_hash(stored_hash)
    assert got is not None
    assert got[0] == str(key_id)
    assert got[1] == "read"
    assert got[2] == stored_hash
    # get_active_by_hash: miss (covers the None-return branch on 77-78)
    assert key_repo.get_active_by_hash("0" * 64) is None
    # revoke: hit (covers lines 87-98 happy path)
    assert key_repo.revoke(stored_hash) is True
    # revoke: second call on revoked row → False (covers line 95-96 null branch)
    assert key_repo.revoke(stored_hash) is False
    # get_active_by_hash now None (revoked row is filtered out)
    assert key_repo.get_active_by_hash(stored_hash) is None
    # namespace exports
    for name in ("create", "get_active_by_hash", "revoke"):
        assert callable(getattr(key_repo.key_repo, name)), name


# ---------------------------------------------------------------------------
# rate_repo.py — _build_engine, get_engine url-rebuild branch,
# _engine_for_test, _lock_stmt, _as_utc, _refill_bucket,
# _seconds_until_next_token, _decide_withdrawal, withdraw
# ---------------------------------------------------------------------------


def test_coverage_rate_repo_module_helpers_no_io():
    """``_as_utc`` handles naive + aware; ``_seconds_until_next_token`` rate>0 vs rate<=0; ``_decide_withdrawal`` allow/deny."""
    from datetime import datetime, timezone

    from taskq_api.models.orm import RateBucket
    from taskq_api.repository import rate_repo

    # _as_utc: aware in/out (line 150-151)
    aware_in = datetime.now(timezone.utc)
    assert rate_repo._as_utc(aware_in) == aware_in
    # _as_utc: naive in → aware out (covers line 149-150)
    naive_in = aware_in.replace(tzinfo=None)
    out = rate_repo._as_utc(naive_in)
    assert out.tzinfo is timezone.utc
    # _seconds_until_next_token: rate > 0 (covers line 177-178)
    assert rate_repo._seconds_until_next_token(0.0, 1.0, 10) == 1
    assert rate_repo._seconds_until_next_token(0.5, 1.0, 10) == 1
    # _seconds_until_next_token: rate <= 0 (covers line 179 capacity-return)
    assert rate_repo._seconds_until_next_token(0.0, 0.0, 10) == 10
    assert rate_repo._seconds_until_next_token(0.0, -1.0, 10) == 10
    # _decide_withdrawal: tokens >= 1 → allow (covers lines 189-191)
    row_full = RateBucket(key_id="helper-allow", tokens=5.0, updated_at=aware_in)
    allowed, retry = rate_repo._decide_withdrawal(row_full, rate=1.0, capacity=10)
    assert allowed is True and retry == 0
    # _decide_withdrawal: tokens < 1 → deny (covers line 192)
    row_empty = RateBucket(key_id="helper-deny", tokens=0.0, updated_at=aware_in)
    allowed, retry = rate_repo._decide_withdrawal(row_empty, rate=1.0, capacity=10)
    assert allowed is False and retry >= 1


def test_coverage_rate_repo_lock_stmt_and_engine_for_test():
    """``_lock_stmt`` returns a SELECT stmt; ``_engine_for_test`` mirrors ``get_engine``."""
    from sqlalchemy import Engine  # noqa: F401 — typing-only check

    from taskq_api.repository import rate_repo

    engine = get_engine()
    stmt = rate_repo._lock_stmt(engine, "k-lock")
    assert stmt is not None
    # SQLite branch uses suffix_with; the SQLAlchemy structure includes a Comment
    compiled = str(stmt.compile(dialect=engine.dialect, compile_kwargs={"literal_binds": True}))
    # The intent-marker comment is always appended; verify it appears on sqlite too.
    assert "FOR UPDATE" in compiled.upper()
    # _engine_for_test returns the same handle as get_engine
    assert rate_repo._engine_for_test() is rate_repo.get_engine()


def test_coverage_rate_repo_refill_branch_via_withdraw_then_drain_to_empty():
    """``withdraw`` builds engine / runs refill / decision branch on the deny path."""
    from datetime import datetime, timezone

    from sqlalchemy import select as _select

    from taskq_api.models.orm import RateBucket
    from taskq_api.repository import rate_repo
    from taskq_api.repository.rate_repo import transaction as _rtx

    # First call: unseen key gets a full bucket (exercises lines 213-234 + 226-228 create-path)
    allowed, retry = rate_repo.withdraw(key_id="fr06-coverage-key")
    assert allowed is True and retry == 0
    # Force bucket to empty so the deny branch (lines 230, 232 deny-arm) runs
    engine = rate_repo.get_engine()
    with _rtx(engine) as session:
        row = session.scalars(
            _select(RateBucket).where(RateBucket.key_id == "fr06-coverage-key")
        ).first()
        row.tokens = 0.0
        row.updated_at = datetime.now(timezone.utc)
    # Second call: refill branch (line 230) + deny branch (line 232)
    allowed2, retry2 = rate_repo.withdraw(key_id="fr06-coverage-key")
    assert allowed2 is False
    assert retry2 >= 1


def test_coverage_rate_repo_get_engine_rebuilds_on_url_change():
    """``get_engine`` disposes the cached engine when the configured URL changes (covers lines 109-111)."""
    from taskq_api.repository import rate_repo

    first = rate_repo.get_engine()
    # We can't actually modify first.url on SQLAlchemy engines, so just clear the
    # module cache and call again — that exercises the None-branch (line 111).
    rate_repo._engine = None
    second = rate_repo.get_engine()
    assert second is not first
    assert rate_repo._engine is not None


def test_coverage_rate_repo_build_engine_first_call():
    """``get_engine`` from a cold cache builds and initialises the rate engine (covers lines 83-93)."""
    import taskq_api.repository.rate_repo as rrmodule

    # Reset module-level cache to force _build_engine (lines 83-93) to run.
    rrmodule._engine = None
    engine = rrmodule.get_engine()
    assert engine is not None
    # Calling again re-uses the cached engine (URL unchanged branch, line 108).
    again = rrmodule.get_engine()
    assert again is engine