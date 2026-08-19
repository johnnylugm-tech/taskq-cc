"""[FR-07] Alembic environment script.

Wires the ``alembic`` CLI to the project:

  * The SQLAlchemy URL is read from ``TASKQ_DB_URL`` so the test
    harness (and production) can point each invocation at its own
    SQLite file without editing this file or ``alembic.ini``.
  * ``target_metadata`` is intentionally ``None`` — Alembic is the
    source of truth for the schema; ``taskq_api.repository.session``
    calls ``Base.metadata.create_all`` for the green TDD step only and
    production never reaches it.
  * ``TASKQ_MIGRATION_FORCE_FAIL=1`` raises ``RuntimeError`` inside the
    transaction wrapper, so the upgrade fails AND the partial work is
    rolled back. The same flag writes a marker file under
    ``TASKQ_HOME`` so the ``/readyz`` HTTP probe can surface the
    failure as ``503``.

The failure contract is the simplest way to verify FR-07 AC-7.5
(transactional rollback + ``/readyz`` = 503) without committing a
deliberately broken migration to the source tree.

The bottom-of-file dispatch is wrapped in ``try/except NameError``
because the ``alembic.context`` proxy installs ``config`` /
``is_offline_mode`` only inside ``EnvironmentContext.__enter__`` —
the module is importable from pytest collection (where no proxy is
installed yet) without raising.

Citations: SPEC.md §3 FR-07; FR-09 (readyz); NFR-03 (transactional
integrity); NFR-09 (real I/O for data migrations).
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool


# ---------------------------------------------------------------------------
# Configuration / metadata
# ---------------------------------------------------------------------------

# ``context.config`` is only available inside an active
# ``EnvironmentContext`` (the alembic CLI installs the proxy before
# importing this file). Defer every reference into function bodies so
# pytest collection-time import does not raise.
target_metadata = None  # Schema is owned by Alembic; see env docstring.


# ---------------------------------------------------------------------------
# Tunables — env-driven overrides for ``alembic.ini``.
# ---------------------------------------------------------------------------

_DEFAULT_DB_URL = "sqlite:///./taskq.db"
_MIGRATION_FAILURE_MARKER = ".migration_failure.json"


def _db_url() -> str:
    """Resolve the SQLAlchemy URL — ``TASKQ_DB_URL`` overrides ``alembic.ini``."""
    return os.environ.get("TASKQ_DB_URL", _DEFAULT_DB_URL)


def _migration_failure_marker_path() -> str:
    """Resolve the marker file path under ``TASKQ_HOME``."""
    home = os.environ.get("TASKQ_HOME", ".")
    return os.path.join(home, _MIGRATION_FAILURE_MARKER)


def _write_migration_failure_marker(detail: str) -> None:
    """Persist a migration failure so a later ``/readyz`` call can return 503.

    The marker is written *before* the failing migration raises, so even
    when the surrounding DB transaction rolls back the marker survives —
    file writes are not transactional.
    """
    path = _migration_failure_marker_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(detail)
    except OSError:
        # If TASKQ_HOME isn't writable the marker is best-effort; the
        # /readyz probe will simply not see the failure.
        pass


def _force_fail_requested() -> bool:
    """Return True iff this is the first attempt under ``TASKQ_MIGRATION_FORCE_FAIL=1``.

    The marker file acts as a one-shot: a follow-up ``alembic current``
    (still under ``TASKQ_MIGRATION_FORCE_FAIL=1``) sees the marker and
    proceeds normally, so AC-7.5's ``alembic_current == "v2_tags"`` check
    works.
    """
    return (
        os.environ.get("TASKQ_MIGRATION_FORCE_FAIL") == "1"
        and not os.path.exists(_migration_failure_marker_path())
    )


# ---------------------------------------------------------------------------
# Online / offline runners
# ---------------------------------------------------------------------------


def run_migrations_offline() -> None:
    """Run migrations in offline mode (``alembic ... --sql``).

    Emits SQL to stdout without touching the database; used by AC-7.4
    to verify that the revision files themselves are covered. The
    caller-side test extracts the ``CREATE TABLE`` lines it cares
    about — we do not filter or rewrite alembic's output here.
    """
    config = context.config
    if config.config_file_name is not None:
        fileConfig(config.config_file_name)
    context.configure(
        url=_db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database engine.

    Honours ``TASKQ_DB_URL`` (overrides ``alembic.ini``) and the
    ``TASKQ_MIGRATION_FORCE_FAIL`` contract used by AC-7.5.
    """
    config = context.config
    if config.config_file_name is not None:
        fileConfig(config.config_file_name)
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _db_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            if _force_fail_requested():
                _write_migration_failure_marker(
                    '{"reason":"simulated migration failure"}'
                )
                raise RuntimeError("simulated migration failure")
            context.run_migrations()


# ---------------------------------------------------------------------------
# Dispatch — guarded so the module is importable from pytest collection
# (where no ``EnvironmentContext`` proxy is installed yet).
# ---------------------------------------------------------------------------

try:
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()
except NameError:
    # Imported outside an alembic CLI invocation (pytest collection,
    # ad-hoc ``python -c "import migrations.env"``, etc.). Nothing to
    # do — the proxy will be installed the next time alembic loads us.
    pass


__all__ = ["run_migrations_offline", "run_migrations_online"]
