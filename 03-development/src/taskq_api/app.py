"""[FR-01/FR-03/FR-08/FR-09/FR-10] FastAPI application factory.

Wires the routers, the RFC 7807 problem+json exception handlers, and
the FR-09 health endpoints (``/healthz``, ``/readyz``,
``/v1/metrics``).

[FR-08] The lifespan context manager invokes ``runner.drain(...)`` on
shutdown so an in-flight long-running subprocess is given the
``TASKQ_DRAIN_TIMEOUT`` budget to complete; stragglers are cancelled
and marked ``state="interrupted"`` so no orphan child process is left
behind on SIGTERM (NFR-08).

[FR-10] The exception surface is split into two exception handlers
plus a catch-all ASGI middleware:

  * :class:`Problem` — every domain-raised RFC 7807 problem (401/403/
    404/409/429/...) is wrapped in a six-field body with the
    ``X-Correlation-Id`` header re-emitted alongside.
  * :class:`RequestValidationError` — FastAPI's 422 envelope is
    rewritten into the same six-field shape so the body type stays
    consistent across every non-2xx.
  * :class:`_ProblemErrorMiddleware` — a catch-all ASGI middleware
    that runs inside Starlette's :class:`ServerErrorMiddleware` (which
    always re-raises) and outside the :class:`ExceptionMiddleware`, so
    it can mask any unhandled ``Exception`` into a 500 + problem+json
    WITHOUT re-raising to the transport — the response ``detail`` is
    scrubbed of the AC-10.2 / SEC-T-05 denylist substrings
    (``Traceback``, ``SQL``, ``/Users``). Because
    :class:`asyncio.CancelledError` is a ``BaseException`` subclass it
    falls through the middleware's ``except Exception`` and propagates
    to the server untouched (FR-10 AC-10.5 / NFR-03).

Every non-2xx response emits a ``logging`` record carrying the
``correlation_id`` so the response header can be stitched back to the
server log (FR-10 AC-10.3 / NFR-09).

Citations: SPEC.md §3 FR-03 (FR-09 exemption) + FR-08 (graceful drain)
+ FR-09 (health routes) + FR-10 (problem+json + log + cancellation);
SAD.md §2.2 L0 app; SEC-T-05 (information disclosure); NFR-03
(cancellation propagation); NFR-09 (correlation stitching).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from taskq_api.api.health import router as health_router
from taskq_api.api.tasks import router as tasks_router
from taskq_api.config import get_settings
from taskq_api.errors import Problem, correlation_id_for
from taskq_api.service import runner

# [FR-10] Substring denylist for the generic 500 handler. The
# AC-10.2 / SEC-T-05 contract requires that ``detail`` not leak
# stack traces, SQL fragments, or absolute filesystem paths. Any
# substring from this list appearing in the raw exception message is
# replaced by a generic sentinel so the response cannot be used as
# an information-disclosure sink.
_INTERNAL_DETAIL_DENYLIST: tuple[str, ...] = ("Traceback", "SQL", "/Users")

# [FR-10] The logger every FR-10 handler emits to. NFR-09 requires
# the ``correlation_id`` to appear in the server log for the same
# request that produced a problem+json response — this name is the
# single funnel so the AC-10.3 grep does not have to scan multiple
# logger names.
_logger = logging.getLogger("taskq_api.errors")


def _sanitize_detail(message: str) -> str:
    """Return ``message`` scrubbed of internal-detail substrings.

    A message containing any of the :data:`_INTERNAL_DETAIL_DENYLIST`
    substrings is replaced with the generic "Internal server error."
    sentinel so the 500 response cannot disclose stack traces, SQL,
    or absolute filesystem paths (AC-10.2 / SEC-T-05). The original
    message is logged via the FR-10 audit log (with
    ``correlation_id``) for operator triage — the body, by contrast,
    carries only the sanitised form.
    """
    for needle in _INTERNAL_DETAIL_DENYLIST:
        if needle in message:
            return "Internal server error."
    return message


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


def _log_problem(correlation_id: str, status: int, path: str) -> None:
    """[FR-10] Emit a log record carrying the request's ``correlation_id``.

    The record format is ``request_id=<cid> status=<n> path=<url>`` so
    a grep for the ``X-Correlation-Id`` response header matches the
    log line for the same request (AC-10.3 / NFR-09).
    """
    _logger.info(
        "request_id=%s status=%s path=%s",
        correlation_id,
        status,
        path,
    )


def _problem_body(
    *,
    type_uri: str,
    title: str,
    status: int,
    detail: str,
    instance: str,
    correlation_id: str,
) -> dict:
    """[FR-10] Build the canonical six-field RFC 7807 envelope.

    Centralises the envelope shape so every non-2xx response emits the
    same six keys (``type`` / ``title`` / ``status`` / ``detail`` /
    ``instance`` / ``correlation_id``) and a future field rename is a
    single edit. The 403 handler keeps its own minimal body inline
    because FR-04 AC-4.2 requires it to drop ``instance`` and
    ``correlation_id`` to keep the resource-existence footprint
    path-independent.
    """
    return {
        "type": type_uri,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
        "correlation_id": correlation_id,
    }


def _make_500_body(cid: str, raw_message: str, path: str) -> dict:
    """Build the canonical 500 problem+json body for the current request."""
    safe_detail = _sanitize_detail(raw_message)
    return _problem_body(
        type_uri="/errors/internal",
        title="Internal server error",
        status=500,
        detail=safe_detail,
        instance=path,
        correlation_id=cid,
    )


class _ProblemErrorMiddleware:
    """[FR-10] Catch-all ASGI middleware that masks unhandled exceptions.

    FastAPI routes the ``Exception`` / ``500`` handler keys to
    Starlette's :class:`ServerErrorMiddleware`, whose contract is to
    re-raise the exception AFTER producing the response (so servers can
    log it). Under ``httpx.ASGITransport`` that re-raise surfaces as a
    propagated exception instead of a ``500`` response, which breaks
    AC-10.2 / SEC-T-05. This middleware is registered as a user
    middleware — inside :class:`ServerErrorMiddleware` but outside
    :class:`ExceptionMiddleware` — so it can convert an unhandled
    ``Exception`` into a sanitised 500 + problem+json and SEND it
    without re-raising.

    ``asyncio.CancelledError`` is a ``BaseException`` subclass and is
    therefore NOT caught by ``except Exception``; it propagates to the
    transport untouched (FR-10 AC-10.5 / NFR-03).

    Citations: SPEC.md §3 FR-10 (problem+json + cancellation);
    SEC-T-05 (information disclosure); NFR-03 (cancellation
    propagation); NFR-09 (correlation stitching).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        except Exception as exc:  # noqa: BLE001 — catch-all 500 mask
            request = Request(scope, receive)
            cid = correlation_id_for(request)
            path = request.url.path
            raw_message = str(exc) or exc.__class__.__name__
            body = _make_500_body(cid, raw_message, path)
            _logger.exception(
                "request_id=%s status=500 path=%s raw_detail=%s",
                cid,
                path,
                raw_message,
            )
            response = _problem_json_response(body, 500, cid)
            await response(scope, receive, send)


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
    # [FR-09] The health router carries ``/healthz``, ``/readyz`` and
    # ``/v1/metrics``. The first two declare no FastAPI dependency so
    # they are exempt from X-API-Key (FR-09 / AC-3.6); the third
    # carries the admin-scope gate on its own Depends.
    app.include_router(health_router)

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
            body = _problem_body(
                type_uri=exc.type_uri,
                title=exc.title,
                status=exc.status,
                detail=exc.detail,
                instance=str(request.url.path),
                correlation_id=cid,
            )
        _log_problem(cid, exc.status, str(request.url.path))
        return _problem_json_response(body, exc.status, cid, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError):  # noqa: ANN202
        cid = correlation_id_for(request)
        body = _problem_body(
            type_uri="/errors/invalid-body",
            title="Invalid request body",
            status=422,
            detail="Request body failed validation.",
            instance=str(request.url.path),
            correlation_id=cid,
        )
        # Drop the raw validation errors so we never leak SQL or paths.
        # Per FR-10, ``detail`` must not contain internal details.
        _log_problem(cid, 422, str(request.url.path))
        return _problem_json_response(body, 422, cid)

    # [FR-10] AC-10.2 / SEC-T-05 — install the catch-all middleware
    # that masks unhandled ``Exception`` into a sanitised 500 +
    # problem+json WITHOUT re-raising (the re-raise of the
    # ``Exception``/``500`` handler keys would surface under
    # ``httpx.ASGITransport`` as a propagated exception, not a 500
    # response). ``asyncio.CancelledError`` is a ``BaseException`` and
    # falls through the middleware's ``except Exception``, so it
    # propagates to the transport (FR-10 AC-10.5 / NFR-03).
    app.add_middleware(_ProblemErrorMiddleware)

    return app


# Module-level ASGI app for ``uvicorn taskq_api.app:app`` (SAD §1.1).
app = create_app()


__all__ = ["create_app", "app"]
