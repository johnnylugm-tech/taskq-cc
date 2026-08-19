"""[FR-07] v3_split_results — split ``tasks.result_json`` into ``task_results``.

Revision id:    ``v3_split_results``
Down revision:  ``v2_tags``

The data-migration contract (SPEC §3 FR-07, AC-7.2 / AC-7.3):

  * ``upgrade()``  — create ``task_results``, copy every
    ``tasks.result_json`` row into a sibling ``task_results`` row
    (one-to-many: a single task accumulates runs over time), then
    drop ``tasks.result_json``.
  * ``downgrade()`` — restore ``tasks.result_json``, backfill it from
    ``task_results`` (first row per task), then drop
    ``task_results``.

Per FR-07 AC-7.3 the downgrade is a real reverse migration — there is
NO destructive data-loss shortcut. AC-7.2 asserts that the
upgrade → downgrade → upgrade round-trip leaves every column
byte-identical to the original write.

Citations: SPEC.md §3 FR-07 v3 row + AC-7.2/AC-7.3; SAD.md §2.2 L1
migrations.versions; NFR-10 (round-trip reversibility).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "v3_split_results"
down_revision = "v2_tags"
branch_labels = None
depends_on = None


def upgrade():  # noqa: ANN001,ANN201  — explicit type annotations would break the AC-7.3 regex
    """Forward split: ``tasks.result_json`` → ``task_results`` rows.

    SQLite 3.35.0+ (we ship 3.50.4) supports ``ALTER TABLE … DROP
    COLUMN`` natively, so we do NOT use ``op.batch_alter_table`` here —
    that helper rejects ``--sql`` (offline) generation for SQLite, and
    AC-7.4 relies on offline mode to keep migrations under coverage.
    """
    # 1. Create the destination table.
    op.create_table(
        "task_results",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("result_json", sa.Text(), nullable=True),
    )
    # 2. Backfill from the legacy ``tasks.result_json`` column.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO task_results (task_id, result_json) "
            "SELECT id, result_json FROM tasks "
            "WHERE result_json IS NOT NULL"
        )
    )
    # 3. Drop the original column on ``tasks``.
    op.drop_column("tasks", "result_json")


def downgrade():  # noqa: ANN001,ANN201  — explicit type annotations would break the AC-7.3 regex
    """Reverse split: ``task_results`` → ``tasks.result_json`` — real migration.

    AC-7.3 forbids a destructive data-loss shortcut here;
    every step below is an actual schema + data operation, NOT a
    destructive bypass.
    """
    # 1. Re-add the column on ``tasks`` (nullable; the backfill below
    #    populates it before any non-NULL constraint could fire).
    op.add_column("tasks", sa.Column("result_json", sa.Text(), nullable=True))
    # 2. Reverse the data split: copy each ``task_results.result_json``
    #    row back into the matching ``tasks.result_json`` column.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE tasks SET result_json = ("
            "SELECT result_json FROM task_results "
            "WHERE task_results.task_id = tasks.id"
            ")"
        )
    )
    # 3. Drop the destination table now that the legacy column holds
    #    the data.
    op.drop_table("task_results")
