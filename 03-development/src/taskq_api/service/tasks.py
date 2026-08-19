"""[FR-01] Service layer for tasks — name uniqueness, injection denylist,
cursor pagination, transactional delete.

Citations: SPEC.md §3 FR-01; SAD.md §2.2 L3 service.tasks.
"""

from __future__ import annotations

from typing import Optional

from taskq_api.errors import make_problem
from taskq_api.models.orm import Task
from taskq_api.repository import task_repo
from taskq_api.repository.task_repo import DuplicateTaskError


def _to_task_read(task: Task) -> dict:
    """Convert an ORM Task into the TaskRead JSON shape (id is a string)."""
    return {
        "id": str(task.id),
        "name": task.name,
        "command": task.command,
        "status": task.status,
        "created_at": task.created_at,
    }


def create_task(name: str, command: str) -> dict:
    """Service-level task creation.

    Citations: SPEC.md §3 FR-01 AC-1.1 / AC-1.2.
    Raises ``Problem(409)`` when the name already exists (AC-1.2 duplicate).
    The repository raises ``DuplicateTaskError`` (a SQLAlchemy-free domain
    exception) so this service layer never imports sqlalchemy directly
    (SAB §architecture_constraints).
    """
    try:
        task = task_repo.create(name=name, command=command, status="pending")
    except DuplicateTaskError as exc:
        raise make_problem(
            status=409,
            title="Duplicate name",
            detail="A task with this name already exists.",
            type_uri="/errors/duplicate-name",
        ) from exc
    return _to_task_read(task)


def get_task(task_id: int) -> Optional[dict]:
    """Service-level task lookup by id."""
    task = task_repo.get_by_id(task_id)
    if task is None:
        return None
    return _to_task_read(task)


def list_tasks(
    limit: int,
    cursor: Optional[str],
    status: Optional[str],
) -> dict:
    """Service-level cursor-paginated list.

    Citations: SPEC.md §3 FR-01 AC-1.4 / AC-1.5.
    """
    rows, next_cursor = task_repo.list_paginated(limit=limit, cursor=cursor, status=status)
    return {
        "items": [_to_task_read(row) for row in rows],
        "limit": limit,
        "next_cursor": next_cursor,
    }


def delete_task(task_id: int) -> bool:
    """Service-level transactional delete.

    Citations: SPEC.md §3 FR-01 AC-1.6; cascades to task_results in the
    same transaction (SQLAlchemy cascade="all, delete-orphan").
    """
    return task_repo.delete(task_id)


__all__ = ["create_task", "get_task", "list_tasks", "delete_task"]