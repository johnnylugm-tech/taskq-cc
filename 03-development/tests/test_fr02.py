"""FR-02: 任務執行端點 — TDD-RED failing tests.

This file is the P3 TDD-RED deliverable for FR-02. The 7 test cases listed
in ``02-architecture/TEST_SPEC.md`` (FR-02 rows 1-7) are realised as 6
distinct function names — TEST_SPEC rows 1 and 6 share the name
``test_ac_2_1_post_run_returns_202_and_runs_to_terminal_state`` (row 1 =
happy path 202, row 6 = unknown id 404), so those two rows are wired as
two ``@pytest.mark.parametrize`` cases on that single function. Two
identically named module-level functions would shadow each other and the
second would silently win; parametrize keeps BOTH rows executing while the
function name still matches the catalog exactly (``spec-coverage-check``
refuses fuzzy matches).

Sub-assertion predicates wired into each test, verbatim from TEST_SPEC.md:

  FR02-run-202          result["status"] == 202                     (1)
  FR02-run-id-present   len(result["run_id"]) > 0                   (1)
  FR02-terminal-state   result["final_state"] in {"done","failed","timeout"} (1, 3)
  FR02-no-shell-true    result["grep_hits"] == 0                    (2, 7)
  FR02-timeout-state    result["final_state"] == "timeout"          (3)
  FR02-no-orphan-pid    len(result["orphan_pids"]) == 0             (3)
  FR02-five-columns     sorted(result["columns"].keys()) == sorted(expected_columns) (4)
  FR02-newest-first     result["runs"][0]["started_at"] > result["runs"][1]["started_at"] (5)
  FR02-not-found-404    result["status"] == 404                     (6)

The mirror check is asserted by binding the HTTP runner results to a
``result = {...}`` dict and asserting with the literal sub-assertion
predicates (e.g. ``assert result["status"] == 202``). This keeps the
test source structurally identical to the TEST_SPEC sub-assertion
predicates so the P3 MIRROR gate's ``_canonical_predicate`` substring
match succeeds.

Expected RED outcome for this step is one of:
  * pytest Exit Code 2 (Collection Error) because ``taskq_api.service.runner``
    does not exist on disk yet — this IS a valid RED state per the brief.
  * AssertionError / 404 from the not-yet-registered ``/v1/tasks/{id}/run``
    and ``/v1/tasks/{id}/runs`` routes.

Per the [UNIT TEST CONTRACT] the imports below are plain top-level imports
with no try/except ImportError shielding. Per [SAB — BINDING MODULE PATHS]
every dotted name imported here is one the ``.methodology/SAB.json`` FR-02
entry declares (``taskq_api.api.tasks``, ``taskq_api.service.runner``,
``taskq_api.repository.task_repo``, ``taskq_api.models.orm``), so the
Gate 1 phantom-module check has no name to complain about.

In-process vs out-of-process (per [INTEGRATION FR GUIDELINES]): the HTTP
acceptance tests run IN-PROCESS through ``httpx.ASGITransport`` so
pytest-cov can measure the route/service/repository code. The commands the
runner executes (``echo hello`` / ``sleep 30``) are genuinely
out-of-process child processes — that is the feature under test, not a
test-harness choice, and the orphan-PID assertion in AC-2.3 depends on it.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Standard top-level imports — RED state.
# ``taskq_api.service.runner`` does not exist on disk yet; pytest will
# report Exit Code 2 (Collection Error), which IS the expected RED state.
# ---------------------------------------------------------------------------
from taskq_api.api.tasks import router as tasks_router  # noqa: F401
from taskq_api.app import app  # noqa: F401
from taskq_api.service import runner  # noqa: F401
from taskq_api.repository.task_repo import task_repo  # noqa: F401
from taskq_api.models.orm import Task, TaskResult  # noqa: F401


_SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
_TERMINAL_STATES = {"done", "failed", "timeout"}
_EXPECTED_RESULT_COLUMNS = [
    "duration_ms",
    "exit_code",
    "finished_at",
    "stderr_tail",
    "stdout_tail",
]


# ---------------------------------------------------------------------------
# Test isolation fixtures — these do not implement the feature, they only
# keep state from leaking between cases (function-scoped, per the
# [INTEGRATION FR GUIDELINES] "state_mode=isolate_per_test" inputs on row 3).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Give each test its own SQLite file + TASKQ_HOME.

    Row 3 of the FR-02 TEST_SPEC declares ``state_mode="isolate_per_test"``
    and ``shared_TASKQ_HOME=false``; this fixture is what makes that true.
    """
    db_path = tmp_path / "fr02_test.db"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("TASKQ_HOME", str(tmp_path))


@pytest.fixture(autouse=True)
def _mock_auth(monkeypatch):
    """Short-circuit API-key resolution so FR-02 tests fail on the FR-02
    feature being absent, not on the FR-03 key store being empty.

    This is test isolation, not feature implementation: the real
    ``taskq_api.service.auth.resolve_api_key(plaintext) -> (key_id, scope)``
    already exists; we only substitute a deterministic in-memory mapping so
    no key rows need seeding.
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
    """Run an async coroutine synchronously (in-process, NFR-10.2)."""
    return asyncio.run(coro)


def _child_pids() -> set[int]:
    """Return the set of direct child PIDs of this pytest process.

    Used by AC-2.3 to prove the timed-out task left no orphan. ``pgrep -P``
    is available on darwin and linux; an empty/failed call yields an empty
    set, which keeps the assertion conservative rather than flaky-green.
    """
    proc = subprocess.run(
        ["pgrep", "-P", str(os.getpid())],
        capture_output=True,
        text=True,
        check=False,
    )
    return {int(line) for line in proc.stdout.split() if line.strip().isdigit()}


def _grep_hits(target: Path, pattern: str) -> int:
    """Count occurrences of ``pattern`` across every .py file under target."""
    hits = 0
    for py_file in sorted(target.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        hits += len(re.findall(re.escape(pattern), py_file.read_text()))
    return hits


async def _seed_task(ac, name: str, command: str) -> str:
    """Create a task through the FR-01 API and return its id."""
    created = await ac.post(
        "/v1/tasks",
        json={"name": name, "command": command},
        headers=_auth_headers("write_key"),
    )
    assert created.status_code == 201, (
        "FR-01 seed step must succeed for FR-02 to be testable end-to-end; "
        f"got {created.status_code}: {created.text}"
    )
    return created.json()["id"]


async def _poll_until_terminal(ac, task_id: str, deadline_sec: float = 10.0) -> str:
    """Poll GET /v1/tasks/{id} until the status is a terminal state.

    AC-2.1's declared verification method is "integration test polls until
    terminal state" — this helper is that poll loop. Returns the last
    observed status so the caller can assert on it either way.
    """
    started = time.monotonic()
    status = "pending"
    while time.monotonic() - started < deadline_sec:
        resp = await ac.get(
            f"/v1/tasks/{task_id}", headers=_auth_headers("read_key")
        )
        assert resp.status_code == 200
        status = resp.json().get("status", "pending")
        if status in _TERMINAL_STATES:
            return status
        await asyncio.sleep(0.05)
    return status


# ---------------------------------------------------------------------------
# AC-2.1 — POST /v1/tasks/{id}/run (TEST_SPEC rows 1 and 6)
# FR02-run-202:        result["status"] == 202          (row 1)
# FR02-run-id-present: len(result["run_id"]) > 0        (row 1)
# FR02-terminal-state: result["final_state"] in {"done","failed","timeout"} (row 1)
# FR02-not-found-404:  result["status"] == 404          (row 6)
# ---------------------------------------------------------------------------


# GREEN TODO: taskq_api.api.tasks must register
#   POST /v1/tasks/{task_id}/run  (scope "write") -> 202 + {"run_id": <str>}
# delegating to taskq_api.service.runner; an unknown task_id must surface as
# 404 + application/problem+json (never 202 for a task that does not exist).
# Parametrize on ``path`` (matches TEST_SPEC input column verbatim) so the
# MIRROR trigger-scope alignment can map each row to its TEST_SPEC case id.
@pytest.mark.parametrize(
    ("path",),
    [
        pytest.param("/v1/tasks/1/run", id="known_id_202"),
        pytest.param("/v1/tasks/999/run", id="unknown_id_404"),
    ],
)
def test_ac_2_1_post_run_returns_202_and_runs_to_terminal_state(  # NFR-10 (integration; ASGITransport), NFR-09 (zero-skip — every parametrize row asserts)
    path,
):
    """AC-2.1 — POST /v1/tasks/{id}/run with a write-scope key returns 202
    with a ``run_id``; the task transitions to ``running`` then to a
    terminal state. An unknown id returns 404 + problem+json.

    TEST_SPEC inputs per parametrize case:
      [known_id_202]   method="POST"; path="/v1/tasks/1/run";
                       api_key="write_key"; command="echo hello"
      [unknown_id_404] method="POST"; path="/v1/tasks/999/run";
                       api_key="write_key"; command="echo"

    Result dict is bound to mirror the TEST_SPEC sub-assertion predicates
    verbatim (``result["status"] == 202``, ``len(result["run_id"]) > 0``,
    ``result["final_state"] in {done,failed,timeout}``,
    ``result["status"] == 404``).
    """
    from httpx import ASGITransport, AsyncClient

    result: dict

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            if path.endswith("/1/run"):
                task_id = await _seed_task(ac, "fr02-run-alpha", "echo hello")
                target = f"/v1/tasks/{task_id}/run"
            else:
                target = path
                task_id = "999"
            response = await ac.post(
                target,
                headers=_auth_headers("write_key"),
            )
            final_state = None
            if response.status_code == 202:
                final_state = await _poll_until_terminal(ac, task_id)
            return response, final_state

    response, final_state = _run_async(_run())

    if path == "/v1/tasks/999/run":
        result = {
            "status": response.status_code,
            "content_type": response.headers.get("content-type", ""),
        }
    else:
        body = response.json()
        result = {
            "status": response.status_code,
            "run_id": str(body.get("run_id", "")),
            "final_state": final_state,
        }

    # The TEST_SPEC sub-assertion predicates are literal comparisons, so
    # mirror the literal values here (split per case so each sub-assertion
    # predicate appears verbatim and the P3 MIRROR substring match passes).
    if path == "/v1/tasks/999/run":
        assert result["status"] == 404  # FR02-not-found-404
        assert "problem+json" in result["content_type"]
        return

    assert result["status"] == 202  # FR02-run-202
    assert len(result["run_id"]) > 0, (  # FR02-run-id-present
        "FR-02 AC-2.1: the 202 body must carry a non-empty run_id"
    )
    assert result["final_state"] in {"done", "failed", "timeout"}, (  # FR02-terminal-state
        "FR-02 AC-2.1: task must reach done|failed|timeout, "
        f"observed {result['final_state']!r}"
    )


# ---------------------------------------------------------------------------
# AC-2.2 — runner uses asyncio.create_subprocess_exec, never shell=True
# FR02-no-shell-true: result["grep_hits"] == 0        (rows 2, 7)
# ---------------------------------------------------------------------------


# GREEN TODO: taskq_api.service.runner must expose
#   async def execute_command(command: str, timeout: float | None = None) -> ExecResult
# whose body calls asyncio.create_subprocess_exec(*shlex.split(command), ...)
# — argv-splitting via shlex, never a shell string, never shell=True.
# ExecResult must carry exit_code / stdout_tail / stderr_tail / duration_ms.
def test_ac_2_2_runner_uses_subprocess_exec_no_shell_true():  # NFR-02 (shell injection prevention; NP-08), NFR-11 (call-site readability)
    """AC-2.2 — the runner invokes
    ``asyncio.create_subprocess_exec(*shlex.split(command))`` and never
    ``shell=True``; a grep over the service layer returns zero hits.

    TEST_SPEC inputs: method="invoke"; target="taskq_api.service.runner";
    hook="subprocess_exec"; precondition="repo-wide grep over
    taskq_api/service/".
    """
    captured: dict[str, object] = {}

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

        class _FakeProc:
            pid = -1
            returncode = 0

            async def communicate(self):
                return b"hello\n", b""

            async def wait(self):
                return 0

            def kill(self):
                return None

        return _FakeProc()

    with patch.object(asyncio, "create_subprocess_exec", _fake_exec):
        _run_async(runner.execute_command("echo hello world"))

    # The runner must have gone through create_subprocess_exec at all.
    assert "args" in captured, (
        "FR-02 AC-2.2: runner must call asyncio.create_subprocess_exec"
    )
    # shlex.split("echo hello world") -> argv, passed positionally.
    assert list(captured["args"]) == ["echo", "hello", "world"], (
        "FR-02 AC-2.2: the command must be shlex-split into argv, not passed "
        f"as one shell string; got {captured['args']!r}"
    )
    kwargs = captured["kwargs"]
    assert "shell" not in kwargs, (
        "FR-02 AC-2.2: shell= must never be forwarded to the exec call"
    )

    # FR02-no-shell-true — grep over the service layer (TEST_SPEC row 2
    # precondition: "repo-wide grep over taskq_api/service/").
    grep_hits = _grep_hits(_SRC_ROOT / "taskq_api" / "service", "shell=True")
    result = {"grep_hits": grep_hits}
    assert result["grep_hits"] == 0, (
        f"FR-02 AC-2.2: shell=True appears {grep_hits}x under taskq_api/service/"
    )


# ---------------------------------------------------------------------------
# AC-2.3 — TASKQ_TASK_TIMEOUT kills the child, no orphans, state == timeout
# FR02-terminal-state: result["final_state"] in {"done","failed","timeout"}
# FR02-timeout-state:  result["final_state"] == "timeout"
# FR02-no-orphan-pid:  len(result["orphan_pids"]) == 0
# ---------------------------------------------------------------------------


# GREEN TODO: taskq_api.service.runner must read TASKQ_TASK_TIMEOUT via
# taskq_api.config.get_settings().task_timeout and, on expiry, call
# process.kill() followed by ``await process.wait()`` (reap, do not just
# signal), then persist the task in state "timeout".
def test_ac_2_3_timeout_kills_child_no_orphans_terminal_state_timeout(monkeypatch):  # NFR-03 (timeout budget; NP-15), NFR-08 (no orphan processes / resource leak)
    """AC-2.3 — a task exceeding ``TASKQ_TASK_TIMEOUT`` is killed
    (``process.kill()`` then ``await process.wait()``), leaves no orphan
    child process, and the final state is ``timeout``.

    TEST_SPEC inputs: method="POST"; path="/v1/tasks/1/run";
    api_key="write_key"; command="sleep 30"; timeout_sec=1;
    state_mode="isolate_per_test"; subprocess_mode="out_of_process";
    shared_TASKQ_HOME=false.

    The ``sleep 30`` child is genuinely out-of-process — that is what makes
    the orphan-PID count meaningful. The HTTP calls stay in-process through
    ASGITransport so pytest-cov still measures the route + runner code.
    """
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "1")

    from httpx import ASGITransport, AsyncClient

    pids_before = _child_pids()

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            task_id = await _seed_task(ac, "fr02-timeout-alpha", "sleep 30")
            response = await ac.post(
                f"/v1/tasks/{task_id}/run",
                headers=_auth_headers("write_key"),
            )
            assert response.status_code == 202
            final_state = await _poll_until_terminal(ac, task_id, deadline_sec=15.0)
            return final_state

    final_state = _run_async(_run())

    orphan_pids = _child_pids() - pids_before
    result = {
        "final_state": final_state,
        "orphan_pids": list(orphan_pids),
    }
    assert result["final_state"] in {"done", "failed", "timeout"}  # FR02-terminal-state
    assert result["final_state"] == "timeout", (  # FR02-timeout-state
        f"FR-02 AC-2.3: expected final state 'timeout', got {result['final_state']!r}"
    )
    assert len(result["orphan_pids"]) == 0, (  # FR02-no-orphan-pid
        "FR-02 AC-2.3: timed-out run leaked child process(es) "
        f"{sorted(orphan_pids)} — process.kill() must be followed by await "
        "process.wait() so the child is reaped, not merely signalled"
    )


# ---------------------------------------------------------------------------
# AC-2.4 — result row carries the five declared columns
# FR02-five-columns: sorted(result["columns"].keys()) == sorted(expected_columns)
# ---------------------------------------------------------------------------


# GREEN TODO: taskq_api.repository.task_repo must expose
#   record_result(task_id, exit_code, stdout_tail, stderr_tail, duration_ms,
#                 finished_at) -> TaskResult
# and a reader (e.g. list_runs(task_id) -> list[TaskResult]) so the service
# layer never touches SQLAlchemy directly (FR-06 layering constraint).
def test_ac_2_4_result_row_carries_five_columns():  # NFR-05 (observability fields recorded), NFR-10 (integration round-trip), NFR-04 (stdout_tail/stderr_tail redaction per AC-N4.1)
    """AC-2.4 — after a run completes, the result row in ``task_results``
    carries ``exit_code``, ``stdout_tail``, ``stderr_tail``, ``duration_ms``,
    ``finished_at``.

    TEST_SPEC inputs: method="POST"; path="/v1/tasks/1/run";
    api_key="write_key"; command="echo hi";
    expected_columns="exit_code,stdout_tail,stderr_tail,duration_ms,finished_at".
    """
    from httpx import ASGITransport, AsyncClient

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            task_id = await _seed_task(ac, "fr02-columns-alpha", "echo hi")
            response = await ac.post(
                f"/v1/tasks/{task_id}/run",
                headers=_auth_headers("write_key"),
            )
            assert response.status_code == 202
            final_state = await _poll_until_terminal(ac, task_id)
            assert final_state in _TERMINAL_STATES
            runs = await ac.get(
                f"/v1/tasks/{task_id}/runs",
                headers=_auth_headers("read_key"),
            )
            return runs

    runs_response = _run_async(_run())
    assert runs_response.status_code == 200
    payload = runs_response.json()
    rows = payload["items"] if isinstance(payload, dict) else payload
    assert rows, "FR-02 AC-2.4: a completed run must persist a task_results row"

    row = rows[0]
    columns = {
        key: row.get(key)
        for key in _EXPECTED_RESULT_COLUMNS
        if key in row
    }
    result = {"columns": columns}
    expected_columns = _EXPECTED_RESULT_COLUMNS
    # FR02-five-columns
    assert sorted(result["columns"].keys()) == sorted(expected_columns), (
        "FR-02 AC-2.4: result row missing columns "
        f"{sorted(set(expected_columns) - set(columns))}"
    )
    for name, value in columns.items():
        assert value is not None, (
            f"FR-02 AC-2.4: column {name!r} must be populated, not NULL"
        )


# ---------------------------------------------------------------------------
# AC-2.5 — GET /v1/tasks/{id}/runs returns history newest-first
# FR02-newest-first: result["runs"][0]["started_at"] > result["runs"][1]["started_at"]
# ---------------------------------------------------------------------------


# GREEN TODO: taskq_api.api.tasks must register
#   GET /v1/tasks/{task_id}/runs  (scope "read") -> run history, newest first.
# taskq_api.models.orm.TaskResult must gain a ``started_at`` column and DROP
# the current unique=True on task_id — FR-02 requires MANY result rows per
# task (three, in this test), which today's one-row-per-task unique
# constraint forbids. This is the FR-07 v3 split_results schema.
def test_ac_2_5_get_runs_returns_history_newest_first():  # NFR-01 (ordered query, no N+1), NFR-10 (integration; three sequential runs)
    """AC-2.5 — GET /v1/tasks/{id}/runs with a read-scope key returns the
    task's run history ordered newest-first.

    TEST_SPEC inputs: method="GET"; path="/v1/tasks/1/runs";
    api_key="read_key"; runs_inserted=3.

    The three runs are produced by issuing three sequential POST
    /v1/tasks/{id}/run calls (each polled to a terminal state before the
    next is started) so ``started_at`` is strictly increasing and the
    newest-first assertion is deterministic rather than tie-broken.
    """
    from httpx import ASGITransport, AsyncClient

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            task_id = await _seed_task(ac, "fr02-history-alpha", "echo hi")
            for _ in range(3):
                response = await ac.post(
                    f"/v1/tasks/{task_id}/run",
                    headers=_auth_headers("write_key"),
                )
                assert response.status_code == 202
                assert await _poll_until_terminal(ac, task_id) in _TERMINAL_STATES
                # Guarantee a strictly increasing started_at between runs.
                await asyncio.sleep(0.02)
            return await ac.get(
                f"/v1/tasks/{task_id}/runs",
                headers=_auth_headers("read_key"),
            )

    response = _run_async(_run())
    assert response.status_code == 200
    payload = response.json()
    runs = payload["items"] if isinstance(payload, dict) else payload
    assert len(runs) == 3, (
        f"FR-02 AC-2.5: expected 3 runs in the history, got {len(runs)}"
    )
    result = {"runs": runs}
    # FR02-newest-first
    assert result["runs"][0]["started_at"] > result["runs"][1]["started_at"], (
        "FR-02 AC-2.5: run history must be newest-first"
    )
    assert result["runs"][1]["started_at"] > result["runs"][2]["started_at"], (
        "FR-02 AC-2.5: newest-first ordering must hold across the whole page"
    )


# ---------------------------------------------------------------------------
# SEC-T-06 — repository-wide grep for shell=True (TEST_SPEC row 7)
# FR02-no-shell-true: result["grep_hits"] == 0
# ---------------------------------------------------------------------------


def test_sec_t06_no_shell_true_in_source():  # NFR-02 (shell injection prevention; NP-08), NFR-06 (architecture constraint enforced statically)
    """SEC-T-06 — a repository-wide grep for ``shell=True`` over
    ``03-development/src/`` returns zero hits.

    TEST_SPEC inputs: method="grep"; target="03-development/src/";
    pattern="shell=True".

    This is the static counterpart to AC-2.2's dynamic call-site check: even
    a helper module that never runs in the FR-02 path may not reintroduce a
    shell.
    """
    grep_hits = _grep_hits(_SRC_ROOT, "shell=True")
    result = {"grep_hits": grep_hits}
    assert result["grep_hits"] == 0, (  # FR02-no-shell-true
        f"SEC-T-06: shell=True appears {grep_hits}x under {_SRC_ROOT} — "
        "subprocess must always be invoked with an argv list"
    )


# ---------------------------------------------------------------------------
# Coverage-gap tests — exercise branches that the TEST_SPEC cases above
# do not reach. These are NOT part of the spec-coverage catalog; they exist
# solely to drive line coverage over the four FR-02 measured modules
# (``api.tasks``, ``service.runner``, ``repository.task_repo``,
# ``models.orm``). Each test name targets one or more specific uncovered
# lines identified by ``coverage report --include=... -m``.
# ---------------------------------------------------------------------------


def test_coverage_get_runs_unknown_task_returns_404_problem_json():  # NFR-02 (no-existence leak — runs of unknown id must surface as 404), NFR-05 (OpenAPI metadata on /v1/tasks/{id}/runs)
    """GET /v1/tasks/{unknown_id}/runs must return 404 + problem+json.

    Drives ``api.tasks:list_runs_endpoint`` into its else-branch
    (``raise _not_found_problem()``) at line 162 by way of
    ``task_repo.get_by_id`` returning ``None`` for a missing row.
    """
    from httpx import ASGITransport, AsyncClient

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            return await ac.get(
                "/v1/tasks/99999/runs",
                headers=_auth_headers("read_key"),
            )

    response = _run_async(_run())
    assert response.status_code == 404
    assert "problem+json" in response.headers.get("content-type", "")


def test_coverage_update_status_nonexistent_task_returns_false():  # NFR-06 (runner no-op on missing row — no spurious 500), NFR-10 (runner failure must not corrupt DB)
    """``task_repo.update_status`` on a missing row must return ``False``.

    Drives ``repository.task_repo.update_status`` into its ``task is None``
    branch (line 164-165), proving the runner can transition a task row
    that has been deleted mid-flight without exploding.
    """
    from taskq_api.repository import task_repo as task_repo_module

    updated = task_repo_module.update_status(task_id=99999, status="running")
    assert updated is False, (
        "FR-02 coverage: update_status on a missing task_id must be a no-op "
        "and return False, not raise"
    )


def test_coverage_get_task_unknown_id_returns_404_problem_json():  # NFR-02 (no-existence leak), NFR-05 (OpenAPI metadata on /v1/tasks/{id})
    """GET /v1/tasks/{unknown_id} must return 404 + problem+json.

    Drives ``api.tasks:get_task_endpoint`` into its else-branch
    (``raise _not_found_problem()``) at line 82.
    """
    from httpx import ASGITransport, AsyncClient

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            return await ac.get(
                "/v1/tasks/99999",
                headers=_auth_headers("read_key"),
            )

    response = _run_async(_run())
    assert response.status_code == 404
    assert "problem+json" in response.headers.get("content-type", "")


def test_coverage_list_tasks_over_limit_returns_422_problem_json():  # NFR-02 (input validation — limit bound), NFR-04 (422 envelope shape)
    """GET /v1/tasks?limit=201 must return 422 + problem+json.

    Drives ``api.tasks:list_tasks_endpoint`` into its ``effective_limit >
    _MAX_LIMIT`` branch (lines 98-106), covering the
    ``make_problem(..., status=422, ...)`` raise.
    """
    from httpx import ASGITransport, AsyncClient

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            return await ac.get(
                "/v1/tasks",
                params={"limit": 201},
                headers=_auth_headers("read_key"),
            )

    response = _run_async(_run())
    assert response.status_code == 422
    assert "problem+json" in response.headers.get("content-type", "")


def test_coverage_delete_nonexistent_task_returns_404_problem_json():  # NFR-02 (no-existence leak), NFR-05 (OpenAPI metadata)
    """DELETE /v1/tasks/{unknown_id} must return 404 + problem+json.

    Drives ``api.tasks:delete_task_endpoint`` into its
    ``raise _not_found_problem()`` branch (lines 119-121) by way of
    ``task_repo.delete`` returning ``False``.
    """
    from httpx import ASGITransport, AsyncClient

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            return await ac.delete(
                "/v1/tasks/99999",
                headers=_auth_headers("admin_key"),
            )

    response = _run_async(_run())
    assert response.status_code == 404
    assert "problem+json" in response.headers.get("content-type", "")


def test_coverage_encode_decode_cursor_roundtrip_and_decode_invalid():  # NFR-01 (cursor opaque-token round-trip), NFR-06 (repository owns SQL)
    """``_encode_cursor`` / ``_decode_cursor`` round-trip + invalid-input fallback.

    Drives ``repository.task_repo._encode_cursor`` (lines 39-40) plus the
    happy-path and ``except`` branches of ``_decode_cursor`` (lines 49-57).
    """
    import base64 as _json_base64

    from taskq_api.repository import task_repo as task_repo_module

    # Round-trip: encode then decode the same id.
    encoded = task_repo_module._encode_cursor(42)
    decoded = task_repo_module._decode_cursor(encoded)
    assert decoded == 42, (
        "FR-02 coverage: _encode_cursor/_decode_cursor round-trip must "
        f"recover the original id, got {decoded}"
    )

    # None on empty input.
    assert task_repo_module._decode_cursor(None) is None
    assert task_repo_module._decode_cursor("") is None

    # None on garbage (not valid base64-url).
    assert task_repo_module._decode_cursor("!!!not-base64!!!") is None

    # None on garbage that IS valid base64 but not JSON.
    valid_b64_not_json = _json_base64.urlsafe_b64encode(b"not-json").decode()
    assert task_repo_module._decode_cursor(valid_b64_not_json) is None

    # None on JSON without the required 'last_id' key.
    valid_b64_no_key = _json_base64.urlsafe_b64encode(b'{"other": 1}').decode()
    assert task_repo_module._decode_cursor(valid_b64_no_key) is None

    # None on JSON whose 'last_id' is not an int (ValueError branch).
    valid_b64_bad_type = _json_base64.urlsafe_b64encode(
        b'{"last_id": "not-an-int"}'
    ).decode()
    assert task_repo_module._decode_cursor(valid_b64_bad_type) is None


def test_coverage_create_duplicate_name_raises_duplicate_task_error():  # NFR-04 (SQLAlchemy-free domain exception in service layer), NFR-06 (layering — repository owns SQL)
    """``task_repo.create`` must raise ``DuplicateTaskError`` on duplicate name.

    Drives the ``except IntegrityError as exc: raise DuplicateTaskError(name)
    from exc`` branch (lines 91-92), proving the service layer can catch
    a SQLAlchemy-free domain exception.
    """
    from taskq_api.repository import task_repo as task_repo_module

    # First insert succeeds.
    task_repo_module.create(name="dup-coverage", command="echo a")
    # Second insert with the same name must raise DuplicateTaskError.
    raised = False
    try:
        task_repo_module.create(name="dup-coverage", command="echo b")
    except task_repo_module.DuplicateTaskError as exc:
        raised = True
        assert exc.args[0] == "dup-coverage", (
            "FR-02 coverage: DuplicateTaskError must carry the offending name"
        )
    assert raised, (
        "FR-02 coverage: task_repo.create must raise DuplicateTaskError on "
        "duplicate name, not silently succeed"
    )


def test_coverage_list_paginated_status_filter_cursor_and_next_cursor():  # NFR-01 (cursor pagination, status filter), NFR-06 (repository owns SQL), NFR-09 (zero-skip — every branch asserts)
    """``task_repo.list_paginated`` must branch on every filter combination.

    Drives lines 117-140: ``status is not None`` filter, ``last_id is not
    None`` cursor, and ``len(rows) > limit`` next_cursor emission.
    """
    from taskq_api.repository import task_repo as task_repo_module

    # Seed three rows: two pending, one done.
    for i in range(3):
        status = "done" if i == 2 else "pending"
        task_repo_module.create(name=f"page-{i}", command="echo x", status=status)

    # 1. status filter branch (lines 127-128): only pending rows returned.
    pending_rows, _ = task_repo_module.list_paginated(
        limit=50, cursor=None, status="pending"
    )
    assert all(row.status == "pending" for row in pending_rows), (
        "FR-02 coverage: status filter must return only matching rows"
    )

    # 2. cursor branch (line 130): encode a cursor for the first id, then
    #    request the page strictly greater than that id.
    first_id = pending_rows[0].id
    cursor = task_repo_module._encode_cursor(first_id)
    page_after, _ = task_repo_module.list_paginated(
        limit=50, cursor=cursor, status=None
    )
    assert all(row.id > first_id for row in page_after), (
        f"FR-02 coverage: cursor pagination must skip rows with id <= {first_id}"
    )

    # 3. next_cursor branch (lines 136-139): when rows > limit, return the
    #    first ``limit`` rows plus a non-None next_cursor pointing at the
    #    tail row's id.
    for i in range(3, 8):
        task_repo_module.create(name=f"more-{i}", command="echo x")
    page_rows, next_cursor = task_repo_module.list_paginated(
        limit=2, cursor=None, status=None
    )
    assert len(page_rows) == 2, (
        "FR-02 coverage: page must be capped at the requested limit"
    )
    assert next_cursor is not None, (
        "FR-02 coverage: list_paginated must emit next_cursor when more rows "
        "are available"
    )
    tail_id = task_repo_module._decode_cursor(next_cursor)
    assert tail_id == page_rows[-1].id, (
        "FR-02 coverage: next_cursor must encode the tail row's id"
    )


def test_coverage_delete_nonexistent_task_returns_false():  # NFR-04 (no-existence leak), NFR-06 (repository owns SQL)
    """``task_repo.delete`` on a missing row must return ``False``.

    Drives ``repository.task_repo.delete`` into its ``task is None``
    branch (lines 150-152).
    """
    from taskq_api.repository import task_repo as task_repo_module

    deleted = task_repo_module.delete(task_id=99999)
    assert deleted is False, (
        "FR-02 coverage: delete on a missing task_id must return False, not raise"
    )


def test_coverage_record_result_appends_row_with_all_columns():  # NFR-05 (observability fields recorded), NFR-06 (repository owns SQL)
    """``task_repo.record_result`` must persist every declared FR-02 column.

    Drives the FR-02 result-row append path explicitly, proving that
    ``exit_code``, ``stdout_tail``, ``stderr_tail``, ``duration_ms``, and
    ``finished_at`` are all stored (not silently dropped).
    """
    from datetime import datetime, timezone

    from taskq_api.repository import task_repo as task_repo_module

    task = task_repo_module.create(name="rec-coverage", command="echo hi")
    started_at = datetime.now(timezone.utc)
    finished_at = datetime.now(timezone.utc)
    row = task_repo_module.record_result(
        task_id=task.id,
        started_at=started_at,
        exit_code=0,
        stdout_tail="hi\n",
        stderr_tail="",
        duration_ms=5,
        finished_at=finished_at,
    )
    assert row.exit_code == 0
    assert row.stdout_tail == "hi\n"
    assert row.stderr_tail == ""
    assert row.duration_ms == 5
    assert row.started_at == started_at
    assert row.finished_at == finished_at


def test_coverage_list_runs_returns_rows_newest_first():  # NFR-01 (ordered query), NFR-06 (repository owns SQL)
    """``task_repo.list_runs`` must return rows ordered by ``started_at`` DESC.

    Drives the explicit ``list_runs`` repository path (lines 199-216) with
    multiple appended rows so the DESC order assertion is meaningful.
    """
    from datetime import datetime, timedelta, timezone

    from taskq_api.repository import task_repo as task_repo_module

    task = task_repo_module.create(name="runs-coverage", command="echo x")
    # Append three rows with strictly increasing started_at so the
    # newest-first ordering is deterministic.
    base = datetime.now(timezone.utc)
    for i in range(3):
        task_repo_module.record_result(
            task_id=task.id,
            started_at=base + timedelta(seconds=i),
            exit_code=0,
            stdout_tail=f"row-{i}\n",
            stderr_tail="",
            duration_ms=1,
            finished_at=base + timedelta(seconds=i),
        )
    rows = task_repo_module.list_runs(task_id=task.id)
    assert len(rows) == 3
    assert rows[0].started_at > rows[1].started_at > rows[2].started_at, (
        f"FR-02 coverage: list_runs must be newest-first; got "
        f"{[r.started_at for r in rows]}"
    )


def test_coverage_get_by_id_existing_returns_row():  # NFR-06 (repository owns SQL), NFR-10 (round-trip)
    """``task_repo.get_by_id`` for an existing row returns the row.

    Drives the happy-path ``select(Task).where(Task.id == task_id)`` branch
    (lines 95-103) explicitly through the repository.
    """
    from taskq_api.repository import task_repo as task_repo_module

    task = task_repo_module.create(name="get-coverage", command="echo z")
    fetched = task_repo_module.get_by_id(task_id=task.id)
    assert fetched is not None
    assert fetched.id == task.id
    assert fetched.name == "get-coverage"
    assert fetched.command == "echo z"

    # And missing row returns None.
    assert task_repo_module.get_by_id(task_id=99999) is None


def test_coverage_list_tasks_within_limit_returns_200():  # NFR-05 (OpenAPI metadata), NFR-06 (list_tasks handler returns 200 on success)
    """GET /v1/tasks with a valid limit must return 200 + items.

    Drives ``api.tasks:list_tasks_endpoint`` past the ``raise`` branch on
    line 100-104 into the success-path ``return service.list_tasks(...)``
    statement on lines 105-109.
    """
    from httpx import ASGITransport, AsyncClient

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            return await ac.get(
                "/v1/tasks",
                params={"limit": 50},
                headers=_auth_headers("read_key"),
            )

    response = _run_async(_run())
    assert response.status_code == 200


def test_coverage_delete_existing_task_returns_204_and_repo_returns_true():  # NFR-03 (transaction boundary), NFR-06 (repository owns SQL)
    """DELETE /v1/tasks/{existing_id} returns 204; ``task_repo.delete`` returns True.

    Drives ``api.tasks:delete_task_endpoint`` into its ``return None`` on
    line 121 (the 204 success path) and ``repository.task_repo.delete``
    into its success-path statements on lines 152-154
    (``session.delete``; ``session.flush``; ``return True``).
    """
    from httpx import ASGITransport, AsyncClient

    from taskq_api.repository import task_repo as task_repo_module

    # Seed a row, then call the repository delete directly so lines 152-154
    # are exercised on their own (the HTTP path runs the service layer).
    task = task_repo_module.create(name="del-existing-coverage", command="echo bye")
    deleted_via_repo = task_repo_module.delete(task_id=task.id)
    assert deleted_via_repo is True, (
        "FR-02 coverage: task_repo.delete on an existing row must return True"
    )

    # And the HTTP endpoint returns 204 for an existing task.
    task2 = task_repo_module.create(name="del-http-coverage", command="echo bye")

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            return await ac.delete(
                f"/v1/tasks/{task2.id}",
                headers=_auth_headers("admin_key"),
            )

    response = _run_async(_run())
    assert response.status_code == 204, (
        "FR-02 coverage: DELETE /v1/tasks/{existing_id} must return 204 "
        f"(success path on line 121), got {response.status_code}"
    )


def test_coverage_submit_admits_and_runs_to_terminal_state(monkeypatch):  # NFR-08 (no orphan subprocess), NFR-13 (admission control happy path)
    """``runner.submit`` must admit, run, and return a terminal ``RunOutcome``.

    Drives ``service.runner.submit`` into its happy path (lines 257-290):
    the ``_get_gate`` first-time-creation branch (58-61), the
    ``_register_in_flight`` body (66-68), the ``STATE_RUNNING`` transition
    in ``_run_inner`` (228-229), the awaited inner task (279), and the
    ``gate.release()`` finally branch (290). The done-callback registered
    by ``_register_in_flight`` covers ``_unregister_in_flight`` (71-74).
    """
    from taskq_api.service import runner as runner_module
    from taskq_api.repository import task_repo as task_repo_module

    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "2")

    task = task_repo_module.create(name="submit-happy", command="echo submit")

    async def _run():
        return await runner_module.submit(
            task_id=task.id, command="echo submit", timeout_sec=5.0
        )

    outcome = _run_async(_run())
    assert outcome.final_state == "done", (
        f"FR-02 coverage: submit happy path must produce final_state=done, "
        f"got {outcome.final_state!r}"
    )
    assert outcome.exit_code == 0
    assert outcome.duration_ms >= 0
    # And the in-flight registry must be empty once the inner task settled.
    assert not runner_module._in_flight, (
        "FR-02 coverage: _unregister_in_flight must run via the done_callback "
        "so _in_flight is empty after the inner task settles"
    )
    # The DB row must be persisted in the terminal state.
    row = task_repo_module.get_by_id(task.id)
    assert row is not None and row.status == "done"


def test_coverage_submit_over_cap_returns_queued_outcome(monkeypatch):  # NFR-13 (admission control — over-cap refusal, FR-08 AC-8.1)
    """Over-cap ``submit`` must return a ``RunOutcome(final_state=queued)``.

    Drives the ``if not gate.try_admit():`` refusal branch (line 258) by
    saturating the gate first (two admits under ``TASKQ_MAX_CONCURRENT=1``
    would be flaky, so we cap at 1 and submit twice — the second
    invocation is refused without spawning a subprocess).
    """
    import asyncio as _asyncio

    from taskq_api.repository import task_repo as task_repo_module
    from taskq_api.service import runner as runner_module
    from taskq_api.service.run_state import STATE_QUEUED

    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "1")
    # Force the gate to be recreated under the new cap.
    runner_module._gate = None

    # First submission: hold the only admission slot open across the
    # event-loop boundary so the second submission observes a saturated
    # gate. We use ``asyncio.Event`` to coordinate; the first submit
    # is wrapped to wait on it before ``gate.release()`` runs.
    blocker = _asyncio.Event()

    async def _slow_submit(task_id, command, timeout_sec=None):
        gate = runner_module._get_gate()
        assert gate.try_admit()
        runner_module._register_in_flight(
            _asyncio.ensure_future(_asyncio.sleep(0)), task_id
        )
        await blocker.wait()
        gate.release()
        return runner_module.RunOutcome(
            exit_code=0,
            stdout_tail="",
            stderr_tail="",
            duration_ms=0,
            final_state="done",
        )

    task_a = task_repo_module.create(name="submit-cap-a", command="echo a")
    task_b = task_repo_module.create(name="submit-cap-b", command="echo b")

    async def _scenario():
        # Saturate the gate with the slow wrapper, then submit normally
        # so the admission path returns False.
        slow = _asyncio.create_task(_slow_submit(task_a.id, "echo a"))
        # Yield so the slow wrapper has actually claimed the slot.
        await _asyncio.sleep(0)
        try:
            outcome = await runner_module.submit(
                task_id=task_b.id, command="echo b", timeout_sec=1.0
            )
        finally:
            blocker.set()
            await slow
        return outcome

    outcome = _run_async(_scenario())
    assert outcome.final_state == STATE_QUEUED, (
        f"FR-02 coverage: over-cap submit must refuse with final_state="
        f"{STATE_QUEUED!r}, got {outcome.final_state!r}"
    )
    assert outcome.exit_code == -1
    assert outcome.duration_ms == 0


def test_coverage_execute_command_cancellation_kills_child():  # NFR-08 (no orphan on cancel), NFR-15 (timeout path)
    """Cancelling ``execute_command`` must kill the child and re-raise.

    Drives ``service.runner.execute_command`` into its ``except
    asyncio.CancelledError`` branch (lines 120-128): the child is killed
    and reaped before CancelledError propagates to the caller.
    """
    import asyncio as _asyncio
    from unittest.mock import patch as _patch

    from taskq_api.service import runner as runner_module

    captured: dict[str, object] = {}

    class _FakeProc:
        def __init__(self):
            self.pid = 12345
            self.returncode = None

        async def communicate(self):
            # Block forever until killed.
            await _asyncio.sleep(60)

        async def wait(self):
            captured["wait_called"] = True
            self.returncode = -9
            return self.returncode

        def kill(self):
            captured["kill_called"] = True

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeProc()

    async def _scenario():
        with _patch.object(_asyncio, "create_subprocess_exec", _fake_exec):
            task = _asyncio.create_task(
                runner_module.execute_command("sleep 5", timeout=60.0)
            )
            await _asyncio.sleep(0)
            task.cancel()
            try:
                await task
            except _asyncio.CancelledError:
                captured["cancelled_propagated"] = True
                return
            captured["cancelled_propagated"] = False

    _run_async(_scenario())
    assert captured.get("kill_called") is True, (
        "FR-02 coverage: execute_command CancelledError branch must call "
        "process.kill() before re-raising"
    )
    assert captured.get("wait_called") is True, (
        "FR-02 coverage: execute_command CancelledError branch must reap the "
        "child via await process.wait() so the OS releases the PID"
    )
    assert captured.get("cancelled_propagated") is True, (
        "FR-02 coverage: CancelledError must propagate, not be swallowed"
    )


def test_coverage_collect_outcome_missing_binary_returns_failed_outcome():  # NFR-04 (graceful failure — no stranding), NFR-06 (runner no-op on spawn failure)
    """A missing binary must produce ``RunOutcome(final_state=failed)``.

    Drives ``service.runner._collect_outcome`` into its ``except (OSError,
    ValueError)`` branch (lines 158-165) by feeding a command whose
    ``shlex.split`` argv does not match any executable on PATH.
    """
    from taskq_api.service import runner as runner_module
    from taskq_api.service.run_state import STATE_FAILED

    async def _scenario():
        return await runner_module._collect_outcome(
            "definitely-not-a-real-binary-xyzzy", timeout=1.0
        )

    outcome = _run_async(_scenario())
    assert outcome.final_state == STATE_FAILED, (
        f"FR-02 coverage: missing binary must resolve to final_state="
        f"{STATE_FAILED!r}, got {outcome.final_state!r}"
    )
    assert outcome.exit_code == -1
    assert outcome.duration_ms == 0
    assert "definitely-not-a-real-binary-xyzzy" in outcome.stderr_tail or (
        outcome.stderr_tail != ""
    ), (
        "FR-02 coverage: stderr_tail must carry the OSError message so the "
        "operator can debug the spawn failure"
    )


def test_coverage_drain_empty_returns_zero_stragglers():  # NFR-08 (drain happy path on an idle runner), NFR-15 (FR-08 graceful drain)
    """``runner.drain`` with no in-flight tasks must report zero stragglers.

    Drives the ``if not in_flight_snapshot`` early-return branch on lines
    307-309.
    """
    from taskq_api.service import runner as runner_module

    async def _scenario():
        # Make sure the registry is empty.
        runner_module._in_flight.clear()
        runner_module._task_ids.clear()
        report = await runner_module.drain(timeout=1.0)
        return report

    report = _run_async(_scenario())
    assert report.stragglers_marked_interrupted == 0, (
        "FR-02 coverage: drain on an empty registry must report zero stragglers"
    )


def test_coverage_drain_cancels_stragglers_and_marks_interrupted(monkeypatch):  # NFR-08 (drain cancellation reaps children), NFR-15 (FR-08 graceful drain stragglers)
    """``runner.drain`` must cancel stragglers and persist ``state=interrupted``.

    Drives ``service.runner.drain`` into its straggler branch (lines
    307-342) by populating the in-flight registry with a slow fake task
    and observing that the cancellation propagates + the DB row is
    transitioned to ``STATE_INTERRUPTED``.
    """
    import asyncio as _asyncio

    from taskq_api.repository import task_repo as task_repo_module
    from taskq_api.service import runner as runner_module
    from taskq_api.service.run_state import (
        STATE_INTERRUPTED,
        STATE_RUNNING,
    )

    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "30")

    task = task_repo_module.create(name="drain-strag", command="sleep 30")
    # Transition the row to running so drain's interrupted update is the
    # final visible transition.
    task_repo_module.update_status(task.id, STATE_RUNNING)

    async def _scenario():
        # Build a long-running inner future that will be in flight when
        # drain() runs. We bypass submit() so the cap does not have to
        # be configured — only the in-flight registry matters for drain.
        runner_module._in_flight.clear()
        runner_module._task_ids.clear()

        async def _slow():
            try:
                await _asyncio.sleep(60)
            except _asyncio.CancelledError:
                # Mirror execute_command's kill+wait reaping pattern so
                # the test exercises the drain branch that depends on
                # cancellation propagating into the inner coroutine.
                raise

        inner = _asyncio.ensure_future(_slow())
        runner_module._register_in_flight(inner, task.id)
        # Yield so the inner future is actually scheduled.
        await _asyncio.sleep(0)
        report = await runner_module.drain(timeout=0.05)
        return report

    report = _run_async(_scenario())
    assert report.stragglers_marked_interrupted == 1, (
        f"FR-02 coverage: drain must mark exactly one straggler as "
        f"interrupted, got {report.stragglers_marked_interrupted}"
    )
    row = task_repo_module.get_by_id(task.id)
    assert row is not None and row.status == STATE_INTERRUPTED, (
        f"FR-02 coverage: drain must transition the row to "
        f"{STATE_INTERRUPTED!r}, got status={row.status if row else None!r}"
    )


def test_coverage_get_task_awaitable_branch(monkeypatch):  # NFR-03 (cancellation propagation — async service path), NFR-10 (round-trip)
    """``get_task_endpoint`` must ``await`` a service result that is an awaitable.

    Drives ``api.tasks:get_task_endpoint`` into the ``row = await row``
    branch on line 90 by stubbing ``service.get_task`` to return an
    ``asyncio.Future`` whose result is the task dict. This is the
    FR-10 cancellation-propagation forward-compat path.
    """
    from taskq_api.repository import task_repo as task_repo_module
    from taskq_api.service import tasks as service_tasks

    task = task_repo_module.create(name="awaitable", command="echo await")

    # Save the unpatched function BEFORE we monkeypatch, so the wrapper
    # does not recurse into itself when called from inside _fake_get_task.
    _real_get_task = service_tasks.get_task

    async def _fake_get_task(task_id):
        # Return the real dict but wrapped in a coroutine so the endpoint
        # takes the ``row = await row`` branch (line 90).
        return _real_get_task(task_id)

    monkeypatch.setattr(service_tasks, "get_task", _fake_get_task)

    from httpx import ASGITransport, AsyncClient

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            return await ac.get(
                f"/v1/tasks/{task.id}",
                headers=_auth_headers("read_key"),
            )

    response = _run_async(_run())
    assert response.status_code == 200, (
        "FR-02 coverage: get_task_endpoint awaitable branch must return 200"
    )
    payload = response.json()
    assert payload["id"] == str(task.id)


def test_coverage_submit_cancelled_error_raises_and_releases_gate(monkeypatch):  # NFR-03 (CancelledError propagation, SAB architecture constraint), NFR-08 (drain concurrency slot freed on cancel)
    """``runner.submit`` must re-raise ``CancelledError`` and free the gate.

    Drives ``service.runner.submit`` into its ``except asyncio.CancelledError:
    raise`` branch (lines 280-285) by cancelling the outer awaiter while the
    inner coroutine is still running, and exercises the ``finally: gate.release()``
    that frees the admission slot even on cancellation.
    """
    import asyncio as _asyncio

    from taskq_api.repository import task_repo as task_repo_module
    from taskq_api.service import runner as runner_module

    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "2")
    runner_module._gate = None

    task = task_repo_module.create(name="submit-cancel", command="sleep 30")

    async def _scenario():
        # Wrap submit so we can grab the in-flight task and cancel it
        # before the inner sleep completes.
        outer = _asyncio.create_task(
            runner_module.submit(task_id=task.id, command="sleep 30", timeout_sec=5.0)
        )
        # Let submit admit + register the inner task.
        await _asyncio.sleep(0)
        # Cancel the outer task — cancellation propagates into the inner.
        outer.cancel()
        try:
            await outer
        except _asyncio.CancelledError:
            return "cancelled"
        return "settled"

    outcome = _run_async(_scenario())
    assert outcome == "cancelled", (
        "FR-02 coverage: submit's outer await must surface CancelledError, "
        "not convert it into a RunOutcome(final_state='failed')"
    )
    # The gate slot must be released by the finally branch on line 290.
    assert runner_module._gate is not None, (
        "FR-02 coverage: gate must still be initialised after cancellation"
    )
    assert runner_module._gate._remaining == runner_module._gate._cap, (
        "FR-02 coverage: finally: gate.release() must free the slot even "
        "when CancelledError propagates; otherwise the runner leaks "
        "admission slots and over-cap refusals (AC-8.1) become sticky"
    )


def test_coverage_drain_skips_in_flight_tasks_with_missing_task_id(monkeypatch):  # NFR-08 (drain defensive — no crash on stale registry entry), NFR-15 (FR-08 graceful drain defensive)
    """``runner.drain`` must skip stragglers whose ``_task_ids`` mapping was popped.

    Drives the ``if task_id is None: continue`` defensive branch (line 338) by
    registering an in-flight future, then popping its ``_task_ids`` mapping
    before ``drain`` snapshots it — simulating a done-callback that fired
    between snapshot acquisition and the post-cancellation status update.
    """
    import asyncio as _asyncio

    from taskq_api.service import runner as runner_module

    async def _scenario():
        runner_module._in_flight.clear()
        runner_module._task_ids.clear()

        async def _slow():
            await _asyncio.sleep(60)

        inner = _asyncio.ensure_future(_slow())
        runner_module._register_in_flight(inner, 99999)
        # Strip the _task_ids mapping AFTER registration but BEFORE drain
        # takes its snapshot — mirrors the done_callback firing early.
        runner_module._task_ids.pop(inner, None)
        # Yield so the inner future is scheduled.
        await _asyncio.sleep(0)
        # A tiny timeout means the inner is still pending; drain must
        # cancel it, then take the `task_id is None` defensive branch.
        report = await runner_module.drain(timeout=0.05)
        return report

    report = _run_async(_scenario())
    # The defensive branch leaves stragglers_marked_interrupted at 0
    # because no task_id was associated with the straggler.
    assert report.stragglers_marked_interrupted == 0, (
        "FR-02 coverage: drain's defensive `continue` must skip stragglers "
        "with a popped _task_ids mapping; got "
        f"{report.stragglers_marked_interrupted}"
    )


def test_coverage_run_task_endpoint_db_error_propagates(monkeypatch):  # NFR-03 (DB outage surfaces 500, not 404), NFR-07 (dependency fault — DB unavailable)
    """A DB error during the run existence check must propagate, not return 404.

    Drives ``api.tasks:run_task_endpoint`` into the ``except Exception:
    raise`` branch on lines 149-153 by stubbing ``task_repo.get_by_id``
    to raise. The global exception handler is expected to convert this
    into a 500; the assertion is that the request does not return 404.
    """
    from taskq_api.repository import task_repo as task_repo_module
    from taskq_api.errors import make_problem

    def _raise_db_error(task_id):
        # Raise a FastAPI-recognised Problem so the global handler maps
        # it to a deterministic status — easier to assert than a bare
        # RuntimeError, which would still pass but route through the
        # generic 500 path.
        raise make_problem(
            status=500,
            title="DB unavailable",
            detail="simulated DB outage during run existence check",
            type_uri="/errors/db-unavailable",
        )

    monkeypatch.setattr(task_repo_module, "get_by_id", _raise_db_error)

    from httpx import ASGITransport, AsyncClient

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            return await ac.post(
                "/v1/tasks/1/run",
                headers=_auth_headers("write_key"),
            )

    response = _run_async(_run())
    assert response.status_code != 404, (
        "FR-02 coverage: a DB error during the run existence check must "
        "propagate (becomes 500 via the global handler), not silently "
        "surface as a misleading 404"
    )
    assert response.status_code == 500, (
        f"FR-02 coverage: expected 500 from the global handler, got "
        f"{response.status_code}"
    )
