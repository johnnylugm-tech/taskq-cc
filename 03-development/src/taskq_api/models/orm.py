"""[FR-01/FR-02/FR-03] SQLAlchemy ORM models.

Citations: SPEC.md §3 FR-01 (tasks) + FR-02 (task_results v3 multi-row)
+ FR-03 (api_keys); SAD.md §2.2 L1 orm.
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

    result: Mapped[list["TaskResult"]] = relationship(
        "TaskResult",
        back_populates="task",
        cascade="all, delete-orphan",
    )


class TaskResult(Base):
    """Task execution result row (FR-02 / FR-07 v3 split_results).

    Citations: SPEC.md §3 FR-02 + FR-07; SAD.md §2.2 orm.TaskResult.

    v3 schema: ``task_id`` is no longer unique — a single task accumulates
    many result rows over time (AC-2.5 requires run history). ``started_at``
    is added so the reader can return rows newest-first deterministically.
    """

    __tablename__ = "task_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    exit_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    stdout_tail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stderr_tail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    task: Mapped[Task] = relationship("Task", back_populates="result")


class ApiKey(Base):
    """FR-03 API key row — only the SHA-256 digest of the plaintext is stored.

    ``key_hash`` is the 64-character lowercase hex of
    ``hashlib.sha256(plaintext.encode()).hexdigest()``. The plaintext
    itself is NEVER persisted (AC-3.2). A row whose ``revoked_at`` is
    non-null is treated as invalid (AC-3.5).

    Citations: SPEC.md §3 FR-03 + NFR-02; SAD.md §2.2 L1 orm.ApiKey.
    """

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = ["Base", "Task", "TaskResult", "ApiKey"]