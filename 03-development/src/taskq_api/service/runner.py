"""[FR-02] Subprocess runner — argv-split, timeout, reap on kill.

The runner owns the lifecycle of one task execution: it spawns the child
process via ``asyncio.create_subprocess_exec`` (no shell, argv-split via
``shlex``), enforces the ``TASKQ_TASK_TIMEOUT`` budget, and reaps the child
on timeout so no orphan PIDs are left.

Citations: SPEC.md §3 FR-02 + NFR-02 + NFR-03 + §8 #16 + §8 #25;
SAD.md §2.2 L3 service.runner.
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

    ``final_state`` is one of ``done``, ``failed``, or ``timeout``; the
    two strings that drive the FR-02 state machine are spelled here so
    callers do not re-derive them from exit_code (which would silently
    diverge if a future state ever means ``exit_code != 0``).
    """

    exit_code: int
    stdout_tail: str
    stderr_tail: str
    duration_ms: int
    final_state: str


def _now() -> datetime:
    """Return a fresh timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


def _decode_tail(raw: Optional[bytes]) -> str:
    """Decode subprocess stdout/stderr capture into a UTF-8-safe string."""
    return (raw or b"").decode(errors="replace")


async def execute_command(command: str, timeout: Optional[float] = None) -> ExecResult:
    """Spawn ``command`` via argv-split subprocess, returning an ``ExecResult``.

    The command string is split with ``shlex.split`` and passed positionally
    to ``asyncio.create_subprocess_exec``; the forbidden ``shell=`` kwarg
    is never forwarded (NFR-02 / SEC-T-06). On timeout the child is
    ``kill()``ed then ``await wait()``ed so the OS reaps it (NFR-08 / AC-2.3).

    Citations: SPEC.md §3 FR-02 + NFR-02 + §8 #16.
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
        # (AC-2.3 / NFR-08). ``wait()`` blocks until the OS reaps.
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

    A successful subprocess returns ``"done"`` on exit 0 and ``"failed"``
    otherwise; a timeout budget returns ``"timeout"`` with the budget as
    the duration. ``execute_command`` guarantees the child is killed and
    reaped before the ``TimeoutError`` propagates, so this function only
    translates the failure mode into a recordable shape.
    """
    try:
        result = await execute_command(command, timeout=timeout)
    except asyncio.TimeoutError:
        return RunOutcome(
            exit_code=-1,
            stdout_tail="",
            stderr_tail="",
            duration_ms=int(timeout * 1000),
            final_state="timeout",
        )
    return RunOutcome(
        exit_code=result.exit_code,
        stdout_tail=result.stdout_tail,
        stderr_tail=result.stderr_tail,
        duration_ms=result.duration_ms,
        final_state="done" if result.exit_code == 0 else "failed",
    )


async def run_task(task_id: int, command: str) -> None:
    """Run ``command`` for ``task_id`` end-to-end: state + persist + result.

    Transitions the task through ``pending → running → {done|failed|timeout}``,
    records the FR-02 result row (FR-07 v3 multi-row schema), and never
    swallows ``asyncio.CancelledError`` (architecture constraint).

    Citations: SPEC.md §3 FR-02 + FR-08 + NFR-03 + §8 #25; SAD.md §2.2 service.runner.
    """
    started_at = _now()
    task_repo.update_status(task_id, "running")

    outcome = await _collect_outcome(command, get_settings().task_timeout)

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


__all__ = ["ExecResult", "RunOutcome", "execute_command", "run_task"]
