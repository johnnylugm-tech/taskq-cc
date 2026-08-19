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
from taskq_api.service import auth


WRITE_KEY = "write_key"
EXISTING_ID = 1
MISSING_ID = 999


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


def test_ac_4_1_insufficient_scope_returns_403_problem_json():
    """AC-4.1 — a ``write`` key against the ``admin``-only DELETE returns 403 + problem+json."""
    response = _request("DELETE", f"/v1/tasks/{EXISTING_ID}", WRITE_KEY)
    result = {
        "status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
    }
    # FR04-403 (applies_to 1)
    assert result["status"] == 403
    assert "problem+json" in result["content_type"]


def test_ac_4_2_403_body_does_not_reveal_resource_existence():
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


def test_ac_4_3_all_v1_routes_resolve_through_single_auth_dependency():
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


def test_sec_t04_write_scope_denies_admin_endpoint_403():
    """SEC-T-04 — a ``write`` scope key is denied on an ``admin`` endpoint with 403."""
    response = _request("DELETE", f"/v1/tasks/{EXISTING_ID}", WRITE_KEY)
    result = {"status": response.status_code}
    # FR04-403 (applies_to 5)
    assert result["status"] == 403
    # Hierarchical scope: write < admin, and the decision is the shared one.
    assert auth.has_scope("write", "admin") is False
    assert auth.has_scope("admin", "write") is True
