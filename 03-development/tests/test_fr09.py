"""FR-09: 健康檢查與可觀測性 — TDD-RED failing tests.

Realises the 6 test cases of ``02-architecture/TEST_SPEC.md`` FR-09:

  1. test_ac_9_1_healthz_returns_200_status_ok_no_api_key_required
  2. test_ac_9_2_readyz_db_unreachable_returns_503_with_db_detail
  3. test_ac_9_2_readyz_db_unreachable_returns_503_with_db_detail
  4. test_ac_9_3_metrics_admin_returns_counts_latency_denial_non_admin_403
  5. test_ac_9_3_metrics_admin_returns_counts_latency_denial_non_admin_403
  6. test_sec_t08_db_url_absent_from_logs_and_metrics

Rows 2/3 of the catalog share ONE function name
(``test_ac_9_2_readyz_db_unreachable_returns_503_with_db_detail``); that
single function therefore exercises both scenarios:

  * (row 2) DB unreachable (db_url points to a closed / non-existent DB)
  * (row 3) alembic current != head (a marker or migration head mismatch)

Rows 4/5 share ONE function name
(``test_ac_9_3_metrics_admin_returns_counts_latency_denial_non_admin_403``);
that single function therefore exercises both scenarios:

  * (row 4) admin scope key returns 200 with the expected metrics fields
  * (row 5) write-scope (non-admin) key returns 403

Sub-assertion predicates wired in verbatim from TEST_SPEC.md FR-09:

  FR09-healthz-200           result["status"] == 200                                  (1)
  FR09-healthz-body          result["body"] == expected_body                          (1)
  FR09-readyz-503-db         result["status"] == 503                                  (2)
  FR09-readyz-503-migration  result["status"] == 503                                  (3)
  FR09-readyz-detail         "db" in result["detail"]                                 (2)
  FR09-metrics-200           result["status"] == 200                                  (4)
  FR09-metrics-fields        sorted(result["fields"]) == sorted(expected_fields)      (4)
  FR09-metrics-non-admin-403 result["status"] == 403                                  (5)
  FR09-db-url-redacted       result["password_in_sinks"] == 0                         (6)

Per [SAB — BINDING MODULE PATHS] the dotted names imported here are the
ones ``.methodology/SAB.json`` declares for FR-09:

  * ``taskq_api.api.health``        (the liveness/readiness/metrics routes)
  * ``taskq_api.app``               (FastAPI app factory)
  * ``taskq_api.config``            (Settings loader)
  * ``taskq_api.repository.session`` (DB engine + transaction boundary)

``taskq_api.api.health`` does NOT exist on disk yet — the
``taskq_api/api/`` package currently only ships ``tasks.py`` and
``deps.py``. Importing it will produce a ``ModuleNotFoundError`` at
collection time, which IS the expected RED state per the task brief
(pytest Exit Code 2, Collection Error).

In-process vs out-of-process: all HTTP assertions run IN-PROCESS through
``httpx.ASGITransport`` so pytest-cov can measure the health/metrics
handlers directly. The subprocess coverage ceiling warning does not
apply to this FR.

Citations: SPEC.md §3 FR-09 + §7 (no auth row for /healthz, 503 row for
/readyz) + §8 #10/#11; SAD.md §2.2; SEC-T-05 (information disclosure).
"""

from __future__ import annotations

import asyncio
import json as json_module
import logging
import os
from pathlib import Path

import httpx
import pytest

# Standard top-level imports — RED state. ``taskq_api.api.health`` does not
# exist on disk yet; pytest will report Exit Code 2 (Collection Error)
# which IS the expected RED state per the task brief. We deliberately do
# NOT wrap these in try/except ImportError — hiding the missing module
# would defeat the RED contract.
from taskq_api.api import health  # noqa: F401  — SAB-declared, intentionally missing
from taskq_api.app import create_app
from taskq_api.config import get_settings
from taskq_api.repository import session as session_module


# ---------------------------------------------------------------------------
# Test isolation — per-test TASKQ_HOME + TASKQ_DB_URL so readiness state
# (DB engine + alembic head marker) cannot leak across cases (per
# [INTEGRATION FR GUIDELINES]).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Fresh ``TASKQ_HOME`` + ``TASKQ_DB_URL`` so each test starts clean.

    The ``/readyz`` scenarios flip between a healthy engine and a closed
    DB file; sharing either across tests would let one case's failure
    bleed into the next.
    """
    db_path = tmp_path / "fr09_test.db"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("TASKQ_HOME", str(tmp_path))


def _request(method: str, path: str, api_key: str = "") -> httpx.Response:
    """Issue one in-process request against the ASGI app."""
    app = create_app()

    async def _go() -> httpx.Response:
        headers: dict[str, str] = {}
        if api_key:
            headers["X-API-Key"] = api_key
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.request(method, path, headers=headers)

    return asyncio.run(_go())


# ---------------------------------------------------------------------------
# AC-9.1 — GET /healthz returns 200 {"status":"ok"} with no X-API-Key
# ---------------------------------------------------------------------------


def test_ac_9_1_healthz_returns_200_status_ok_no_api_key_required():  # NFR-12 (smoke — liveness probe is the most basic contract), NFR-09 (zero-skip — every test asserts), NFR-10 (integration)
    """AC-9.1 — liveness probe returns ``200 {"status":"ok"}`` with no auth.

    Covers TEST_SPEC FR-09 row 1.

    # GREEN TODO: ``taskq_api.api.health.healthz`` must be wired as a
    # public route returning the exact body ``{"status":"ok"}`` and
    # accepting requests WITHOUT an ``X-API-Key`` header (no auth dep).
    """
    response = _request("GET", "/healthz", api_key="")
    body_text = response.text.strip()
    # Some servers may pretty-print; normalise then re-parse so the
    # byte-equality check on TEST_SPEC ``expected_body`` is robust.
    try:
        parsed_body = json_module.loads(body_text)
    except json_module.JSONDecodeError:
        parsed_body = None

    result = {
        "status": response.status_code,
        "body": body_text,
        "parsed_body": parsed_body,
    }
    # FR09-healthz-200 (applies_to 1)
    assert result["status"] == 200
    # FR09-healthz-body (applies_to 1)
    assert result["parsed_body"] == {"status": "ok"}, (
        f"/healthz body must be exactly '{{\"status\":\"ok\"}}', got {result['body']!r}"
    )


# ---------------------------------------------------------------------------
# AC-9.2 — GET /readyz returns 503 when DB unreachable OR migration not at head
# ---------------------------------------------------------------------------


def test_ac_9_2_readyz_db_unreachable_returns_503_with_db_detail():  # NFR-07 (DB outage surfaced as 503), NFR-12 (smoke), NFR-09 (zero-skip), NFR-10 (integration)
    """AC-9.2 — readiness probe fails closed with 503 + body naming the failing side.

    Covers TEST_SPEC FR-09 rows 2 AND 3 (both share this single function
    name):

      * row 2 — DB unreachable: point ``TASKQ_DB_URL`` at a closed file so
        the engine's ``SELECT 1`` probe raises. Body MUST include a
        ``detail`` (or equivalent body field) whose substring ``"db"``
        names the failing side.
      * row 3 — migration not at head: simulate ``alembic current !=
        head`` via the marker file the production code reads (or an
        equivalent injection) and assert the 503 response.
    """
    app = create_app()

    async def _go(path: str) -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.get(path)

    # --- Row 2: DB unreachable ---------------------------------------------
    # Point the engine at a path whose parent directory does not exist
    # AND cannot be created — the connection string itself names a
    # regular file, so SQLite refuses to open it and raises
    # ``OperationalError: unable to open database file``. This is the
    # cleanest "closed DB" trigger available without mocking internals
    # (per [UNIT TEST CONTRACT] — tests must fail because the FEATURE
    # is missing, not because of external side-effects; this is a real
    # DB-state failure, not a mock).
    closed_db_path = Path(os.environ["TASKQ_HOME"]) / "closed_db_dir" / "wont_open.db"
    # Do NOT create closed_db_dir — the absent parent is what forces
    # SQLite to refuse the connection.
    os.environ["TASKQ_DB_URL"] = f"sqlite:///{closed_db_path}"
    # Force a fresh engine build so the new URL is honoured.
    session_module._reset_engine_for_tests()  # type: ignore[attr-defined]

    db_unreachable = asyncio.run(_go("/readyz"))
    db_body_text = db_unreachable.text
    try:
        db_body = json_module.loads(db_body_text)
    except json_module.JSONDecodeError:
        db_body = {"_raw": db_body_text}

    result_db = {
        "status": db_unreachable.status_code,
        "body": db_body_text,
        "detail": json_module.dumps(db_body),
    }
    # FR09-readyz-503-db (applies_to 2)
    assert result_db["status"] == 503
    # FR09-readyz-detail (applies_to 2) — the failing side must be named.
    # The detail can be in any field that ends up in the body; we
    # serialise the entire body and assert the substring ``"db"``
    # appears so an implementation choice of ``"detail"`` or
    # ``"reason"`` both pass.
    assert "db" in result_db["detail"], (
        f"/readyz 503 body must name the failing side ('db'); got {db_body_text!r}"
    )

    # --- Row 3: alembic current != head ------------------------------------
    # Switch the DB URL back to a writable file and seed an empty
    # migration version table (i.e. ``alembic current`` returns empty,
    # so the implementation's "current != head" check fails).
    good_db = Path(os.environ["TASKQ_HOME"]) / "migration.db"
    os.environ["TASKQ_DB_URL"] = f"sqlite:///{good_db}"
    session_module._reset_engine_for_tests()  # type: ignore[attr-defined]

    migration_unready = asyncio.run(_go("/readyz"))
    migration_body_text = migration_unready.text

    result_migration = {
        "status": migration_unready.status_code,
        "body": migration_body_text,
    }
    # FR09-readyz-503-migration (applies_to 3)
    assert result_migration["status"] == 503, (
        f"/readyz must return 503 when alembic current != head; "
        f"got {migration_unready.status_code} with body {migration_body_text!r}"
    )
    # The body must additionally name the failing side ("migration" or
    # equivalent substring). The TEST_SPEC prose AC requires the body
    # to "name the failing side" — we keep this as a permissive check
    # so the GREEN agent can choose the exact wording.
    assert (
        "migration" in migration_body_text.lower()
        or "alembic" in migration_body_text.lower()
    ), (
        f"/readyz 503 body must name the failing migration side; "
        f"got {migration_body_text!r}"
    )


# ---------------------------------------------------------------------------
# AC-9.3 — GET /v1/metrics: admin returns counts+latency+denials; non-admin 403
# ---------------------------------------------------------------------------


def test_ac_9_3_metrics_admin_returns_counts_latency_denial_non_admin_403():  # NFR-02 (admin scope gates /v1/metrics), NFR-09 (zero-skip), NFR-10 (integration)
    """AC-9.3 — admin-scope key returns the metrics payload; write scope is denied.

    Covers TEST_SPEC FR-09 rows 4 AND 5 (both share this single function
    name):

      * row 4 — admin-scope key returns 200 with the five declared
        metrics fields (``task_counts``, ``latency_p50``, ``latency_p95``,
        ``latency_p99``, ``rate_limit_denials``).
      * row 5 — write-scope (non-admin) key returns 403.

    Test isolation wires two stubs in conftest via the
    ``stub_admin_and_write_keys`` autouse fixture below.
    """
    # --- Row 4: admin scope returns 200 with expected fields ---------------
    admin_response = _request("GET", "/v1/metrics", api_key="admin_key")
    admin_text = admin_response.text
    try:
        admin_body = json_module.loads(admin_text)
    except json_module.JSONDecodeError:
        admin_body = {}

    expected_fields = [
        "task_counts",
        "latency_p50",
        "latency_p95",
        "latency_p99",
        "rate_limit_denials",
    ]
    result_admin = {
        "status": admin_response.status_code,
        "fields": sorted(admin_body.keys()) if isinstance(admin_body, dict) else [],
    }
    # FR09-metrics-200 (applies_to 4)
    assert result_admin["status"] == 200, (
        f"/v1/metrics must return 200 for admin scope; "
        f"got {admin_response.status_code} with body {admin_text!r}"
    )
    # FR09-metrics-fields (applies_to 4)
    assert result_admin["fields"] == sorted(expected_fields), (
        f"/v1/metrics must return exactly the five declared fields "
        f"{sorted(expected_fields)}; got {result_admin['fields']!r}"
    )

    # --- Row 5: write scope (non-admin) is denied with 403 -----------------
    write_response = _request("GET", "/v1/metrics", api_key="write_key")
    result_write = {"status": write_response.status_code}
    # FR09-metrics-non-admin-403 (applies_to 5)
    assert result_write["status"] == 403, (
        f"/v1/metrics must return 403 for write (non-admin) scope; "
        f"got {write_response.status_code} with body {write_response.text!r}"
    )


# ---------------------------------------------------------------------------
# Helper fixture for AC-9.3 — install the two scope stubs that AC-9.3 needs.
#
# We isolate the FR-09 metric tests from FR-04's ``stub_key_resolution`` by
# binding fresh keys HERE rather than monkeypatching at module import. This
# is NOT "implementing the feature" — it is the per-task-brief autouse
# isolation required so the test fails because the metrics route is
# missing, not because of bad auth state.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def stub_admin_and_write_keys(monkeypatch):  # noqa: ANN001
    """Bind ``admin_key`` -> admin scope and ``write_key`` -> write scope.

    Without this the auth dependency would hit the key repository / DB and
    the metrics tests would fail on infrastructure rather than on the
    FR-09 metrics-route gating under test.
    """
    from taskq_api.api import deps
    from taskq_api.service import auth

    def _resolve(plaintext: str):
        if plaintext == "admin_key":
            return ("key-admin", "admin")
        if plaintext == "write_key":
            return ("key-write", "write")
        return None

    monkeypatch.setattr(auth, "resolve_api_key", _resolve)
    monkeypatch.setattr(deps.auth, "resolve_api_key", _resolve)
    yield


# ---------------------------------------------------------------------------
# SEC-T-08 — DB URL (with password) absent from logs AND /v1/metrics response
# ---------------------------------------------------------------------------


def test_sec_t08_db_url_absent_from_logs_and_metrics():  # NFR-08 (information disclosure — DB URL password must not appear in observable sinks), NFR-09 (zero-skip), NFR-10 (integration)
    """SEC-T-08 — a DB URL containing a password substring never leaks into logs or metrics.

    Citations: SEC-T-05 (information disclosure); SAD §6 STRIDE.

    The test sets ``TASKQ_DB_URL`` to a URL whose password substring
    (``hunter2``) is intentionally distinctive. It then captures the
    Python ``logging`` output during app construction + a /v1/metrics
    fetch and asserts the substring appears in NEITHER sink.

    # GREEN TODO: ``taskq_api.config.get_settings()`` and the metrics
    # handler MUST redact the password (e.g. via a custom URL masker
    # that replaces the userinfo substring) before any stringification
    # reaches a log handler or the response body.
    """
    # Capture every record the root logger emits during the test.
    captured_records: list[logging.LogRecord] = []

    class _CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:  # noqa: ANN401
            captured_records.append(record)

    handler = _CapturingHandler(level=logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(handler)
    old_level = root.level
    root.setLevel(logging.DEBUG)

    try:
        # 1. Configure a DB URL whose password is distinctive enough that
        #    a leak is unambiguous.
        password_substring = "hunter2"
        os.environ["TASKQ_DB_URL"] = (
            f"postgresql://app_user:{password_substring}@db.local:5432/taskq"
        )
        os.environ["TASKQ_HOME"] = os.environ.get("TASKQ_HOME", ".")

        # 2. Re-read settings — this is when a careless ``logging.info(
        #    settings.db_url)`` call would leak.
        settings = get_settings()
        # Force any stringification path to run.
        settings_repr = repr(settings)

        # 3. Build the app + fetch /v1/metrics; capture any body / log.
        app = create_app()

        async def _go() -> httpx.Response:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.get(
                    "/v1/metrics", headers={"X-API-Key": "admin_key"}
                )

        metrics_response = asyncio.run(_go())
        metrics_body = metrics_response.text
        metrics_repr = repr(metrics_response)
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)

    # Collect every string the password substring could have leaked into.
    log_blob = "\n".join(
        f"{r.getMessage()}\n{r.format(record=True) if False else ''}"
        for r in captured_records
    )
    sinks_blob = "\n".join(
        [
            settings_repr,
            metrics_body,
            metrics_repr,
            log_blob,
        ]
    )

    # FR09-db-url-redacted (applies_to 6)
    password_hits = sinks_blob.count(password_substring)
    result = {"password_in_sinks": password_hits}
    assert result["password_in_sinks"] == 0, (
        f"DB URL password {password_substring!r} leaked into observable "
        f"sinks ({password_hits} occurrences). Logged records: "
        f"{[r.getMessage() for r in captured_records]!r}. "
        f"Metrics body: {metrics_body!r}"
    )


# ---------------------------------------------------------------------------
# COVERAGE — additional cases to lift the per-module coverage of the
# FR-09-scoped modules above the 80% Gate 1 threshold. These are NOT
# extra TEST_SPEC cases; they are targeted unit tests for the lines
# pytest-cov flags as uncovered under the existing four RED/GREEN
# cases. Each test asserts behaviour the GREEN code already guarantees;
# a future refactor that breaks the line is caught here.
# ---------------------------------------------------------------------------


def test_coverage_readyz_migration_marker_file_returns_503():  # coverage — health.py line 157
    """[COVERAGE] ``/readyz`` returns 503 with ``detail="migration"`` when the FR-07 marker file exists.

    The marker is written by the FR-07 alembic env when
    ``TASKQ_MIGRATION_FORCE_FAIL=1`` aborts an upgrade. A half-applied
    migration is worse than a missing DB, so the marker takes
    precedence over the DB-reachability probe (per the comment in
    ``readyz_route``).
    """
    home = Path(os.environ["TASKQ_HOME"])
    marker_path = home / ".migration_failure.json"
    marker_path.write_text('{"forced_failure": true}')

    response = _request("GET", "/readyz", api_key="")
    assert response.status_code == 503, (
        f"/readyz must surface migration marker as 503; got {response.status_code}"
    )
    body_text = response.text
    assert "migration" in body_text.lower(), (
        f"/readyz 503 body must name 'migration' side; got {body_text!r}"
    )


def test_coverage_readyz_alembic_version_present_but_not_at_head_returns_503():  # coverage — health.py lines 102-107
    """[COVERAGE] ``/readyz`` returns 503 when ``alembic_version`` exists but points at a non-head revision.

    Exercises the ``SELECT version_num FROM alembic_version`` branch
    inside ``_migration_is_at_head`` (lines 102-107) — the existing
    RED/GREEN case only triggers the ``alembic_version`` table-missing
    branch. We seed a stale revision (``v1_initial``) into the table
    directly so the SELECT path is exercised end-to-end.
    """
    db_path = Path(os.environ["TASKQ_HOME"]) / "alembic_mismatch.db"
    os.environ["TASKQ_DB_URL"] = f"sqlite:///{db_path}"
    session_module._reset_engine_for_tests()  # type: ignore[attr-defined]

    # Seed alembic_version with a non-head revision.
    engine = session_module.get_engine()
    with engine.connect() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        conn.exec_driver_sql(
            "INSERT INTO alembic_version (version_num) VALUES ('v1_initial')"
        )
        conn.commit()

    response = _request("GET", "/readyz", api_key="")
    assert response.status_code == 503, (
        f"/readyz must return 503 when alembic_version != head; "
        f"got {response.status_code} with body {response.text!r}"
    )
    assert "migration" in response.text.lower(), (
        f"/readyz 503 body must name 'migration' side; got {response.text!r}"
    )


def test_coverage_readyz_alembic_at_head_returns_200():  # coverage — health.py lines 175-176
    """[COVERAGE] ``/readyz`` returns 200 + ``{"status":"ready"}`` when alembic points at head AND DB is reachable.

    Covers the final ``return {"status": "ready"}`` branch (lines
    175-176). Seeds ``alembic_version`` with the actual head revision
    (``v3_split_results`` — see migrations/versions/v3_split_results.py)
    so ``_migration_is_at_head()`` returns ``True``.
    """
    db_path = Path(os.environ["TASKQ_HOME"]) / "alembic_at_head.db"
    os.environ["TASKQ_DB_URL"] = f"sqlite:///{db_path}"
    session_module._reset_engine_for_tests()  # type: ignore[attr-defined]

    engine = session_module.get_engine()
    with engine.connect() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        conn.exec_driver_sql(
            "INSERT INTO alembic_version (version_num) VALUES ('v3_split_results')"
        )
        conn.commit()

    response = _request("GET", "/readyz", api_key="")
    assert response.status_code == 200, (
        f"/readyz must return 200 when alembic is at head; "
        f"got {response.status_code} with body {response.text!r}"
    )
    body = json_module.loads(response.text)
    assert body == {"status": "ready"}, (
        f"/readyz 200 body must be {{'status': 'ready'}}; got {body!r}"
    )


def test_coverage_readyz_alembic_version_table_empty_returns_503():  # coverage — health.py line 106
    """[COVERAGE] ``_migration_is_at_head`` returns ``False`` when ``alembic_version`` row is ``None``.

    Covers line 106 — the ``if version is None: return False`` branch —
    which is reached when the ``alembic_version`` table exists but has
    no rows. The ``/readyz`` endpoint surfaces this as a 503 with
    ``detail="migration"``.
    """
    db_path = Path(os.environ["TASKQ_HOME"]) / "alembic_empty.db"
    os.environ["TASKQ_DB_URL"] = f"sqlite:///{db_path}"
    session_module._reset_engine_for_tests()  # type: ignore[attr-defined]

    engine = session_module.get_engine()
    with engine.connect() as conn:
        # Create the table but leave it empty — fetchone() returns None.
        conn.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        conn.commit()

    response = _request("GET", "/readyz", api_key="")
    assert response.status_code == 503, (
        f"/readyz must return 503 when alembic_version row is NULL; "
        f"got {response.status_code} with body {response.text!r}"
    )
    assert "migration" in response.text.lower(), (
        f"/readyz 503 body must name 'migration' side; got {response.text!r}"
    )


def test_coverage_readyz_alembic_probe_exception_returns_503():  # coverage — health.py lines 171-174
    """[COVERAGE] ``/readyz`` returns 503 + ``migration`` when ``_migration_is_at_head`` raises.

    Covers lines 171-174 — the ``except Exception: return _not_ready(
    "migration")`` defensive branch. The branch fires when the
    alembic probe itself raises (e.g. alembic_version table was
    dropped between the metadata query and the version_num query).
    We exercise it by patching ``_migration_is_at_head`` to raise —
    the production path that raises is the same one a real race would
    trigger.
    """
    from taskq_api.api import health as health_module

    db_path = Path(os.environ["TASKQ_HOME"]) / "alembic_probe_exc.db"
    os.environ["TASKQ_DB_URL"] = f"sqlite:///{db_path}"
    session_module._reset_engine_for_tests()  # type: ignore[attr-defined]

    original = health_module._migration_is_at_head

    def _raise():
        raise RuntimeError("simulated alembic probe failure")

    health_module._migration_is_at_head = _raise  # type: ignore[assignment]
    try:
        response = _request("GET", "/readyz", api_key="")
    finally:
        health_module._migration_is_at_head = original  # type: ignore[assignment]

    assert response.status_code == 503, (
        f"/readyz must return 503 when alembic probe raises; "
        f"got {response.status_code} with body {response.text!r}"
    )
    assert "migration" in response.text.lower(), (
        f"/readyz 503 body must name 'migration' side; got {response.text!r}"
    )


def test_coverage_app_validation_handler_422_problem_json():  # coverage — app.py lines 114-127
    """[COVERAGE] FastAPI ``RequestValidationError`` is rendered as 422 + problem+json by ``_validation_handler``.

    Sends a POST to ``/v1/tasks`` with an empty body to trigger
    ``RequestValidationError`` (the route's ``TaskCreate`` schema
    rejects the missing fields). Exercises the
    ``_validation_handler`` registered on the app (lines 114-127).
    """
    app = create_app()

    async def _go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.post(
                "/v1/tasks", json={}, headers={"X-API-Key": "write_key"}
            )

    response = asyncio.run(_go())
    assert response.status_code == 422, (
        f"Empty POST body must trigger 422 from validation handler; "
        f"got {response.status_code} with body {response.text!r}"
    )
    assert "problem+json" in response.headers.get("content-type", ""), (
        f"422 response must carry application/problem+json; "
        f"got content-type {response.headers.get('content-type')!r}"
    )
    body = json_module.loads(response.text)
    assert body["status"] == 422
    assert body["type"] == "/errors/invalid-body"
    assert body["title"] == "Invalid request body"


def test_coverage_app_non_403_problem_handler():  # coverage — app.py line 104 (else branch)
    """[COVERAGE] Non-403 ``Problem`` responses are rendered via the full ``else`` branch of ``_problem_handler``.

    Triggers a 404 (missing task) which raises a ``Problem(status=404)``
    that flows through ``_problem_handler``. The ``else`` branch
    builds the full body including ``instance`` and ``correlation_id``.
    """
    app = create_app()

    async def _go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.get(
                "/v1/tasks/99999", headers={"X-API-Key": "admin_key"}
            )

    response = asyncio.run(_go())
    # The route returns 404 via Problem; assert the else-branch body shape.
    assert response.status_code == 404, (
        f"GET /v1/tasks/99999 must return 404; got {response.status_code}"
    )
    assert "problem+json" in response.headers.get("content-type", "")
    body = json_module.loads(response.text)
    # else-branch keys present
    assert "instance" in body, (
        f"Non-403 problem body must include 'instance'; got {body!r}"
    )
    assert "correlation_id" in body, (
        f"Non-403 problem body must include 'correlation_id'; got {body!r}"
    )


def test_coverage_app_lifespan_drain_finally_branch_runs():  # coverage — app.py lines 64-67
    """[COVERAGE] The FastAPI ``lifespan`` ``finally`` branch invokes ``runner.drain`` on shutdown.

    Exercises the lifespan context manager around an empty
    startup/shutdown cycle. The ``try / yield / finally`` block at
    lines 64-67 must run end-to-end without raising.
    """
    from taskq_api.service import runner as runner_module

    drain_calls: list[float] = []
    original_drain = runner_module.drain

    async def _spy_drain(timeout: float):
        drain_calls.append(timeout)
        return await original_drain(timeout)

    async def _run_lifespan():
        monkeypatched_app = create_app()
        import taskq_api.app as app_module

        original_runner = app_module.runner
        app_module.runner = runner_module  # ensure attribute is the module
        runner_module.drain = _spy_drain  # type: ignore[assignment]
        try:
            async with monkeypatched_app.router.lifespan_context(monkeypatched_app):
                pass  # nothing in-flight — drain returns immediately
        finally:
            runner_module.drain = original_drain  # type: ignore[assignment]
            app_module.runner = original_runner

    asyncio.run(_run_lifespan())

    assert len(drain_calls) == 1, (
        f"lifespan finally must invoke runner.drain exactly once; "
        f"saw {len(drain_calls)} calls"
    )


def test_coverage_config_cors_origins_non_empty_parses_csv():  # coverage — config.py line 71
    """[COVERAGE] ``_tuple_env`` returns the parsed tuple when ``TASKQ_CORS_ORIGINS`` is non-empty.

    Covers the ``return tuple(item.strip() for item in raw.split(",") if
    item.strip())`` branch (line 71) which is skipped when the env var
    is unset or empty.
    """
    os.environ["TASKQ_CORS_ORIGINS"] = "https://a.example, https://b.example ,,"
    # Force a fresh Settings load.
    settings = get_settings()
    assert "https://a.example" in settings.cors_origins
    assert "https://b.example" in settings.cors_origins
    # Empty entries (from the trailing comma) MUST be stripped — line 71
    # has the ``if item.strip()`` guard that enforces this.
    assert "" not in settings.cors_origins
    assert len(settings.cors_origins) == 2


def test_coverage_session_transaction_rollback_on_raise():  # coverage — session.py lines 103-105
    """[COVERAGE] ``transaction`` rolls back AND re-raises on any ``Exception`` (FR-06 AC-6.2).

    Verifies the ``except Exception: session.rollback(); raise`` branch
    (lines 103-105). A test-only ``session_factory`` records commit and
    rollback invocations so we can assert the boundary fired.
    """
    from taskq_api.repository.session import transaction

    class _SpySession:
        def __init__(self) -> None:
            self.committed = False
            self.rolled_back = False
            self.closed = False

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            self.closed = True

    spy = _SpySession()

    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom):
        with transaction(lambda: spy):  # type: ignore[arg-type,return-value]
            raise _Boom("intentional")

    assert spy.rolled_back is True, (
        "transaction must call session.rollback() on exception"
    )
    assert spy.committed is False, (
        "transaction must NOT call session.commit() when an exception escaped"
    )
    assert spy.closed is True, (
        "transaction must close the session in the finally block"
    )


def test_coverage_session_get_insert_engine_and_insert_scope():  # coverage — session.py lines 183, 211-212
    """[COVERAGE] ``get_insert_engine`` returns a distinct engine and ``insert_scope`` opens a transactional session.

    Lines 183 and 211-212 are the bodies of ``get_insert_engine`` and
    ``insert_scope``. We assert both are reachable from a fresh test
    process and that ``insert_scope`` yields a usable session (insert
    + commit + read-back) so a future regression that breaks the
    write-side engine surfaces here.
    """
    from taskq_api.models.orm import Task
    from taskq_api.repository.session import (
        get_insert_engine,
        insert_scope,
    )

    insert_engine = get_insert_engine()
    assert insert_engine is not None
    # The insert engine is distinct from the read engine — the FR-06
    # contract requires both to coexist so the SQLAlchemy event
    # listeners on the read engine do not observe write traffic.
    assert insert_engine is not session_module.get_engine()

    # insert_scope yields a real session — exercise it end-to-end so
    # the ``with transaction(_insert.factory()) as session: yield`` body
    # (lines 211-212) is covered.
    with insert_scope() as session:
        session.add(Task(name="fr09_coverage_task", command="echo hi", status="pending"))
    # Read it back via the read engine to prove the commit landed.
    from taskq_api.repository.session import session_scope

    with session_scope() as session:
        row = session.query(Task).filter_by(name="fr09_coverage_task").one()
        assert row.status == "pending"


# -- Coverage-fix: ``_percentile`` edge branches (metrics.py:38-43) ------
def test_percentile_pct_zero_returns_min():
    """COVERAGE-FIX FR-09: ``pct <= 0`` branch returns the smallest value."""
    from taskq_api.repository.metrics import _percentile

    assert _percentile([10, 20, 30, 40, 50], 0.0) == 10.0
    assert _percentile([10, 20, 30, 40, 50], -5.0) == 10.0


def test_percentile_pct_hundred_returns_max():
    """COVERAGE-FIX FR-09: ``pct >= 100`` branch returns the largest value."""
    from taskq_api.repository.metrics import _percentile

    assert _percentile([10, 20, 30, 40, 50], 100.0) == 50.0
    assert _percentile([10, 20, 30, 40, 50], 250.0) == 50.0


def test_percentile_pct_fifty_returns_median():
    """COVERAGE-FIX FR-09: nominal ``rank = round(pct/100 * (n-1))`` branch."""
    from taskq_api.repository.metrics import _percentile

    assert _percentile([10, 20, 30, 40, 50], 50.0) == 30.0
