"""[FR-10] RFC 7807 problem+json error builders.

Independence module — no sibling imports. Exposes the :class:`Problem`
domain exception, the :func:`make_problem` factory routes raise to
signal non-2xx responses, and :func:`correlation_id_for` so the
exception handlers in :mod:`taskq_api.app` can stamp the response
header + log line with the same id (AC-10.3 / NFR-09).

Citations: SPEC.md §3 FR-10 (error contract); SAD.md §2.2 L0 errors.
"""

# pragma: no error-handling  (exception class + factory — handlers live in app.py)

from __future__ import annotations

import uuid

from fastapi import Request


class Problem(Exception):
    """Domain exception carrying a structured RFC 7807 problem body.

    Raised by service / API code to signal a non-2xx response; the
    exception handlers in :mod:`taskq_api.app` translate it into a
    :class:`JSONResponse` with the standard six-field shape.
    """

    def __init__(
        self,
        status: int,
        title: str,
        detail: str,
        type_uri: str = "about:blank",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.title = title
        self.detail = detail
        self.type_uri = type_uri
        # [FR-05] Extra response headers carried by the problem — the
        # 429 contract requires a ``Retry-After`` alongside the
        # problem+json body, and the header must survive the exception
        # handler.
        # Citations: SPEC.md §3 FR-05 + §7 row 429.
        self.headers = headers or {}
        super().__init__(detail)


def correlation_id_for(request: Request | None = None) -> str:
    """Return the request's correlation id, or mint a fresh one.

    Honours an incoming ``X-Correlation-Id`` header so distributed
    traces stitch back together (AC-10.3 / NFR-09); falls back to a
    random hex when no header is present or no request is in scope.
    """
    if request is not None:
        header_id = request.headers.get("X-Correlation-Id")
        if header_id:
            return header_id
    return uuid.uuid4().hex


def make_problem(
    status: int,
    title: str,
    detail: str,
    type_uri: str = "about:blank",
    headers: dict[str, str] | None = None,
) -> Problem:
    """Construct a :class:`Problem` with the standard FR-10 attributes."""
    return Problem(
        status=status,
        title=title,
        detail=detail,
        type_uri=type_uri,
        headers=headers,
    )


__all__ = ["Problem", "make_problem", "correlation_id_for"]