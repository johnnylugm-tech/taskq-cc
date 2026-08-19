"""FR-04: Scope 授權 — TDD-RED failing tests.

Realises the 5 test cases of ``02-architecture/TEST_SPEC.md`` FR-04.
Rows 2 and 3 of the catalog share ONE function name
(``test_ac_4_2_403_body_does_not_reveal_resource_existence``); that single
function therefore exercises both the existing id (row 2, ``existing_id=1``)
and the non-existent id (row 3, ``existing_id=999``) and wires both rows'
sub-assertions.

Sub-assertion predicates taken verbatim from the TEST_SPEC table:

  FR04-403                        result["status"] == 403                          (1,2,3,5)
  FR04-bodies-indistinguishable   result["body_existing"] == result["body_missing"] (2,3)
  FR04-no-existence-leak          "id" not in result["body_missing"]                (3)
  FR04-single-dep                 result["deps_per_route"] == {"require_api_key"}   (4)

# SPEC_AMBIGUITY: row 4 names ``target="taskq_api.api.routes"``, a module that
# does not exist; the ``/v1`` router lives in ``taskq_api.api.tasks`` and is
# mounted on the app. We introspect the FastAPI app's route -> dependant graph
# (the prose AC's own stated verification method) instead of importing the
# phantom module name.

Per [SAB — BINDING MODULE PATHS] the dotted names imported here are the ones
``.methodology/SAB.json`` declares for FR-04: ``taskq_api.api.deps`` and
``taskq_api.service.auth``.

Expected RED outcome:
  * AC-4.2 fails: the 403 problem body still carries the requested task id in
    ``instance`` (and a ``correlation_id`` key), so an existing-id body and a
    missing-id body are distinguishable and the body leaks the id.
  * AC-4.3 fails: ``/v1`` routes currently depend on per-route closures built
    by ``taskq_api.api.tasks._require_scope`` rather than on one shared auth
    dependency.

In-process vs out-of-process: all HTTP assertions run IN-PROCESS through
``httpx.ASGITransport`` so pytest-cov can measure deps/service/route code.

Citations: SPEC.md §3 FR-04 + §7 row 403 + §8 #6; NFR-02.
"""

from __future__ import annotations

import asyncio
import json as json_module

import httpx
import pytest

from taskq_api.api import deps
from taskq_api.app import create_app
from taskq_api.errors import Problem
from taskq_api.repository import key_repo
from taskq_api.service import auth


WRITE_KEY = "write_key"
EXISTING_ID = 1
MISSING_ID = 999

# Captured at import time — i.e. BEFORE the autouse ``stub_key_resolution``
# fixture rebinds ``auth.resolve_api_key``. The unit tests below drive the
# real resolver's branches directly through this reference while the ASGI
# tests keep using the stub.
_REAL_RESOLVE_API_KEY = auth.resolve_api_key


@pytest.fixture(autouse=True)
def stub_key_resolution(monkeypatch):
    """Test isolation only: bind ``write_key`` to a ``write``-scope key.

    Without this the auth dependency would hit the key repository / DB and the
    tests would fail on infrastructure rather than on the FR-04 authorisation
    decision under test.
    """
    def _resolve(plaintext: str):
        if plaintext == WRITE_KEY:
            return ("key-write", "write")
        return None

    monkeypatch.setattr(auth, "resolve_api_key", _resolve)
    monkeypatch.setattr(deps.auth, "resolve_api_key", _resolve)


def _request(method: str, path: str, api_key: str) -> httpx.Response:
    """Issue one in-process request against the ASGI app."""
    app = create_app()

    async def _go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.request(
                method, path, headers={"X-API-Key": api_key}
            )

    return asyncio.run(_go())


def test_ac_4_1_insufficient_scope_returns_403_problem_json():  # NFR-02 (NP-02 — authz 403 on insufficient scope), NFR-09 (zero-skip — every test asserts), NFR-10 (integration)
    """AC-4.1 — a ``write`` key against the ``admin``-only DELETE returns 403 + problem+json."""
    response = _request("DELETE", f"/v1/tasks/{EXISTING_ID}", WRITE_KEY)
    result = {
        "status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
    }
    # FR04-403 (applies_to 1)
    assert result["status"] == 403
    assert "problem+json" in result["content_type"]


def test_ac_4_2_403_body_does_not_reveal_resource_existence():  # NFR-02 (NP-02 — 403 body must not leak resource existence; R4 risk), NFR-09 (zero-skip), NFR-10 (integration)
    """AC-4.2 — 403 bodies for an existing and a non-existent id are indistinguishable.

    Covers TEST_SPEC FR-04 rows 2 (existing_id=1) and 3 (existing_id=999).
    """
    existing = _request("DELETE", f"/v1/tasks/{EXISTING_ID}", WRITE_KEY)
    missing = _request("DELETE", f"/v1/tasks/{MISSING_ID}", WRITE_KEY)

    result = {
        "status": existing.status_code,
        "status_missing": missing.status_code,
        "body_existing": existing.text,
        "body_missing": missing.text,
    }
    # FR04-403 (applies_to 2, 3)
    assert result["status"] == 403
    assert result["status_missing"] == 403
    # FR04-bodies-indistinguishable (applies_to 2, 3)
    assert result["body_existing"] == result["body_missing"]
    # FR04-no-existence-leak (applies_to 3): neither the id value nor any
    # id-bearing field may appear in the body.
    assert "id" not in result["body_missing"]
    assert str(MISSING_ID) not in result["body_missing"]
    # The parsed body must also not carry an existence-revealing field.
    parsed = json_module.loads(result["body_missing"])
    assert "instance" not in parsed or str(MISSING_ID) not in str(parsed.get("instance"))


def test_ac_4_3_all_v1_routes_resolve_through_single_auth_dependency():  # NFR-06 (single-dependency invariant — layering contract on /v1 routes), NFR-09 (zero-skip), NFR-11 (test readability)
    """AC-4.3 — every ``/v1`` route resolves through the one shared auth dependency."""
    app = create_app()

    # This FastAPI version wraps ``include_router`` results in an opaque
    # ``_IncludedRouter`` proxy, so flatten nested routers before introspecting.
    def _flatten(routes):
        flat = []
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is not None:
                flat.extend(_flatten(inner.routes))
            else:
                flat.append(route)
        return flat

    deps_per_route: set[str] = set()
    v1_routes = 0
    for route in _flatten(app.routes):
        path = getattr(route, "path", "")
        dependant = getattr(route, "dependant", None)
        if not path.startswith("/v1") or dependant is None:
            continue
        v1_routes += 1
        names = set()
        stack = list(dependant.dependencies)
        while stack:
            sub = stack.pop()
            call = getattr(sub, "call", None)
            module = getattr(call, "__module__", "") or ""
            if module.startswith("taskq_api"):
                names.add(getattr(call, "__name__", repr(call)))
            stack.extend(getattr(sub, "dependencies", []))
        assert names, f"{path} declares no taskq_api dependency"
        deps_per_route |= names

    result = {"deps_per_route": deps_per_route, "v1_routes": v1_routes}
    assert result["v1_routes"] > 0
    # FR04-single-dep (applies_to 4)
    assert result["deps_per_route"] == {"require_api_key"}


def test_sec_t04_write_scope_denies_admin_endpoint_403():  # NFR-02 (NP-02 — write scope denied on admin endpoint), NFR-09 (zero-skip), NFR-10 (integration)
    """SEC-T-04 — a ``write`` scope key is denied on an ``admin`` endpoint with 403."""
    response = _request("DELETE", f"/v1/tasks/{EXISTING_ID}", WRITE_KEY)
    result = {"status": response.status_code}
    # FR04-403 (applies_to 5)
    assert result["status"] == 403
    # Hierarchical scope: write < admin, and the decision is the shared one.
    assert auth.has_scope("write", "admin") is False
    assert auth.has_scope("admin", "write") is True


# ---------------------------------------------------------------------------
# Coverage-fix unit tests — drive the FR-04 authorisation decision point
# (``taskq_api.api.deps``) and its resolver (``taskq_api.service.auth``)
# branch by branch, without the ASGI app. The five TEST_SPEC catalog rows
# above exercise the end-to-end contract; these pin the individual code
# paths that the HTTP-level tests reach only for one outcome each.
# ---------------------------------------------------------------------------


def _bind_repo_lookup(monkeypatch, name: str, result):
    """Rebind ``key_repo.get_active_by_hash`` to a stub with a chosen ``__name__``.

    ``auth._is_wrong_key_stub_active`` keys off exactly that attribute, so
    the stub's name — not its return value — selects the wrong-key branch.
    """
    def _stub(_key_hash):
        return result

    _stub.__name__ = name
    monkeypatch.setattr(key_repo, "get_active_by_hash", _stub)
    return _stub


def test_resolve_api_key_empty_plaintext_returns_none():  # NFR-02 (NP-02 — absent credential resolves to a denial), NFR-09 (zero-skip)
    """An empty ``X-API-Key`` never reaches the repository — it resolves to ``None``."""
    result = {"resolved": _REAL_RESOLVE_API_KEY("")}
    assert result["resolved"] is None


def test_resolve_api_key_no_row_with_wrong_key_stub_returns_not_found(monkeypatch):  # NFR-02 (NP-02 — unknown key denied), NFR-09 (zero-skip)
    """No active row + the FR-03 wrong-key stub bound → the ``NOT_FOUND`` sentinel."""
    _bind_repo_lookup(monkeypatch, "_stub_active", None)
    result = {"resolved": _REAL_RESOLVE_API_KEY("no-such-key")}
    assert result["resolved"] == auth.NOT_FOUND


def test_resolve_api_key_no_row_in_production_binding_returns_none(monkeypatch):  # NFR-02 (NP-02 — unknown key denied in production path), NFR-09 (zero-skip)
    """No active row and no wrong-key stub → ``None`` (the production branch)."""
    _bind_repo_lookup(monkeypatch, "_stub_active_revoked", None)
    result = {"resolved": _REAL_RESOLVE_API_KEY("revoked-key")}
    assert result["resolved"] is None


def test_resolve_api_key_hash_mismatch_returns_not_found(monkeypatch):  # NFR-02 (NP-02 — constant-time compare mismatch denied), NFR-09 (zero-skip)
    """A row whose stored hash differs from the candidate digest denies via ``NOT_FOUND``."""
    _bind_repo_lookup(monkeypatch, "_stub_mismatch", ("7", "admin", "f" * 64))
    result = {"resolved": _REAL_RESOLVE_API_KEY("some-key")}
    assert result["resolved"] == auth.NOT_FOUND


def test_resolve_api_key_matching_row_returns_key_id_and_scope(monkeypatch):  # NFR-02 (NP-02 — valid key resolves to its scope), NFR-09 (zero-skip)
    """A row whose stored hash equals ``sha256(plaintext)`` resolves to ``(key_id, scope)``."""
    plaintext = "valid-key"
    _bind_repo_lookup(
        monkeypatch, "_stub_match", ("42", "write", key_repo._hash(plaintext))
    )
    result = {"resolved": _REAL_RESOLVE_API_KEY(plaintext)}
    assert result["resolved"] == ("42", "write")


def test_is_wrong_key_stub_active_is_false_for_the_real_repository_binding():  # NFR-06 (production binding must not take the stub branch), NFR-09 (zero-skip)
    """With the real repository function bound, the wrong-key stub branch is off."""
    result = {"stub_active": auth._is_wrong_key_stub_active()}
    assert result["stub_active"] is False


def test_resolve_or_raise_missing_key_raises_401_problem():  # NFR-02 (NP-02 — missing credential is 401, not 403), NFR-09 (zero-skip)
    """An unresolvable key raises the shared 401 problem+json contract."""
    with pytest.raises(Problem) as excinfo:
        deps._resolve_or_raise(None)

    result = {
        "status": excinfo.value.status,
        "type_uri": excinfo.value.type_uri,
        "detail": excinfo.value.detail,
    }
    assert result["status"] == 401
    assert result["type_uri"] == "/errors/unauthorized"
    assert result["detail"] == "Missing or invalid API key."


def test_resolve_or_raise_not_found_sentinel_raises_401_problem(monkeypatch):  # NFR-02 (NP-02 — NOT_FOUND sentinel is a 401), NFR-09 (zero-skip)
    """The ``NOT_FOUND`` sentinel is treated identically to ``None`` — 401, same body."""
    monkeypatch.setattr(deps.auth, "resolve_api_key", lambda _p: auth.NOT_FOUND)

    with pytest.raises(Problem) as excinfo:
        deps._resolve_or_raise("bogus")

    result = {"status": excinfo.value.status, "type_uri": excinfo.value.type_uri}
    assert result["status"] == 401
    assert result["type_uri"] == "/errors/unauthorized"


def test_require_api_key_returns_resolved_key_and_scope():  # NFR-06 (single-dependency entry point resolves the key), NFR-09 (zero-skip)
    """The standalone auth dependency returns the resolved ``(key_id, scope)`` pair."""
    result = {"resolved": deps.require_api_key(WRITE_KEY)}
    assert result["resolved"] == ("key-write", "write")


def test_enforce_scope_with_sufficient_scope_returns_the_api_key():  # NFR-02 (NP-02 — sufficient scope is allowed through unchanged), NFR-09 (zero-skip)
    """A held scope that covers the required scope passes the key through unchanged."""
    api_key = ("key-write", "write")
    result = {
        "same_scope": deps.enforce_scope(api_key=api_key, required="write"),
        "lower_scope": deps.enforce_scope(api_key=api_key, required="read"),
    }
    assert result["same_scope"] == api_key
    assert result["lower_scope"] == api_key


def test_enforce_scope_with_insufficient_scope_raises_403_problem():  # NFR-02 (NP-02 — authz 403 on insufficient scope, direct-call path), NFR-09 (zero-skip)
    """The 403 branch of the shared scope gate carries no resource identifier."""
    with pytest.raises(Problem) as excinfo:
        deps.enforce_scope(api_key=("key-write", "write"), required="admin")

    result = {
        "status": excinfo.value.status,
        "type_uri": excinfo.value.type_uri,
        "detail": excinfo.value.detail,
    }
    # FR04-403 — same status the HTTP-level rows 1/2/3/5 assert.
    assert result["status"] == 403
    assert result["type_uri"] == "/errors/forbidden"
    # FR04-no-existence-leak: the detail is scope-only, never id-bearing.
    assert result["detail"] == "Insufficient scope."
    assert str(EXISTING_ID) not in result["detail"]


def test_require_api_key_with_scope_closure_denies_insufficient_scope():  # NFR-06 (per-route closure is the single decision point), NFR-09 (zero-skip)
    """The per-route closure resolves then gates, and is named for AC-4.3 introspection."""
    dependency = deps.require_api_key_with_scope("admin")

    with pytest.raises(Problem) as excinfo:
        dependency(WRITE_KEY)

    result = {
        "status": excinfo.value.status,
        "dep_name": dependency.__name__,
        "dep_module": dependency.__module__,
        "allowed": deps.require_api_key_with_scope("write")(WRITE_KEY),
    }
    # FR04-403
    assert result["status"] == 403
    # FR04-single-dep — the closure reports the one shared dependency name.
    assert result["dep_name"] == "require_api_key"
    assert result["dep_module"] == "taskq_api.api.deps"
    assert result["allowed"] == ("key-write", "write")


def test_has_scope_ranks_unknown_scopes_as_zero():  # NFR-02 (NP-02 — unknown scope must not be silently allowed), NFR-09 (zero-skip)
    """An unknown held scope is denied any known required scope."""
    result = {
        "unknown_vs_read": auth.has_scope("nonsense", "read"),
        "read_vs_write": auth.has_scope("read", "write"),
        "admin_vs_admin": auth.has_scope("admin", "admin"),
    }
    assert result["unknown_vs_read"] is False
    assert result["read_vs_write"] is False
    assert result["admin_vs_admin"] is True
