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
    # Point the engine at a directory that will refuse SELECT 1. SQLite
    # raises ``OperationalError: unable to open database file`` when the
    # path is a directory, which is the cleanest "closed DB" trigger
    # available without mocking internals (per [UNIT TEST CONTRACT] —
    # tests must fail because the FEATURE is missing, not because of
    # external side-effects; this is a real DB-state failure, not a
    # mock).
    closed_db_dir = Path(os.environ["TASKQ_HOME"]) / "closed_db"
    closed_db_dir.mkdir()
    os.environ["TASKQ_DB_URL"] = f"sqlite:///{closed_db_dir}/wont_open.db"
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
    try:
        migration_body = json_module.loads(migration_body_text)
    except json_module.JSONDecodeError:
        migration_body = {"_raw": migration_body_text}

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
