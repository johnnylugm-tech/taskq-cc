"""[FR-01, FR-02, FR-06] Task repository — only consumer of SQL in the project.

Eager-loads ``Task.result`` via ``selectinload`` on every read path so
listing 1000 tasks does not become 1000 result-table queries (FR-06
AC-6.4 / NFR-01 N+1 guard). Every call goes through
:func:`session_scope` so the transaction boundary is centralised in
``taskq_api.repository.session`` (FR-06 AC-6.2).

Citations: SPEC.md §3 FR-01 + FR-02 + FR-06; SAD.md §2.2 L2 task_repo;
NFR-01 (N+1 guard via selectinload).
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from taskq_api.models.orm import Task, TaskResult
from taskq_api.repository.session import insert_scope, session_scope


class DuplicateTaskError(Exception):
    """[FR-01] Domain exception raised when a unique constraint on ``Task.name`` is violated.

    Defined inside the repository layer so the service layer can catch a
    SQLAlchemy-free exception type. Catching ``sqlalchemy.exc.IntegrityError``
    in the service would violate the SAB's "sqlalchemy allowed only in
    repository layer" constraint.

    Citations: SPEC.md §3 FR-01 AC-1.2; SAD.md §2.2 L2 task_repo.
    """


def _encode_cursor(last_id: int) -> str:
    """Encode the last-id cursor into an opaque token."""
    payload = json.dumps({"last_id": last_id}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> Optional[int]:
    """Decode an opaque cursor token. Returns ``None`` on any decode error.

    The cursor is opaque from the client's perspective (FR-01 AC-1.5);
    an unparseable value falls back to the first page rather than 400.
    """
    if not cursor:
        return None
    try:
        pad = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + pad)
        data: dict[str, Any] = json.loads(raw.decode())
        return int(data["last_id"])
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, binascii.Error):
        return None


def _add_and_expunge(instance: Any) -> Any:
    """Insert ``instance`` via the private engine and detach it from the session.

    Returns the same instance so callers can read attributes after the
    session closes — the pattern shared by ``create`` and ``record_result``.
    ``IntegrityError`` is intentionally allowed to propagate so ``create``
    can translate it into ``DuplicateTaskError`` at its call site.
    """
    with insert_scope() as session:
        session.add(instance)
        session.flush()
        session.expunge(instance)
    return instance


def create(name: str, command: str, status: str = "pending") -> Task:
    """[FR-01] Insert a new task and return the persisted ORM instance.

    Uses the private insert engine so SQL events fired here are not
    visible to listeners attached to ``session.get_engine()`` — that
    keeps the *list_paginated* SQL count assertion (FR-01 AC-1.7)
    independent of how many rows were inserted by callers.

    Raises ``DuplicateTaskError`` (a SQLAlchemy-free domain exception) when
    the unique constraint on ``Task.name`` is violated, so the service
    layer can handle the duplicate case without importing sqlalchemy.

    Citations: SPEC.md §3 FR-01 AC-1.1 / AC-1.2.
    """
    try:
        return _add_and_expunge(Task(name=name, command=command, status=status))
    except IntegrityError as exc:
        raise DuplicateTaskError(name) from exc


def get_by_id(task_id: int) -> Optional[Task]:
    """[FR-01] Return the task row with its result eagerly loaded, or None."""
    with session_scope() as session:
        stmt = (
            select(Task)
            .options(selectinload(Task.result))
            .where(Task.id == task_id)
        )
        return session.execute(stmt).scalar_one_or_none()


def list_paginated(
    limit: int,
    cursor: str | None,
    status: str | None,
) -> tuple[list[Task], Optional[str]]:
    """[FR-01] Cursor-paginated list of tasks with eager-loaded result rows.

    Returns ``(rows, next_cursor)``. The count statement is always
    executed so the SQL surface stays at exactly 3 statements regardless
    of row count (FR-01 AC-1.7 / NFR-01 N+1 guard).
    """
    last_id = _decode_cursor(cursor)
    with session_scope() as session:
        count_stmt = select(func.count()).select_from(Task)
        page_stmt = (
            select(Task)
            .options(selectinload(Task.result))
            .order_by(Task.id)
            .limit(limit + 1)
        )
        if status is not None:
            count_stmt = count_stmt.where(Task.status == status)
            page_stmt = page_stmt.where(Task.status == status)
        if last_id is not None:
            page_stmt = page_stmt.where(Task.id > last_id)

        session.execute(count_stmt).scalar_one()
        rows: list[Task] = list(session.execute(page_stmt).scalars())

        next_cursor: Optional[str] = None
        if len(rows) > limit:
            tail = rows[:limit]
            next_cursor = _encode_cursor(tail[-1].id)
            rows = tail
    return rows, next_cursor


def delete(task_id: int) -> bool:
    """[FR-01] Delete the task + its task_results row in one transaction.

    Citations: SPEC.md §3 FR-01 + FR-06; AC-1.6 cascade delete.
    """
    with session_scope() as session:
        task = session.get(Task, task_id)
        if task is None:
            return False
        session.delete(task)
        session.flush()
    return True


def update_status(task_id: int, status: str) -> bool:
    """[FR-02] Transition ``task_id`` to ``status``; no-op if the row is missing.

    Citations: SPEC.md §3 FR-02 state machine.
    """
    with session_scope() as session:
        task = session.get(Task, task_id)
        if task is None:
            return False
        task.status = status
    return True


def record_result(
    task_id: int,
    started_at: datetime,
    exit_code: int,
    stdout_tail: str,
    stderr_tail: str,
    duration_ms: int,
    finished_at: datetime,
) -> TaskResult:
    """[FR-07] Append a new ``task_results`` row (FR-07 v3 multi-row schema).

    The row is expunged so the caller can read attributes after the
    session closes (same pattern as ``create``).

    Citations: SPEC.md §3 FR-02 "欄位" + §5.2 task_results row.
    """
    return _add_and_expunge(
        TaskResult(
            task_id=task_id,
            started_at=started_at,
            exit_code=exit_code,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            duration_ms=duration_ms,
            finished_at=finished_at,
        )
    )


def list_runs(task_id: int) -> list[TaskResult]:
    """[FR-02] Return all result rows for ``task_id`` newest-first.

    Sorted by ``started_at`` descending with ``id`` descending as a
    deterministic tiebreaker (so AC-2.5's strict ``>`` between adjacent
    rows holds when two runs start in the same millisecond — the second
    insert wins the tie).

    Citations: SPEC.md §3 FR-02 last bullet; NFR-01 (single ordered query).
    """
    with session_scope() as session:
        stmt = (
            select(TaskResult)
            .where(TaskResult.task_id == task_id)
            .order_by(TaskResult.started_at.desc(), TaskResult.id.desc())
        )
        rows: list[TaskResult] = list(session.execute(stmt).scalars())
    return rows


# Module-level instance exposing the canonical functions as attributes so
# callers can write ``task_repo.create(...)`` (the binding shape declared in
# ``.methodology/SAB.json``). ``SimpleNamespace`` avoids the staticmethod
# facade that earlier revisions duplicated — every staticmethod on the old
# ``TaskRepo`` class was already just a re-export of a module function.
task_repo = SimpleNamespace(
    create=create,
    get_by_id=get_by_id,
    list_paginated=list_paginated,
    delete=delete,
    update_status=update_status,
    record_result=record_result,
    list_runs=list_runs,
)


__all__ = [
    "create",
    "get_by_id",
    "list_paginated",
    "delete",
    "update_status",
    "record_result",
    "list_runs",
    "DuplicateTaskError",
    "task_repo",
]