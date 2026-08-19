"""FR-08: 非同步執行器 — TDD-RED failing tests.

Realises the 6 test cases of ``02-architecture/TEST_SPEC.md`` FR-08:

  1. test_ac_8_1_over_cap_tasks_are_queued_no_unbounded_coroutines
  2. test_ac_8_2_timed_out_task_killed_and_awaited_no_orphans
  3. test_ac_8_3_graceful_drain_then_interrupt_remaining_no_orphans
  4. test_ac_8_4_cancelled_error_propagates_not_swallowed_by_except_exception
  5. test_ac_8_5_runner_constructs_taskgroup_not_bare_gather_or_fire_and_forget
  6. test_sec_t07_timeout_kills_subprocess_no_orphan

Per [SAB — BINDING MODULE PATHS] the dotted names imported here are the
ones ``.methodology/SAB.json`` declares for FR-08:

  * ``taskq_api.service.runner``     (the asyncio.TaskGroup coordinator)
  * ``taskq_api.app``                (FastAPI app / lifespan that triggers drain)

``taskq_api.service.runner`` exists on disk but the FR-08 coordinator API
it must expose (``submit(task_id, command) -> Awaitable[...]``,
``drain(timeout: float) -> None``, asyncio.TaskGroup usage, queue-of-N
admission control) does NOT — every test below either invokes a method
that does not yet exist (AttributeError) or asserts an invariant the
current source does not satisfy (AssertionError on the AST scan). Both
outcomes are the expected RED state per the task brief; pytest reports
the failure verbatim and the spec-coverage check still finds the named
tests.

Sub-assertion predicates wired in verbatim from TEST_SPEC.md FR-08:

  FR08-cap-plus-one        result["live_subprocess_count"] <= max_concurrent + 1  (1)
  FR08-queued              result["queued_count"] >= max_concurrent               (1)
  FR08-timeout-state       result["final_state"] == "timeout"                    (2, 6)
  FR08-no-orphan           len(result["orphan_pids"]) == 0                       (2, 3, 6)
  FR08-drain-interrupted   result["stragglers_marked_interrupted"] > 0           (3)
  FR08-cancelled-propagates result["propagated"] == True                         (4)
  FR08-taskgroup-present   result["taskgroup_hits"] >= 1                         (5)
  FR08-no-bare-gather      result["bare_gather_hits"] == 0                       (5)

In-process vs out-of-process (per [INTEGRATION FR GUIDELINES]):
* AC-8.1 is IN-PROCESS (instrumented ``asyncio.create_subprocess_exec``
  counter inside the runner) so the cap assertion is deterministic and
  coverage can trace ``service.runner``. The ``sleep`` children the
  runner spawns ARE genuinely out-of-process — that is the feature under
  test, not a harness choice.
* AC-8.2 / AC-8.3 / AC-8.6 (SEC-T-07) are IN-PROCESS through
  ``runner.submit`` so the kill/wait, drain, and orphan-PID properties
  are observable. The subprocess spawned by the runner is
  out-of-process; we verify it via the live-children PID set just like
  AC-2.3 does.
* AC-8.4 is a UNIT test that injects ``asyncio.CancelledError`` into
  the runner's inner coroutine and asserts it surfaces. We drive it
  via the runner's ``submit`` entry point so the test fails with
  AttributeError today (RED) and exercises the TaskGroup-based
  coordinator path the GREEN agent must build.
* AC-8.5 is a STATIC AST scan of ``taskq_api/service/runner.py`` —
  no subprocess, no DB.

Citations: SPEC.md §3 FR-08 + NFR-03 + §8 #25; SAD.md §2.2 L3
service.runner (NP-13 admission control + NP-15 timeout).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Standard top-level imports — RED state.
# ``taskq_api.service.runner`` exists on disk but lacks the FR-08
# coordinator surface (asyncio.TaskGroup, submit, drain, queue-of-N
# admission control). Tests below invoke those surfaces and will fail
# with AttributeError — which IS the expected RED state per the brief.
# GREEN TODO: implement ``service.runner.submit(...)`` and
# ``service.runner.drain(timeout)`` using asyncio.TaskGroup plus an
# admission-controlled queue keyed off ``TASKQ_MAX_CONCURRENT``.
# ---------------------------------------------------------------------------
from taskq_api.service import runner  # noqa: F401  — SAB: taskq_api.service.runner
from taskq_api.app import app, create_app  # noqa: F401  — SAB: taskq_api.app

# ---------------------------------------------------------------------------
# Test isolation fixtures — same pattern as test_fr02 / test_fr07.
# ---------------------------------------------------------------------------

_SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
_RUNNER_PATH = _SRC_ROOT / "taskq_api" / "service" / "runner.py"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Give each test its own SQLite file + TASKQ_HOME.

    FR-08 row 3 declares ``state_mode="isolate_per_test"``;
    this fixture is what makes that true. The runner persists
    state through the task_results table, so a per-test DB keeps
    state from leaking across cases.
    """
    db_path = tmp_path / "fr08_test.db"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("TASKQ_HOME", str(tmp_path))


def _auth_headers(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key} if api_key else {}


def _run_async(coro):
    """Run an async coroutine synchronously (in-process)."""
    return asyncio.run(coro)


def _child_pids() -> set[int]:
    """Return the set of direct child PIDs of this pytest process.

    Same helper as test_fr02 — used by AC-8.2, AC-8.3 and SEC-T-07
    to prove the timed-out / drained tasks left no orphan children.
    ``pgrep -P`` is available on darwin and linux; an empty/failed
    call yields an empty set, which keeps the assertion conservative
    rather than flaky-green.
    """
    proc = subprocess.run(
        ["pgrep", "-P", str(os.getpid())],
        capture_output=True,
        text=True,
        check=False,
    )
    return {int(line) for line in proc.stdout.split() if line.strip().isdigit()}


# ---------------------------------------------------------------------------
# AC-8.1 — over-cap tasks are queued, not spawned unbounded
# FR08-cap-plus-one: result["live_subprocess_count"] <= max_concurrent + 1
# FR08-queued:       result["queued_count"] >= max_concurrent
# ---------------------------------------------------------------------------


# GREEN TODO: taskq_api.service.runner must expose
#   async def submit(task_id: int, command: str) -> Awaitable[RunOutcome]
# that uses an ``asyncio.TaskGroup`` (Python 3.11+) gated by a semaphore
# of size ``TASKQ_MAX_CONCURRENT``. Tasks beyond the cap must wait in an
# internal ``asyncio.Queue`` (no ``asyncio.create_task`` fire-and-forget,
# no unbounded coroutine generation).
def test_ac_8_1_over_cap_tasks_are_queued_no_unbounded_coroutines(  # NFR-03 (no orphan / unbounded coroutines), NP-13 (admission control)
    monkeypatch,
):
    """AC-8.1 — with ``TASKQ_MAX_CONCURRENT=4``, submitting 20 long-running
    tasks must NOT spawn 20 concurrent subprocesses. At most ``cap + 1``
    subprocesses are alive at any moment; at least ``cap`` are queued
    waiting for admission.

    Covers TEST_SPEC FR-08 row 1 (tasks=20; max_concurrent=4;
    precondition="instrumented subprocess counter"). The cap+1 slack
    mirrors Python 3.11's TaskGroup admit-one-extra semantics on
    cancellation; a strict ``<= cap`` would forbid the in-flight child
    that the timeout path is currently killing.

    The test instruments ``asyncio.create_subprocess_exec`` with a
    wrapper that records the live process count at every entry/exit, so
    the assertion can target the observed maximum (not just the
    cumulative call count). The wrapper is removed before pytest
    teardown via the ``with`` block's exit, so subsequent tests are not
    affected.

    Failure mode (RED): ``runner.submit`` does not exist; the test
    raises ``AttributeError`` at the first call site. The GREEN agent
    must implement an admission-controlled, TaskGroup-backed
    ``submit`` entry point.
    """
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "4")
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "30")

    # Instrument asyncio.create_subprocess_exec to count LIVE processes.
    # The original ``runner.execute_command`` calls ``create_subprocess_exec``
    # directly; monkey-patching that function lets us observe peak concurrency
    # even though the runner itself is a black box during RED.
    original_create = asyncio.create_subprocess_exec
    live_count = 0
    peak_live = 0
    total_calls = 0
    call_lock = asyncio.Lock() if False else None  # plain int math; subprocess is sync-bound at the entry

    def _track_peak() -> None:
        nonlocal peak_live
        if live_count > peak_live:
            peak_live = live_count

    async def _instrumented(*args: Any, **kwargs: Any):
        nonlocal live_count, total_calls
        total_calls += 1
        live_count += 1
        _track_peak()
        try:
            proc = await original_create(*args, **kwargs)
            return proc
        finally:
            live_count -= 1

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _instrumented)

    max_concurrent = 4
    task_count = 20

    async def _drive():
        # The runner must expose a queue-backed submit method that we
        # can call many times in quick succession. The GREEN agent
        # implements this entry point; until then, AttributeError.
        coros = [
            runner.submit(task_id=i, command=f"sleep 1")  # GREEN TODO: runner.submit
            for i in range(task_count)
        ]
        # Wait for all of them — queued ones will run after the cap
        # releases a slot. A reasonable bound for the test itself.
        results = await asyncio.wait_for(
            asyncio.gather(*coros, return_exceptions=True),
            timeout=20.0,
        )
        return results

    results = _run_async(_drive())

    # The test must observe at least ``max_concurrent`` admissions (the
    # cap is full at peak) and never exceed ``max_concurrent + 1`` live
    # subprocesses. queued_count is the number of tasks that did not
    # start their subprocess until a slot freed up: total - admitted.
    admitted = total_calls  # total spawns == admitted (1 per task)
    queued_count = task_count - admitted
    # In the strict "no extra spawns" world, queued_count is the number
    # of coroutines that sat in the queue while the cap was saturated;
    # in practice the runner can admit one extra after a slot frees up,
    # so queued_count >= max_concurrent is the conservative invariant.
    live_subprocess_count = peak_live

    # Suppress unused-variable warnings: results carries the RunOutcome
    # list (or exception objects) from the runner; we only need the
    # counters, but binding results keeps the assertion shape obvious.
    assert results is not None

    result = {
        "live_subprocess_count": live_subprocess_count,
        "queued_count": queued_count,
    }
    # FR08-cap-plus-one (applies_to 1) — peak live subprocess count.
    assert result["live_subprocess_count"] <= max_concurrent + 1, (
        f"FR-08 AC-8.1: live subprocess count peaked at "
        f"{live_subprocess_count}, exceeding cap+1={max_concurrent + 1} — "
        "the runner is not gating admission on TASKQ_MAX_CONCURRENT"
    )
    # FR08-queued (applies_to 1) — at least ``cap`` tasks waited in the queue.
    assert result["queued_count"] >= max_concurrent, (
        f"FR-08 AC-8.1: only {queued_count} of {task_count} tasks were queued "
        f"behind the cap={max_concurrent} gate — unbounded coroutine "
        "generation detected"
    )


# ---------------------------------------------------------------------------
# AC-8.2 — timed-out task has its child kill()ed and await wait()ed
# FR08-timeout-state: result["final_state"] == "timeout"
# FR08-no-orphan:     len(result["orphan_pids"]) == 0
# ---------------------------------------------------------------------------


# GREEN TODO: taskq_api.service.runner.submit must wire asyncio.wait_for
# around the inner subprocess coroutine and, on TimeoutError, call
# ``proc.kill()`` followed by ``await proc.wait()`` (reap, do not just
# signal) — and persist final_state == "timeout" via the FR-02
# record_result / update_status path.
def test_ac_8_2_timed_out_task_killed_and_awaited_no_orphans(monkeypatch):  # NFR-03 (timeout budget; NP-15), NFR-08 (no orphan processes)
    """AC-8.2 — a task exceeding its timeout has its child killed
    (``proc.kill()`` + ``await proc.wait()``), leaves no orphan child
    process, and the final state is ``"timeout"``.

    Covers TEST_SPEC FR-08 row 2 (command="sleep 30"; timeout_sec=1;
    state_mode="isolate_per_test"; subprocess_mode="out_of_process";
    shared_TASKQ_HOME=false). The ``sleep 30`` child is genuinely
    out-of-process — that is what makes the orphan-PID count
    meaningful.

    Failure mode (RED): ``runner.submit`` is missing — AttributeError
    on the call. GREEN must add the timeout-aware submit path.
    """
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "8")
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "1")

    pids_before = _child_pids()

    async def _run():
        return await runner.submit(  # GREEN TODO: runner.submit
            task_id=1, command="sleep 30", timeout_sec=1
        )

    outcome = _run_async(_run())

    orphan_pids = _child_pids() - pids_before
    result = {
        "final_state": getattr(outcome, "final_state", None),
        "orphan_pids": sorted(orphan_pids),
    }
    # FR08-timeout-state (applies_to 2)
    assert result["final_state"] == "timeout", (
        f"FR-08 AC-8.2: expected final state 'timeout', got "
        f"{result['final_state']!r} — the runner must surface "
        "wait_for's TimeoutError as state='timeout' via the FR-02 "
        "record_result path"
    )
    # FR08-no-orphan (applies_to 2)
    assert len(result["orphan_pids"]) == 0, (
        "FR-08 AC-8.2: timed-out run leaked child process(es) "
        f"{result['orphan_pids']} — process.kill() must be followed "
        "by await process.wait() so the child is reaped"
    )


# ---------------------------------------------------------------------------
# AC-8.3 — graceful drain on shutdown; stragglers marked interrupted
# FR08-no-orphan:           len(result["orphan_pids"]) == 0
# FR08-drain-interrupted:   result["stragglers_marked_interrupted"] > 0
# ---------------------------------------------------------------------------


# GREEN TODO: taskq_api.service.runner must expose
#   async def drain(timeout: float) -> DrainReport
# which awaits in-flight tasks up to ``timeout`` seconds (via the
# TaskGroup ``__aexit__``), then cancels any stragglers and marks them
# state="interrupted" in the repository. The FastAPI lifespan (in
# taskq_api.app) must invoke ``runner.drain(get_settings().drain_timeout)``
# on shutdown so a SIGTERM during a long-running command leaves no
# orphans.
def test_ac_8_3_graceful_drain_then_interrupt_remaining_no_orphans(  # NFR-03 (graceful shutdown; no orphan), NFR-08 (resource leak prevention)
    monkeypatch,
):
    """AC-8.3 — on shutdown, in-flight tasks get up to ``TASKQ_DRAIN_TIMEOUT``
    to complete; tasks still running after the drain are marked
    ``interrupted``; no orphan child processes are left behind.

    Covers TEST_SPEC FR-08 row 3 (drain_timeout_sec=2; command="sleep 30";
    precondition="lifespan shutdown invokes runner.drain(2)").
    state_mode="isolate_per_test" so the long-running task from a
    sibling test cannot leak into this one.

    Failure mode (RED): ``runner.drain`` does not exist — AttributeError
    on the call. GREEN must implement the drain entry point plus the
    lifespan hook in ``taskq_api.app.create_app`` (FastAPI's
    ``lifespan`` context manager, or equivalent) that invokes it.
    """
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "8")
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "30")
    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "2")

    pids_before = _child_pids()

    async def _scenario():
        # Kick off one long-running task. The runner queues it and
        # spawns the subprocess; we then immediately request a drain
        # so the straggler is interrupted mid-flight.
        submit_task = asyncio.create_task(
            runner.submit(  # GREEN TODO: runner.submit
                task_id=1, command="sleep 30", timeout_sec=30
            )
        )
        # Give the runner a moment to actually spawn the child so the
        # drain has something to interrupt (rather than cancelling a
        # not-yet-started coroutine).
        await asyncio.sleep(0.2)
        drain_report = await runner.drain(2.0)  # GREEN TODO: runner.drain
        # Surface submit_task's outcome (Cancelled or exception) but
        # do not raise — we only care about the drain report.
        try:
            await asyncio.wait_for(submit_task, timeout=5.0)
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        return drain_report

    drain_report = _run_async(_scenario())

    # DrainReport carries the count of stragglers the runner forced to
    # "interrupted" state. GREEN TODO: the runner exposes an attribute
    # ``stragglers_marked_interrupted`` (int) on the returned object.
    stragglers_marked_interrupted = getattr(
        drain_report, "stragglers_marked_interrupted", 0
    )
    orphan_pids = sorted(_child_pids() - pids_before)
    result = {
        "stragglers_marked_interrupted": stragglers_marked_interrupted,
        "orphan_pids": orphan_pids,
    }
    # FR08-drain-interrupted (applies_to 3) — at least one straggler
    # was marked interrupted (we started one long-running task and
    # did not wait for it to complete).
    assert result["stragglers_marked_interrupted"] > 0, (
        "FR-08 AC-8.3: drain(2.0) interrupted 0 stragglers — the "
        "long-running task should have been cancelled and marked "
        "'interrupted' once the drain budget elapsed"
    )
    # FR08-no-orphan (applies_to 3) — kill+wait must still run for
    # the interrupted straggler.
    assert len(result["orphan_pids"]) == 0, (
        "FR-08 AC-8.3: drain left orphan process(es) "
        f"{result['orphan_pids']} — drain must process.kill() and "
        "await process.wait() each straggler, not just cancel the "
        "TaskGroup"
    )


# ---------------------------------------------------------------------------
# AC-8.4 — CancelledError propagates, not swallowed by except Exception
# FR08-cancelled-propagates: result["propagated"] == True
# ---------------------------------------------------------------------------


# GREEN TODO: taskq_api.service.runner.submit must wrap its work in an
# asyncio.TaskGroup and NOT catch ``asyncio.CancelledError`` under a
# blanket ``except Exception``. The architecture_constraints list in
# SAB.json ("CancelledError must propagate, never be swallowed as a
# generic Exception") is the binding constraint — GREEN must honour it.
def test_ac_8_4_cancelled_error_propagates_not_swallowed_by_except_exception(  # NFR-03 (cancellation semantics; architecture constraint)
    monkeypatch,
):
    """AC-8.4 — an ``asyncio.CancelledError`` raised inside a task
    handler propagates out of the runner (it is not caught by a
    blanket ``except Exception``).

    Covers TEST_SPEC FR-08 row 4 (method="unit";
    target="taskq_api.service.runner";
    injected="asyncio.CancelledError"). The SAB architecture_constraints
    entry ``"CancelledError must propagate, never be swallowed as a
    generic Exception"`` is the binding rule.

    We monkey-patch ``runner.execute_command`` (the inner coroutine
    invoked by the runner per task) to raise ``CancelledError`` and
    assert that the error surfaces from ``runner.submit`` —
    specifically, that it is NOT converted into a generic
    ``RunOutcome(final_state="failed")`` by an over-eager
    ``except Exception`` in the runner.

    Failure mode (RED): ``runner.submit`` does not exist — AttributeError
    on the call. GREEN must add the submit entry point and ensure the
    CancelledError surfaces.
    """
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "8")
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "30")

    async def _raising_execute_command(*args: Any, **kwargs: Any):
        # Simulate a CancelledError raised inside the task body —
        # the runner's inner coroutine raises it as if the TaskGroup
        # had cancelled the task. The runner must NOT swallow this.
        raise asyncio.CancelledError()

    monkeypatch.setattr(runner, "execute_command", _raising_execute_command)

    async def _run():
        return await runner.submit(  # GREEN TODO: runner.submit
            task_id=1, command="anything", timeout_sec=5
        )

    propagated = False
    try:
        outcome = _run_async(_run())
    except asyncio.CancelledError:
        propagated = True
        outcome = None

    # The runner must NOT swallow CancelledError by converting it to a
    # RunOutcome with final_state="failed" or final_state="timeout".
    if outcome is not None and not isinstance(outcome, BaseException):
        final_state = getattr(outcome, "final_state", None)
        assert final_state != "failed", (
            "FR-08 AC-8.4: runner swallowed CancelledError as a generic "
            "Exception and persisted final_state='failed' — CancelledError "
            "must propagate, not be caught by except Exception "
            "(SAB architecture_constraints)"
        )
        assert final_state != "timeout", (
            "FR-08 AC-8.4: runner swallowed CancelledError and persisted "
            "final_state='timeout' — CancelledError is not a timeout"
        )

    result = {"propagated": propagated}
    # FR08-cancelled-propagates (applies_to 4) — the CancelledError must
    # surface to the caller (the runner does not catch it).
    assert result["propagated"] is True, (
        "FR-08 AC-8.4: asyncio.CancelledError was swallowed by the runner "
        "— it must propagate out per NFR-03 and the SAB "
        "architecture_constraints list"
    )


# ---------------------------------------------------------------------------
# AC-8.5 — runner constructs asyncio.TaskGroup, not bare gather/create_task
# FR08-taskgroup-present: result["taskgroup_hits"] >= 1
# FR08-no-bare-gather:    result["bare_gather_hits"] == 0
# ---------------------------------------------------------------------------


def test_ac_8_5_runner_constructs_taskgroup_not_bare_gather_or_fire_and_forget():  # NFR-03 (cancellation semantics), NFR-11 (architecture constraint enforced statically)
    """AC-8.5 — ``taskq_api/service/runner.py`` constructs an
    ``asyncio.TaskGroup`` to coordinate background execution. It does
    NOT rely on bare ``asyncio.gather`` (which has no structured
    concurrency) or fire-and-forget ``asyncio.create_task`` (which has
    no cancellation surface).

    Covers TEST_SPEC FR-08 row 5 (method="static";
    target="taskq_api/service/runner.py"; expected="asyncio.TaskGroup").

    This is a static AST scan — we walk the source's AST and count the
    relevant ``Attribute`` nodes. ``asyncio.TaskGroup`` shows up as an
    ``ast.Attribute`` whose ``attr == "TaskGroup"`` (and whose value
    is ``asyncio``); ``asyncio.gather`` and ``asyncio.create_task``
    show up similarly.

    Failure mode (RED): the current ``runner.py`` does not import or
    instantiate ``asyncio.TaskGroup`` — ``taskgroup_hits == 0``, so
    the FR08-taskgroup-present sub-assertion fails. GREEN must add a
    ``with asyncio.TaskGroup() as tg: ...`` block to the runner.
    """
    assert _RUNNER_PATH.is_file(), (
        f"FR-08 AC-8.5: runner module missing at {_RUNNER_PATH}"
    )
    source = _RUNNER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Count ``asyncio.TaskGroup`` references — any Attribute node whose
    # attr is "TaskGroup" is the TaskGroup class lookup.
    taskgroup_hits = sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "TaskGroup"
    )

    # Count bare ``asyncio.gather(...)`` calls — Call nodes whose func
    # is an Attribute with attr=="gather". These are the forbidden
    # coordination mechanism (no structured concurrency).
    bare_gather_hits = sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "gather"
    )

    # Count fire-and-forget ``asyncio.create_task(...)`` calls — the
    # runner must NOT use them as its coordination mechanism. The
    # current ``api/tasks.py`` line uses one, but that is the route
    # layer, not the runner; the test scans only ``service/runner.py``.
    create_task_hits = sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_task"
    )

    result = {
        "taskgroup_hits": taskgroup_hits,
        "bare_gather_hits": bare_gather_hits,
        "create_task_hits": create_task_hits,
    }
    # FR08-taskgroup-present (applies_to 5) — the runner must use
    # asyncio.TaskGroup for structured concurrency.
    assert result["taskgroup_hits"] >= 1, (
        f"FR-08 AC-8.5: asyncio.TaskGroup appears {result['taskgroup_hits']}x "
        f"in {_RUNNER_PATH} — the runner must construct an "
        "asyncio.TaskGroup to coordinate background execution"
    )
    # FR08-no-bare-gather (applies_to 5) — bare asyncio.gather is
    # forbidden; TaskGroup's __aexit__ is what gives us cancellation
    # propagation.
    assert result["bare_gather_hits"] == 0, (
        f"FR-08 AC-8.5: bare asyncio.gather appears "
        f"{result['bare_gather_hits']}x in {_RUNNER_PATH} — use "
        "asyncio.TaskGroup instead, which propagates cancellation"
    )
    # The runner must not use fire-and-forget ``asyncio.create_task`` —
    # that is the pattern AC-8.1 explicitly forbids (no unbounded
    # coroutines, no orphan surface).
    assert result["create_task_hits"] == 0, (
        f"FR-08 AC-8.5: fire-and-forget asyncio.create_task appears "
        f"{result['create_task_hits']}x in {_RUNNER_PATH} — the runner "
        "must orchestrate tasks via asyncio.TaskGroup, not "
        "fire-and-forget coroutines"
    )


# ---------------------------------------------------------------------------
# SEC-T-07 — timeout kills subprocess, no orphan
# FR08-timeout-state: result["final_state"] == "timeout"
# FR08-no-orphan:     len(result["orphan_pids"]) == 0
# ---------------------------------------------------------------------------


# GREEN TODO: same as AC-8.2 — runner.submit must timeout-kill the
# subprocess and leave zero orphans. SEC-T-07 is the security-control
# framing (SEC: T-07 denial-of-service via long-running command;
# NP-15), so the assertion set is identical to AC-8.2 but the failure
# message names the threat.
def test_sec_t07_timeout_kills_subprocess_no_orphan(monkeypatch):  # NFR-03 (timeout budget; NP-15; SEC T-07), NFR-08 (no orphan)
    """SEC-T-07 — a long-running command (``sleep 30``) under a 1-second
    timeout has its child subprocess killed (with ``proc.kill()`` +
    ``await proc.wait()``), persists ``final_state="timeout"``, and
    leaves zero orphan child processes.

    Covers TEST_SPEC FR-08 row 6 (method="inv"; command="sleep 30";
    timeout_sec=1). SEC-T-07 is the SEC: T-07 (denial_of_service via
    long-running command) control — NP-15 is the active NFR pattern.
    The assertion set mirrors AC-8.2 but the failure mode is framed
    as a security property: a leaked child is a resource-exhaustion
    vector under sustained timeout pressure.

    Failure mode (RED): ``runner.submit`` does not exist — AttributeError
    on the call.
    """
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "8")
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "1")

    pids_before = _child_pids()

    async def _run():
        return await runner.submit(  # GREEN TODO: runner.submit
            task_id=1, command="sleep 30", timeout_sec=1
        )

    outcome = _run_async(_run())

    orphan_pids = sorted(_child_pids() - pids_before)
    result = {
        "final_state": getattr(outcome, "final_state", None),
        "orphan_pids": orphan_pids,
    }
    # FR08-timeout-state (applies_to 6)
    assert result["final_state"] == "timeout", (
        f"SEC-T-07: timeout did not produce final_state='timeout'; got "
        f"{result['final_state']!r} — SEC T-07 requires the runner to "
        "translate asyncio.TimeoutError into a 'timeout' terminal state"
    )
    # FR08-no-orphan (applies_to 6)
    assert len(result["orphan_pids"]) == 0, (
        "SEC-T-07: timed-out subprocess leaked as orphan PID(s) "
        f"{result['orphan_pids']} — an attacker issuing long-running "
        "commands could exhaust PIDs/file-descriptors; the runner "
        "must kill+wait the child"
    )
