"""[FR-02, FR-08] Pure state primitives for the subprocess runner.

Holds the FR-02 / FR-08 state-machine constants, the frozen value
objects the runner hands back (:class:`ExecResult`,
:class:`RunOutcome`, :class:`DrainReport`), the two decoding helpers,
and the :class:`_AdmissionGate` counter. Everything here is pure — no
asyncio, no subprocess, no repository access — which is what lets
:mod:`taskq_api.service.runner` stay under the NFR-11 400-line file
budget while keeping one owner per concept.

Citations: SPEC.md §3 FR-02 + FR-08; SAD.md §2.2 L3 service.runner;
NFR-11 (單一檔案 ≤ 400 行).
"""

# pragma: no error-handling  (frozen value objects + pure helpers — no I/O to handle)

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from taskq_api.config import redact

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
    """[FR-02] One execution attempt — fields written to ``task_results``."""

    exit_code: int
    stdout_tail: str
    stderr_tail: str
    duration_ms: int


@dataclass(frozen=True)
class RunOutcome:
    """[FR-02] [FR-08] Final state of a single task run — persist + transition.

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


def now() -> datetime:
    """[FR-02] Return a fresh timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


def decode_tail(raw: Optional[bytes]) -> str:
    """[FR-02] [NFR-04] Decode a subprocess capture into a redacted string.

    The bytes are decoded UTF-8-safely and then passed through
    :func:`taskq_api.config.redact` so a secret echoed by the child
    command (an ``sk-`` key, a ``token=``/``Bearer`` credential, or a
    ``postgres://`` DSN) never reaches the ``task_results.stdout_tail``
    / ``stderr_tail`` columns (AC-N4.1).
    """
    return redact((raw or b"").decode(errors="replace"))


class _AdmissionGate:
    """[FR-08] One-shot admission gate keyed off ``TASKQ_MAX_CONCURRENT``.

    Admits at most ``cap`` concurrent submissions; once saturated,
    ``try_admit`` returns ``False`` until a settled submission calls
    ``release``. The gate is recreated by the runner's ``_get_gate``
    helper whenever the configured cap changes. The model is what makes
    AC-8.1's ``queued_count >= max_concurrent`` invariant hold under
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
        """[FR-08] Atomically claim one admission slot; return ``False`` if saturated."""
        if self._remaining > 0:
            self._remaining -= 1
            return True
        return False

    def release(self) -> None:
        """[FR-08] Give a claimed slot back once its submission has settled.

        Without this the cap would be a *lifetime* budget rather than a
        concurrency limit: after ``cap`` submissions the gate would refuse
        every later ``submit`` forever, even with nothing in flight.
        """
        if self._remaining < self._cap:
            self._remaining += 1


__all__ = [
    "STATE_PENDING",
    "STATE_RUNNING",
    "STATE_DONE",
    "STATE_FAILED",
    "STATE_TIMEOUT",
    "STATE_INTERRUPTED",
    "STATE_QUEUED",
    "ExecResult",
    "RunOutcome",
    "DrainReport",
    "now",
    "decode_tail",
]
