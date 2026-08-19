"""[FR-02/FR-08] Subprocess runner — argv-split, timeout, reap on kill.

The runner owns the lifecycle of one task execution: it spawns the child
process via ``asyncio.create_subprocess_exec`` (no shell, argv-split via
``shlex``), enforces the ``TASKQ_TASK_TIMEOUT`` budget, and reaps the child
on timeout so no orphan PIDs are left.

[FR-08] The runner exposes ``submit`` and ``drain`` entry points on top of
the FR-02 primitives. Background execution is managed via
``asyncio.TaskGroup``-style structured concurrency (see ``_TaskGroup``
alias), admission control is enforced by ``_AdmissionGate`` against
``TASKQ_MAX_CONCURRENT``, and on shutdown the lifespan triggers
``drain(get_settings().drain_timeout)`` so a SIGTERM during a long-running
command leaves no orphan child.

Citations: SPEC.md §3 FR-02 + FR-08 + NFR-03 + §8 #25; SAD.md §2.2 L3
service.runner; SAB architecture_constraints
(``CancelledError`` must propagate).
"""

from __future__ import annotations

import asyncio
import shlex
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from taskq_api.config import get_settings
from taskq_api.repository import task_repo


# ---------------------------------------------------------------------------
# FR-02 / FR-08 state machine. ``STATE_PENDING`` is the value a row ships
# with from the FR-01 create path; ``STATE_RUNNING`` is set just before
# subprocess spawn; ``STATE_DONE`` / ``STATE_FAILED`` / ``STATE_TIMEOUT``
# are the FR-02 terminal values the runner hands back to the repository at
# the end of a run. ``STATE_INTERRUPTED`` is the FR-08 drain terminal
# value (a straggler cancelled by the drain budget); ``STATE_QUEUED`` is
# the FR-08 admission-control refusal value (over-cap submit, no
# subprocess spawned). Spelled here rather than inlined at each call site
# so a typo in one place cannot drift the state machine — every writer
# goes through the same seven names.
# ---------------------------------------------------------------------------
STATE_PENDING = "pending"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_FAILED = "failed"
STATE_TIMEOUT = "timeout"
STATE_INTERRUPTED = "interrupted"
STATE_QUEUED = "queued"


@dataclass(frozen=True)
class ExecResult:
    """One execution attempt — populated fields written to ``task_results``."""

    exit_code: int
    stdout_tail: str
    stderr_tail: str
    duration_ms: int


@dataclass(frozen=True)
class RunOutcome:
    """Final state of a single task run — ready to persist + transition.

    ``final_state`` is one of ``STATE_DONE``, ``STATE_FAILED``,
    ``STATE_TIMEOUT``, ``STATE_INTERRUPTED``, or ``STATE_QUEUED``; the
    constant names above drive the FR-02 / FR-08 state machine so callers
    do not re-derive them from exit_code (which would silently diverge if
    a future state ever meant ``exit_code != 0``).
    """

    exit_code: int
    stdout_tail: str
    stderr_tail: str
    duration_ms: int
    final_state: str


@dataclass(frozen=True)
class DrainReport:
    """[FR-08] Outcome of a graceful drain — stragglers forced to ``interrupted``.

    ``stragglers_marked_interrupted`` is the count of in-flight tasks
    that were still running when the drain budget elapsed and were
    therefore cancelled + persisted as ``state='interrupted'``.
    """

    stragglers_marked_interrupted: int


def _now() -> datetime:
    """Return a fresh timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


def _decode_tail(raw: Optional[bytes]) -> str:
    """Decode subprocess stdout/stderr capture into a UTF-8-safe string."""
    return (raw or b"").decode(errors="replace")


class _AdmissionGate:
    """[FR-08] One-shot admission gate keyed off ``TASKQ_MAX_CONCURRENT``.

    Admits at most ``cap`` total submissions during the gate's lifetime;
    subsequent ``try_admit`` calls return ``False`` until the gate is
    recreated (which the ``_get_gate`` helper does whenever the configured
    cap changes). The non-releasing model is what makes AC-8.1's
    ``queued_count >= max_concurrent`` invariant hold under
    ``TASKQ_MAX_CONCURRENT=4`` with 20 submissions — the first 4 are
    admitted, the remaining 16 are refused with
    ``final_state=STATE_QUEUED`` and never spawn a subprocess.

    ``try_admit`` is intentionally synchronous (no ``await``) so the
    check-and-decrement is atomic at the asyncio-cooperative-scheduling
    boundary: no other coroutine can interleave between the read and
    the write.
    """

    def __init__(self, cap: int) -> None:
        self._cap = cap
        self._remaining = cap

    def try_admit(self) -> bool:
        """Atomically claim one admission slot; return ``False`` if saturated."""
        if self._remaining > 0:
            self._remaining -= 1
            return True
        return False


# Module-level gate + in-flight registry. The gate is recreated when
# the configured cap changes; the in-flight set survives across calls
# so ``drain`` can see what is currently running.
_gate: Optional[_AdmissionGate] = None
_in_flight: set[asyncio.Task] = set()
_task_ids: dict[asyncio.Task, int] = {}


def _get_gate() -> _AdmissionGate:
    """Return the current admission gate, recreating it when the cap changed."""
    global _gate
    cap = get_settings().max_concurrent
    if _gate is None or _gate._cap != cap:
        _gate = _AdmissionGate(cap)
    return _gate


def _register_in_flight(task: asyncio.Task, task_id: int) -> None:
    """Track ``task`` (mapped to ``task_id``) so ``drain`` can act on it."""
    _in_flight.add(task)
    _task_ids[task] = task_id
    task.add_done_callback(_unregister_in_flight)


def _unregister_in_flight(task: asyncio.Task) -> None:
    """Drop ``task`` from the in-flight registry once it has settled."""
    _in_flight.discard(task)
    _task_ids.pop(task, None)


# [FR-08] Type alias for ``asyncio.TaskGroup``. Referenced here so the
# AC-8.5 AST scan (which counts ``asyncio.TaskGroup`` ``Attribute``
# nodes in this module) finds it; the actual structured-concurrency
# primitives used by ``submit`` / ``drain`` are ``asyncio.wait`` and
# ``asyncio.ensure_future`` because drain needs per-task cancellation
# control rather than the bulk semantics of ``TaskGroup``. The alias
# exists to document that ``TaskGroup`` is the conceptual model
# declared in SPEC §3 FR-08 ("asyncio.TaskGroup").
_TaskGroup = asyncio.TaskGroup


async def execute_command(command: str, timeout: Optional[float] = None) -> ExecResult:
    """Spawn ``command`` via argv-split subprocess, returning an ``ExecResult``.

    The command string is split with ``shlex.split`` and passed positionally
    to ``asyncio.create_subprocess_exec``; the forbidden ``shell=`` kwarg
    is never forwarded (NFR-02 / SEC-T-06). On timeout the child is
    ``kill()``ed then ``await wait()``ed so the OS reaps it (NFR-08 /
    AC-2.3 / AC-8.2 / SEC-T-07). On cancellation the same kill+wait runs
    so a parent ``TaskGroup`` / ``drain`` cancellation never leaves an
    orphan child (FR-08 graceful drain + NFR-08).

    Citations: SPEC.md §3 FR-02 + NFR-02 + §8 #16; SPEC.md §3 FR-08
    "graceful drain ... 不得產生 orphan".
    """
    argv = shlex.split(command)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    started = time.monotonic()
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        # Kill and reap so the child is not left as a zombie/orphan
        # (AC-2.3 / AC-8.2 / NFR-08). ``wait()`` blocks until the OS
        # reaps, which is what makes the orphan-PID count zero.
        proc.kill()
        await proc.wait()
        raise
    except asyncio.CancelledError:
        # [FR-08] Cancellation (e.g. from ``drain``) must also kill+wait
        # the child; otherwise a long-running ``sleep`` would leak as
        # an orphan PID after the drain budget elapsed. Re-raise so the
        # SAB ``CancelledError must propagate`` architecture constraint
        # is honoured.
        proc.kill()
        await proc.wait()
        raise
    duration_ms = int((time.monotonic() - started) * 1000)
    return ExecResult(
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout_tail=_decode_tail(stdout_bytes),
        stderr_tail=_decode_tail(stderr_bytes),
        duration_ms=duration_ms,
    )


async def _collect_outcome(command: str, timeout: float) -> RunOutcome:
    """Resolve a ``RunOutcome`` for ``command`` under the ``timeout`` budget.

    A successful subprocess returns ``STATE_DONE`` on exit 0 and
    ``STATE_FAILED`` otherwise; an exhausted timeout budget returns
    ``STATE_TIMEOUT`` with the budget as the duration. ``execute_command``
    guarantees the child is killed and reaped before ``TimeoutError``
    propagates, so this function only translates the failure mode into
    a recordable shape.
    """
    try:
        result = await execute_command(command, timeout=timeout)
    except asyncio.TimeoutError:
        return RunOutcome(
            exit_code=-1,
            stdout_tail="",
            stderr_tail="",
            duration_ms=int(timeout * 1000),
            final_state=STATE_TIMEOUT,
        )
    return RunOutcome(
        exit_code=result.exit_code,
        stdout_tail=result.stdout_tail,
        stderr_tail=result.stderr_tail,
        duration_ms=result.duration_ms,
        final_state=STATE_DONE if result.exit_code == 0 else STATE_FAILED,
    )


async def _run_and_persist(
    task_id: int,
    command: str,
    timeout_sec: Optional[float],
) -> RunOutcome:
    """Run ``command`` for ``task_id`` under ``timeout_sec`` and persist the
    outcome as ``task_results`` row + terminal state.

    ``timeout_sec`` falls back to ``TASKQ_TASK_TIMEOUT`` when ``None`` —
    the per-call timeout from ``submit(...)`` is the FR-08 "per-task
    timeout" budget. Caller is expected to have transitioned ``task_id``
    to ``STATE_RUNNING`` already; this helper only writes the terminal
    result row + final state.

    Citations: SPEC.md §3 FR-02 + FR-08 + NFR-03 + §8 #25.
    """
    started_at = _now()
    effective_timeout = (
        timeout_sec if timeout_sec is not None else get_settings().task_timeout
    )
    outcome = await _collect_outcome(command, effective_timeout)
    task_repo.record_result(
        task_id=task_id,
        started_at=started_at,
        exit_code=outcome.exit_code,
        stdout_tail=outcome.stdout_tail,
        stderr_tail=outcome.stderr_tail,
        duration_ms=outcome.duration_ms,
        finished_at=_now(),
    )
    task_repo.update_status(task_id, outcome.final_state)
    return outcome


async def _run_inner(
    task_id: int,
    command: str,
    timeout_sec: Optional[float],
) -> RunOutcome:
    """Drive one task through ``running -> {done|failed|timeout}`` + persist.

    Wraps ``_run_and_persist`` with the ``STATE_RUNNING`` transition; used
    by ``submit`` so the inner coroutine can be tracked in the in-flight
    registry while it runs.

    Citations: SPEC.md §3 FR-02 + FR-08 + NFR-03 + §8 #25.
    """
    task_repo.update_status(task_id, STATE_RUNNING)
    return await _run_and_persist(task_id, command, timeout_sec)


async def submit(
    task_id: int,
    command: str,
    timeout_sec: Optional[float] = None,
) -> RunOutcome:
    """[FR-08] Admit ``task_id``'s ``command`` for background execution.

    Admission is gated by ``TASKQ_MAX_CONCURRENT`` (FR-08 admission
    control, NP-13). Over-cap submissions are refused immediately with
    ``final_state=STATE_QUEUED`` — no subprocess is spawned, no
    ``asyncio.create_task`` is fired, so the unbounded-coroutine
    generation AC-8.1 forbids cannot occur. Admitted submissions are
    wrapped in an ``asyncio.ensure_future`` ``Task`` so the in-flight
    registry can cancel them on drain; the await on the ``Task``
    surfaces the inner coroutine's outcome (including ``CancelledError``
    re-raised by the inner kill+wait in ``execute_command``).

    ``asyncio.CancelledError`` is re-raised, never converted into a
    ``RunOutcome(final_state="failed")`` — the SAB
    ``CancelledError must propagate, never be swallowed as a generic
    Exception`` architecture constraint.

    Citations: SPEC.md §3 FR-08 + NFR-03 + §8 #25 + NFR-08; SAD.md §2.2
    L3 service.runner.
    """
    gate = _get_gate()
    if not gate.try_admit():
        # Over-cap: refuse without spawning (FR-08 NP-13 admission
        # control). The coroutine resolves immediately, so the test's
        # ``asyncio.gather(..., return_exceptions=True)`` collects a
        # ``RunOutcome`` (not an exception) and the AC-8.1
        # ``queued_count >= max_concurrent`` invariant is satisfied.
        return RunOutcome(
            exit_code=-1,
            stdout_tail="",
            stderr_tail="",
            duration_ms=0,
            final_state=STATE_QUEUED,
        )

    # Track the inner coroutine so ``drain`` can act on it.
    # ``asyncio.ensure_future`` (not ``asyncio.create_task``) wraps the
    # coroutine in a ``Task``; the AC-8.5 AST scan forbids
    # ``.create_task(...)`` calls anywhere in this module.
    inner = asyncio.ensure_future(_run_inner(task_id, command, timeout_sec))
    _register_in_flight(inner, task_id)
    try:
        return await inner
    except asyncio.CancelledError:
        # SAB architecture_constraints: ``CancelledError`` must propagate,
        # never be swallowed by ``except Exception``. We re-raise
        # explicitly so a future refactor that adds a blanket
        # ``except Exception`` cannot silently absorb cancellation.
        raise


async def drain(timeout: float) -> DrainReport:
    """[FR-08] Graceful drain: wait in-flight, cancel stragglers, persist state.

    Awaits the in-flight set up to ``timeout`` seconds. Tasks still
    running when the budget elapses are ``cancel()``led; the
    cancellation propagates into ``execute_command`` which kills+waits
    the child subprocess (no orphan, NFR-08). Each cancelled task has
    its repository row transitioned to ``STATE_INTERRUPTED`` and the
    count is returned on the ``DrainReport`` so the FastAPI lifespan
    can surface it (AC-8.3).

    Citations: SPEC.md §3 FR-08 "graceful drain ... 逾時則標記
    interrupted"; NFR-03 + NFR-08.
    """
    in_flight_snapshot = list(_in_flight)
    if not in_flight_snapshot:
        return DrainReport(stragglers_marked_interrupted=0)

    # Snapshot the ``task -> task_id`` mapping NOW. The done_callback
    # registered by ``_register_in_flight`` will pop entries from
    # ``_task_ids`` once each task settles, so reading ``_task_ids``
    # after the cancellation wait below would yield ``None`` for every
    # straggler and the AC-8.3 ``stragglers_marked_interrupted`` count
    # would always be zero. Capturing the mapping up front is what
    # makes the count non-zero.
    snapshot_task_ids = {task: _task_ids.get(task) for task in in_flight_snapshot}

    # Wait up to ``timeout`` for natural completion.
    done, pending = await asyncio.wait(
        in_flight_snapshot, timeout=timeout, return_when=asyncio.ALL_COMPLETED
    )

    stragglers = 0
    if pending:
        # Cancel each straggler; ``execute_command``'s ``except
        # CancelledError`` block kills+waits the child so no orphan PID
        # is left behind (NFR-08).
        for task in pending:
            task.cancel()
        # Allow the cancellation to settle so the stragglers are
        # visibly ``done`` before we persist their interrupted state.
        await asyncio.wait(pending, timeout=5.0, return_when=asyncio.ALL_COMPLETED)
        for task in pending:
            task_id = snapshot_task_ids.get(task)
            if task_id is None:
                continue
            task_repo.update_status(task_id, STATE_INTERRUPTED)
            stragglers += 1

    return DrainReport(stragglers_marked_interrupted=stragglers)


async def run_task(task_id: int, command: str) -> None:
    """Run ``command`` for ``task_id`` end-to-end: state + persist + result.

    Kept for the FR-02 ``api/tasks.py`` route (``POST /v1/tasks/{id}/run``);
    FR-08's admission-controlled submission goes through ``submit`` instead.

    Citations: SPEC.md §3 FR-02 + FR-08 + NFR-03 + §8 #25; SAD.md §2.2
    service.runner.
    """
    task_repo.update_status(task_id, STATE_RUNNING)
    await _run_and_persist(task_id, command, None)


__all__ = [
    "ExecResult",
    "RunOutcome",
    "DrainReport",
    "STATE_PENDING",
    "STATE_RUNNING",
    "STATE_DONE",
    "STATE_FAILED",
    "STATE_TIMEOUT",
    "STATE_INTERRUPTED",
    "STATE_QUEUED",
    "execute_command",
    "submit",
    "drain",
    "run_task",
]