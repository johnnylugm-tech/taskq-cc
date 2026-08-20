"""[FR-07] v2_tags — many-to-many tags + unique index on tasks.name.

Revision id:    ``v2_tags``
Down revision:  ``v1_initial``

Citations: SPEC.md §3 FR-07 v2 row; SAD.md §2.2 L1 migrations.versions.
"""

# pragma: no error-handling  (Alembic DDL — alembic op.* handles its own errors)

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "v2_tags"
down_revision = "v1_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ``tags`` / ``task_tags`` (m2m) and the unique index on ``tasks.name``."""
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False, unique=True),
    )
    op.create_table(
        "task_tags",
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            sa.Integer(),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    # SQLite cannot ALTER TABLE to add a UNIQUE constraint; the
    # cross-dialect equivalent is a unique INDEX, which both
    # ``create_unique_constraint`` (Postgres/MySQL) and
    # ``create_index(..., unique=True)`` (SQLite) support.
    op.create_index("uq_tasks_name", "tasks", ["name"], unique=True)


def downgrade() -> None:
    """Drop the unique index, the m2m table, and ``tags`` — symmetric to upgrade.

    Order matters: the unique index references ``tasks``; the m2m
    table references both ``tasks`` and ``tags``. Drop dependents
    first.
    """
    op.drop_index("uq_tasks_name", table_name="tasks")
    op.drop_table("task_tags")
    op.drop_table("tags")
