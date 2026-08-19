"""[FR-07] Alembic migrations package.

Provides the three Alembic revision modules and the ``env.py`` that
glues Alembic to ``TASKQ_DB_URL`` / ``TASKQ_HOME``.

The package lives at ``03-development/src/migrations/`` so the parent
process (pytest via ``conftest.py``) and the ``alembic`` CLI subprocess
both resolve ``from migrations import env`` /
``from migrations.versions import v1_initial`` against the same path.

Citations: SPEC.md §3 FR-07; SAD.md §2.2 L1 migrations.
"""
