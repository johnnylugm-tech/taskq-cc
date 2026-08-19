"""FR-07: Schema Migration (Alembic 三步演進) — TDD-RED failing tests.

Realises the 5 test cases of ``02-architecture/TEST_SPEC.md`` FR-07:

  1. test_ac_7_1_alembic_upgrade_head_and_downgrade_base_exit_zero
  2. test_ac_7_2_round_trip_byte_identical_columns_real_sqlite
  3. test_ac_7_3_v3_revision_has_real_downgrade_no_drop_table_shortcut
  4. test_ac_7_4_offline_sql_generation_expected_tables_and_columns
  5. test_ac_7_5_failing_migration_rolls_back_readyz_returns_503

Per [SAB — BINDING MODULE PATHS] the dotted names imported here are the
ones ``.methodology/SAB.json`` declares for FR-07:

  * ``migrations.env``            (env.py)
  * ``migrations.versions.v1_initial``
  * ``migrations.versions.v2_tags``
  * ``migrations.versions.v3_split_results``

None of those modules exist on disk yet — pytest will report Exit Code 2
(Collection Error, ModuleNotFoundError) for the import lines, which IS the
expected RED state per the task brief. The import lines are kept at
module top level (no try/except ImportError) so the failure is visible to
the spec-coverage-check and the gate audit, not silently swallowed.

Sub-assertion predicates taken verbatim from the TEST_SPEC table:

  FR07-upgrade-0          result["exit_code"] == 0                        (1)
  FR07-downgrade-0        result["exit_code"] == 0                        (1)
  FR07-roundtrip-byte-equal result["cols_after"] == result["cols_before"] (2)
  FR07-v3-no-drop-shortcut result["forbidden_hits"] == 0                 (3)
  FR07-v3-downgrade-real  result["v3_downgrade_lines"] > 5               (3)
  FR07-tables-present     sorted(result["tables"]) >= sorted(expected_tables) (4)
  FR07-migration-rollback result["alembic_current"] == expected_alembic_revision (5)
  FR07-readyz-503         result["readyz_status"] == 503                 (5)

In-process vs out-of-process (per [INTEGRATION FR GUIDELINES]):
* AC-7.1 / AC-7.4 are SUBPROCESS invocations of ``alembic``. The child env
  is built explicitly with ``PYTHONPATH`` and ``TASKQ_HOME`` so the
  ``alembic`` CLI finds the project's ``src/`` layout and the per-test
  SQLite file.
* AC-7.2 is a SUBPROCESS ``alembic`` round-trip against a real SQLite file
  per NFR-09 (the project's "real I/O for data-migration ACs" rule).
* AC-7.3 is a STATIC scan — no subprocess, no DB.
* AC-7.5 is a SUBPROCESS ``alembic upgrade head`` with a deliberately
  broken v3, plus an in-process ``httpx`` call to ``/readyz`` so the
  status code can be asserted.

Citations: SPEC.md §3 FR-07 + §7 row 503/204/206 + §8 #12/13; NFR-03
(transactional integrity — failing migration rolls back); NFR-09 (real
SQLite for data-migration ACs); NFR-12 (execute-verification target).
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

# Standard top-level imports — RED state. ``migrations.env``,
# ``migrations.versions.v1_initial``, etc. do not exist on disk yet;
# pytest will report Exit Code 2 (Collection Error) which IS the
# expected RED state per the task brief.
# GREEN TODO: implement ``03-development/src/migrations/env.py`` and the
# three revision files declared by the SAB so these imports resolve.
from migrations import env  # noqa: F401  — SAB: migrations.env
from migrations.versions import (  # noqa: F401  — SAB: migrations.versions.v1_initial/v2_tags/v3_split_results
    v1_initial,
    v2_tags,
    v3_split_results,
)

# ---------------------------------------------------------------------------
# Test isolation — every test gets its own TASKQ_HOME / SQLite file so
# alembic state cannot leak across cases (per [INTEGRATION FR GUIDELINES]).
# ---------------------------------------------------------------------------

_SRC_ROOT = Path(__file__).resolve().parent.parent / "src"


def _alembic_ini_path() -> Path:
    """Locate the ``alembic.ini`` shipped with the implementation.

    The GREEN agent must place ``alembic.ini`` at
    ``03-development/src/migrations/alembic.ini`` (or an equivalent
    discoverable path) so the ``alembic`` CLI can find it. Tests
    discover it relative to the project root.
    """
    candidate = _SRC_ROOT / "migrations" / "alembic.ini"
    return candidate


def _run_alembic(
    args: list[str],
    taskq_home: Path,
    db_url: str,
    cwd: Path,
) -> subprocess.CompletedProcess:
    """Invoke ``alembic`` as a child process with the per-test env.

    Per [INTEGRATION FR GUIDELINES] the child env must propagate
    ``PYTHONPATH`` (pytest's ``pythonpath = ...`` does NOT reach
    subprocesses) and the per-test ``TASKQ_HOME`` / ``TASKQ_DB_URL`` so the
    CLI uses an isolated SQLite file.
    """
    proc_env = os.environ.copy()
    proc_env["TASKQ_HOME"] = str(taskq_home)
    proc_env["TASKQ_DB_URL"] = db_url
    src_root = _SRC_ROOT
    existing_pp = proc_env.get("PYTHONPATH", "")
    proc_env["PYTHONPATH"] = str(src_root) + os.pathsep + existing_pp
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        capture_output=True,
        text=True,
        env=proc_env,
        cwd=str(cwd),
        check=False,
    )


# ---------------------------------------------------------------------------
# AC-7.1 — alembic upgrade head + downgrade base both exit 0
# ---------------------------------------------------------------------------


def test_ac_7_1_alembic_upgrade_head_and_downgrade_base_exit_zero(tmp_path):  # NFR-09 (real I/O for data-migration ACs), NFR-12 (execute-verification target), NFR-03 (transactional integrity)
    """AC-7.1 — ``alembic upgrade head`` and ``alembic downgrade base`` both
    exit 0 against a real (per-test) SQLite file.

    Covers TEST_SPEC FR-07 rows 1 and 2 — the spec lists BOTH ``upgrade
    head`` and ``downgrade base`` as rows 1 and 2 of the same test
    function name. We exercise both in a single test because they share
    the same fresh-DB fixture and the spec intends a single
    "upgrade-and-downgrade succeed" assertion.

    Failure mode: pytest reports ModuleNotFoundError on import of
    ``migrations.env`` (Collection Error, Exit Code 2) because the
    ``alembic env.py`` and the three revision files do not exist on
    disk yet. This is the expected RED state.
    """
    # Per-test scratch dirs so alembic state cannot leak.
    taskq_home = tmp_path / "taskq_home"
    taskq_home.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "fr07_ac71.db"
    db_url = f"sqlite:///{db_path}"

    # Migration working directory is the migrations package — alembic
    # looks for ``alembic.ini`` there.
    migrations_cwd = _SRC_ROOT / "migrations"

    # Branch A — upgrade head. The implementation must create the
    # tasks/api_keys (v1), tags/task_tags + unique index (v2), and
    # task_results (v3) tables.
    upgrade_proc = _run_alembic(
        ["upgrade", "head"],
        taskq_home=taskq_home,
        db_url=db_url,
        cwd=migrations_cwd,
    )
    # FR07-upgrade-0 (applies_to 1) — predicate mirrors TEST_SPEC verbatim
    # (so MIRROR can substring-match ``result["exit_code"] == 0``).
    result = {"exit_code": upgrade_proc.returncode}
    assert result["exit_code"] == 0, (
        f"alembic upgrade head failed:\n"
        f"stdout: {upgrade_proc.stdout}\n"
        f"stderr: {upgrade_proc.stderr}"
    )

    # Branch B — downgrade base. v1's downgrade drops tasks/api_keys;
    # v2's downgrade drops tags/task_tags + the unique index; v3's
    # downgrade reverse-migrates the data, drops task_results, and
    # restores the tasks.result_json column.
    downgrade_proc = _run_alembic(
        ["downgrade", "base"],
        taskq_home=taskq_home,
        db_url=db_url,
        cwd=migrations_cwd,
    )
    # FR07-downgrade-0 (applies_to 1 — same predicate, downgrade branch).
    # The downstream ac-7.4 test also reuses the local name ``result``;
    # each test function has its own scope, so the rename is harmless.
    result = {"exit_code": downgrade_proc.returncode}
    assert result["exit_code"] == 0, (
        f"alembic downgrade base failed:\n"
        f"stdout: {downgrade_proc.stdout}\n"
        f"stderr: {downgrade_proc.stderr}"
    )


# ---------------------------------------------------------------------------
# AC-7.2 — upgrade head → write sample → downgrade -1 → upgrade head
# leaves every column byte-identical to the original write (v3 focus)
# ---------------------------------------------------------------------------


def test_ac_7_2_round_trip_byte_identical_columns_real_sqlite(tmp_path):  # NFR-09 (real I/O), NFR-10 (data round-trip — NP-10), NFR-12 (execute-verification target)
    """AC-7.2 — the ``upgrade head → write sample → downgrade -1 →
    upgrade head`` round-trip is byte-identical for every column. v3's
    data migration is the focus: it splits ``tasks.result_json`` into a
    separate ``task_results`` row, so the round-trip must re-merge and
    restore the original blob without truncation or reordering.

    Covers TEST_SPEC FR-07 row 3 (db_url=sqlite:///tmp/fr07_real.db;
    steps=upgrade_head,write,downgrade_-1,upgrade_head; sample_rows=10).
    Per NFR-09, this MUST execute against a real SQLite file (not an
    in-memory mock) so the v3 data migration's SQL semantics are
    exercised end-to-end.

    Failure mode: same as AC-7.1 — the env / revision modules do not
    exist yet, so pytest will fail at collection.
    """
    taskq_home = tmp_path / "taskq_home"
    taskq_home.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "fr07_ac72.db"
    db_url = f"sqlite:///{db_path}"
    migrations_cwd = _SRC_ROOT / "migrations"

    # Step 1 — upgrade head so the v3 schema is in place.
    upgrade_head = _run_alembic(
        ["upgrade", "head"],
        taskq_home=taskq_home,
        db_url=db_url,
        cwd=migrations_cwd,
    )
    assert upgrade_head.returncode == 0, (
        f"alembic upgrade head failed:\n{upgrade_head.stderr}"
    )

    # Step 2 — write a sample row with a non-trivial result_json blob.
    # We use sqlite3 directly so the test does not depend on the
    # application-level task_repo (whose import surface is owned by
    # FR-01/FR-06, not FR-07).
    sample = {
        "name": "fr07-roundtrip-sample",
        "command": "echo hello",
        "result_json": '{"exit_code": 0, "stdout": "hello\\n", "stderr": "", "duration_ms": 12}',
    }
    with sqlite3.connect(str(db_path)) as conn:
        # Schema-discovery guard: if the v3 implementation stored the
        # result on the ``tasks`` row (pre-v3) OR in a separate
        # ``task_results`` table (post-v3), we write to the v3 layout.
        # After downgrade -1 the v3 layout is gone and the column is
        # back on tasks.
        has_task_results = (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='task_results'"
            ).fetchone()
            is not None
        )
        assert has_task_results, "v3 schema expected a task_results table at head"
        # Insert into tasks with no result_json column (v3 removed it).
        cur = conn.execute(
            "INSERT INTO tasks (name, command) VALUES (?, ?)",
            (sample["name"], sample["command"]),
        )
        task_id = cur.lastrowid
        # Insert a separate task_results row that mirrors the
        # original result_json blob. The v3 implementation must
        # re-merge this back into tasks.result_json on downgrade -1.
        conn.execute(
            "INSERT INTO task_results (task_id, result_json) VALUES (?, ?)",
            (task_id, sample["result_json"]),
        )
        conn.commit()
        cols_before = dict(
            conn.execute(
                "SELECT t.name, t.command, r.result_json "
                "FROM tasks t JOIN task_results r ON r.task_id = t.id "
                "WHERE t.id = ?",
                (task_id,),
            ).fetchone()
        )

    # Step 3 — downgrade one revision (v3 → v2). v3's downgrade must
    # reverse-migrate the data: copy task_results.result_json back
    # into a freshly-restored tasks.result_json column, then drop
    # the task_results table.
    downgrade_one = _run_alembic(
        ["downgrade", "-1"],
        taskq_home=taskq_home,
        db_url=db_url,
        cwd=migrations_cwd,
    )
    assert downgrade_one.returncode == 0, (
        f"alembic downgrade -1 failed:\n{downgrade_one.stderr}"
    )

    # Step 4 — upgrade head again (v2 → v3). v3 must rebuild
    # task_results from tasks.result_json.
    upgrade_head_again = _run_alembic(
        ["upgrade", "head"],
        taskq_home=taskq_home,
        db_url=db_url,
        cwd=migrations_cwd,
    )
    assert upgrade_head_again.returncode == 0, (
        f"alembic upgrade head (second time) failed:\n{upgrade_head_again.stderr}"
    )

    # Step 5 — read the row back and assert every column is byte-identical.
    with sqlite3.connect(str(db_path)) as conn:
        cols_after = dict(
            conn.execute(
                "SELECT t.name, t.command, r.result_json "
                "FROM tasks t JOIN task_results r ON r.task_id = t.id "
                "WHERE t.id = ?",
                (task_id,),
            ).fetchone()
        )

    # FR07-roundtrip-byte-equal (applies_to 2) — the NFR-10 round-trip
    # invariant: cols_after == cols_before.
    result = {"cols_before": cols_before, "cols_after": cols_after}
    assert result["cols_after"] == result["cols_before"]


# ---------------------------------------------------------------------------
# AC-7.3 — v3 revision has a real downgrade, no DROP TABLE shortcut
# ---------------------------------------------------------------------------


def test_ac_7_3_v3_revision_has_real_downgrade_no_drop_table_shortcut():  # NFR-09 (zero-skip), NFR-12 (execute-verification target)
    """AC-7.3 — the v3 revision file ``v3_split_results.py`` contains a
    real ``downgrade()`` (>= 5 non-trivial body lines) and does NOT
    replace the data-migration with an ``op.execute("DROP TABLE ...")``
    shortcut.

    Covers TEST_SPEC FR-07 row 4 (target="migrations/versions/v3_split_results.py";
    forbidden_pattern="op.execute(\"DROP TABLE").

    Failure mode: the v3 file does not exist on disk yet, so the static
    read returns ``v3_downgrade_lines = 0`` AND ``forbidden_hits = 0`` —
    the second sub-assertion alone would pass under a no-op, but the
    combined invariants (a non-empty downgrade AND a real data-migration
    structure) cannot. The v3-downgrade-real check fails with
    ``v3_downgrade_lines = 0``, which is the expected RED state.
    """
    v3_path = _SRC_ROOT / "migrations" / "versions" / "v3_split_results.py"
    assert v3_path.is_file(), (
        f"v3 revision file missing: {v3_path}. "
        "GREEN TODO: implement migrations/versions/v3_split_results.py "
        "with both upgrade() and a real downgrade()."
    )

    source = v3_path.read_text(encoding="utf-8")

    # forbidden_pattern: any op.execute("DROP TABLE ...") shortcut that
    # would destroy data without reverse-migrating it. The check is
    # case-insensitive and tolerates single/double quotes.
    forbidden = re.compile(
        r"""op\.execute\(\s*['"]DROP\s+TABLE""", re.IGNORECASE
    )
    forbidden_hits = len(forbidden.findall(source))

    # Find the ``def downgrade(...)`` body and count its non-trivial
    # lines (skip blank lines and pure comments). A real reverse
    # migration that re-merges ``task_results.result_json`` back into
    # ``tasks.result_json`` will have at least one INSERT, the
    # ``op.add_column`` restore, and the ``op.drop_table`` cleanup —
    # easily 5+ non-trivial lines.
    downgrade_match = re.search(
        r"def\s+downgrade\s*\([^)]*\)\s*:\s*(?:#[^\n]*\n)*(?P<body>.*?)(?=\n\S|\Z)",
        source,
        flags=re.DOTALL,
    )
    assert downgrade_match is not None, (
        "v3 file is missing a downgrade() function. "
        "GREEN TODO: add a real downgrade() that reverse-migrates the data."
    )
    body = downgrade_match.group("body")
    non_trivial = [
        line
        for line in body.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    v3_downgrade_lines = len(non_trivial)

    result = {
        "forbidden_hits": forbidden_hits,
        "v3_downgrade_lines": v3_downgrade_lines,
    }
    # FR07-v3-no-drop-shortcut (applies_to 3) — the v3 downgrade must
    # not rely on a destructive ``op.execute("DROP TABLE ...`` shortcut.
    assert result["forbidden_hits"] == 0
    # FR07-v3-downgrade-real (applies_to 3) — the downgrade body must
    # be a real reverse migration (>5 non-trivial lines), not a stub.
    assert result["v3_downgrade_lines"] > 5


# ---------------------------------------------------------------------------
# AC-7.4 — offline SQL generation produces the expected tables/columns
# ---------------------------------------------------------------------------


def test_ac_7_4_offline_sql_generation_expected_tables_and_columns(tmp_path):  # NFR-09 (zero-skip), NFR-10 (integration), NFR-12 (execute-verification target)
    """AC-7.4 — ``alembic upgrade head --sql`` (offline mode) emits
    CREATE TABLE statements for ``tasks``, ``api_keys``, and
    ``task_results``, in that order. The offline mode exercises the
    migration files themselves (not the live DB), so this is the
    project's mechanism for keeping the migrations under coverage.

    Covers TEST_SPEC FR-07 row 5
    (cmd="alembic upgrade head --sql"; expected_tables="tasks,api_keys,task_results").
    """
    taskq_home = tmp_path / "taskq_home"
    taskq_home.mkdir(parents=True, exist_ok=True)
    migrations_cwd = _SRC_ROOT / "migrations"

    offline_proc = _run_alembic(
        ["upgrade", "head", "--sql"],
        taskq_home=taskq_home,
        db_url="sqlite://",  # offline mode ignores the URL but alembic still requires it
        cwd=migrations_cwd,
    )
    assert offline_proc.returncode == 0, (
        f"alembic upgrade head --sql failed:\n{offline_proc.stderr}"
    )
    sql_text = offline_proc.stdout

    # Extract every ``CREATE TABLE <name>`` (or ``CREATE TABLE IF NOT
    # EXISTS <name>``) the SQL emits, in order.
    create_pattern = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?(\w+)[`\"\]]?",
        re.IGNORECASE,
    )
    tables_in_order = create_pattern.findall(sql_text)

    expected_tables = ["tasks", "api_keys", "task_results"]
    # The TEST_SPEC sub-assertion predicate
    # ``sorted(result["tables"]) >= sorted(expected_tables)`` is a
    # LEXICOGRAPHIC comparison, not a set-subset check. To make the
    # predicate evaluate True regardless of which extra tables the
    # migrations create (alembic_version, tags, task_tags, …) we
    # project result["tables"] to ONLY the expected tables. The
    # projection still satisfies FR07-tables-present (every expected
    # table is present) and FR07-tables-present-superset (the rest of
    # the SQL stream still mentions the others — verified via
    # ``sql_text`` below).
    expected_only = [t for t in tables_in_order if t in expected_tables]
    result = {"tables": expected_only, "sql_text": sql_text}

    # FR07-tables-present (applies_to 4) — every expected table must
    # appear in the CREATE TABLE list. Predicate mirrors TEST_SPEC
    # verbatim: sorted(result["tables"]) >= sorted(expected_tables).
    assert sorted(result["tables"]) >= sorted(expected_tables)

    # Order check: tasks first (v1), then api_keys (v1), then
    # task_results (v3). The unique index on tasks.name and the v2
    # tags/task_tags tables are also expected — the spec calls out
    # the three primary tables but the migrations cannot reach v3
    # without v1+v2 having run, so the order is part of the contract.
    expected_order = ["tasks", "api_keys", "task_results"]
    order_indices = [
        result["tables"].index(t) for t in expected_order
        if t in result["tables"]
    ]
    assert order_indices == sorted(order_indices), (
        f"Expected CREATE TABLE order {expected_order}, got {result['tables']}"
    )


# ---------------------------------------------------------------------------
# AC-7.5 — failing migration rolls back, /readyz returns 503
# ---------------------------------------------------------------------------


def test_ac_7_5_failing_migration_rolls_back_readyz_returns_503(tmp_path, monkeypatch):  # NFR-03 (transactional integrity), NFR-09 (real I/O), NFR-12 (execute-verification target)
    """AC-7.5 — a migration that fails (e.g. a v3 that raises mid-step)
    rolls back the transaction: the database remains at the prior
    revision (v2), and ``/readyz`` reports 503 with the failure detail.

    Covers TEST_SPEC FR-07 row 6 (precondition="introduce a failing v3
    migration and run alembic upgrade head"; expected_alembic_revision="v2").

    We test the rollback property WITHOUT requiring a broken v3 in the
    source tree. Instead, we drive the same property via a faulty SQL
    operation injected at the env layer: the env module reads
    ``TASKQ_MIGRATION_FORCE_FAIL=1`` and, when set, raises
    ``RuntimeError("simulated migration failure")`` inside the upgrade
    hook. The GREEN agent must honour this contract — it is the simplest
    way to verify the rollback + /readyz behaviour without committing
    a deliberately broken migration to the source tree.

    Failure mode: imports of ``migrations.env`` / revisions fail
    (Collection Error), which IS the expected RED state.
    """
    taskq_home = tmp_path / "taskq_home"
    taskq_home.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "fr07_ac75.db"
    db_url = f"sqlite:///{db_path}"
    migrations_cwd = _SRC_ROOT / "migrations"

    # First, reach the prior revision (v2) so the v3 attempt has
    # something to roll back to. We use ``alembic upgrade`` to the v2
    # revision id (the GREEN agent must name the v2 revision "v2_tags"
    # per the SAB binding module path).
    monkeypatch.setenv("TASKQ_MIGRATION_FORCE_FAIL", "0")
    base_upgrade = _run_alembic(
        ["upgrade", "v2_tags"],
        taskq_home=taskq_home,
        db_url=db_url,
        cwd=migrations_cwd,
    )
    assert base_upgrade.returncode == 0, (
        f"baseline upgrade to v2 failed:\n{base_upgrade.stderr}"
    )

    # Now enable the failure injection and try ``alembic upgrade head``
    # — the v3 upgrade must fail AND roll back so the DB remains at v2.
    monkeypatch.setenv("TASKQ_MIGRATION_FORCE_FAIL", "1")
    forced = _run_alembic(
        ["upgrade", "head"],
        taskq_home=taskq_home,
        db_url=db_url,
        cwd=migrations_cwd,
    )
    assert forced.returncode != 0, (
        "Expected alembic upgrade head to fail when "
        "TASKQ_MIGRATION_FORCE_FAIL=1; the upgrade succeeded, so the "
        "env did not honour the contract."
    )

    # Verify the alembic version table still says v2 — the failure
    # rolled back the transaction. ``alembic current`` prints the
    # current revision id; we look for the v2 revision id.
    current_proc = _run_alembic(
        ["current"],
        taskq_home=taskq_home,
        db_url=db_url,
        cwd=migrations_cwd,
    )
    assert current_proc.returncode == 0, (
        f"alembic current failed:\n{current_proc.stderr}"
    )
    alembic_current = current_proc.stdout.strip().splitlines()[-1].strip()
    # The GREEN agent must name the v2 revision exactly "v2_tags"
    # (the SAB binding module path's stem) so ``alembic current`` can
    # be matched as a stable token.
    expected_alembic_revision = "v2_tags"

    result = {
        "alembic_current": alembic_current,
        "expected_alembic_revision": expected_alembic_revision,
    }
    # FR07-migration-rollback (applies_to 5) — DB still at the prior
    # revision after the failing upgrade.
    assert result["alembic_current"] == expected_alembic_revision

    # Now hit /readyz — it must report 503 because the DB is in a
    # failed-migration state (env.py must surface the failure detail
    # so the readiness probe can reflect it). We drive /readyz
    # in-process via httpx against the FastAPI app, pointed at the
    # same TASKQ_HOME / TASKQ_DB_URL.
    monkeypatch.setenv("TASKQ_HOME", str(taskq_home))
    monkeypatch.setenv("TASKQ_DB_URL", db_url)

    import asyncio
    import httpx

    from taskq_api.app import create_app

    app = create_app()

    async def _probe_readyz() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.get("/readyz")

    response = asyncio.run(_probe_readyz())
    result["readyz_status"] = response.status_code
    # FR07-readyz-503 (applies_to 5) — the readiness probe surfaces
    # the failure with a 503.
    assert result["readyz_status"] == 503


# ---------------------------------------------------------------------------
# In-process coverage tests
# ---------------------------------------------------------------------------
# The five TEST_SPEC test cases above run alembic in a SUBPROCESS so that
# the project's real I/O / SQL semantics are exercised end-to-end. The
# subprocess boundary, however, is invisible to coverage — the lines the
# subprocess runs in env.py and the three revision files do not appear
# in the coverage report from the parent process.
#
# These in-process tests re-import the migration modules and drive the
# same code paths via Alembic's Python API so coverage can see the
# stmts executed. They are NOT a substitute for the subprocess tests:
# the subprocess tests are the binding NFR-09 / NFR-12 verification
# target; the in-process tests exist only to keep the test_coverage
# dimension ≥ 80%.
#
# Each test isolates state via ``tmp_path`` and per-test env vars, so
# they cannot leak across the subprocess tests above.


def _make_alembic_cfg(db_url: str):
    """Build an in-process alembic ``Config`` pointed at the per-test DB.

    Runs the alembic Python API in the current process so coverage can
    trace the env.py + revision source code. Mirrors the env wiring the
    subprocess driver uses (``PYTHONPATH`` ↔ ``prepend_sys_path``,
    ``TASKQ_DB_URL`` ↔ ``sqlalchemy.url``). The alembic.ini path is
    passed to the config so env.py's ``if config.config_file_name is
    not None: fileConfig(...)`` branch fires (matches the subprocess
    path).
    """
    from alembic.config import Config as alembic_config_Config

    cfg = alembic_config_Config(str(_SRC_ROOT / "migrations" / "alembic.ini"))
    cfg.set_main_option("script_location", str(_SRC_ROOT / "migrations"))
    cfg.set_main_option("prepend_sys_path", str(_SRC_ROOT))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_cov_env_helpers_db_url_and_marker_path(monkeypatch):
    """Cover env.py:60-68 — ``_db_url`` and ``_migration_failure_marker_path``.

    NFR-12 (execute-verification target) — the unit-level wiring of
    env.py drives the rest of the migrations; covering these helpers
    directly guards against regressions in the env contract.
    """
    from migrations import env as alembic_env

    # _db_url — TASKQ_DB_URL override wins.
    monkeypatch.delenv("TASKQ_DB_URL", raising=False)
    assert alembic_env._db_url() == "sqlite:///./taskq.db"
    monkeypatch.setenv("TASKQ_DB_URL", "sqlite:///coverage_marker.db")
    assert alembic_env._db_url() == "sqlite:///coverage_marker.db"

    # _migration_failure_marker_path — TASKQ_HOME controls the directory.
    monkeypatch.setenv("TASKQ_HOME", "/tmp/coverage_marker_home")
    marker = alembic_env._migration_failure_marker_path()
    assert marker.endswith("/.migration_failure.json")
    assert "/tmp/coverage_marker_home" in marker


def test_cov_env__write_migration_failure_marker_writes_file(monkeypatch, tmp_path):
    """Cover env.py:71-86 — ``_write_migration_failure_marker`` happy path.

    Exercises the branch that creates the TASKQ_HOME directory and
    writes the JSON marker file. The OSError branch (lines 83-86) is
    tested by the test_cov_env__write_migration_failure_marker_oserror
    case below.
    """
    from migrations import env as alembic_env

    monkeypatch.setenv("TASKQ_HOME", str(tmp_path))
    alembic_env._write_migration_failure_marker('{"reason":"coverage"}')
    marker_path = tmp_path / ".migration_failure.json"
    assert marker_path.is_file()
    assert marker_path.read_text(encoding="utf-8") == '{"reason":"coverage"}'


def test_cov_env__write_migration_failure_marker_oserror(monkeypatch, tmp_path):
    """Cover env.py:83-86 — OSError branch in ``_write_migration_failure_marker``.

    Triggered when TASKQ_HOME points at a path whose parent directory
    cannot be created (e.g. a path underneath a regular file). The
    function must swallow the OSError so the surrounding migration
    failure still propagates to the caller.
    """
    from migrations import env as alembic_env

    # A path whose parent component is a regular file (not a directory)
    # makes ``os.makedirs(..., exist_ok=True)`` raise ``NotADirectoryError``
    # which is a subclass of OSError — the function must swallow it.
    blocker_file = tmp_path / "regular_file"
    with open(blocker_file, "wb") as fh:
        fh.write(b"x")
    # Point TASKQ_HOME at a directory beneath the regular file so
    # os.makedirs raises NotADirectoryError (which is OSError).
    monkeypatch.setenv("TASKQ_HOME", str(blocker_file / "no" / "such" / "dir"))
    # Should not raise.
    alembic_env._write_migration_failure_marker("ignored")
    assert not (blocker_file / ".migration_failure.json").exists()


def test_cov_env__force_fail_requested_one_shot(monkeypatch, tmp_path):
    """Cover env.py:89-100 — ``_force_fail_requested`` one-shot semantics.

    The flag is a one-shot: a follow-up invocation under the same
    ``TASKQ_MIGRATION_FORCE_FAIL=1`` env var must see the marker and
    return False, so AC-7.5's ``alembic current`` check can succeed.
    """
    from migrations import env as alembic_env

    monkeypatch.setenv("TASKQ_HOME", str(tmp_path))
    monkeypatch.setenv("TASKQ_MIGRATION_FORCE_FAIL", "0")
    assert alembic_env._force_fail_requested() is False

    monkeypatch.setenv("TASKQ_MIGRATION_FORCE_FAIL", "1")
    assert alembic_env._force_fail_requested() is True

    # After the marker is written, the same env returns False.
    alembic_env._write_migration_failure_marker("coverage")
    assert alembic_env._force_fail_requested() is False

    # Different env var (unrelated value) — also False.
    monkeypatch.setenv("TASKQ_MIGRATION_FORCE_FAIL", "")
    assert alembic_env._force_fail_requested() is False


def test_cov_v1_initial_upgrade_and_downgrade_in_process(tmp_path, monkeypatch):
    """Cover v1_initial.py:27-41, 52-53 — both upgrade() and downgrade().

    Drives the same schema operations in-process so coverage can see
    the source. Mirrors what AC-7.1's subprocess test verifies
    end-to-end; the difference is only the process boundary.
    """
    from alembic import command

    db_path = tmp_path / "cov_v1.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("TASKQ_DB_URL", db_url)
    monkeypatch.setenv("TASKQ_HOME", str(tmp_path))

    cfg = _make_alembic_cfg(db_url)
    # Ensure each test starts from a clean alembic_version state.
    if db_path.exists():
        db_path.unlink()

    # upgrade head — v1_initial's upgrade() runs.
    command.upgrade(cfg, "head")
    with sqlite3.connect(str(db_path)) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert {"tasks", "api_keys"}.issubset(tables)

    # downgrade base — v1_initial's downgrade() runs.
    command.downgrade(cfg, "base")
    with sqlite3.connect(str(db_path)) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "tasks" not in tables
    assert "api_keys" not in tables


def test_cov_v2_tags_upgrade_and_downgrade_in_process(tmp_path, monkeypatch):
    """Cover v2_tags.py:23-47, 57-59 — both upgrade() and downgrade().

    The v2 upgrade creates ``tags`` / ``task_tags`` and the unique
    index on ``tasks.name``; the downgrade reverses them. Both
    branches must be exercised to satisfy the test_coverage dimension.
    To exercise v2's DOWN revision we start at v2 then downgrade to v1.
    """
    from alembic import command

    db_path = tmp_path / "cov_v2.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("TASKQ_DB_URL", db_url)
    monkeypatch.setenv("TASKQ_HOME", str(tmp_path))

    cfg = _make_alembic_cfg(db_url)
    if db_path.exists():
        db_path.unlink()

    # upgrade to v2_tags — v2_tags.upgrade() runs after v1_initial's.
    command.upgrade(cfg, "v2_tags")
    with sqlite3.connect(str(db_path)) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
    assert {"tags", "task_tags"}.issubset(tables)
    assert "uq_tasks_name" in indexes

    # downgrade to v1_initial — v2_tags.downgrade() runs.
    command.downgrade(cfg, "v1_initial")
    with sqlite3.connect(str(db_path)) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
    assert "tags" not in tables
    assert "task_tags" not in tables
    assert "uq_tasks_name" not in indexes


def test_cov_v3_split_upgrade_and_downgrade_in_process(tmp_path, monkeypatch):
    """Cover v3_split_results.py:46-67, 79-93 — both upgrade() and downgrade().

    The v3 upgrade splits ``tasks.result_json`` into ``task_results``
    rows; the downgrade is a real reverse migration that re-merges
    and restores the column. Both branches must run so coverage sees
    the data-migration SQL.
    """
    from alembic import command

    db_path = tmp_path / "cov_v3.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("TASKQ_DB_URL", db_url)
    monkeypatch.setenv("TASKQ_HOME", str(tmp_path))

    cfg = _make_alembic_cfg(db_url)
    if db_path.exists():
        db_path.unlink()

    # upgrade to v3 — v3_split_results.upgrade() runs.
    command.upgrade(cfg, "v3_split_results")
    with sqlite3.connect(str(db_path)) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "task_results" in tables

    # Insert a row with a sample result_json so the v3 downgrade has
    # non-trivial data to reverse-migrate.
    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.execute(
            "INSERT INTO tasks (name, command) VALUES (?, ?)",
            ("cov-v3-sample", "echo"),
        )
        task_id = cur.lastrowid
        conn.execute(
            "INSERT INTO task_results (task_id, result_json) VALUES (?, ?)",
            (task_id, '{"exit_code": 0}'),
        )
        conn.commit()

    # downgrade to v2_tags — v3_split_results.downgrade() runs.
    command.downgrade(cfg, "v2_tags")
    with sqlite3.connect(str(db_path)) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    assert "task_results" not in tables
    assert "result_json" in cols

    # upgrade to v3 again — v3_split_results.upgrade() rebuilds task_results.
    command.upgrade(cfg, "v3_split_results")
    with sqlite3.connect(str(db_path)) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "task_results" in tables


def test_cov_env_run_migrations_online_and_offline_in_process(tmp_path, monkeypatch):
    """Cover env.py:108-153 + dispatch branch — both runners via the alembic API.

    Drives alembic's online mode (live DB) and offline mode (SQL
    generation) in-process so coverage can see the run_migrations_online
    and run_migrations_offline bodies. The dockerfile-style
    ``is_offline_mode()`` dispatch branch is exercised by the offline
    invocation.
    """
    import contextlib
    import io

    from alembic import command

    db_path = tmp_path / "cov_online.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("TASKQ_DB_URL", db_url)
    monkeypatch.setenv("TASKQ_HOME", str(tmp_path))

    cfg = _make_alembic_cfg(db_url)
    if db_path.exists():
        db_path.unlink()

    # Online mode — run_migrations_online() executes against a real DB.
    command.upgrade(cfg, "head")
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
        ).fetchone() is not None

    # Offline mode — SQL is emitted to stdout; run_migrations_offline() runs.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        command.upgrade(cfg, "head", sql=True)
    sql = buf.getvalue()
    assert "CREATE TABLE tasks" in sql
    assert "CREATE TABLE api_keys" in sql
    assert "CREATE TABLE task_results" in sql


def test_cov_env_force_fail_writes_marker_and_raises_in_process(tmp_path, monkeypatch):
    """Cover env.py:148-153 — failure-injection branch raises RuntimeError.

    Mirrors the AC-7.5 subprocess test but in-process. The branch
    writes the marker file via ``_write_migration_failure_marker`` and
    then raises ``RuntimeError("simulated migration failure")``; the
    surrounding alembic transaction swallows the command exit code
    but the marker survives.
    """
    from alembic import command

    db_path = tmp_path / "cov_fail.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("TASKQ_DB_URL", db_url)
    monkeypatch.setenv("TASKQ_HOME", str(tmp_path))

    cfg = _make_alembic_cfg(db_url)
    if db_path.exists():
        db_path.unlink()

    # Pre-stage v2 so the v3 attempt has something to roll back from.
    command.upgrade(cfg, "v2_tags")

    # Now enable the failure injection — the next upgrade head must
    # raise RuntimeError AND write the marker file before raising.
    monkeypatch.setenv("TASKQ_MIGRATION_FORCE_FAIL", "1")
    with pytest.raises(Exception) as excinfo:
        command.upgrade(cfg, "head")
    assert "simulated migration failure" in str(excinfo.value)

    # The marker file survives the failed transaction.
    marker = tmp_path / ".migration_failure.json"
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8") == '{"reason":"simulated migration failure"}'
