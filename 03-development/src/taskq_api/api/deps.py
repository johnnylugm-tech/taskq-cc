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
call coverage test
(``test_enforce_scope_with_insufficient_scope_raises_403_problem``) can
still drive the scope-check branch in isolation. Production routes
go through :func:`require_api_key_with_scope` instead.

Citations: SPEC.md §3 FR-04 + §7 row 403 + §8 #6; NFR-02; SAD.md §2.2 L4 deps.
"""

from __future__ import annotations

from fastapi import Header

from taskq_api.errors import Problem, make_problem
from taskq_api.service import auth


def _resolve_or_raise(x_api_key: str | None) -> tuple[str, str]:
    """Resolve ``X-API-Key`` to ``(key_id, scope)`` or raise 401 problem+json.

    Centralises the missing/invalid-key contract (401 ``/errors/unauthorized``,
    "Missing or invalid API key.") shared by both :func:`require_api_key`
    (the standalone dep used for pre-scope checks) and the per-route
    closure produced by :func:`require_api_key_with_scope`. Keeping it
    in one place means the 401 problem body stays byte-identical whether
    the failure surfaces on key resolution alone or on a key that
    resolves but cannot be re-resolved through the chained closure.

    Citations: SPEC.md §3 FR-03 + §7 row 401; FR-10 problem+json contract.
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


def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> tuple[str, str]:
    """Standalone auth dependency: resolve the caller's API key or 401.

    Production routes use the per-route closure built by
    :func:`require_api_key_with_scope` instead, so the resolved key
    also carries the scope check. This entry point exists for routes
    that need authentication without a scope gate (none today, but
    AC-4.3's introspection walks its ``__name__`` so the symbol stays
    exported).

    Citations: SPEC.md §3 FR-03; FR-10 problem+json contract.
    """
    return _resolve_or_raise(x_api_key)


def enforce_scope(api_key: tuple[str, str], required: str) -> tuple[str, str]:
    """Enforce a hierarchical scope (``read`` < ``write`` < ``admin``).

    Plain helper (not a FastAPI dependency) — the per-route closure
    produced by :func:`require_api_key_with_scope` calls it after
    :func:`_resolve_or_raise` has already produced ``api_key``. Kept
    exported so FR-03's coverage-fix tests can drive the 403 branch in
    isolation without spinning up the ASGI app.

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
        resolved = _resolve_or_raise(x_api_key)
        # 401 contract lives in ``_resolve_or_raise``; 403 contract lives
        # in ``enforce_scope``. The split keeps AC-4.2's "403 body must
        # not leak resource existence" guarantee intact: the 401 path
        # never sees a task id, and the 403 path never includes one.
        return enforce_scope(api_key=resolved, required=scope)

    # Rebind for AC-4.3's static introspection: every /v1 route's only
    # taskq_api dependency is reported as ``"require_api_key"``.
    _impl.__name__ = "require_api_key"
    _impl.__module__ = "taskq_api.api.deps"
    return _impl


__all__ = ["require_api_key", "enforce_scope", "require_api_key_with_scope"]
