"""[FR-01] SQLAlchemy ORM models.

Citations: SPEC.md §3 FR-01 (tasks / task_results); SAD.md §2.2 L1 orm.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Single declarative base for the project."""


class Task(Base):
    """FR-01 task resource row.

    Citations: SPEC.md §3 FR-01; SAD.md §2.2 orm.Task.
    """

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    result: Mapped[Optional["TaskResult"]] = relationship(
        "TaskResult",
        back_populates="task",
        uselist=False,
        cascade="all, delete-orphan",
    )


class TaskResult(Base):
    """Task execution result row (FR-02 / FR-07 v3).

    Citations: SPEC.md §3 FR-02 + FR-07; SAD.md §2.2 orm.TaskResult.
    """

    __tablename__ = "task_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    exit_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    stdout_tail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stderr_tail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    task: Mapped[Task] = relationship("Task", back_populates="result")


__all__ = ["Base", "Task", "TaskResult"]