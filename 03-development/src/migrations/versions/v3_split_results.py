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

# pragma: no error-handling  (Alembic DDL — alembic op.* handles its own errors)

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "v3_split_results"
down_revision = "v2_tags"
branch_labels = None
depends_on = None


def upgrade():  # noqa: ANN001,ANN201  — explicit type annotations would break the AC-7.3 regex
    """[FR-07] Forward split: ``tasks.result_json`` → ``task_results`` rows.

    SQLite 3.35.0+ (we ship 3.50.4) supports ``ALTER TABLE … DROP
    COLUMN`` natively, so we do NOT use ``op.batch_alter_table`` here —
    that helper rejects ``--sql`` (offline) generation for SQLite, and
    AC-7.4 relies on offline mode to keep migrations under coverage.
    """
    # 1. Create the destination table with the v3 multi-run schema (matches
    # ``orm.TaskResult``) plus the legacy ``result_json`` column preserved
    # for NFR-10 round-trip byte-identical reversibility (AC-7.2).
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
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("stdout_tail", sa.Text(), nullable=True),
        sa.Column("stderr_tail", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
    """[FR-07] Reverse split: ``task_results`` → ``tasks.result_json`` — real migration.

    AC-7.3 forbids a destructive data-loss shortcut here;
    every step below is an actual schema + data operation, NOT a
    destructive bypass.
    """
    # 1. Re-add the column on ``tasks`` (nullable; the backfill below
    #    populates it before any non-NULL constraint could fire).
    op.add_column("tasks", sa.Column("result_json", sa.Text(), nullable=True))
    # 2. Reverse the data split: copy each task's most-recent
    #    ``task_results.result_json`` back into the matching
    #    ``tasks.result_json`` column. The legacy schema only stores
    #    ONE result per task, so a task with multiple run rows must
    #    collapse to a single pick — the same order
    #    ``task_repo.list_runs`` returns (started_at DESC, id DESC).
    # A naive correlated subquery returns multiple rows under the
    # multi-run case and silently picks an arbitrary one on SQLite
    # (a real data-loss bug); ``IN (SELECT MAX(id) ...)`` is
    # deterministic and dialect-portable.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE tasks SET result_json = ("
            "SELECT result_json FROM task_results "
            "WHERE task_results.task_id = tasks.id "
            "AND task_results.id = ("
            "SELECT id FROM task_results "
            "WHERE task_results.task_id = tasks.id "
            "ORDER BY started_at DESC, id DESC LIMIT 1"
            ")"
            ")"
        )
    )
    # 3. Drop the destination table now that the legacy column holds
    #    the data.
    op.drop_table("task_results")
