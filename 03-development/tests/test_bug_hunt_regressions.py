"""Adversarial bug-hunt regression tests (Gate 3 — adversarial_review).

Each test here reproduces a CONFIRMED finding from
``.methodology/bug_hunt_report.json``. They were written RED (failing
against the pre-fix source) and are the anti-fabrication evidence for
the corresponding ``resolution.repro_test`` entries.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from taskq_api.api import health
from taskq_api.repository import task_repo
from taskq_api.service import ratelimit, runner

_SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
_MIGRATIONS_CWD = _SRC_ROOT / "migrations"


@pytest.fixture()
def _isolated(tmp_path, monkeypatch):
    """Point every engine at a fresh per-test sqlite file."""
    from taskq_api.repository import rate_repo, session

    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{tmp_path}/t.db")
    session.reset_engine()
    rate_repo._engine = None
    runner._gate = None
    yield
    session.reset_engine()
    rate_repo._engine = None
    runner._gate = None


def test_bughunt_runner_admission_slot_released_after_completion(_isolated, monkeypatch):
    """runner#1 — a finished submit must free its admission slot.

    Pre-fix ``_AdmissionGate`` decremented ``_remaining`` and never gave
    it back, so after ``TASKQ_MAX_CONCURRENT`` *lifetime* submissions
    every later submit returned ``queued`` without ever spawning a
    subprocess — a permanent, process-wide execution outage.
    """
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "2")
    runner._gate = None

    async def _drive():
        states = []
        for i in range(4):
            row = task_repo.create(name=f"gate-{i}", command="/bin/echo hi")
            outcome = await runner.submit(row.id, "/bin/echo hi")
            states.append(outcome.final_state)
        return states

    states = asyncio.run(_drive())
    assert states == [runner.STATE_DONE] * 4, states


def test_bughunt_runner_unspawnable_command_reaches_terminal_state(_isolated):
    """runner#2 — a command whose binary does not exist must not hang the row.

    ``asyncio.create_subprocess_exec`` raises ``FileNotFoundError``;
    pre-fix that escaped ``_collect_outcome`` so no ``task_results`` row
    was written and the task was stranded in ``running`` forever.
    """
    row = task_repo.create(name="ghost", command="/nonexistent/binary arg")

    asyncio.run(runner.run_task(row.id, "/nonexistent/binary arg"))

    assert task_repo.get_by_id(row.id).status == runner.STATE_FAILED
    runs = task_repo.list_runs(row.id)
    assert len(runs) == 1
    assert runs[0].exit_code != 0


def test_bughunt_metrics_reports_live_rate_limit_denials(_isolated):
    """health#1 — /v1/metrics must report the live denial counter.

    ``api.health`` bound ``rate_limit_denials`` to the *value* of
    ``ratelimit.denial_count`` at import time; ``record_denial`` rebinds
    the service-module global, so the metrics body was frozen at the
    import-time value (0) forever.
    """
    before = health.metrics_route()["rate_limit_denials"]
    ratelimit.record_denial()
    ratelimit.record_denial()
    after = health.metrics_route()["rate_limit_denials"]

    assert after == before + 2


def _run_alembic(args: list[str], taskq_home: Path, db_url: str) -> subprocess.CompletedProcess:
    """Invoke ``alembic`` as a child process with the per-test env."""
    proc_env = os.environ.copy()
    proc_env["TASKQ_HOME"] = str(taskq_home)
    proc_env["TASKQ_DB_URL"] = db_url
    src_root = str(_SRC_ROOT)
    existing_pp = proc_env.get("PYTHONPATH", "")
    proc_env["PYTHONPATH"] = src_root + os.pathsep + existing_pp
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        capture_output=True,
        text=True,
        env=proc_env,
        cwd=str(_MIGRATIONS_CWD),
        check=False,
    )


def test_bughunt_v3_downgrade_restores_latest_result_for_multi_run_tasks(tmp_path):
    """v3_split_results#1 — downgrade must not silently drop run history.

    Pre-fix the v3 downgrade used a correlated subquery
    ``UPDATE tasks SET result_json = (SELECT result_json FROM task_results
    WHERE task_results.task_id = tasks.id)`` that returns multiple rows
    when a task has accumulated more than one run. On SQLite the
    multi-row scalar subquery silently picks an arbitrary row instead
    of erroring, so the downgrade "succeeds" but two of the three run
    payloads are dropped — AC-7.2's byte-identical round-trip is
    violated.
    """
    db_path = tmp_path / "v3_downgrade_probe.db"
    if db_path.exists():
        db_path.unlink()
    home = tmp_path / "taskq_home"
    home.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{db_path}"

    # Apply v3 schema.
    upgrade = _run_alembic(["upgrade", "head"], taskq_home=home, db_url=db_url)
    assert upgrade.returncode == 0, upgrade.stderr

    # Insert ONE task with THREE task_results rows (run history).
    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.execute(
            "INSERT INTO tasks (name, command) VALUES (?, ?)",
            ("multi-run", "/bin/echo"),
        )
        task_id = cur.lastrowid
        for i in range(3):
            conn.execute(
                "INSERT INTO task_results (task_id, result_json, started_at, exit_code, stdout_tail, stderr_tail, duration_ms, finished_at) "
                "VALUES (?, ?, datetime('now', ?), ?, ?, ?, ?, datetime('now', ?))",
                (task_id, f'{{"run": {i}}}', f"+{i} seconds", i, f"run-{i}", "", 100 + i, f"+{i} seconds"),
            )
        conn.commit()

    # Downgrade to v2 — the off-by-row count must be visible.
    downgrade = _run_alembic(["downgrade", "-1"], taskq_home=home, db_url=db_url)
    assert downgrade.returncode == 0, downgrade.stderr

    # Read the restored result_json. The downgrade MUST restore the
    # most-recent run (started_at DESC, id DESC, matching list_runs's
    # ordering) — picking an arbitrary row from a multi-row bucket is
    # data loss.
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT result_json FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
    assert row is not None and row[0] is not None
    parsed = row[0]
    assert parsed == '{"run": 2}', (
        f"v3 downgrade must pick the latest task_results row; got {parsed!r}"
    )
