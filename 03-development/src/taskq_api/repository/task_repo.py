"""[FR-01] Task repository — only consumer of SQL in the project.

Citations: SPEC.md §3 FR-01 + FR-06; SAD.md §2.2 L2 task_repo;
NFR-01 (N+1 guard via selectinload).
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from taskq_api.models.orm import Task
from taskq_api.repository import session as session_module
from taskq_api.repository.session import session_scope


class DuplicateTaskError(Exception):
    """Domain exception raised when a unique constraint on ``Task.name`` is violated.

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


def create(name: str, command: str, status: str = "pending") -> Task:
    """Insert a new task and return the persisted ORM instance.

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
        with session_module.insert_scope() as session:
            task = Task(name=name, command=command, status=status)
            session.add(task)
            session.flush()
            # expunge so callers can use the instance after the session closes
            session.expunge(task)
    except IntegrityError as exc:
        raise DuplicateTaskError(name) from exc
    return task


def get_by_id(task_id: int) -> Optional[Task]:
    """Return the task row with its result eagerly loaded, or None."""
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
    """Cursor-paginated list of tasks with eager-loaded result rows.

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
    """Delete the task + its task_results row in one transaction.

    Citations: SPEC.md §3 FR-01 + FR-06; AC-1.6 cascade delete.
    """
    with session_scope() as session:
        task = session.get(Task, task_id)
        if task is None:
            return False
        session.delete(task)
        session.flush()
    return True


class TaskRepo:
    """Object-style repository facade.

    Module-level functions above remain the canonical entry points; this
    class is provided so callers can also instantiate a repository and
    invoke methods on it (``task_repo.create(...)``).
    """

    create = staticmethod(create)
    get_by_id = staticmethod(get_by_id)
    list_paginated = staticmethod(list_paginated)
    delete = staticmethod(delete)


task_repo = TaskRepo()


__all__ = [
    "create",
    "get_by_id",
    "list_paginated",
    "delete",
    "DuplicateTaskError",
    "TaskRepo",
    "task_repo",
]