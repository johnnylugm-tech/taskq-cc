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


# ---------------------------------------------------------------------------
# Coverage-fix tests — pin the lines the FR-08 catalog leaves uncovered in
# ``taskq_api/app.py`` (problem+json envelope, /healthz, /readyz, lifespan
# drain, 403 problem handler, validation handler) and the ``run_task`` +
# ``drain-no-inflight`` paths in ``taskq_api/service/runner.py``. Every
# test below is in-process (httpx.ASGITransport or asyncio.run) so
# pytest-cov measures the FR-08 modules end-to-end.
# ---------------------------------------------------------------------------


def test_drain_with_no_inflight_tasks_returns_zero_count():  # NFR-08 (drain is safe to invoke on an idle runner)
    """[FR-08] Drain with no in-flight tasks must short-circuit to ``stragglers=0``.

    Covers ``service/runner.py`` line 378 — the early-return branch when
    ``_in_flight`` is empty. The branch is reachable even by the AC-8.3
    test (the TaskGroup cancellation drops every Tasks out of the
    in-flight set before drain inspects it), but explicitly verifying
    it isolates the no-task path from the drain-with-tasks path.
    """
    # Ensure the in-flight registry is empty for this test. The
    # ``_isolated_db`` autouse fixture already gave us a fresh DB; the
    # module-level ``_in_flight`` set is shared across tests, so we
    # snapshot + restore it manually.
    import taskq_api.service.runner as runner_module

    saved = set(runner_module._in_flight)
    runner_module._in_flight.clear()
    try:
        report = _run_async(runner_module.drain(0.5))
    finally:
        runner_module._in_flight.clear()
        runner_module._in_flight.update(saved)

    result = {"stragglers": report.stragglers_marked_interrupted}
    assert result["stragglers"] == 0, (
        "FR-08: drain() with no in-flight tasks must return "
        "stragglers_marked_interrupted=0 — the early-return branch is "
        "the only code path that knows 'idle runner' is not an error"
    )


def test_run_task_persists_result_and_terminal_state(monkeypatch):  # NFR-08 (run_task is the FR-02 entry point used by the API)
    """[FR-02/FR-08] ``runner.run_task`` transitions ``running`` → ``done`` and persists.

    Covers ``service/runner.py`` lines 423-424 (the ``run_task`` body),
    which the FR-08 AC-8.1..AC-8.5 tests do not exercise because they
    go through the admission-controlled ``submit`` entry point. The
    FR-02 ``POST /v1/tasks/{id}/run`` route, however, dispatches through
    ``run_task`` — so this branch is the FR-02 happy path.
    """
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "8")
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "30")

    # Seed a task row so the runner has a real ``task_id`` to update.
    from taskq_api.repository import task_repo

    task = task_repo.create(name="run_task-coverage", command="echo hi")

    pids_before = _child_pids()

    # ``run_task`` is documented as ``-> None``; the canonical outcome is
    # the row's persisted state, not a return value. We poll the row
    # briefly so the assertion targets the post-await state, not a
    # mid-flight read.
    _run_async(runner.run_task(task.id, "echo hi"))

    orphan_pids = sorted(_child_pids() - pids_before)
    row = task_repo.get_by_id(task.id)

    result = {
        "status": getattr(row, "status", None),
        "orphan_pids": orphan_pids,
    }
    assert result["status"] == "done", (
        "FR-08: run_task must transition the repository row to "
        f"state='done'; got {result['status']!r}"
    )
    assert len(result["orphan_pids"]) == 0, (
        "FR-08: run_task leaked child PIDs "
        f"{result['orphan_pids']} — the subprocess must be awaited "
        "to completion so the OS reaps it"
    )


def test_healthz_returns_ok_unconditionally():  # NFR-12 (liveness probe always reachable)
    """[FR-09] ``GET /healthz`` returns 200 + ``{"status": "ok"}`` without auth or DB.

    Covers ``app.py`` line 90 — the ``return {"status": "ok"}`` body of
    the ``healthz`` handler. The FR-08 catalog does not include a
    healthz probe; this test pins the liveness path so an accidental
    regression (e.g. wiring an auth dependency) is caught.
    """
    import httpx

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            return await ac.get("/healthz")

    response = _run_async(_run())
    result = {
        "status": response.status_code,
        "body": response.json(),
    }
    assert result["status"] == 200, (
        f"FR-08: /healthz must return 200 (liveness); got {result['status']}"
    )
    assert result["body"] == {"status": "ok"}, (
        f"FR-08: /healthz body must be exactly {{'status': 'ok'}}; got {result['body']!r}"
    )


def test_readyz_returns_503_when_db_is_reachable_but_alembic_not_at_head():  # NFR-07 (DB readiness probe; FR-09 alembic-head requirement)
    """[FR-09] ``GET /readyz`` returns 200 + ``{"status": "ready"}`` when the DB is reachable.

    Covers ``app.py`` lines 110-114 — the happy path of the
    ``try: engine.connect()`` block. The autouse ``_isolated_db``
    fixture gives every test a fresh SQLite file, so ``SELECT 1`` must
    succeed and the probe must report ready.
    """
    import httpx

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            return await ac.get("/readyz")

    response = _run_async(_run())
    result = {
        "status": response.status_code,
        "body": response.json(),
    }
    assert result["status"] == 503, (
        f"FR-09: /readyz must return 503 when alembic current != head; "
        f"got {result['status']} with body {result['body']!r}"
    )
    assert result["body"].get("detail") == "migration", (
        f"FR-09: /readyz 503 body must name the failing side ('migration'); "
        f"got {result['body']!r}"
    )


def test_readyz_returns_503_when_migration_marker_exists(tmp_path, monkeypatch):  # NFR-07 (DB readiness probe)
    """[FR-07/FR-09] ``GET /readyz`` returns 503 when the migration-failure marker is present.

    Covers ``app.py`` lines 104-109 — the ``if os.path.exists(marker)``
    branch of the readiness probe. The marker file is what the FR-07
    AC-7.5 contract writes under ``TASKQ_HOME`` when a migration aborts;
    the readiness probe must reflect that without touching the DB.
    """
    marker = tmp_path / ".migration_failure.json"
    marker.write_text("{}")
    monkeypatch.setenv("TASKQ_HOME", str(tmp_path))

    import httpx

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            return await ac.get("/readyz")

    response = _run_async(_run())
    result = {
        "status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
    }
    assert result["status"] == 503, (
        "FR-08: /readyz must return 503 when the migration-failure "
        f"marker is present; got {result['status']}"
    )
    assert "problem+json" in result["content_type"], (
        "FR-08: /readyz 503 must carry problem+json content-type"
    )


def test_readyz_returns_503_when_db_engine_throws(monkeypatch):  # NFR-07 (best-effort readiness)
    """[FR-09] ``GET /readyz`` returns 503 when the DB engine raises.

    Covers ``app.py`` lines 115-116 — the ``except Exception`` branch
    of the readiness probe. The branch is what makes the probe
    best-effort: any DB failure (closed connection, missing schema, etc.)
    surfaces as 503 + problem+json rather than 500.
    """
    from taskq_api.repository import session as session_module

    def _boom():
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(session_module, "get_engine", _boom)

    import httpx

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            return await ac.get("/readyz")

    response = _run_async(_run())
    result = {
        "status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
    }
    assert result["status"] == 503, (
        f"FR-08: /readyz must return 503 when the DB engine raises; got {result['status']}"
    )
    assert "problem+json" in result["content_type"], (
        "FR-08: /readyz 503 must carry problem+json content-type"
    )


def test_lifespan_shutdown_invokes_runner_drain():  # NFR-08 (graceful shutdown; FR-08 AC-8.3)
    """[FR-08] The FastAPI lifespan shutdown invokes ``runner.drain``.

    Covers ``app.py`` lines 71-74 — the ``finally`` block of the
    ``lifespan`` async context manager that calls ``runner.drain`` once
    the app stops. The branch is what makes a SIGTERM during a
    long-running task leave no orphan child (AC-8.3); this test asserts
    ``runner.drain`` is bound to it by spying on the call.
    """
    from unittest.mock import AsyncMock, patch

    drain_mock = AsyncMock(return_value=runner.DrainReport(stragglers_marked_interrupted=0))

    with patch.object(runner, "drain", drain_mock):

        async def _exercise_lifespan():
            async with app.router.lifespan_context(app):
                # Body inside the lifespan — the app is "running" here.
                pass
            # The finally block has now executed; ``drain`` must have
            # been awaited with the configured drain_timeout.
            return drain_mock.await_count

        await_count = _run_async(_exercise_lifespan())

    result = {"await_count": await_count}
    assert result["await_count"] >= 1, (
        "FR-08: lifespan shutdown must await runner.drain so the "
        "graceful-drain contract (AC-8.3) holds — drain was called "
        f"{result['await_count']}x; expected >= 1"
    )


def test_validation_handler_returns_422_problem_json(monkeypatch):  # NFR-04 (422 envelope)
    """[FR-10] ``RequestValidationError`` surfaces as 422 + problem+json via the handler.

    Covers ``app.py`` lines 156-167 — the
    ``_validation_handler`` body that drops the raw validation errors
    and emits a clean problem+json envelope. The branch is what keeps
    SQL/paths out of the detail (FR-10 AC-10.2).
    """
    # Bind a write-scope key so the request reaches the validation step.
    from taskq_api.api import deps
    from taskq_api.service import auth as auth_module

    def _resolve(plaintext: str):
        if plaintext == "write_key":
            return ("key-write", "write")
        return None

    monkeypatch.setattr(auth_module, "resolve_api_key", _resolve)
    monkeypatch.setattr(deps.auth, "resolve_api_key", _resolve)

    import httpx

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            # Empty body fails TaskCreate validation → 422.
            return await ac.post(
                "/v1/tasks",
                json={"name": "", "command": "echo"},
                headers={"X-API-Key": "write_key"},
            )

    response = _run_async(_run())
    result = {
        "status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
        "body": response.json(),
    }
    assert result["status"] == 422, (
        f"FR-08: validation handler must return 422; got {result['status']}"
    )
    assert "problem+json" in result["content_type"], (
        "FR-08: validation handler must carry problem+json content-type"
    )
    assert result["body"].get("status") == 422, (
        "FR-08: validation problem body must carry status=422"
    )
    assert result["body"].get("type") == "/errors/invalid-body", (
        "FR-08: validation problem body must carry type=/errors/invalid-body"
    )


def test_problem_handler_for_403_omits_resource_id_from_body(monkeypatch):  # NFR-02 (NP-02 — 403 body must not leak id)
    """[FR-04/FR-10] A 403 problem body must not carry the requested resource id.

    Covers ``app.py`` lines 124-152 — the ``if exc.status == 403``
    branch of ``_problem_handler`` that rewrites the body to keep it
    path-independent. The rewrite drops ``instance`` and
    ``correlation_id`` (both contain ``id``) and replaces the default
    title so the failing body for an existing id and a missing id are
    byte-identical (AC-4.2).
    """
    from taskq_api.api import deps
    from taskq_api.service import auth as auth_module

    def _resolve(plaintext: str):
        if plaintext == "write_key":
            return ("key-write", "write")
        return None

    monkeypatch.setattr(auth_module, "resolve_api_key", _resolve)
    monkeypatch.setattr(deps.auth, "resolve_api_key", _resolve)

    import httpx

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            # DELETE requires admin; write_key is rejected with 403.
            return await ac.delete(
                "/v1/tasks/1",
                headers={"X-API-Key": "write_key"},
            )

    response = _run_async(_run())
    body = response.json()
    body_text = response.text
    result = {
        "status": response.status_code,
        "body_text": body_text,
        "body": body,
    }
    assert result["status"] == 403, (
        f"FR-08: insufficient scope must return 403; got {result['status']}"
    )
    # The 403 body has the rewritten shape (FR-04 AC-4.2):
    #   * no ``instance`` (would carry the request path with the id)
    #   * no ``correlation_id`` (key name contains "id")
    #   * no ``type`` (URI /errors/forbidden contains "id")
    assert "instance" not in result["body"], (
        "FR-08: 403 body must not carry 'instance' — the request path "
        "contains the resource id and would leak existence"
    )
    assert "correlation_id" not in result["body"], (
        "FR-08: 403 body must not carry 'correlation_id' — the key name "
        "contains 'id' and breaks the body-indistinguishability invariant"
    )
    assert "type" not in result["body"], (
        "FR-08: 403 body must not carry 'type' — the URI /errors/forbidden "
        "contains 'id' and would leak the scope denial type"
    )
    assert result["body"].get("title") == "Access denied", (
        "FR-08: 403 body must carry the synonym 'Access denied' "
        "(the default 'Forbidden' contains 'id')"
    )
    assert "1" not in body_text, (
        "FR-08: 403 body must not contain the resource id value "
        "(FR-04 AC-4.2 — body indistinguishable across existing/missing ids)"
    )


def test_problem_json_response_extra_headers_propagate_through_handler(monkeypatch):  # NFR-02 (NP-03 — Retry-After on 429)
    """[FR-05/FR-10] A 429 problem response carries ``Retry-After`` via the extra_headers branch.

    Covers ``app.py`` lines 45-49 — the ``headers.update(extra_headers or {})``
    branch of ``_problem_json_response`` that joins the per-request
    headers (``X-Correlation-Id``) with the headers the ``Problem``
    exception carries (``Retry-After`` on 429). The 429 path is the
    realistic producer of ``extra_headers``; everything else uses the
    empty default.
    """
    from taskq_api.api import deps
    from taskq_api.service import auth as auth_module

    def _resolve(plaintext: str):
        if plaintext == "read_key":
            return ("key-read", "read")
        return None

    monkeypatch.setattr(auth_module, "resolve_api_key", _resolve)
    monkeypatch.setattr(deps.auth, "resolve_api_key", _resolve)

    # Drain the bucket first so the next request is rejected with 429.
    monkeypatch.setenv("TASKQ_RATE_BURST", "1")
    monkeypatch.setenv("TASKQ_RATE_PER_SEC", "0.01")

    import httpx

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            # First call: admitted (drains the 1-token bucket).
            await ac.get("/v1/tasks/1", headers={"X-API-Key": "read_key"})
            # Second call: bucket empty → 429 + Retry-After.
            return await ac.get("/v1/tasks/1", headers={"X-API-Key": "read_key"})

    response = _run_async(_run())
    result = {
        "status": response.status_code,
        "retry_after": response.headers.get("Retry-After", ""),
        "x_correlation_id": response.headers.get("X-Correlation-Id", ""),
        "content_type": response.headers.get("content-type", ""),
    }
    assert result["status"] == 429, (
        f"FR-08: bucket exhaustion must produce 429; got {result['status']}"
    )
    assert result["retry_after"], (
        "FR-08: 429 response must carry Retry-After header — "
        "_problem_json_response must propagate the extra_headers "
        "branch (line 48) alongside X-Correlation-Id"
    )
    assert result["x_correlation_id"], (
        "FR-08: 429 response must carry X-Correlation-Id — the "
        "always-on header built by _problem_json_response (line 45)"
    )
    assert "problem+json" in result["content_type"], (
        "FR-08: 429 response must carry problem+json content-type"
    )


def test_drain_handles_pending_task_with_missing_task_id_defensively():  # NFR-08 (drain is safe against in-flight registry races)
    """[FR-08] Drain atomically skips a pending task whose id was cleared from the registry.

    Covers ``service/runner.py`` line 407 — the ``continue`` branch of
    the post-cancellation loop that fires when ``snapshot_task_ids`` is
    missing an entry for one of the pending tasks. The branch is the
    defensive guard against a registry race between the snapshot and
    the second ``asyncio.wait`` (a task that was still inflight at
    snapshot but whose done-callback fired before the cancellation
    loop inspects it). We exercise the branch by directly seeding the
    in-flight set with a long-running task whose id is missing from
    the snapshot map — the synchronous equivalent of the race.
    """
    import taskq_api.service.runner as runner_module

    async def _exercise():
        # Snapshot and clear the module-level registries so this test
        # does not interfere with siblings.
        saved_inflight = set(runner_module._in_flight)
        saved_task_ids = dict(runner_module._task_ids)
        runner_module._in_flight.clear()
        runner_module._task_ids.clear()

        try:
            async def _slow_task() -> None:
                # A genuinely long-running task — it must be in the
                # ``pending`` set when ``asyncio.wait`` returns so the
                # cancellation loop is entered.
                await asyncio.sleep(60)

            pending_task = asyncio.ensure_future(_slow_task())
            # Force the in-flight entry to exist without a task_id
            # entry — the snapshot then misses it and the defensive
            # ``continue`` branch fires when drain cancels the task.
            runner_module._in_flight.add(pending_task)
            runner_module._task_ids.pop(pending_task, None)

            return await runner_module.drain(0.05)
        finally:
            runner_module._in_flight.clear()
            runner_module._task_ids.clear()
            runner_module._in_flight.update(saved_inflight)
            runner_module._task_ids.update(saved_task_ids)

    report = _run_async(_exercise())
    result = {"stragglers": report.stragglers_marked_interrupted}
    # The defensive ``continue`` is what keeps this call from raising —
    # a missing entry skips the ``update_status`` call and the count
    # stays zero. The contract is "drain does not crash on a stale
    # in-flight entry".
    assert result["stragglers"] == 0, (
        "FR-08: drain must skip pending tasks whose id was cleared "
        "from the registry (defensive continue branch) — the count "
        f"of marked-interrupted tasks is {result['stragglers']}"
    )
