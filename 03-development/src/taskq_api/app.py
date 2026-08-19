"""[FR-01] FastAPI application factory.

Wires the routers and problem+json exception handler. Other routes
(``/healthz``, ``/readyz``, ``/v1/metrics``) ship in later FRs.

Citations: SPEC.md §3 FR-09 + FR-10; SAD.md §2.2 L0 app.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from taskq_api.api.tasks import router as tasks_router
from taskq_api.errors import Problem, correlation_id_for


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

    @app.exception_handler(Problem)
    async def _problem_handler(request: Request, exc: Problem):  # noqa: ANN202
        cid = correlation_id_for(request)
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