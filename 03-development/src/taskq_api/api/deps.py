"""[FR-01] Single authorization decision point (FR-04).

Every ``/v1/*`` route depends on ``require_api_key``. Scope checks happen
in the route bodies via ``enforce_scope`` so the dependency tree is
linear and testable.

Citations: SPEC.md §3 FR-03 / FR-04; SAD.md §2.2 L4 deps.
"""

from __future__ import annotations

from fastapi import Depends, Header

from taskq_api.errors import make_problem
from taskq_api.service import auth


def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> tuple[str, str]:
    """Resolve the caller's API key, raising 401 on miss.

    Citations: SPEC.md §3 FR-03; FR-10 problem+json contract.
    """
    resolved = auth.resolve_api_key(x_api_key or "")
    # ``resolve_api_key`` returns ``None`` for an empty header and the
    # ``auth.NOT_FOUND`` sentinel tuple when no row matches the candidate
    # digest — both are "missing or invalid" from the route's view.
    if resolved is None or resolved == auth.NOT_FOUND:
        raise make_problem(
            status=401,
            title="Unauthorized",
            detail="Missing or invalid API key.",
            type_uri="/errors/unauthorized",
        )
    return resolved


def enforce_scope(
    api_key: tuple[str, str] = Depends(require_api_key),
    required: str = "read",
) -> tuple[str, str]:
    """Enforce a hierarchical scope (``read`` < ``write`` < ``admin``).

    Citations: SPEC.md §3 FR-04.
    """
    _, held_scope = api_key
    if not auth.has_scope(held_scope, required):
        raise make_problem(
            status=403,
            title="Forbidden",
            detail="Insufficient scope.",
            type_uri="/errors/forbidden",
        )
    return api_key


__all__ = ["require_api_key", "enforce_scope"]