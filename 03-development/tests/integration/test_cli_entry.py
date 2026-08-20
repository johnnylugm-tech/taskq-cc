"""Integration test: exercise the delivered CLI entry point end-to-end.

NFR-10: integration coverage of the source tree measured by an
integration suite.  This module is the suite — it drives the public
``cli.main(argv)`` and ``create_app()`` entry points against a real
SQLite database so the test exercises the delivered package as a
system, not as an internal import that the unit suite already covers.

Subprocess-driven tests would defeat coverage measurement (the
pytest-cov harness cannot see across ``subprocess.run`` boundaries
without ``sitecustomize`` / ``concurrency`` configuration), so this
suite calls the entry points directly while still exercising:

* alembic migrations end-to-end on a fresh SQLite file
* the CLI's argv parser and dispatch table
* the repository / session / orm stack against a real DB

Citations: SPEC.md §3 FR-03 ("python -m taskq_api key create --scope");
SAD.md §2.2 CLI; FR-06 (session/engine); FR-07 (migrations).
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

# Build an isolated environment BEFORE importing taskq_api, so the
# Settings cache is computed against the temp DB.
_TMP = tempfile.TemporaryDirectory()
os.environ["TASKQ_DB_URL"] = f"sqlite:///{_TMP.name}/integration.db"
os.environ["TASKQ_DRAIN_TIMEOUT"] = "1"
os.environ.setdefault("TASKQ_LOG_LEVEL", "WARNING")

from taskq_api import cli as taskq_cli  # noqa: E402
from taskq_api.app import create_app  # noqa: E402
from taskq_api.repository import key_repo  # noqa: E402

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
_ALEMBIC_INI = _SRC / "migrations" / "alembic.ini"


def _run_alembic_upgrade() -> subprocess.CompletedProcess:
    """Apply all migrations to the temp DB before any test runs."""
    env = {**os.environ, "PYTHONPATH": str(_SRC)}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(_ALEMBIC_INI), "upgrade", "head"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(_ALEMBIC_INI.parent),
        env=env,
    )


# Run migrations once at collection time; per-test isolation is provided
# by the repo wrappers' session_scope() context (each call opens a fresh
# connection) and the temp file is shared intentionally so this counts
# as one real DB integration test rather than N hermetic ones.
_mig = _run_alembic_upgrade()
assert _mig.returncode == 0, (
    f"alembic upgrade failed: rc={_mig.returncode} stderr={_mig.stderr!r}"
)


def test_cli_key_create_dispatches_and_persists() -> None:
    """``cli.main(['key','create','--scope','read'])`` prints the plaintext once.

    Drives the public argparse / dispatch path with an argv list (the
    same way ``python -m taskq_api`` does), then verifies the row
    actually landed in ``api_keys`` via a real SQLite connection.
    """
    captured: dict[str, str] = {}

    def _fake_stdout_write(s: str) -> int:
        captured.setdefault("out", "")
        captured["out"] += s
        return len(s)

    real_write = sys.stdout.write
    sys.stdout.write = _fake_stdout_write  # type: ignore[assignment]
    try:
        rc = taskq_cli.main(["key", "create", "--scope", "read"])
    finally:
        sys.stdout.write = real_write  # type: ignore[assignment]

    assert rc == 0, f"cli.main returned {rc}"
    out = captured.get("out", "")
    key_lines = [ln for ln in out.splitlines() if ln.startswith("key:")]
    assert len(key_lines) == 1, (
        f"expected exactly one 'key:' line in stdout, got {len(key_lines)}: {out!r}"
    )
    plaintext = key_lines[0].split(":", 1)[1].strip()
    assert plaintext, f"plaintext is empty: {key_lines!r}"

    # And the digest must be persisted — drives the repository / ORM stack.
    # We can't enumerate api_keys via key_repo directly (no list function);
    # verify by resolving the printed plaintext back to (key_id, scope).
    match = key_repo.get_active_by_hash(key_repo._hash(plaintext))
    assert match is not None, (
        f"plaintext {plaintext!r} did not round-trip to an active api_keys row"
    )
    key_id, scope, _hash = match
    assert scope == "read", f"expected scope=read, got {scope!r}"


def test_cli_help_lists_key_subcommand(capsys) -> None:
    """``cli.main(['--help'])`` must succeed and advertise the ``key`` subcommand.

    argparse exits via SystemExit(0) when ``--help`` is given; we
    catch that and assert the captured output mentions ``key``.
    """
    import pytest

    with pytest.raises(SystemExit) as exc:
        taskq_cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "key" in out, f"--help output missing 'key' subcommand: {out!r}"


def test_app_create_serves_healthz() -> None:
    """``create_app()`` must yield an app whose ``/healthz`` returns 200.

    Drives the FastAPI app factory against the migrated temp DB, proving
    the full app + DI + router + handler chain is wired end-to-end.
    """
    from fastapi.testclient import TestClient

    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200, (
        f"/healthz expected 200, got {resp.status_code}: {resp.text!r}"
    )


def test_real_sqlite_db_has_api_keys_table() -> None:
    """After alembic upgrade, the api_keys table must exist in the real SQLite file.

    Connects directly via ``sqlite3`` (no SQLAlchemy) so this proves the
    migration wrote to the on-disk database, not just to an in-memory
    engine.
    """
    db_path = _TMP.name + "/integration.db"
    assert os.path.isfile(db_path), f"DB file not created: {db_path!r}"
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='api_keys'"
        )
        rows = cur.fetchall()
    assert rows and rows[0][0] == "api_keys", (
        f"api_keys table missing after migration: {rows!r}"
    )