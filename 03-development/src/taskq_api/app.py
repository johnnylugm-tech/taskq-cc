"""[FR-01/FR-03] FastAPI application factory.

Wires the routers, the RFC 7807 problem+json exception handlers, and
the FR-03 exempt health endpoints (``/healthz`` and ``/readyz``).

Citations: SPEC.md §3 FR-03 (FR-09 exemption) + FR-10 (problem+json);
SAD.md §2.2 L0 app.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from taskq_api.api.tasks import router as tasks_router
from taskq_api.errors import Problem, correlation_id_for
from taskq_api.repository import session as session_module


def _problem_json_response(payload: dict, status: int, correlation_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=payload,
        media_type="application/problem+json",
        headers={"X-Correlation-Id": correlation_id},
    )


def create_app() -> FastAPI:
    app = FastAPI(
        title="taskq-api",
        version="0.1.0",
        description="FR-01 task CRUD API.",
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
        return _problem_json_response(body, exc.status, cid)

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