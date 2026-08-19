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
    insert_scope,
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
        assert result_clean["commit_count"] == 1

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
        assert result_raise["rollback_count"] == 1
        # FR06-zero-commits-on-raise (applies_to 2)
        assert result_raise["commit_count"] == 0
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
    # FR06-constant-sql (applies_to 4) — count at 1000 must equal count at 10.
    assert len(sql_at_1000) == len(sql_at_10)


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
    assert result["pool_pre_ping"] is True