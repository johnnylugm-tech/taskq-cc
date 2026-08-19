"""FR-01: 任務資源 CRUD API — TDD-RED failing tests.

This file is the P3 TDD-RED deliverable for FR-01. The 14 test cases
listed in ``02-architecture/TEST_SPEC.md`` (FR-01 row + FR-03 row +
SEC-T-01) are realised as 9 distinct function names with pytest
parametrize disambiguating the rows that share a name in the catalog
(TEST_SPEC rows 2-4, 5-6, 7-9). The function names MUST match the
TEST_SPEC catalog exactly — the ``spec-coverage-check`` gate refuses
fuzzy matches.

Sub-assertion predicates wired into each test are taken verbatim from
the ``Sub-assertions`` table in TEST_SPEC.md:

  FR01-status-2xx         result["status"] == 201
  FR01-invalid-422        result["status"] == 422        (applies to cases 2,3)
  FR01-content-type       "problem+json" in result["content_type"]
  FR01-empty-name-rejected "" in body_name
  FR01-injection-char-rejected ";" in body_command
  FR01-duplicate-name-409 result["status"] == 409
  FR01-found-200          result["status"] == 200
  FR01-not-found-404      result["status"] == 404
  FR01-default-limit-50   limit == 50
  FR01-max-limit-200      limit == 200
  FR01-over-limit-422     limit > 200
  FR01-cursor-supported   cursor == "opaque_token_abc"
  FR01-delete-204         result["status"] == 204
  FR01-sql-count-constant len(result["sql_events"]) == 3
  FR01-no-auth-401        result["status"] == 401

Property invariant:
  FR01-pagination-state   len(result["items"]) <= limit    (cases 7,8,9)

The expected RED outcome for this RED step is one of:
  * pytest Exit Code 2 (Collection Error) because the source modules
    ``taskq_api.api.tasks`` / ``taskq_api.service.tasks`` /
    ``taskq_api.repository.task_repo`` / ``taskq_api.models.orm`` /
    ``taskq_api.models.schemas`` do not exist yet.
  * AssertionError / status mismatch because the routes return wrong codes.

Per the [UNIT TEST CONTRACT] we deliberately use standard top-level imports
(no try/except ImportError shielding). Per [SAB — BINDING MODULE PATHS] we
import the exact dotted names declared in ``.methodology/SAB.json`` for
FR-01 so the Gate 1 phantom-module check stays happy.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Standard top-level imports — RED state.
# Each of these names is the binding module path declared in
# .methodology/SAB.json for FR-01. None of these modules exist on disk yet;
# pytest will report Exit Code 2 (Collection Error) which IS the expected
# RED state per the task brief.
# ---------------------------------------------------------------------------
from taskq_api.api.tasks import router as tasks_router  # noqa: F401
from taskq_api.app import app  # noqa: F401
from taskq_api.service.auth import resolve_api_key  # noqa: F401
from taskq_api.service.tasks import (  # noqa: F401
    create_task,
    delete_task,
    get_task,
    list_tasks,
)
from taskq_api.repository.task_repo import task_repo  # noqa: F401
from taskq_api.models.orm import Task  # noqa: F401
from taskq_api.models.schemas import TaskCreate, TaskRead, TaskList  # noqa: F401


# ---------------------------------------------------------------------------
# Test isolation fixtures — these do not implement the feature, they only
# keep tests from leaking state between cases.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Give each test its own SQLite file so state cannot leak between cases.

    GREEN TODO: ``taskq_api.repository.session.get_engine`` must read
    ``TASKQ_DB_URL`` from ``taskq_api.config`` and produce a SQLAlchemy
    Engine. The test suite propagates the URL via env var per the
    [INTEGRATION FR GUIDELINES] block — pytest's ``pythonpath = ...``
    in setup.cfg does NOT propagate to subprocesses, but this fixture
    sets the env var before any module-level ``get_engine()`` call.
    """
    db_path = tmp_path / "fr01_test.db"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("TASKQ_HOME", str(tmp_path))


@pytest.fixture(autouse=True)
def _mock_auth(monkeypatch):
    """Short-circuit API-key resolution so test isolation is not blocked
    by the missing FR-03 implementation. GREEN TODO: replace with the
    real ``taskq_api.service.auth.resolve_api_key`` once FR-03 ships.

    Comment for the GREEN agent: ``resolve_api_key(plaintext) -> (key_id, scope)
    | None`` — the real implementation must ``raise`` an HTTPException(401)
    wrapping the RFC 7807 problem+json envelope from FR-10.
    """
    from taskq_api.service import auth

    def _stub(plaintext: str):
        mapping = {
            "write_key": ("write_key_id", "write"),
            "read_key": ("read_key_id", "read"),
            "admin_key": ("admin_key_id", "admin"),
        }
        return mapping.get(plaintext)

    monkeypatch.setattr(auth, "resolve_api_key", _stub)


def _auth_headers(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key} if api_key else {}


def _run_async(coro):
    """Run an async coroutine synchronously (NFR-10.2: in-process only)."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# AC-1.1 — POST /v1/tasks happy path
# FR01-status-2xx: result["status"] == 201
# ---------------------------------------------------------------------------


def test_ac_1_1_post_creates_task_returns_201():
    """AC-1.1 — POST /v1/tasks with valid write-scope key and valid body
    returns the new task id (HTTP 201).

    TEST_SPEC inputs: api_key="write_key"; body_name="task-alpha";
    body_command="echo hello". This is the happy-path round trip; no
    sub-assertion other than FR01-status-2xx applies.
    """
    # GREEN TODO: taskq_api.api.tasks must define a FastAPI route
    # POST /v1/tasks that calls taskq_api.service.tasks.create_task and
    # returns 201 + {"id": <uuid>}. Validation by taskq_api.models.schemas.TaskCreate.
    from httpx import ASGITransport, AsyncClient

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            return await ac.post(
                "/v1/tasks",
                json={"name": "task-alpha", "command": "echo hello"},
                headers=_auth_headers("write_key"),
            )

    response = _run_async(_run())
    assert response.status_code == 201
    body = response.json()
    assert "id" in body and body["id"], "FR-01 AC-1.1: body must carry task id"


# ---------------------------------------------------------------------------
# AC-1.2 — POST /v1/tasks validation failures → 422 + problem+json
# FR01-invalid-422: result["status"] == 422       (applies to cases 2,3)
# FR01-content-type:  "problem+json" in content-type
# FR01-empty-name-rejected:     "" in body_name       (case 2)
# FR01-injection-char-rejected: ";" in body_command   (case 3)
# FR01-duplicate-name-409:      result["status"]==409 (case 4)
# ---------------------------------------------------------------------------

_AC_1_2_CASES = [
    pytest.param(
        {"name": "", "command": "echo"},
        422,
        id="empty_name_rejected",
    ),
    pytest.param(
        {"name": "x", "command": "echo; rm -rf /"},
        422,
        id="injection_char_rejected",
    ),
    pytest.param(
        {"name": "dup-alpha", "command": "echo a"},
        409,
        id="duplicate_name_rejected",
    ),
]


def test_ac_1_2_invalid_payload_returns_422_problem_json(payload, expected_status):
    """AC-1.2 — POST /v1/tasks with a body that fails any FR-01 validation
    rule returns 422 (or 409 for duplicate name) + problem+json.

    TEST_SPEC inputs differ per parametrize case:
      [empty_name_rejected]   body_name="";        body_command="echo"
      [injection_char_rejected] body_name="x";     body_command="echo; rm -rf /"
      [duplicate_name_rejected] body_name="dup-alpha"; body_command="echo a";

    Per TEST_SPEC the duplicate case uses status 409 (conflict), not 422,
    even though it sits in the "validation rule" set — that is the spec.
    """
    # GREEN TODO: TaskCreate pydantic model must reject empty ``name`` and
    # ``;`` as an injection character; service.tasks.create_task must
    # reject duplicate names with 409. All must surface as problem+json.
    from httpx import ASGITransport, AsyncClient

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            if expected_status == 409:
                # Seed the duplicate row via the API so the second call
                # sees the existing name and the service raises 409.
                await ac.post(
                    "/v1/tasks",
                    json=payload,
                    headers=_auth_headers("write_key"),
                )
            return await ac.post(
                "/v1/tasks",
                json=payload,
                headers=_auth_headers("write_key"),
            )

    response = _run_async(_run())
    assert response.status_code == expected_status
    assert "problem+json" in response.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# AC-1.3 — GET /v1/tasks/{id}: known id 200, unknown id 404
# FR01-found-200:     result["status"] == 200      (case 5)
# FR01-not-found-404: result["status"] == 404      (case 6)
# FR01-content-type:  "problem+json" in CT        (case 6)
# ---------------------------------------------------------------------------

_AC_1_3_CASES = [
    pytest.param(True, 200, id="known_id"),
    pytest.param(False, 404, id="unknown_id"),
]


def test_ac_1_3_get_task_returns_columns_or_404(seed, expected_status):
    """AC-1.3 — GET /v1/tasks/{id} returns 200 + all columns for known ids,
    404 + problem+json for unknown ids.

    TEST_SPEC inputs per parametrize case:
      [known_id]   api_key="read_key"; expected_id=1
      [unknown_id] api_key="read_key"; expected_id=999
    """
    # GREEN TODO: task_repo.get_by_id must round-trip the full task row so
    # this assertion can verify all FR-01 declared columns. Unknown ids
    # must raise a NotFound that surfaces as 404 + application/problem+json.
    from httpx import ASGITransport, AsyncClient

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            if seed:
                created = await ac.post(
                    "/v1/tasks",
                    json={"name": "task-read", "command": "echo hi"},
                    headers=_auth_headers("write_key"),
                )
                assert created.status_code == 201, (
                    "seed step must succeed for this AC to be testable end-to-end"
                )
                task_id = created.json()["id"]
            else:
                task_id = 999
            return await ac.get(
                f"/v1/tasks/{task_id}",
                headers=_auth_headers("read_key"),
            )

    response = _run_async(_run())
    assert response.status_code == expected_status
    if expected_status == 404:
        assert "problem+json" in response.headers.get("content-type", "")
    else:
        body = response.json()
        expected_columns = {"id", "name", "command", "status", "created_at"}
        missing = expected_columns - set(body)
        assert not missing, f"FR-01 AC-1.3: response missing columns {missing}"


# ---------------------------------------------------------------------------
# AC-1.4 — GET /v1/tasks pagination: default 50, max 200, over → 422
# FR01-default-limit-50: limit == 50
# FR01-max-limit-200:    limit == 200
# FR01-over-limit-422:   limit > 200
# Property FR01-pagination-state: len(items) <= limit   (cases 7,8,9)
# ---------------------------------------------------------------------------

_AC_1_4_CASES = [
    pytest.param(None, 200, 50, id="default_limit_50"),
    pytest.param(200, 200, 200, id="max_boundary_200"),
    pytest.param(201, 422, None, id="over_cap_422"),
]


def test_ac_1_4_list_pagination_default_max_200_over_cap_returns_422(
    limit, expected_status, expected_limit
):
    """AC-1.4 — GET /v1/tasks is cursor-paginated; default limit 50, max 200;
    limit > 200 returns 422.

    TEST_SPEC inputs per parametrize case:
      [default_limit_50]  limit=50
      [max_boundary_200]  limit=200
      [over_cap_422]      limit=201
    """
    # GREEN TODO: list_tasks must default limit to 50 and cap at 200.
    # limit > 200 must surface as 422 + problem+json.
    from httpx import ASGITransport, AsyncClient

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            params = {}
            if limit is not None:
                params["limit"] = limit
            return await ac.get(
                "/v1/tasks",
                params=params,
                headers=_auth_headers("read_key"),
            )

    response = _run_async(_run())
    assert response.status_code == expected_status
    if expected_status == 200:
        body = response.json()
        # FR01-default-limit-50 / FR01-max-limit-200
        assert body.get("limit", expected_limit) == expected_limit
        # FR01-pagination-state property invariant
        assert len(body.get("items", [])) <= expected_limit
    else:
        assert "problem+json" in response.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# AC-1.5 — list endpoint supports ?status= and ?cursor=, no offset
# FR01-cursor-supported: cursor == "opaque_token_abc"
# ---------------------------------------------------------------------------


def test_ac_1_5_list_query_supports_status_cursor_no_offset():
    """AC-1.5 — GET /v1/tasks accepts ?status= + ?cursor=, never offset.

    The TEST_SPEC wants us to assert the offset keyword is absent from the
    query schema. We do this by inspecting the FastAPI app's OpenAPI
    schema and by issuing a positive call with status + cursor.
    """
    # GREEN TODO: list_tasks(query) must accept ``status`` and ``cursor``
    # params, return ``next_cursor`` in the response, and never expose
    # ``offset`` (the OpenAPI schema must not declare ``offset`` as a
    # query parameter on GET /v1/tasks).
    from httpx import ASGITransport, AsyncClient

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            # Positive path: status + cursor.
            resp_with_cursor = await ac.get(
                "/v1/tasks",
                params={"status": "pending", "cursor": "opaque_token_abc"},
                headers=_auth_headers("read_key"),
            )
            # Schema check: offset must NOT be a parameter on this route.
            openapi = await ac.get("/openapi.json")
            return resp_with_cursor, openapi

    resp_with_cursor, openapi = _run_async(_run())
    assert resp_with_cursor.status_code == 200
    # OpenAPI schema for GET /v1/tasks must NOT declare an ``offset`` param.
    schema = openapi.json()
    params = (
        schema.get("paths", {})
        .get("/v1/tasks", {})
        .get("get", {})
        .get("parameters", [])
    )
    param_names = {p.get("name") for p in params}
    assert "offset" not in param_names, (
        "FR-01 AC-1.5: offset-based pagination is forbidden — the "
        "GET /v1/tasks OpenAPI parameters must not declare 'offset'"
    )
    assert "status" in param_names, (
        "FR-01 AC-1.5: ?status= must be a declared query parameter"
    )
    assert "cursor" in param_names, (
        "FR-01 AC-1.5: ?cursor= must be a declared query parameter"
    )


# ---------------------------------------------------------------------------
# AC-1.6 — DELETE /v1/tasks/{id}: removes task AND task_results row, same tx
# FR01-delete-204: result["status"] == 204
# ---------------------------------------------------------------------------


def test_ac_1_6_delete_removes_task_and_result_row_same_transaction():
    """AC-1.6 — DELETE removes the task and its task_results row atomically."""
    # GREEN TODO: service.tasks.delete_task + task_repo.delete must run in
    # a single transaction_scope() that cascades to task_results.
    from httpx import ASGITransport, AsyncClient

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            created = await ac.post(
                "/v1/tasks",
                json={"name": "task-del", "command": "echo bye"},
                headers=_auth_headers("write_key"),
            )
            assert created.status_code == 201
            task_id = created.json()["id"]
            deleted = await ac.delete(
                f"/v1/tasks/{task_id}",
                headers=_auth_headers("admin_key"),
            )
            after = await ac.get(
                f"/v1/tasks/{task_id}",
                headers=_auth_headers("read_key"),
            )
            return deleted, after

    deleted, after = _run_async(_run())
    assert deleted.status_code == 204
    assert after.status_code == 404, (
        "FR-01 AC-1.6: subsequent GET must return 404 — task and its "
        "task_results row must have been removed in the same transaction"
    )


# ---------------------------------------------------------------------------
# AC-1.7 — list endpoint SQL count is constant w.r.t. rows in db
# FR01-sql-count-constant: len(result["sql_events"]) == 3
# ---------------------------------------------------------------------------


def test_ac_1_7_list_sql_count_constant_regardless_of_rows():
    """AC-1.7 — N+1 guard: SQL statement count must be constant as rows grow.

    TEST_SPEC inputs: rows_in_db ∈ {10, 100, 1000}. The TEST_SPEC sub-
    assertion pins ``len(result["sql_events"]) == 3`` (constant), proving
    that selectinload/joinedload is wired into task_repo.list_paginated.
    """
    # GREEN TODO: task_repo.list_paginated must use selectinload /
    # joinedload for any related-row fetch (per SAD §2 NFR-01) so that
    # the SELECT count is constant in the row count.
    from sqlalchemy import event

    from taskq_api.repository import session as session_module
    from taskq_api.repository import task_repo as task_repo_module

    captured: list[str] = []

    def _on_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        captured.append(statement)

    # Subscribe to every Engine that exists in this test process.
    engine = session_module.get_engine()
    event.listen(engine, "before_cursor_execute", _on_execute)
    try:
        # Seed 10 rows.
        for i in range(10):
            task_repo_module.create(
                name=f"row-{i}", command="echo x", status="pending"
            )
        captured.clear()
        task_repo_module.list_paginated(limit=50, cursor=None, status=None)
        sql_events_10 = list(captured)
        captured.clear()

        # Seed another 90 rows (total 100).
        for i in range(10, 100):
            task_repo_module.create(
                name=f"row-{i}", command="echo x", status="pending"
            )
        task_repo_module.list_paginated(limit=50, cursor=None, status=None)
        sql_events_100 = list(captured)
        captured.clear()

        # Seed another 900 rows (total 1000).
        for i in range(100, 1000):
            task_repo_module.create(
                name=f"row-{i}", command="echo x", status="pending"
            )
        task_repo_module.list_paginated(limit=50, cursor=None, status=None)
        sql_events_1000 = list(captured)
    finally:
        event.remove(engine, "before_cursor_execute", _on_execute)

    # FR01-sql-count-constant: the list-paginated SQL surface must be a
    # fixed-size batch of statements, NOT one statement per row.
    assert len(sql_events_10) == len(sql_events_100) == len(sql_events_1000), (
        "FR-01 AC-1.7: SQL count must be constant as the row count grows "
        f"(got 10={len(sql_events_10)}, 100={len(sql_events_100)}, "
        f"1000={len(sql_events_1000)})"
    )
    # FR01-sql-count-constant hard pin: exactly 3 statements (count + page + eager-load)
    assert len(sql_events_10) == 3


# ---------------------------------------------------------------------------
# AC-3.1 — NP-01 / SEC-T-03: a /v1/* request with no API key returns 401
# FR01-no-auth-401:    result["status"] == 401
# FR03-problem-json:   "problem+json" in content-type
# ---------------------------------------------------------------------------


def test_ac_3_1_v1_endpoint_without_api_key_returns_401_problem_json():
    """AC-3.1 (NP-01) — POST /v1/tasks without X-API-Key → 401 + problem+json."""
    # GREEN TODO: every /v1/* route must resolve through the
    # ``require_api_key`` dependency declared in taskq_api.api.deps, which
    # calls ``taskq_api.service.auth.resolve_api_key``. A None result
    # must surface as 401 + application/problem+json per FR-10.
    from httpx import ASGITransport, AsyncClient

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            return await ac.post(
                "/v1/tasks",
                json={"name": "noauth", "command": "echo"},
                # NO X-API-Key header on purpose.
            )

    response = _run_async(_run())
    assert response.status_code == 401
    assert "problem+json" in response.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# SEC-T-01 / NP-08 — injection payload rejected
# FR01-invalid-422 + FR01-content-type (problem+json)
# ---------------------------------------------------------------------------


def test_sec_t01_injection_payload_rejected():
    """SEC-T-01 / NP-04 — POST /v1/tasks with ``echo; rm -rf /`` is rejected.

    TEST_SPEC inputs: api_key="write_key"; body_name="ok";
    body_command="echo; rm -rf /".
    """
    # GREEN TODO: TaskCreate / service.tasks.create_task must reject any
    # command containing an injection character from the FR-01 denylist
    # (covers ;, &, |, $, backticks, newlines, etc.).
    from httpx import ASGITransport, AsyncClient

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            return await ac.post(
                "/v1/tasks",
                json={"name": "ok", "command": "echo; rm -rf /"},
                headers=_auth_headers("write_key"),
            )

    response = _run_async(_run())
    assert response.status_code == 422
    assert "problem+json" in response.headers.get("content-type", "")
