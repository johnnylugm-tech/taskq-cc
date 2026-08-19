"""[FR-07] Alembic script template (mako).

Standard template — Alembic only consults this when a NEW revision is
generated, which the GREEN implementation does not do (the three
revision modules are hand-authored), but Alembic requires the file to
exist before ``alembic revision`` succeeds.

Citations: SPEC.md §3 FR-07; alembic script.py.mako reference.
"""
# revision identifiers, used by Alembic.
revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
