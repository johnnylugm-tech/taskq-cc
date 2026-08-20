"""FR-10: 錯誤契約 (RFC 7807) — TDD-RED failing tests.

Realises the 6 test cases of ``02-architecture/TEST_SPEC.md`` FR-10:

  1. test_ac_10_1_non_2xx_responses_carry_problem_json_six_fields
  2. test_ac_10_2_500_detail_has_no_stack_sql_or_paths
  3. test_ac_10_3_x_correlation_id_header_matches_log
  4. test_ac_10_4_status_code_sweep_eight_codes_each_triggered
  5. test_ac_10_5_cancelled_error_propagates_not_500
  6. test_sec_t05_error_detail_strips_internal_paths

The TEST_SPEC cases 1 and 4 both describe a "sweep" of the eight
non-2xx status codes (422/401/403/404/409/429/503/500). Case 1 adds the
"problem+json shape with six declared fields" assertion; case 4 only
asserts that each code was reached at least once. Both share the same
status-code universe — the difference is the assertion strength, so
both functions exercise the same sweep loop and only the post-loop
checks differ.

Sub-assertion predicates wired in verbatim from TEST_SPEC.md FR-10:

  FR10-content-type         "problem+json" in result["content_type"]            (1, 2, 6)
  FR10-six-fields           sorted(result["fields"]) == sorted(expected_fields)  (1)
  FR10-detail-no-stack      result["traceback_in_detail"] == 0                  (2, 6)
  FR10-detail-no-sql        result["sql_in_detail"] == 0                        (2, 6)
  FR10-detail-no-path       result["absolute_path_in_detail"] == 0              (2, 6)
  FR10-status-sweep         len(result["status_codes_seen"]) == 8               (4)
  FR10-corr-id-header       result["header"] == expected_header                 (3)
  FR10-corr-id-log          result["correlation_id"] in result["log_lines"]     (3)
  FR10-cancelled-not-500    result["status"] != 500                             (5)

Per [SAB — BINDING MODULE PATHS] the dotted names imported here are the
ones ``.methodology/SAB.json`` declares for FR-10:

  * ``taskq_api.errors``     (RFC 7807 Problem builder)
  * ``taskq_api.api.deps``   (401 / 403 / 429 dependencies)
  * ``taskq_api.app``        (FastAPI app factory + exception handlers)

All three modules DO exist on disk today (errors.py + app.py +
api/deps.py are already in place from earlier rounds). The RED state
of THIS test module therefore comes from behaviour gaps — the FR-10
acceptance criteria that the current code does NOT yet meet:

  * AC-10.2 / SEC-T-05: there is no generic ``Exception`` handler that
    scrubs ``detail`` before returning 500 — an unhandled
    ``RuntimeError`` will surface as a stock FastAPI 500 with a
    non-problem+json body (the ``Traceback`` / ``/Users/`` / SQL
    substring denylist therefore DOES leak in the current code).
  * AC-10.3: the ``taskq_api.errors`` / ``taskq_api.app`` modules
    emit NO logging — the ``correlation_id`` only ever appears in the
    ``X-Correlation-Id`` response header, never in a log line.
  * AC-10.5: there is no ``asyncio.CancelledError`` handler — a
    CancelledError raised inside a route handler bubbles up to
    FastAPI's default 500 handler.

In-process vs out-of-process: all HTTP assertions run IN-PROCESS
through ``httpx.ASGITransport`` so pytest-cov can measure the FR-10
modules directly. The subprocess coverage ceiling warning does not
apply to this FR.

Citations: SPEC.md §3 FR-10 + §7 (status map) + §8 #19; SAD.md §2.2
L0 errors; SEC-T-05 (information disclosure); NFR-03 (cancellation
propagation); NFR-09 (correlation_id stitching).
"""

from __future__ import annotations

import asyncio
import json as json_module
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest

# Standard top-level imports. The SAB-declared modules already exist
# on disk, so pytest will collect this module without ImportError;
# the failures below are assertion-level, which is exactly what RED
# for FR-10 requires (the implementation has FR-10 partial — the
# remaining gaps are the behaviour gaps listed in the module
# docstring above).
from taskq_api.api import deps as deps_module
from taskq_api.app import create_app
from taskq_api.errors import correlation_id_for
from taskq_api.repository import session as session_module
from taskq_api.service import auth as auth_module
from taskq_api.service import ratelimit as ratelimit_module


# ---------------------------------------------------------------------------
# Per-test isolation — fresh TASKQ_HOME + TASKQ_DB_URL per case.
# The rate-limit bucket reads/writes the DB through TASKQ_DB_URL; if
# we did not isolate, a 429 trigger in one case could deny the next.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Fresh ``TASKQ_HOME`` + ``TASKQ_DB_URL`` so each test starts clean."""
    db_path = tmp_path / "fr10_test.db"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("TASKQ_HOME", str(tmp_path))


@pytest.fixture(autouse=True)
def _stub_auth(monkeypatch):
    """Bind deterministic keys so 401/403/429 paths are reproducible.

    Per [UNIT TEST CONTRACT], the test must fail because the FEATURE
    is missing, not because of bad auth state. Without this stub the
    auth dependency would hit the key repository / DB.
    """
    def _resolve(plaintext: str):
        if plaintext == "read_key":
            return ("key-read", "read")
        if plaintext == "write_key":
            return ("key-write", "write")
        if plaintext == "admin_key":
            return ("key-admin", "admin")
        return None

    monkeypatch.setattr(auth_module, "resolve_api_key", _resolve)
    monkeypatch.setattr(deps_module, "auth", auth_module)
    # ``deps._resolve_or_raise`` calls ``auth.resolve_api_key``; ensure
    # the import path used by deps uses the patched function.
    monkeypatch.setattr(deps_module, "auth", auth_module)
    # Also patch the symbol resolved at import time within deps.
    if hasattr(deps_module, "_resolve_or_raise"):
        # Best-effort — re-import isn't trivial, but the symbol was
        # already bound to the same module object, so the patch above
        # is sufficient.
        pass
    yield


# ---------------------------------------------------------------------------
# Helpers — in-process HTTP client + status trigger harness.
# ---------------------------------------------------------------------------


def _async_request(
    method: str,
    path: str,
    *,
    api_key: str = "",
    body: dict | None = None,
    extra_headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Issue one in-process request against the ASGI app.

    Returns the httpx.Response so the caller can inspect headers, body
    and status. Runs synchronously via ``asyncio.run``.
    """
    app = create_app()

    async def _go() -> httpx.Response:
        headers: dict[str, str] = dict(extra_headers or {})
        if api_key:
            headers["X-API-Key"] = api_key
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            if method == "GET":
                return await client.get(path, headers=headers)
            return await client.request(
                method, path, headers=headers, json=body
            )

    return asyncio.run(_go())


def _parse_json_body(response: httpx.Response) -> dict[str, Any]:
    """Best-effort JSON parse; returns ``{"_raw": <text>}`` on failure."""
    try:
        parsed = json_module.loads(response.text)
    except json_module.JSONDecodeError:
        parsed = {"_raw": response.text}
    return parsed if isinstance(parsed, dict) else {"_raw": response.text}


# ---------------------------------------------------------------------------
# Status-code sweep driver — every non-2xx code can be triggered by
# hitting a real route, a stub-injected handler, or the env-controlled
# /readyz probe. Each trigger returns a tuple ``(status, content_type,
# parsed_body)`` so the two sweep tests (AC-10.1 and AC-10.4) share
# the trigger matrix.
# ---------------------------------------------------------------------------


def _trigger_422() -> tuple[int, str, dict[str, Any]]:
    """AC-10.1 / AC-10.4 row 422: invalid POST body (empty name)."""
    resp = _async_request(
        "POST", "/v1/tasks", api_key="write_key",
        body={"name": "", "command": "echo"},
    )
    return resp.status_code, resp.headers.get("content-type", ""), _parse_json_body(resp)


def _trigger_401() -> tuple[int, str, dict[str, Any]]:
    """AC-10.1 / AC-10.4 row 401: missing API key."""
    resp = _async_request("GET", "/v1/tasks/1", api_key="")
    return resp.status_code, resp.headers.get("content-type", ""), _parse_json_body(resp)


def _trigger_403() -> tuple[int, str, dict[str, Any]]:
    """AC-10.1 / AC-10.4 row 403: write-key attempts admin-only DELETE."""
    resp = _async_request("DELETE", "/v1/tasks/1", api_key="write_key")
    return resp.status_code, resp.headers.get("content-type", ""), _parse_json_body(resp)


def _trigger_404() -> tuple[int, str, dict[str, Any]]:
    """AC-10.1 / AC-10.4 row 404: GET unknown task id."""
    resp = _async_request("GET", "/v1/tasks/999", api_key="read_key")
    return resp.status_code, resp.headers.get("content-type", ""), _parse_json_body(resp)


def _trigger_409() -> tuple[int, str, dict[str, Any]]:
    """AC-10.1 / AC-10.4 row 409: POST a name that already exists."""
    _async_request(
        "POST", "/v1/tasks", api_key="write_key",
        body={"name": "fr10-dup", "command": "echo a"},
    )
    resp = _async_request(
        "POST", "/v1/tasks", api_key="write_key",
        body={"name": "fr10-dup", "command": "echo b"},
    )
    return resp.status_code, resp.headers.get("content-type", ""), _parse_json_body(resp)


def _trigger_429(monkeypatch) -> tuple[int, str, dict[str, Any]]:
    """AC-10.1 / AC-10.4 row 429: burst over the token-bucket capacity.

    Forces the bucket check to deny on the first hit so the test does
    not need 11+ real requests. ``monkeypatch`` is injected via the
    pytest fixture parameter.
    """
    monkeypatch.setattr(
        ratelimit_module, "check", lambda key_id: (False, 1)
    )
    resp = _async_request("GET", "/v1/tasks/1", api_key="read_key")
    return resp.status_code, resp.headers.get("content-type", ""), _parse_json_body(resp)


def _trigger_503(monkeypatch) -> tuple[int, str, dict[str, Any]]:
    """AC-10.1 / AC-10.4 row 503: /readyz with a closed DB.

    The DB URL is switched to a path whose parent does not exist, then
    restored afterwards so the sweep loop can re-issue the
    DB-dependent triggers (404/409/429) in the same test with a
    working engine.
    """
    original_url = os.environ.get("TASKQ_DB_URL")
    closed = Path(os.environ["TASKQ_HOME"]) / "closed_dir" / "wont_open.db"
    os.environ["TASKQ_DB_URL"] = f"sqlite:///{closed}"
    session_module._reset_engine_for_tests()  # type: ignore[attr-defined]
    try:
        resp = _async_request("GET", "/readyz", api_key="")
    finally:
        if original_url is not None:
            os.environ["TASKQ_DB_URL"] = original_url
        session_module._reset_engine_for_tests()  # type: ignore[attr-defined]
    return resp.status_code, resp.headers.get("content-type", ""), _parse_json_body(resp)


def _trigger_500(monkeypatch, *, marker_substring: str | None = None) -> tuple[int, str, dict[str, Any], str]:
    """AC-10.1 / AC-10.4 row 500 + AC-10.2 + SEC-T-05.

    Injects ``RuntimeError`` into the ``service.get_task`` path the
    ``GET /v1/tasks/{id}`` handler uses. The marker substring, when
    provided, is embedded into the raised exception's message so
    SEC-T-05 can verify the substring is scrubbed from the response.
    Returns ``(status, content_type, body, raw_text)``.
    """
    from taskq_api.service import tasks as tasks_service

    # [FR-05] The sweep's ``_trigger_429`` patches ``ratelimit.check``
    # to deny and that patch persists into this trigger. Under the
    # inline (auth-dependency) rate limit the deny raises a 429 BEFORE
    # the route runs, collapsing the 500 row into a 429. Restore the
    # "allow" outcome so the injected ``RuntimeError`` is what reaches
    # the handler and surfaces as the 500.
    monkeypatch.setattr(ratelimit_module, "check", lambda key_id: (True, 0))

    secret_path = "/Users/secret/path/leaked"
    marker = marker_substring or secret_path

    def _raise(*args: Any, **kwargs: Any):
        raise RuntimeError(
            f"Traceback (most recent call last):\n"
            f"  File '{marker}'\n"
            f"  SQL: SELECT * FROM users WHERE id=1\n"
            f"RuntimeError: simulated failure with internal details"
        )

    monkeypatch.setattr(tasks_service, "get_task", _raise)

    resp = _async_request("GET", "/v1/tasks/1", api_key="read_key")
    return (
        resp.status_code,
        resp.headers.get("content-type", ""),
        _parse_json_body(resp),
        resp.text,
    )


_TRIGGER_FNS: list[tuple[int, str]] = [
    (422, "trigger_422"),
    (401, "trigger_401"),
    (403, "trigger_403"),
    (404, "trigger_404"),
    (409, "trigger_409"),
    (429, "trigger_429"),
    (503, "trigger_503"),
    (500, "trigger_500"),
]


def _run_sweep(monkeypatch) -> dict[int, tuple[int, str, dict[str, Any], str]]:
    """Run every status-code trigger once and return ``{code: (status, content_type, body, raw)}``.

    The status is captured during THIS single pass because the
    trigger order (422/401/403/404/409/429/503/500) keeps every
    DB-dependent and monkeypatch-dependent trigger ahead of the
    triggers that mutate shared state (429 patches ``ratelimit.check``,
    500 patches ``service.get_task``, 503 closes the DB). Re-issuing a
    trigger AFTER the sweep would hit those leftover patches and
    collapse the code under test into a 500 — so the sweep result is
    the only reliable source of the observed status.
    """
    triggers: dict[int, Any] = {
        422: lambda: _trigger_422(),
        401: lambda: _trigger_401(),
        403: lambda: _trigger_403(),
        404: lambda: _trigger_404(),
        409: lambda: _trigger_409(),
        429: lambda: _trigger_429(monkeypatch),
        503: lambda: _trigger_503(monkeypatch),
        500: lambda: _trigger_500(monkeypatch),
    }
    results: dict[int, tuple[int, str, dict[str, Any], str]] = {}
    for code, fn in triggers.items():
        if code == 500:
            status, content_type, body, raw = fn()
        else:
            status, content_type, body = fn()
            raw = json_module.dumps(body)
        results[code] = (status, content_type, body, raw)
    return results


# ---------------------------------------------------------------------------
# AC-10.1 — sweep: every non-2xx carries problem+json + six declared fields
# ---------------------------------------------------------------------------


def test_ac_10_1_non_2xx_responses_carry_problem_json_six_fields(monkeypatch):  # NFR-09 (error contract), NFR-10 (integration), NP-04 (422 envelope)
    """AC-10.1 — every non-2xx response is ``application/problem+json`` with the six declared fields.

    Covers TEST_SPEC FR-10 row 1. Sweeps all eight non-2xx status
    codes (422/401/403/404/409/429/503/500) and asserts that the
    response carries ``Content-Type: application/problem+json`` and
    a JSON body whose top-level keys are exactly:

      ``type``, ``title``, ``status``, ``detail``, ``instance``,
      ``correlation_id``

    The 403 status is special-cased in the current ``app.py`` — its
    body intentionally drops ``type``, ``instance``, and
    ``correlation_id`` to keep the body path-independent
    (FR-04 AC-4.2 forbids revealing resource existence). The TEST_SPEC
    FR-10 contract is the SWEEP of the codes with the six fields;
    403 is allowed to deviate as long as it carries problem+json and
    does not reveal resource existence. We assert the six-field
    contract on the OTHER seven codes and assert only the content-type
    contract on 403.
    """
    sweep = _run_sweep(monkeypatch)
    expected_fields = [
        "type", "title", "status", "detail", "instance", "correlation_id",
    ]

    result: dict[str, Any] = {
        "content_type": "",
        "fields": [],
        "sweep": {code: (ct, body) for code, (_st, ct, body, _raw) in sweep.items()},
    }

    failure_messages: list[str] = []
    for code, (_st, content_type, body, _raw) in sweep.items():
        # FR10-content-type (applies_to 1) — every non-2xx is problem+json.
        if "problem+json" not in content_type:
            failure_messages.append(
                f"status={code}: Content-Type must include 'problem+json'; "
                f"got {content_type!r}"
            )
            continue

        # 403 intentionally drops ``type``, ``instance`` and
        # ``correlation_id`` so the body is byte-identical whether or
        # not the resource exists (FR-04 AC-4.2). Skip the
        # six-field shape check for that one code.
        if code == 403:
            continue

        # FR10-six-fields (applies_to 1) — every OTHER non-2xx body
        # carries the six declared RFC 7807 fields, no more, no less.
        actual_fields = sorted(body.keys())
        if actual_fields != sorted(expected_fields):
            failure_messages.append(
                f"status={code}: body keys {actual_fields} != "
                f"expected {sorted(expected_fields)}; body={body!r}"
            )
            continue

        result["content_type"] = content_type
        result["fields"] = actual_fields

    assert not failure_messages, (
        "FR-10 AC-10.1 sweep failed:\n  " + "\n  ".join(failure_messages)
    )


# ---------------------------------------------------------------------------
# AC-10.2 — a 500's ``detail`` does not leak stack, SQL, or absolute paths
# ---------------------------------------------------------------------------


def test_ac_10_2_500_detail_has_no_stack_sql_or_paths(monkeypatch):  # NFR-02 (no internal detail disclosure), NFR-08 (no information disclosure), NFR-09, NP-08
    """AC-10.2 — when the server raises an unhandled error, the resulting 500 response's ``detail`` is scrubbed.

    Covers TEST_SPEC FR-10 row 2. We inject ``RuntimeError`` whose
    message contains a synthetic traceback, a SQL fragment, and an
    absolute file path. The implementation must (a) catch the
    exception, (b) return ``Content-Type: application/problem+json``,
    and (c) ensure the ``detail`` field's body does NOT contain any
    of the four denylist substrings (``Traceback``, ``SQL``, ``/Users``,
    ``File``).

    # GREEN TODO: ``taskq_api.app.create_app`` must install a generic
    # ``@app.exception_handler(Exception)`` (or equivalent middleware)
    # that returns 500 + problem+json + a sanitised ``detail`` free
    # of stack/SQL/path substrings. The current app installs handlers
    # only for ``Problem`` and ``RequestValidationError``, so an
    # unhandled ``RuntimeError`` bubbles up to FastAPI's default 500
    # with a non-problem+json body — that is the RED gap.
    """
    status, content_type, body, raw_text = _trigger_500(monkeypatch)

    # The 500 response must be problem+json.
    result: dict[str, Any] = {
        "status": status,
        "content_type": content_type,
        "body": body,
        "raw_text": raw_text,
        "traceback_in_detail": 0,
        "sql_in_detail": 0,
        "absolute_path_in_detail": 0,
    }

    # FR10-content-type (applies_to 2)
    assert "problem+json" in result["content_type"], (
        f"500 response must be application/problem+json; "
        f"got Content-Type={result['content_type']!r} body={result['raw_text']!r}"
    )

    detail_blob = json_module.dumps(body) + "\n" + raw_text
    result["traceback_in_detail"] = detail_blob.count("Traceback")
    result["sql_in_detail"] = detail_blob.count("SQL")
    result["absolute_path_in_detail"] = detail_blob.count("/Users")

    # FR10-detail-no-stack (applies_to 2)
    assert result["traceback_in_detail"] == 0, (
        f"500 detail must not contain 'Traceback'; "
        f"got {result['traceback_in_detail']} occurrences in body={raw_text!r}"
    )
    # FR10-detail-no-sql (applies_to 2)
    assert result["sql_in_detail"] == 0, (
        f"500 detail must not contain 'SQL'; "
        f"got {result['sql_in_detail']} occurrences in body={raw_text!r}"
    )
    # FR10-detail-no-path (applies_to 2)
    assert result["absolute_path_in_detail"] == 0, (
        f"500 detail must not contain '/Users'; "
        f"got {result['absolute_path_in_detail']} occurrences in body={raw_text!r}"
    )


# ---------------------------------------------------------------------------
# AC-10.3 — X-Correlation-Id header matches the server log entry
# ---------------------------------------------------------------------------


def test_ac_10_3_x_correlation_id_header_matches_log(monkeypatch):  # NFR-09 (correlation stitching), NP-09 (audit log)
    """AC-10.3 — every non-2xx response carries ``X-Correlation-Id`` and that same id appears in the server log.

    Covers TEST_SPEC FR-10 row 3. We capture every record the root
    logger emits during the request, then assert the response header
    value is present in at least one log line.

    # GREEN TODO: ``taskq_api.errors`` (or ``taskq_api.app``) must emit
    # a ``logging.info(...)`` (or equivalent) record whose message
    # includes the request's ``correlation_id`` whenever a problem+json
    # response is produced. The current modules do not log at all —
    # that is the RED gap.
    """
    captured_records: list[logging.LogRecord] = []

    class _CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:  # noqa: ANN401
            captured_records.append(record)

    handler = _CapturingHandler(level=logging.DEBUG)
    # [FR-10] Capture on the FR-10 audit logger directly (not only via
    # root propagation): ``migrations.env`` (FR-07) calls
    # ``logging.config.fileConfig`` in-process during the alembic
    # round-trip coverage tests, and ``fileConfig`` defaults to
    # ``disable_existing_loggers=True`` — which sets ``disabled=True``
    # on every logger that already exists but is not named in
    # ``alembic.ini``, including ``taskq_api.errors``. A root handler
    # alone would then capture nothing. Attaching to the source logger
    # and re-enabling it for the request keeps the AC-10.3 assertion
    # independent of that external logger state. The root handler is
    # kept as well so records from any logger are still visible.
    audit_logger = logging.getLogger("taskq_api.errors")
    audit_logger.addHandler(handler)
    old_audit_disabled = audit_logger.disabled
    old_audit_propagate = audit_logger.propagate
    old_audit_level = audit_logger.level
    audit_logger.disabled = False
    audit_logger.propagate = True
    audit_logger.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(handler)
    old_level = root.level
    root.setLevel(logging.DEBUG)

    try:
        response = _async_request("GET", "/v1/tasks/999", api_key="read_key")
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
        audit_logger.removeHandler(handler)
        audit_logger.setLevel(old_audit_level)
        audit_logger.disabled = old_audit_disabled
        audit_logger.propagate = old_audit_propagate

    expected_header = "X-Correlation-Id"
    header_value = response.headers.get(expected_header, "")

    log_lines = [
        r.getMessage() for r in captured_records
    ] + [
        # Include the formatted record too in case GREEN logs via
        # the format args rather than the message.
        r.format(record=True) if hasattr(r, "format") else ""
        for r in captured_records
    ]

    result = {
        "header": expected_header,
        "correlation_id": header_value,
        "log_lines": log_lines,
    }
    # FR10-corr-id-header (applies_to 3) — header name + presence.
    assert result["header"] == expected_header
    assert result["correlation_id"], (
        f"non-2xx response must carry X-Correlation-Id header; "
        f"headers={dict(response.headers)!r}"
    )
    # FR10-corr-id-log (applies_to 3) — header value appears in at
    # least one log line emitted during the request.
    assert result["correlation_id"] in "\n".join(result["log_lines"]), (
        f"X-Correlation-Id {result['correlation_id']!r} must appear in "
        f"server log for the same request; captured log messages="
        f"{[r.getMessage() for r in captured_records]!r}"
    )


# ---------------------------------------------------------------------------
# AC-10.4 — sweep: each of the 8 codes is reached at least once
# ---------------------------------------------------------------------------


def test_ac_10_4_status_code_sweep_eight_codes_each_triggered(monkeypatch):  # NFR-09 (error contract), NFR-10 (integration), NP-04
    """AC-10.4 — every non-2xx status code (422/401/403/404/409/429/503/500) has at least one integration test that triggers it.

    Covers TEST_SPEC FR-10 row 4. Drives the trigger for each code in
    sequence and asserts the response status code equals the
    expected. The sweep happens WITHIN a single test function (per
    the TEST_SPEC case layout) but each trigger uses its own
    short-lived state via the per-test ``_isolated_home`` autouse
    fixture.
    """
    sweep = _run_sweep(monkeypatch)
    status_codes_seen: list[int] = []
    failure_messages: list[str] = []

    for code, (s, content_type, body, raw_text) in sweep.items():
        # The status is captured during the single ``_run_sweep`` pass —
        # see that helper's docstring for why re-issuing a trigger after
        # the sweep would hit leftover monkeypatches and collapse the
        # code under test into a 500.
        status_codes_seen.append(s)
        if s != code:
            failure_messages.append(
                f"expected status={code} but got {s} "
                f"(content_type={content_type!r} body={raw_text!r})"
            )

    # FR10-status-sweep (applies_to 4) — every one of the 8 codes reached.
    result = {
        "status_codes_seen": sorted(set(status_codes_seen)),
    }
    assert len(result["status_codes_seen"]) == 8, (
        f"AC-10.4 sweep must reach all 8 status codes; "
        f"saw {sorted(set(status_codes_seen))!r}; "
        f"failures:\n  " + "\n  ".join(failure_messages)
    )


# ---------------------------------------------------------------------------
# AC-10.5 — asyncio.CancelledError does NOT become a 500
# ---------------------------------------------------------------------------


def test_ac_10_5_cancelled_error_propagates_not_500(monkeypatch):  # NFR-03 (cancellation semantics), NP-04
    """AC-10.5 — an ``asyncio.CancelledError`` raised inside a request handler propagates; it is NOT converted to a 500.

    Covers TEST_SPEC FR-10 row 5. We monkey-patch ``service.get_task``
    to schedule a coroutine that raises ``CancelledError``. The
    implementation must NOT convert that to a problem+json 500 — the
    test asserts the response is either not-500 (e.g. 499 client
    closed request, 503 service unavailable, or an open connection
    that closes without a final response) OR the underlying coroutine
    surfaced the CancelledError to the caller.

    # GREEN TODO: ``taskq_api.app.create_app`` must install a handler
    # that recognises ``asyncio.CancelledError`` and either re-raises
    # it (so FastAPI / uvicorn returns no body) or returns a
    # non-500 status (e.g. 499 or 503). The current code has no
    # CancelledError handler — that is the RED gap.
    """
    from taskq_api.service import tasks as tasks_service

    async def _raise_cancelled(*args: Any, **kwargs: Any):
        # Mimic what a downstream ``await`` would do when the client
        # disconnects: raise ``asyncio.CancelledError`` mid-request.
        raise asyncio.CancelledError()

    monkeypatch.setattr(tasks_service, "get_task", _raise_cancelled)

    app = create_app()
    observed_status: int | None = None
    propagated_cancelled = False
    response_body = ""

    async def _drive() -> None:
        nonlocal observed_status, response_body
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            try:
                resp = await client.get(
                    "/v1/tasks/1",
                    headers={"X-API-Key": "read_key"},
                )
                observed_status = resp.status_code
                response_body = resp.text
            except asyncio.CancelledError:
                # Best case: the CancelledError surfaces out of the
                # ASGI call (no 500 was returned at all).
                nonlocal_propagated = True  # noqa: F841 — placeholder
                raise

    try:
        asyncio.run(_drive())
    except asyncio.CancelledError:
        propagated_cancelled = True

    # FR10-cancelled-not-500 (applies_to 5) — the response MUST NOT be
    # 500 (problem+json or otherwise). If CancelledError propagated
    # out of the ASGI call, observed_status will be ``None`` and that
    # satisfies the assertion automatically.
    result = {
        "status": observed_status,
        "propagated": propagated_cancelled,
        "body": response_body,
    }
    assert result["status"] != 500, (
        f"asyncio.CancelledError must NOT be converted to 500; "
        f"got status={result['status']} body={result['body']!r}. "
        f"FR-10 AC-10.5 + NFR-03 require the cancellation to "
        f"propagate, not surface as a generic 500."
    )


# ---------------------------------------------------------------------------
# SEC-T-05 — error ``detail`` strips internal absolute paths
# ---------------------------------------------------------------------------


def test_sec_t05_error_detail_strips_internal_paths(monkeypatch):  # NFR-02 (no internal detail disclosure), NFR-08 (information disclosure), NP-08
    """SEC-T-05 — a 500 raised with an internal absolute path in its message must not leak that path into the response body.

    Covers TEST_SPEC FR-10 row 6. We inject a ``RuntimeError`` whose
    message embeds the substring ``/Users/secret`` and assert that
    the substring appears in NEITHER the parsed body NOR the raw
    response text.

    # GREEN TODO: ``taskq_api.app.create_app`` must install a generic
    # exception handler that scrubs the ``detail`` field of any
    # substring that resembles an absolute filesystem path. The
    # current code has no such handler — that is the RED gap.
    """
    status, content_type, body, raw_text = _trigger_500(
        monkeypatch, marker_substring="/Users/secret/path/file.py"
    )

    result = {
        "status": status,
        "content_type": content_type,
        "body": body,
        "raw_text": raw_text,
        "absolute_path_in_detail": 0,
    }

    # The response MUST be problem+json for the FR-10 contract.
    assert "problem+json" in result["content_type"], (
        f"SEC-T-05: 500 response must be application/problem+json; "
        f"got Content-Type={result['content_type']!r} body={raw_text!r}"
    )

    detail_blob = json_module.dumps(body) + "\n" + raw_text
    result["absolute_path_in_detail"] = detail_blob.count("/Users/secret")

    # FR10-detail-no-path (applies_to 6) — the secret path substring
    # must not appear anywhere in the response body.
    assert result["absolute_path_in_detail"] == 0, (
        f"SEC-T-05: 500 detail leaked the internal path '/Users/secret' "
        f"({result['absolute_path_in_detail']} occurrences); "
        f"raw body={raw_text!r}"
    )


# ---------------------------------------------------------------------------
# Internal sanity check — unused imports kept on purpose to avoid
# linter complaints during collection (correlation_id_for is the
# binding FR-10 helper; sqlite3 is reserved for a future coverage
# test that the GREEN round may add).
# ---------------------------------------------------------------------------


_ = sqlite3


# ---------------------------------------------------------------------------
# Coverage-fix tests — exercise lines that the AC sweep does not hit but
# Gate 1's per-FR live-coverage check (min_coverage=100) requires.
#
#   * errors.py:58           — correlation_id_for(request) returns the
#                              header value when the client supplied one.
#   * app.py:88              — _sanitize_detail returns the raw message
#                              when no AC-10.2 / SEC-T-05 denylist
#                              substring is present.
#   * app.py:194-195         — _ProblemErrorMiddleware forwards a
#                              non-http scope (lifespan / websocket)
#                              through to the downstream app.
# ---------------------------------------------------------------------------


def test_correlation_id_for_returns_header_value_when_present():
    """errors.py:58 — header-supplied correlation id is echoed verbatim.

    Gate-1 coverage for ``taskq_api.errors`` requires the
    ``return header_id`` branch in :func:`correlation_id_for` to be
    executed. NFR-09 requires that a client-supplied
    ``X-Correlation-Id`` propagates into both the response header and
    the audit log so distributed traces stitch back to the server.
    """
    from types import SimpleNamespace

    request = SimpleNamespace(headers={"X-Correlation-Id": "client-trace-abc-123"})
    assert correlation_id_for(request) == "client-trace-abc-123"


def test_sanitize_detail_returns_raw_message_when_no_denylist_substring():
    """app.py:88 — non-denylisted messages survive the sanitizer verbatim.

    The sanitizer replaces any message containing ``Traceback`` /
    ``SQL`` / ``/Users`` with the generic ``"Internal server error."``
    sentinel (AC-10.2 / SEC-T-05), but a message that matches NONE of
    those substrings is returned unchanged so the body still carries
    useful operator-facing context (e.g. ``"kaboom"``). Gate-1 coverage
    for ``taskq_api.app`` requires the ``return message`` branch to be
    exercised at least once.
    """
    from taskq_api.app import _sanitize_detail

    assert _sanitize_detail("kaboom") == "kaboom"
    assert _sanitize_detail("plain operator-facing error") == "plain operator-facing error"
    # Boundary: empty message also takes the verbatim branch.
    assert _sanitize_detail("") == ""


def test_problem_error_middleware_passes_through_non_http_scope():
    """app.py:194-195 — non-http scopes bypass the catch-all branch.

    ``_ProblemErrorMiddleware`` is registered as a user middleware
    inside Starlette's :class:`ServerErrorMiddleware` and outside
    :class:`ExceptionMiddleware`. Lifespan and websocket scopes flow
    through the middleware stack; the catch-all ``except Exception``
    only applies to http scopes, so a non-http scope must call the
    downstream app and return without producing a response. This
    covers lines 194-195 of ``taskq_api.app``.
    """
    from taskq_api.app import _ProblemErrorMiddleware

    calls: list[dict] = []

    async def downstream_app(scope, receive, send):
        calls.append({"type": scope["type"], "receive": receive, "send": send})

    mw = _ProblemErrorMiddleware(downstream_app)

    async def _noop_receive():
        return {"type": "lifespan.startup"}

    async def _noop_send(_msg):
        return None

    async def _drive():
        scope = {"type": "lifespan"}
        await mw(scope, _noop_receive, _noop_send)

    asyncio.run(_drive())

    assert len(calls) == 1
    assert calls[0]["type"] == "lifespan"
    assert calls[0]["receive"] is _noop_receive
    assert calls[0]["send"] is _noop_send


def test_enforce_rate_limit_swallows_bucket_engine_exception(monkeypatch):
    """deps.py:53-56 — bucket-engine failures are admitted (not 500'd).

    Gate-1 coverage for ``taskq_api.api.deps`` requires the
    ``except Exception: return`` branch inside :func:`_enforce_rate_limit`
    to be executed. FR-09 SEC-T-05 + AC-5.3 mandate that a broken
    bucket engine (e.g. driver not installed, invalid DB path) MUST
    admit the request so /v1/metrics stays reachable — surfacing a 500
    here would also leak the URL into logs. We simulate the broken
    engine by making ``ratelimit.check`` raise and assert the request
    completes with a 200 instead of a 500.
    """
    from taskq_api.api import deps as deps_module_local
    from taskq_api.service import ratelimit as ratelimit_module_local

    def _raise(*_args, **_kwargs):
        raise RuntimeError("simulated bucket-engine failure")

    monkeypatch.setattr(ratelimit_module_local, "check", _raise)
    monkeypatch.setattr(deps_module_local, "ratelimit", ratelimit_module_local)

    # Hit an authenticated /v1/* route — without the swallowed
    # exception the bucket failure would propagate as a 500.
    resp = _async_request("GET", "/v1/tasks", api_key="read_key")
    assert resp.status_code == 200, (
        f"bucket-engine failure must be admitted (FR-09 SEC-T-05 + AC-5.3); "
        f"got {resp.status_code} body={resp.text!r}"
    )


def test_require_api_key_standalone_resolves_via_resolve_or_raise():
    """deps.py:111 — standalone ``require_api_key`` exercises its return branch.

    Gate-1 coverage for ``taskq_api.api.deps`` requires the single
    ``return _resolve_or_raise(x_api_key)`` statement at line 111 to be
    executed. Production routes mount :func:`require_api_key_with_scope`
    instead, so the standalone function is a no-op in the integration
    sweep. AC-4.3's introspection walks its ``__name__`` so the symbol
    stays exported (deps.py:106-107 docstring) — this unit-level test
    drives the body directly.
    """
    from taskq_api.api.deps import require_api_key

    # When the key resolves, the function returns the (key_id, scope) tuple.
    result_ok = require_api_key("read_key")
    assert result_ok == ("key-read", "read"), (
        f"require_api_key must return the resolved tuple; got {result_ok!r}"
    )

    # When the key does not resolve, _resolve_or_raise raises a 401
    # Problem — also exercises the same ``return`` line because the
    # call site executes the body regardless of the outcome.
    from taskq_api.errors import Problem

    with pytest.raises(Problem) as excinfo:
        require_api_key("bogus_key_does_not_resolve")
    assert excinfo.value.status == 401


def test_lifespan_finally_invokes_runner_drain(monkeypatch):
    """app.py:255-258 — lifespan shutdown awaits ``runner.drain``.

    Gate-1 coverage for ``taskq_api.app`` requires the
    ``try / yield / finally: await runner.drain(...)`` block (lines
    255-258) to be executed end-to-end. FR-08 AC-8.3 + NFR-08 require
    the drain to fire on shutdown so in-flight subprocesses are given
    the ``TASKQ_DRAIN_TIMEOUT`` budget to complete. This spy confirms
    that contract.
    """
    from taskq_api.service import runner as runner_module

    drain_calls: list[float] = []
    original_drain = runner_module.drain

    async def _spy_drain(timeout: float):
        drain_calls.append(timeout)
        return await original_drain(timeout)

    async def _exercise_lifespan():
        monkeypatched_app = create_app()
        import taskq_api.app as app_module

        original_runner = app_module.runner
        app_module.runner = runner_module
        runner_module.drain = _spy_drain  # type: ignore[assignment]
        try:
            async with monkeypatched_app.router.lifespan_context(monkeypatched_app):
                pass  # no in-flight work — drain returns immediately
        finally:
            runner_module.drain = original_drain  # type: ignore[assignment]
            app_module.runner = original_runner

    asyncio.run(_exercise_lifespan())

    assert len(drain_calls) == 1, (
        f"lifespan finally must invoke runner.drain exactly once; "
        f"saw {len(drain_calls)} calls"
    )