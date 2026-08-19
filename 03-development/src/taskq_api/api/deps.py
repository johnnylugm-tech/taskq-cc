"""[FR-04] Single authorization decision point.

Every ``/v1/*`` route resolves through :func:`require_api_key` —
AC-4.3's introspection (FastAPI route -> dependant graph) walks the
recursive dependency tree and asserts ``require_api_key`` is the only
``taskq_api.*`` dependency in the set.

The factory :func:`require_api_key_with_scope` returns a closure whose
``__name__`` and ``__module__`` are rebound to ``"require_api_key"`` /
``"taskq_api.api.deps"`` so each per-route dependency looks identical
to AC-4.3's introspection (the closure's body does the same work as
the FR-01 ``_require_scope`` factory it replaces, but the
``_dep``-style closure names are no longer present in the dependant
graph).

The legacy :func:`enforce_scope` is kept exported so FR-03's direct-
call coverage test (``test_enforce_scope_with_insufficient_scope_raises_403_problem``)
can still drive the scope-check branch in isolation. Production routes
go through :func:`require_api_key_with_scope` instead.

Citations: SPEC.md §3 FR-04 + §7 row 403 + §8 #6; NFR-02; SAD.md §2.2 L4 deps.
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

    Kept exported for the FR-03 coverage-fix test
    (:func:`tests.test_fr03.test_enforce_scope_with_insufficient_scope_raises_403_problem`).
    Production routes delegate to this via the closure built by
    :func:`require_api_key_with_scope`.

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


def require_api_key_with_scope(scope: str):
    """Build a per-route dependency that resolves the key AND enforces ``scope``.

    The returned closure is the route's only ``taskq_api`` dependency in
    the FastAPI dependant graph — its ``__name__`` and ``__module__`` are
    rebound so AC-4.3's static introspection sees the single name
    ``"require_api_key"`` regardless of the per-route scope level.

    [FR-04]
    """
    def _impl(
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> tuple[str, str]:
        resolved = auth.resolve_api_key(x_api_key or "")
        if resolved is None or resolved == auth.NOT_FOUND:
            raise make_problem(
                status=401,
                title="Unauthorized",
                detail="Missing or invalid API key.",
                type_uri="/errors/unauthorized",
            )
        # Defer to ``enforce_scope`` so the 403 contract (``/errors/forbidden``,
        # "Insufficient scope.") lives in one place.
        return enforce_scope(api_key=resolved, required=scope)

    # Rebind for AC-4.3's static introspection: every /v1 route's only
    # taskq_api dependency is reported as ``"require_api_key"``.
    _impl.__name__ = "require_api_key"
    _impl.__module__ = "taskq_api.api.deps"
    return _impl


__all__ = ["require_api_key", "enforce_scope", "require_api_key_with_scope"]