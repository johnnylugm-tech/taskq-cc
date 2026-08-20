"""[FR-07] v1_initial — base schema (tasks + api_keys).

Revision id:    ``v1_initial``
Down revision:  ``None`` (this is the head of the chain)

Citations: SPEC.md §3 FR-07 v1 row; SAD.md §2.2 L1 migrations.versions.
"""

# pragma: no error-handling  (Alembic DDL — alembic op.* handles its own errors)

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "v1_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the ``tasks`` and ``api_keys`` tables.

    ``tasks.result_json`` is included here so v3 has a column to split
    into ``task_results`` (see ``v3_split_results``).
    """
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("key_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Drop the ``tasks`` and ``api_keys`` tables — symmetric to upgrade."""
    op.drop_table("api_keys")
    op.drop_table("tasks")
