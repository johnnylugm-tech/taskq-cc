"""Adversarial bug-hunt regression tests (Gate 3 — adversarial_review).

Each test here reproduces a CONFIRMED finding from
``.methodology/bug_hunt_report.json``. They were written RED (failing
against the pre-fix source) and are the anti-fabrication evidence for
the corresponding ``resolution.repro_test`` entries.
"""

from __future__ import annotations

import asyncio

import pytest

from taskq_api.api import health
from taskq_api.repository import task_repo
from taskq_api.service import ratelimit, runner


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
