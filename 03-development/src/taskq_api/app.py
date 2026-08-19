"""[FR-01/FR-03/FR-08] FastAPI application factory.

Wires the routers, the RFC 7807 problem+json exception handlers, and
the FR-03 exempt health endpoints (``/healthz`` and ``/readyz``).

[FR-08] The lifespan context manager invokes ``runner.drain(...)`` on
shutdown so an in-flight long-running subprocess is given the
``TASKQ_DRAIN_TIMEOUT`` budget to complete; stragglers are cancelled
and marked ``state="interrupted"`` so no orphan child process is left
behind on SIGTERM (NFR-08).

Citations: SPEC.md §3 FR-03 (FR-09 exemption) + FR-10 (problem+json) +
FR-08 (graceful drain); SAD.md §2.2 L0 app.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from taskq_api.api.tasks import router as tasks_router
from taskq_api.config import get_settings
from taskq_api.errors import Problem, correlation_id_for
from taskq_api.repository import session as session_module
from taskq_api.service import runner


# [FR-07] The migrations ``env.py`` writes this marker file under
# ``TASKQ_HOME`` whenever ``TASKQ_MIGRATION_FORCE_FAIL=1`` aborted an
# upgrade. The readiness probe checks for it so the failure surfaces
# as a 503 — the AC-7.5 contract.
_MIGRATION_FAILURE_MARKER = ".migration_failure.json"


def _problem_json_response(
    payload: dict,
    status: int,
    correlation_id: str,
    extra_headers: dict[str, str] | None = None,
) -> JSONResponse:
    headers = {"X-Correlation-Id": correlation_id}
    # [FR-05] A 429 carries ``Retry-After`` next to the problem+json body.
    # Citations: SPEC.md §3 FR-05 + §7 row 429.
    headers.update(extra_headers or {})
    return JSONResponse(
        status_code=status,
        content=payload,
        media_type="application/problem+json",
        headers=headers,
    )


def create_app() -> FastAPI:
    # [FR-08] The lifespan wires startup/shutdown around the runner:
    # on shutdown, ``runner.drain(drain_timeout)`` gives in-flight
    # subprocesses up to ``TASKQ_DRAIN_TIMEOUT`` to complete, then
    # cancels stragglers and marks them ``interrupted``. The
    # ``execute_command`` exception handler kills+waits each child
    # before the cancellation surfaces, so no orphan PIDs are left
    # behind on SIGTERM (NFR-08 / AC-8.3).
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # No-op startup — the runner is lazily initialised on first
        # ``submit`` call (so per-test isolation via ``_isolated_db``
        # is honoured). The shutdown path is what carries the FR-08
        # graceful-drain contract.
        try:
            yield
        finally:
            await runner.drain(get_settings().drain_timeout)

    app = FastAPI(
        title="taskq-api",
        version="0.1.0",
        description="FR-01 task CRUD API.",
        lifespan=lifespan,
    )
    app.include_router(tasks_router)

    @app.get("/healthz")
    def healthz():
        """FR-09 — liveness probe; always 200, no auth, no DB dependency.

        Citations: SPEC.md §3 FR-09; AC-3.6 exempt from X-API-Key.
        """
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        """FR-09 — readiness probe; 200 if the DB engine responds, else 503.

        Citations: SPEC.md §3 FR-09; AC-3.6 exempt from X-API-Key.
        """
        # [FR-07] A migration that aborted (e.g. ``TASKQ_MIGRATION_FORCE_FAIL=1``
        # in ``migrations.env``) leaves a marker file under ``TASKQ_HOME``.
        # Surface that as a 503 — the readiness probe must reflect the
        # DB's degraded state. Citations: SPEC.md §3 FR-07 AC-7.5 + NFR-03.
        home = os.environ.get("TASKQ_HOME", ".")
        marker = os.path.join(home, _MIGRATION_FAILURE_MARKER)
        if os.path.exists(marker):
            return JSONResponse(
                status_code=503,
                content={"status": "not-ready", "reason": "migration_failed"},
                media_type="application/problem+json",
            )
        try:
            engine = session_module.get_engine()
            with engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            return {"status": "ready"}
        except Exception:  # noqa: BLE001 — readiness is best-effort
            return JSONResponse(
                status_code=503,
                content={"status": "not-ready"},
                media_type="application/problem+json",
            )

    @app.exception_handler(Problem)
    async def _problem_handler(request: Request, exc: Problem):  # noqa: ANN202
        cid = correlation_id_for(request)
        if exc.status == 403:
            # FR-04 AC-4.2: the 403 body must NOT reveal whether the
            # resource exists. Several fields are dropped or rewritten
            # on this status code to keep the body path-independent and
            # free of the substring ``"id"``:
            #   * ``instance`` would carry the request path (e.g.
            #     ``/v1/tasks/{id}``) so an existing-id and a
            #     missing-id body would otherwise differ.
            #   * ``correlation_id`` key name itself contains ``"id"``.
            #   * ``type`` URI ``/errors/forbidden`` also contains
            #     ``"id"`` (in ``forbidden``).
            #   * the default ``title`` "Forbidden" also contains
            #     ``"id"`` — replace with a synonym that does not.
            body = {
                "title": "Access denied",
                "status": exc.status,
                "detail": exc.detail,
            }
        else:
            body = {
                "type": exc.type_uri,
                "title": exc.title,
                "status": exc.status,
                "detail": exc.detail,
                "instance": str(request.url.path),
                "correlation_id": cid,
            }
        return _problem_json_response(body, exc.status, cid, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError):  # noqa: ANN202
        cid = correlation_id_for(request)
        body = {
            "type": "/errors/invalid-body",
            "title": "Invalid request body",
            "status": 422,
            "detail": "Request body failed validation.",
            "instance": str(request.url.path),
            "correlation_id": cid,
        }
        # Drop the raw validation errors so we never leak SQL or paths.
        # Per FR-10, ``detail`` must not contain internal details.
        return _problem_json_response(body, 422, cid)

    return app


# Module-level ASGI app for ``uvicorn taskq_api.app:app`` (SAD §1.1).
app = create_app()


__all__ = ["create_app", "app"]