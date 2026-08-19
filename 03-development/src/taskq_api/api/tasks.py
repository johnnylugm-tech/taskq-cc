"""[FR-01] HTTP routes for the task resource.

Citations: SPEC.md §3 FR-01; SAD.md §2.2 L4 api.tasks.
"""

from __future__ import annotations

from typing import Optional, Tuple

from fastapi import APIRouter, Depends, Query

from taskq_api.api.deps import enforce_scope, require_api_key
from taskq_api.errors import make_problem
from taskq_api.models.schemas import TaskCreate
from taskq_api.service import tasks as service

router = APIRouter(prefix="/v1", tags=["tasks"])


_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


def _not_found_problem():
    """RFC 7807 problem body for an unknown / missing task id."""
    return make_problem(
        status=404,
        title="Not found",
        detail="Task not found.",
        type_uri="/errors/not-found",
    )


def _require_scope(required: str):
    """Build a dependency that enforces ``required`` scope."""
    def _dep(
        api_key: Tuple[str, str] = Depends(require_api_key),
    ) -> Tuple[str, str]:
        return enforce_scope(api_key=api_key, required=required)
    return _dep


@router.post("/tasks", status_code=201)
def create_task_endpoint(
    body: TaskCreate,
    _api_key: Tuple[str, str] = Depends(_require_scope("write")),
):
    """Create a task. FR-01 AC-1.1 / AC-1.2 / SEC-T-01."""
    return service.create_task(name=body.name, command=body.command)


@router.get("/tasks/{task_id}")
def get_task_endpoint(
    task_id: int,
    _api_key: Tuple[str, str] = Depends(_require_scope("read")),
):
    """Fetch one task by id. FR-01 AC-1.3."""
    row = service.get_task(task_id)
    if row is None:
        raise _not_found_problem()
    return row


@router.get("/tasks")
def list_tasks_endpoint(
    status: Optional[str] = Query(default=None),
    limit: Optional[int] = Query(default=None),
    cursor: Optional[str] = Query(default=None),
    _api_key: Tuple[str, str] = Depends(_require_scope("read")),
):
    """List tasks, cursor-paginated. FR-01 AC-1.4 / AC-1.5.

    ``limit`` defaults to 50, max 200; ``limit > 200`` returns 422.
    ``offset`` is intentionally not exposed (FR-01: cursor only).
    """
    effective_limit = limit if limit is not None else _DEFAULT_LIMIT
    if effective_limit > _MAX_LIMIT:
        raise make_problem(
            status=422,
            title="Limit out of range",
            detail="limit must be <= 200",
            type_uri="/errors/invalid-limit",
        )
    return service.list_tasks(
        limit=effective_limit,
        cursor=cursor,
        status=status,
    )


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task_endpoint(
    task_id: int,
    _api_key: Tuple[str, str] = Depends(_require_scope("admin")),
):
    """Delete a task (and its result row) atomically. FR-01 AC-1.6."""
    if not service.delete_task(task_id):
        raise _not_found_problem()
    return None


__all__ = ["router"]