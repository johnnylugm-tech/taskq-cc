"""[FR-01] RFC 7807 problem+json error builders.

Independence module — no sibling imports except ``taskq_api.config``
for correlation_id propagation.

Citations: SPEC.md §3 FR-10 (error contract); SAD.md §2.2 L0 errors.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from taskq_api.config import get_settings


class Problem(Exception):
    """Domain exception carrying a structured RFC 7807 problem body."""

    def __init__(
        self,
        status: int,
        title: str,
        detail: str,
        type_uri: str = "about:blank",
    ) -> None:
        self.status = status
        self.title = title
        self.detail = detail
        self.type_uri = type_uri
        super().__init__(detail)

    def to_response(self, correlation_id: str) -> JSONResponse:
        body = {
            "type": self.type_uri,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "instance": "",
            "correlation_id": correlation_id,
        }
        return JSONResponse(
            status_code=self.status,
            content=body,
            media_type="application/problem+json",
            headers={"X-Correlation-Id": correlation_id},
        )


def correlation_id_for(request: Request | None = None) -> str:
    """Return correlation id from the request or mint a fresh one."""
    if request is not None:
        header_id = request.headers.get("X-Correlation-Id")
        if header_id:
            return header_id
    return uuid.uuid4().hex


def problem_response(
    status: int,
    title: str,
    detail: str,
    type_uri: str = "about:blank",
) -> JSONResponse:
    """Build a JSONResponse carrying an RFC 7807 body + correlation header."""
    cid = correlation_id_for()
    body: dict[str, Any] = {
        "type": type_uri,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": "",
        "correlation_id": cid,
    }
    return JSONResponse(
        status_code=status,
        content=body,
        media_type="application/problem+json",
        headers={"X-Correlation-Id": cid},
    )


def make_problem(
    status: int,
    title: str,
    detail: str,
    type_uri: str = "about:blank",
) -> Problem:
    return Problem(status=status, title=title, detail=detail, type_uri=type_uri)


__all__ = [
    "Problem",
    "problem_response",
    "correlation_id_for",
    "make_problem",
    "get_settings",
]