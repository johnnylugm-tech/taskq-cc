"""[FR-01] Pydantic v2 request/response models.

Citations: SPEC.md §3 FR-01; SAD.md §2.2 L1 schemas.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# FR-01 denylist of injection characters (NFR-02 security).
_INJECTION_CHARS = re.compile(r"[;&|`$><\n\r\\]")


class TaskCreate(BaseModel):
    """Body for ``POST /v1/tasks``.

    Citations: SPEC.md §3 FR-01 validation rules.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "summary": "Create task",
            "description": (
                "FR-01: ``POST /v1/tasks``. Creates a new task with the "
                "given name and shell-free command. Name must be unique."
            ),
        }
    )

    name: str = Field(..., min_length=1, max_length=1000)
    command: str = Field(..., min_length=1, max_length=1000)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("name must not be empty or whitespace")
        return value

    @field_validator("command")
    @classmethod
    def _command_no_injection(cls, value: str) -> str:
        if _INJECTION_CHARS.search(value):
            raise ValueError(
                "command contains forbidden injection characters"
            )
        return value


class TaskRead(BaseModel):
    """Response body for ``GET /v1/tasks/{id}`` and ``POST /v1/tasks``.

    Citations: SPEC.md §3 FR-01; SAD.md §2.2 schemas.TaskRead.
    """

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "summary": "Task resource",
            "description": "FR-01 task row.",
        },
    )

    id: str
    name: str
    command: str
    status: str
    created_at: datetime


class TaskList(BaseModel):
    """Response body for ``GET /v1/tasks``.

    Citations: SPEC.md §3 FR-01; SAD.md §2.2 schemas.TaskList.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "summary": "Task list (cursor-paginated)",
            "description": (
                "FR-01: cursor-paginated task list. ``limit`` is the "
                "server-applied page size (default 50, max 200); "
                "``next_cursor`` is the opaque continuation token."
            ),
        }
    )

    items: list[TaskRead]
    limit: int
    next_cursor: Optional[str] = None


__all__ = ["TaskCreate", "TaskRead", "TaskList"]