"""FR-03: API Key 認證 — TDD-RED failing tests.

This file is the P3 TDD-RED deliverable for FR-03. The 9 test cases
listed in ``02-architecture/TEST_SPEC.md`` (FR-03 rows 1-9) are realised
as 7 distinct function names with pytest parametrize disambiguating the
three rows that share a name in the catalog (rows 1, 2, 3 all share the
name ``test_ac_3_1_v1_endpoint_without_api_key_returns_401_problem_json``).
The function names MUST match the TEST_SPEC catalog exactly — the
``spec-coverage-check`` gate refuses fuzzy matches.

Sub-assertion predicates wired into each test are taken verbatim from
the ``Sub-assertions`` table in TEST_SPEC.md:

  FR03-no-key-401        result["status"] == 401                       (1,2,3)
  FR03-problem-json      "problem+json" in result["content_type"]      (1,2,3,7,9)
  FR03-hash-64-hex       len(result["stored_hash"]) == 64              (4)
  FR03-hash-pattern      result["stored_hash"] == result["sha256_hex"] (4)
  FR03-compare-digest-true   result["compare_ok"] == True             (5)
  FR03-compare-digest-false  result["compare_ok"] == False            (5)
  FR03-plaintext-once    result["stdout_count"] == 1                   (6)
  FR03-plaintext-flux    result["plaintext_in_sinks"] == 0            (6)
  FR03-revoked-401       result["status"] == 401                       (7)
  FR03-healthz-200       result["status"] == 200                       (8)

The expected RED outcome for this step is one of:
  * pytest Exit Code 2 (Collection Error) because
    ``taskq_api.repository.key_repo`` does not exist on disk yet —
    this IS a valid RED state per the brief.
  * AssertionError / status mismatch because the ``/healthz`` and
    ``/readyz`` routes are not yet registered and the CLI ``key create``
    subcommand is not yet wired into ``taskq_api.app``.

Per the [UNIT TEST CONTRACT] we deliberately use standard top-level
imports (no try/except ImportError shielding). Per [SAB — BINDING
MODULE PATHS] every dotted name imported here is one the
``.methodology/SAB.json`` FR-03 entry declares
(``taskq_api.api.deps``, ``taskq_api.service.auth``,
``taskq_api.repository.key_repo``, ``taskq_api.models.orm``), so the
Gate 1 phantom-module check stays happy.

In-process vs out-of-process (per [INTEGRATION FR GUIDELINES]):
* The HTTP 401/200 acceptance tests run IN-PROCESS through
  ``httpx.ASGITransport`` so pytest-cov can measure the route / deps /
  service code.
* The CLI acceptance tests (AC-3.2, AC-3.4) run the CLI OUT-OF-PROCESS
  via ``subprocess.run([sys.executable, "-m", "taskq_api", ...])``
  because the CLI is the user-facing entry point under test. We also
  add in-process coverage of the same paths so GATE1 test_coverage
  can measure the entry-point module. PYTHONPATH is propagated to the
  child because pytest's ``pythonpath = ...`` setup.cfg does NOT
  propagate to subprocesses.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import io
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Standard top-level imports — RED state.
# ``taskq_api.repository.key_repo`` does not exist on disk yet;
# pytest will report Exit Code 2 (Collection Error), which IS the
# expected RED state per the task brief.
# ---------------------------------------------------------------------------
from taskq_api.api import deps  # noqa: F401
from taskq_api.service import auth  # noqa: F401
from taskq_api.repository import key_repo  # noqa: F401
from taskq_api.models.orm import ApiKey  # noqa: F401
from taskq_api.app import app  # noqa: F401


_SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Test isolation fixtures — keep state out of the host process and ensure
# subprocess tests see the same env the parent sees.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Give each test its own SQLite file + TASKQ_HOME.

    Per [INTEGRATION FR GUIDELINES]: function-scoped (not module-scoped),
    so per-case state cannot leak between AC-3.2 / AC-3.4 / AC-3.5.
    """
    db_path = tmp_path / "fr03_test.db"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("TASKQ_HOME", str(tmp_path))


@pytest.fixture
def subprocess_env():
    """Build a child env that inherits the parent's TASKQ_DB_URL,
    TASKQ_HOME and PYTHONPATH. Used by the CLI subprocess tests.

    Per [INTEGRATION FR GUIDELINES]: pytest's ``pythonpath = ...`` in
    setup.cfg does NOT propagate to subprocesses — we set it explicitly.
    """
    env = os.environ.copy()
    src_root = str(_SRC_ROOT)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src_root + os.pathsep + existing
    return env


def _auth_headers(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key} if api_key else {}


def _run_async(coro):
    """Run an async coroutine synchronously (in-process, NFR-10.2)."""
    return asyncio.run(coro)


def _extract_plaintext(stdout: str) -> str:
    """Extract the plaintext API key from a CLI ``key create`` stdout.

    The plaintext is the only long hex-ish token on the stdout line and is
    the substring following the ``key:`` prefix emitted by the GREEN CLI.
    Returns the empty string if no plaintext token can be found.
    """
    match = re.search(r"key:\s*([A-Za-z0-9_\-]+)", stdout)
    if match:
        return match.group(1)
    # Fallback: last whitespace-delimited token on the only stdout line.
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped.split()[-1]
    return ""


# ---------------------------------------------------------------------------
# AC-3.1 — three rows: GET/POST/DELETE /v1/* without X-API-Key → 401 + problem+json
# FR03-no-key-401:    result["status"] == 401                       (rows 1,2,3)
# FR03-problem-json:  "problem+json" in result["content_type"]      (rows 1,2,3)
# ---------------------------------------------------------------------------


_AC_3_1_CASES = [
    pytest.param("GET", "/v1/tasks/1", id="get_no_key"),
    pytest.param("POST", "/v1/tasks", id="post_no_key"),
    pytest.param("DELETE", "/v1/tasks/1", id="delete_no_key"),
]


# GREEN TODO: taskq_api.api.deps.require_api_key must be applied to every
# /v1/* route via FastAPI Depends. Each of the three /v1/* endpoints
# (GET /v1/tasks/{id}, POST /v1/tasks, DELETE /v1/tasks/{id}) must
# return HTTP 401 + application/problem+json when X-API-Key is missing.
# Parametrize on ``(method, path)`` so the MIRROR trigger-scope alignment
# can map each TEST_SPEC row to its case id.
@pytest.mark.parametrize(("method", "path"), _AC_3_1_CASES)
def test_ac_3_1_v1_endpoint_without_api_key_returns_401_problem_json(  # NFR-01 (NP-01 — auth 401), NFR-09 (every parametrize row asserts), NFR-10 (integration)
    method, path
):
    """AC-3.1 — every ``/v1/*`` endpoint without ``X-API-Key`` returns
    HTTP 401 + ``application/problem+json``.

    TEST_SPEC inputs per parametrize case:
      [get_no_key]    method="GET";    path="/v1/tasks/1";  api_key=""
      [post_no_key]   method="POST";   path="/v1/tasks";    api_key=""
      [delete_no_key] method="DELETE"; path="/v1/tasks/1";  api_key=""

    Three distinct ``/v1/*`` endpoints exercised in isolation; each row
    asserts both 401 and the problem+json content type so a regression
    in any single endpoint (e.g. a route forgetting the dependency) is
    surfaced with a precise row id.
    """
    from httpx import ASGITransport, AsyncClient

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            if method == "GET":
                return await ac.get(path, headers={})
            if method == "POST":
                return await ac.post(
                    path,
                    json={"name": "fr03-no-key", "command": "echo hi"},
                    headers={},
                )
            if method == "DELETE":
                return await ac.delete(path, headers={})
            raise AssertionError(f"unhandled method {method!r}")

    response = _run_async(_run())
    result = {
        "status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
    }
    assert result["status"] == 401  # FR03-no-key-401
    assert "problem+json" in result["content_type"], (  # FR03-problem-json
        f"FR-03 AC-3.1 ({method} {path}): 401 must be application/problem+json, "
        f"got content-type {result['content_type']!r}"
    )


# ---------------------------------------------------------------------------
# AC-3.2 — key create persists sha256(key) only, no plaintext anywhere in DB
# FR03-hash-64-hex:  len(result["stored_hash"]) == 64              (row 4)
# FR03-hash-pattern: result["stored_hash"] == result["sha256_hex"] (row 4)
# ---------------------------------------------------------------------------


# GREEN TODO: ``python -m taskq_api key create --scope <scope>`` must
# (a) generate a fresh opaque plaintext key, (b) compute
# ``hashlib.sha256(plaintext.encode()).hexdigest()`` and store ONLY the
# hex digest in ``api_keys.key_hash``, and (c) leave no trace of the
# plaintext in any row of the api_keys table. ``taskq_api.repository.key_repo``
# must expose ``create(scope) -> (key_id, plaintext, key_hash)``.
def test_ac_3_2_api_keys_table_stores_sha256_hash_no_plaintext(subprocess_env):  # NFR-02 (NP-08 — no plaintext at rest; SHA-256 only), NFR-10 (CLI subprocess)
    """AC-3.2 — ``python -m taskq_api key create --scope read`` mints a
    key and persists ``sha256(plaintext).hexdigest()`` (64 lowercase hex
    chars) in the ``api_keys`` table; the plaintext string itself must
    not appear in any column.

    TEST_SPEC inputs: method="cli"; cmd="python -m taskq_api key create
    --scope read"; expected_hash_len=64; alphabet="hex".

    The CLI is exercised OUT-OF-PROCESS so it is the real user-facing
    entry point (a missing CLI subcommand fails the test rather than
    silently no-op-ing). An in-process call to the same handler is added
    so pytest-cov can measure the entry-point module (subprocess coverage
    ceiling = 0%).
    """
    # ---- out-of-process CLI run ---------------------------------------
    proc = subprocess.run(
        [sys.executable, "-m", "taskq_api", "key", "create", "--scope", "read"],
        capture_output=True,
        text=True,
        env=subprocess_env,
        check=False,
    )
    assert proc.returncode == 0, (
        "FR-03 AC-3.2: `python -m taskq_api key create --scope read` "
        f"exited {proc.returncode}; stderr={proc.stderr!r}"
    )
    plaintext = _extract_plaintext(proc.stdout)
    assert plaintext, (
        "FR-03 AC-3.2: CLI stdout must include the freshly minted plaintext "
        f"key; got {proc.stdout!r}"
    )

    # ---- in-process path (coverage of the same entry-point module) ---
    captured: dict[str, object] = {}
    buf = io.StringIO()
    try:
        from taskq_api import cli as taskq_cli  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 — RED: cli module absent
        captured["import_error"] = repr(exc)
    else:
        with contextlib.redirect_stdout(buf):
            try:
                rc = taskq_cli.main(
                    ["key", "create", "--scope", "read"]
                )
            except SystemExit as exc_:
                rc = exc_.code
        captured["rc"] = rc
        captured["stdout"] = buf.getvalue()
    if "stdout" in captured:
        in_proc_plaintext = _extract_plaintext(captured["stdout"])
        assert in_proc_plaintext, (
            "FR-03 AC-3.2: in-process CLI call must also emit a plaintext key"
        )

    # ---- DB assertions -----------------------------------------------
    from sqlalchemy import create_engine, text

    db_url = os.environ["TASKQ_DB_URL"]
    engine = create_engine(db_url)
    with engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }
        assert "api_keys" in tables, (
            "FR-03 AC-3.2: api_keys table must exist after key create; "
            f"got tables={sorted(tables)}"
        )
        rows = conn.execute(
            text("SELECT key_hash, scope, revoked_at FROM api_keys")
        ).fetchall()

    assert rows, "FR-03 AC-3.2: api_keys must contain at least one row"
    key_hash, scope, revoked_at = rows[0]

    expected_hex = hashlib.sha256(plaintext.encode()).hexdigest()
    expected_hash_len = 64
    result = {
        "stored_hash": str(key_hash),
        "sha256_hex": expected_hex,
    }
    assert len(result["stored_hash"]) == expected_hash_len, (  # FR03-hash-64-hex
        f"FR-03 AC-3.2: stored hash must be {expected_hash_len} lowercase hex chars, "
        f"got len={len(result['stored_hash'])} value={result['stored_hash']!r}"
    )
    assert all(c in "0123456789abcdef" for c in result["stored_hash"]), (
        "FR-03 AC-3.2: stored hash must be lowercase hex only, got "
        f"{result['stored_hash']!r}"
    )
    assert result["stored_hash"] == result["sha256_hex"], (  # FR03-hash-pattern
        "FR-03 AC-3.2: stored hash must equal sha256(plaintext).hexdigest(); "
        f"stored={result['stored_hash']!r} expected={result['sha256_hex']!r}"
    )

    # No plaintext in any DB column of any row.
    for row in rows:
        for col in row:
            if col is None:
                continue
            assert plaintext not in str(col), (
                "FR-03 AC-3.2: plaintext must never appear in any api_keys "
                f"column; leaked in {col!r}"
            )

    assert scope == "read", (
        f"FR-03 AC-3.2: stored scope must match the CLI --scope arg; "
        f"got {scope!r}"
    )
    assert revoked_at is None, (
        "FR-03 AC-3.2: a freshly minted key must not have revoked_at set"
    )


# ---------------------------------------------------------------------------
# AC-3.3 — auth.resolve_api_key uses hmac.compare_digest; succeeds on match,
# returns None for a wrong key (constant-time compare).
# FR03-compare-digest-true:   result["compare_ok"] == True        (row 5)
# FR03-compare-digest-false:  result["compare_ok"] == False       (row 5)
# ---------------------------------------------------------------------------


# GREEN TODO: taskq_api.service.auth.resolve_api_key(plaintext) -> (key_id, scope) | None
# must (a) compute sha256(plaintext), (b) look the hash up via
# taskq_api.repository.key_repo.get_active_by_hash(hash), (c) verify the
# candidate via ``hmac.compare_digest`` (constant-time), and (d) skip rows
# whose ``revoked_at`` is non-null. The wrong-key path must also go through
# hmac.compare_digest for constant-time behaviour.
def test_ac_3_3_hmac_compare_digest_successful_and_constant_time_for_wrong_key(monkeypatch):  # NFR-02 (constant-time compare), NFR-04 (sha256, not plaintext), NFR-11 (test readability)
    """AC-3.3 — ``resolve_api_key`` uses ``hmac.compare_digest`` to compare
    the candidate key's sha256 digest with every stored hash; a matching
    key resolves to ``(key_id, scope)`` and a non-matching key resolves to
    ``None``.

    TEST_SPEC inputs: method="unit"; target="taskq_api.service.auth.resolve_api_key";
    plaintext="candidate_x"; stored_hash="hash_x".
    """
    candidate_plaintext = "candidate_x"
    candidate_hash = hashlib.sha256(candidate_plaintext.encode()).hexdigest()

    # ---- static check: implementation must reference hmac.compare_digest
    src = inspect.getsource(auth.resolve_api_key)
    assert "hmac.compare_digest" in src, (
        "FR-03 AC-3.3: resolve_api_key must use hmac.compare_digest for "
        "constant-time comparison; source contains none"
    )

    # ---- behavioural check: matching hash returns (key_id, scope) -----
    def _stub_active(hash_value):  # noqa: ANN001
        if hash_value == candidate_hash:
            return ("key_id_x", "read", candidate_hash)
        return None

    monkeypatch.setattr(key_repo, "get_active_by_hash", _stub_active)

    compare_ok = auth.resolve_api_key(candidate_plaintext)
    result = {"compare_ok": compare_ok == ("key_id_x", "read")}
    assert result["compare_ok"] == True, (  # FR03-compare-digest-true
        "FR-03 AC-3.3: a candidate key whose sha256 digest matches a row "
        f"in api_keys must resolve to (key_id, scope); got {compare_ok!r}"
    )

    # ---- behavioural check: wrong key returns None --------------------
    compare_ok = auth.resolve_api_key("not-the-candidate")
    result = {"compare_ok": compare_ok is None}
    assert result["compare_ok"] == False, (  # FR03-compare-digest-false
        "FR-03 AC-3.3: a candidate key whose sha256 digest matches no "
        f"row in api_keys must resolve to None; got {compare_ok!r}"
    )

    # ---- behavioural check: revoked key (revoked_at != null) -> None ---
    def _stub_active_revoked(hash_value):  # noqa: ANN001
        # Even if the row exists, the repo must filter out revoked rows
        # so resolve_api_key never sees them.
        return None

    monkeypatch.setattr(key_repo, "get_active_by_hash", _stub_active_revoked)
    compare_ok = auth.resolve_api_key(candidate_plaintext)
    assert compare_ok is None, (
        "FR-03 AC-3.3: a revoked key (revoked_at != null) must be invisible "
        f"to resolve_api_key; got {compare_ok!r}"
    )


# ---------------------------------------------------------------------------
# AC-3.4 — key create prints plaintext exactly once; not in any persistent sink
# FR03-plaintext-once:  result["stdout_count"] == 1                (row 6)
# FR03-plaintext-flux:  result["plaintext_in_sinks"] == 0         (row 6)
# ---------------------------------------------------------------------------


# GREEN TODO: the CLI ``key create`` handler must print the plaintext
# exactly once to stdout at mint time and must NOT write the plaintext to
# any persistent sink (log file, metrics file, audit log).
def test_ac_3_4_key_create_cli_prints_plaintext_once_no_persistent_store(subprocess_env, tmp_path):  # NFR-02 (plaintext one-shot), NFR-04 (no plaintext in logs/metrics)
    """AC-3.4 — ``python -m taskq_api key create --scope admin`` prints
    the plaintext exactly once to stdout; the plaintext does not appear
    in any persistent store (log, metrics, audit file).

    TEST_SPEC inputs: method="cli"; cmd="python -m taskq_api key create
    --scope admin"; sinks="log,db,metrics".
    """
    # Run CLI in a temporary TASKQ_HOME so we can scan that tree for
    # plaintext leakage.
    taskq_home = tmp_path / "taskq_home"
    taskq_home.mkdir()
    subprocess_env["TASKQ_HOME"] = str(taskq_home)

    proc = subprocess.run(
        [sys.executable, "-m", "taskq_api", "key", "create", "--scope", "admin"],
        capture_output=True,
        text=True,
        env=subprocess_env,
        check=False,
    )
    assert proc.returncode == 0, (
        "FR-03 AC-3.4: `python -m taskq_api key create --scope admin` "
        f"exited {proc.returncode}; stderr={proc.stderr!r}"
    )
    plaintext = _extract_plaintext(proc.stdout)
    assert plaintext, (
        "FR-03 AC-3.4: CLI stdout must include the plaintext once; got "
        f"{proc.stdout!r}"
    )

    stdout_count = proc.stdout.count(plaintext)
    result = {"stdout_count": stdout_count}
    assert result["stdout_count"] == 1, (  # FR03-plaintext-once
        "FR-03 AC-3.4: plaintext must be printed to stdout EXACTLY once; "
        f"observed {stdout_count} occurrences in {proc.stdout!r}"
    )

    # Plaintext must NOT be in stderr either (only stdout carries the one-shot
    # reveal; stderr is operational logging).
    assert plaintext not in proc.stderr, (
        "FR-03 AC-3.4: plaintext must not appear in stderr"
    )

    # Scan the entire TASKQ_HOME tree for plaintext leakage.
    sinks_content: list[str] = []
    for path in taskq_home.rglob("*"):
        if not path.is_file():
            continue
        try:
            sinks_content.append(path.read_text(errors="replace"))
        except OSError:
            continue
    plaintext_in_sinks = sum(c.count(plaintext) for c in sinks_content)
    result["plaintext_in_sinks"] = plaintext_in_sinks
    assert result["plaintext_in_sinks"] == 0, (  # FR03-plaintext-flux
        "FR-03 AC-3.4: plaintext must not appear in any persistent sink "
        f"(log/metric/audit); observed {plaintext_in_sinks} occurrences "
        f"under {taskq_home}"
    )


# ---------------------------------------------------------------------------
# AC-3.5 — a key whose revoked_at is set returns 401 even if its hash would match
# FR03-revoked-401:    result["status"] == 401                       (row 7)
# FR03-problem-json:   "problem+json" in result["content_type"]      (row 7)
# ---------------------------------------------------------------------------


# GREEN TODO: taskq_api.repository.key_repo.get_active_by_hash(hash) must
# filter out rows whose revoked_at IS NOT NULL. The route's auth
# dependency (deps.require_api_key) must call this repo function, so a
# revoked key surfaces as 401 + problem+json on the next /v1/* call.
def test_ac_3_5_revoked_key_returns_401_even_if_hash_matches():  # NFR-02 (NP-08 — revoked key never valid), NFR-09 (zero-skip), NFR-10 (integration)
    """AC-3.5 — a key with ``revoked_at`` set returns 401 even when its
    sha256 digest would otherwise match the row in ``api_keys``.

    TEST_SPEC inputs: method="GET"; path="/v1/tasks/1"; api_key="revoked_key";
    revoked_at="2026-01-01".
    """
    from sqlalchemy import create_engine, text

    plaintext = "revoked_key"
    candidate_hash = hashlib.sha256(plaintext.encode()).hexdigest()

    # Seed the api_keys table directly so the test does not depend on the
    # CLI being present (this test focuses on the revoke path, not mint).
    db_url = os.environ["TASKQ_DB_URL"]
    engine = create_engine(db_url)
    from taskq_api.models.orm import Base

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        # Create the api_keys table by hand if GREEN has not yet added the
        # model; this is test-only setup, not feature implementation.
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS api_keys ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  key_hash VARCHAR(64) NOT NULL UNIQUE,"
                "  scope VARCHAR(32) NOT NULL,"
                "  revoked_at TIMESTAMP NULL"
                ")"
            )
        )
        conn.execute(
            text(
                "INSERT INTO api_keys (key_hash, scope, revoked_at) "
                "VALUES (:h, :s, :r)"
            ),
            {"h": candidate_hash, "s": "read", "r": "2026-01-01 00:00:00"},
        )

    from httpx import ASGITransport, AsyncClient

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            return await ac.get(
                "/v1/tasks/1",
                headers=_auth_headers(plaintext),
            )

    response = _run_async(_run())
    result = {
        "status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
    }
    assert result["status"] == 401, (  # FR03-revoked-401
        "FR-03 AC-3.5: a key whose revoked_at is set must return 401 "
        "even if its sha256 digest would otherwise match; "
        f"got {response.status_code}: {response.text!r}"
    )
    assert "problem+json" in result["content_type"], (  # FR03-problem-json
        "FR-03 AC-3.5: 401 body must be application/problem+json, "
        f"got {result['content_type']!r}"
    )


# ---------------------------------------------------------------------------
# AC-3.6 — /healthz and /readyz are reachable without X-API-Key
# FR03-healthz-200: result["status"] == 200                          (row 8)
# ---------------------------------------------------------------------------


# GREEN TODO: taskq_api.app.create_app must register
#   GET /healthz  (no auth) -> 200 + {"status": "ok"}
#   GET /readyz   (no auth) -> 200 (or 503 if DB / migration not ready)
# These routes must NOT have require_api_key in their dependency tree.
def test_ac_3_6_healthz_readyz_reachable_without_api_key():  # NFR-09 (NP-13 — health-check reachable), NFR-10 (integration)
    """AC-3.6 — ``/healthz`` and ``/readyz`` are reachable without
    ``X-API-Key`` and return their non-401 responses.

    TEST_SPEC inputs: method="GET"; path="/healthz"; api_key="".

    ``/healthz`` MUST return 200 (no DB dependency); ``/readyz`` MUST
    return either 200 or 503 (DB / migration status) but MUST NOT
    return 401 — it is exempt from the FR-03 X-API-Key requirement.
    """
    from httpx import ASGITransport, AsyncClient

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            healthz = await ac.get("/healthz", headers={})
            readyz = await ac.get("/readyz", headers={})
            return healthz, readyz

    healthz_resp, readyz_resp = _run_async(_run())

    result = {
        "status": healthz_resp.status_code,
        "content_type": healthz_resp.headers.get("content-type", ""),
    }
    assert result["status"] == 200, (  # FR03-healthz-200
        "FR-03 AC-3.6: /healthz must return 200 without X-API-Key; "
        f"got {healthz_resp.status_code}: {healthz_resp.text!r}"
    )

    # /readyz is exempt from auth (must not 401) but may be 200 or 503
    # depending on DB / migration state.
    assert readyz_resp.status_code != 401, (
        "FR-03 AC-3.6: /readyz must NOT require X-API-Key; "
        f"got 401: {readyz_resp.text!r}"
    )
    assert readyz_resp.status_code in (200, 503), (
        "FR-03 AC-3.6: /readyz must return 200 or 503 without X-API-Key; "
        f"got {readyz_resp.status_code}: {readyz_resp.text!r}"
    )


# ---------------------------------------------------------------------------
# SEC-T-03 — an invalid or revoked X-API-Key returns 401 + problem+json
# FR03-no-key-401 (alias):  result["status"] == 401                (row 9)
# FR03-problem-json:        "problem+json" in content_type         (row 9)
# ---------------------------------------------------------------------------


# GREEN TODO: deps.require_api_key must call auth.resolve_api_key (which
# uses key_repo + hmac.compare_digest + revoked_at filter) and surface
# any failure as 401 + application/problem+json.
def test_sec_t03_invalid_or_revoked_key_returns_401():  # NFR-01 (NP-01 — auth 401 on invalid/revoked), NFR-02 (NP-08 — forged key denied), NFR-10 (integration)
    """SEC-T-03 — a request to ``/v1/tasks/1`` with a forged (unknown)
    X-API-Key returns 401 + ``application/problem+json``.

    TEST_SPEC inputs: method="GET"; path="/v1/tasks/1"; api_key="bogus_key".

    The same path also covers the revoked case: if the GREEN agent routes
    every unknown / revoked candidate through ``resolve_api_key``, both
    branches converge on 401 + problem+json. A revoked-key-specific check
    lives in ``test_ac_3_5_revoked_key_returns_401_even_if_hash_matches``.
    """
    from httpx import ASGITransport, AsyncClient

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            return await ac.get(
                "/v1/tasks/1",
                headers=_auth_headers("bogus_key"),
            )

    response = _run_async(_run())
    result = {
        "status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
    }
    assert result["status"] == 401, (
        "SEC-T-03: a forged X-API-Key must return 401; "
        f"got {response.status_code}: {response.text!r}"
    )
    assert "problem+json" in result["content_type"], (
        "SEC-T-03: 401 body must be application/problem+json; "
        f"got {result['content_type']!r}"
    )