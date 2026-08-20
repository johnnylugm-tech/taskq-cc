"""[FR-01, FR-02] HTTP routes for the task resource.

Citations: SPEC.md §3 FR-01 + FR-02; SAD.md §2.2 L4 api.tasks.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from typing import Optional, Set, Tuple

from fastapi import APIRouter, Depends, Query

from taskq_api.api.deps import require_api_key_with_scope
from taskq_api.errors import make_problem
from taskq_api.models.orm import TaskResult
from taskq_api.models.schemas import TaskCreate
from taskq_api.repository import task_repo
from taskq_api.service import runner, tasks as service

router = APIRouter(prefix="/v1", tags=["tasks"])


# Holds strong references to fire-and-forget background tasks so they are
# not garbage-collected mid-run (py-create-task-unreferenced lint rule).
_TASKS: "Set[asyncio.Task]" = set()


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


def _run_row_to_dict(row: TaskResult) -> dict:
    """Project a ``TaskResult`` ORM row into the FR-02 run-history JSON shape.

    Centralises the column list so adding a column (or renaming one) is a
    single-line change here rather than a sweep across every caller of
    ``list_runs_endpoint``.
    """
    return {
        "id": row.id,
        "started_at": row.started_at,
        "exit_code": row.exit_code,
        "stdout_tail": row.stdout_tail,
        "stderr_tail": row.stderr_tail,
        "duration_ms": row.duration_ms,
        "finished_at": row.finished_at,
    }


@router.post("/tasks", status_code=201)
def create_task_endpoint(
    body: TaskCreate,
    _api_key: Tuple[str, str] = Depends(require_api_key_with_scope("write")),
):
    """Create a task. FR-01 AC-1.1 / AC-1.2 / SEC-T-01."""
    return service.create_task(name=body.name, command=body.command)


@router.get("/tasks/{task_id}")
async def get_task_endpoint(
    task_id: int,
    _api_key: Tuple[str, str] = Depends(require_api_key_with_scope("read")),
):
    """Fetch one task by id. FR-01 AC-1.3.

    [FR-10] AC-10.5 / NFR-03 — declared ``async`` and awaits the
    service lookup (when it returns an awaitable) so a
    :class:`asyncio.CancelledError` raised inside ``service.get_task``
    surfaces out of the route handler instead of being deferred into a
    serialisation error. A sync return value (the normal path) is used
    unchanged.

    Citations: SPEC.md §3 FR-10 status map (CancelledError is not a
    500); NFR-03 (cancellation propagation).
    """
    row = service.get_task(task_id)
    if inspect.isawaitable(row):
        row = await row
    if row is None:
        raise _not_found_problem()
    return row


@router.get("/tasks")
def list_tasks_endpoint(
    status: Optional[str] = Query(default=None),
    limit: Optional[int] = Query(default=None),
    cursor: Optional[str] = Query(default=None),
    _api_key: Tuple[str, str] = Depends(require_api_key_with_scope("read")),
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
    _api_key: Tuple[str, str] = Depends(require_api_key_with_scope("admin")),
):
    """Delete a task (and its result row) atomically. FR-01 AC-1.6."""
    if not service.delete_task(task_id):
        raise _not_found_problem()
    return None


@router.post("/tasks/{task_id}/run", status_code=202)
async def run_task_endpoint(
    task_id: int,
    _api_key: Tuple[str, str] = Depends(require_api_key_with_scope("write")),
):
    """Kick off a task run. FR-02 AC-2.1 / AC-2.3.

    Returns 202 + ``{"run_id": <str>}`` immediately and schedules the
    actual subprocess execution on the running event loop. An unknown
    ``task_id`` surfaces as 404 + application/problem+json.

    Citations: SPEC.md §3 FR-02 + NFR-10; SAD.md §2.2 L4 api.tasks.
    """
    task = task_repo.get_by_id(task_id)
    if task is None:
        raise _not_found_problem()
    run_id = uuid.uuid4().hex
    # Fire-and-forget: the event loop runs the coroutine while the
    # response is in flight; the test polls GET /v1/tasks/{id} for the
    # terminal state, which gives the loop time to drive the runner.
    # Keep a strong reference so the task is not garbage-collected mid-run
    # (py-create-task-unreferenced lint rule).
    _bg_task = asyncio.create_task(runner.run_task(task_id, task.command))
    _bg_task.add_done_callback(_TASKS.discard)
    _TASKS.add(_bg_task)
    return {"run_id": run_id}


@router.get("/tasks/{task_id}/runs")
def list_runs_endpoint(
    task_id: int,
    _api_key: Tuple[str, str] = Depends(require_api_key_with_scope("read")),
):
    """Run history for a task, newest-first. FR-02 AC-2.4 / AC-2.5.

    Returns ``{"items": [...]}``; each item carries the five FR-02 result
    columns plus ``started_at`` so the client can render history.
    Unknown ``task_id`` surfaces as 404 + problem+json.

    Citations: SPEC.md §3 FR-02 last bullet + "欄位"; SAD.md §2.2 L4 api.tasks.
    """
    if task_repo.get_by_id(task_id) is None:
        raise _not_found_problem()
    rows = task_repo.list_runs(task_id)
    return {"items": [_run_row_to_dict(row) for row in rows]}


__all__ = ["router"]
