"""[FR-09] Health checks and observability endpoints.

Three routes live here, each named for the SPEC.md §3 FR-09 row it
satisfies:

* :func:`healthz_route`     — liveness probe; always 200, no auth
* :func:`readyz_route`      — readiness probe; 200 if the DB engine
  answers ``SELECT 1`` AND alembic has reached ``head``, else 503 with
  a body that names the failing side
* :func:`metrics_route`     — admin-scope metrics view: task counts
  by status, execution latency p50/p95/p99, rate-limit denials

The module is registered as a FastAPI ``APIRouter`` so :mod:`taskq_api.app`
can ``include_router`` it. The router is mounted at the root for
``/healthz`` and ``/readyz`` (no auth), and at ``/v1`` for ``/metrics``
(which goes through the standard scope-gated dependency).

Citations: SPEC.md §3 FR-09 + §7 row 503 + §8 #10 / #11;
SAD.md §2.2 L4 api.health; SEC-T-05 (information disclosure — DB URL
password must not leak into /v1/metrics); NP-07 (DB outage → 503).
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from taskq_api.api.deps import require_api_key_with_scope
from taskq_api.config import get_settings
from taskq_api.errors import correlation_id_for
from taskq_api.repository import metrics as metrics_repo
from taskq_api.repository import session as session_module
from taskq_api.service import ratelimit as ratelimit_service

router = APIRouter()

# [FR-09] The migration failure marker the lifecycle stores under
# ``TASKQ_HOME`` whenever ``TASKQ_MIGRATION_FORCE_FAIL=1`` aborted an
# upgrade. The readiness probe checks for it so the failure surfaces
# as a 503 with ``detail="migration"``.
_MIGRATION_FAILURE_MARKER = ".migration_failure.json"

# [FR-09] Re-export the rate-limit denial counter maintained by
# :mod:`taskq_api.service.ratelimit`. The metrics handler reads from
# there; the counter is incremented by :mod:`taskq_api.api.deps`
# whenever the token bucket rejects a request. Living in the service
# layer (rather than in :mod:`api.health`) avoids a circular import
# between ``api.deps`` and ``api.health``.
# [FR-09] Import-time snapshot of the rate-limit denial counter, kept
# only as a legacy exported name. ``ratelimit.record_denial`` rebinds
# the *service* module global, so this int never changes — the metrics
# handler therefore reads ``ratelimit_service.denial_count`` live.
rate_limit_denials = ratelimit_service.denial_count


def _migration_head_revisions() -> frozenset[str]:
    """Return the set of alembic head revisions — leaves of the revision DAG.

    Walks every migration module the package ships and keeps only the
    revisions whose ``down_revision`` chain does NOT continue (i.e.
    nothing points to them as a predecessor). Reading at request time
    keeps the value honest against a future migration that bumps the
    head without restarting the process.

    We deliberately avoid importing the alembic runtime — the project
    pins alembic as a build tool but does not require the runtime API
    to be importable at request time.
    """
    # Lazy import so a process started without the migrations package
    # importable (e.g. a slim install that only ships the runtime) does
    # not fail at probe time.
    from migrations.versions import v1_initial, v2_tags, v3_split_results

    modules = (v1_initial, v2_tags, v3_split_results)
    all_revisions = {
        mod.revision for mod in modules if getattr(mod, "revision", None)
    }
    down_revisions = {
        getattr(mod, "down_revision", None) for mod in modules
    } - {None}
    return frozenset(all_revisions - down_revisions)


def _migration_is_at_head() -> bool:
    """Return ``True`` when ``alembic_version`` points at a revision head.

    Strategy: scan the configured DB for an ``alembic_version`` row
    whose ``version_num`` matches one of the heads discovered by
    :func:`_migration_head_revisions`. Reading the version table
    directly is faster than invoking alembic at every probe.
    """
    head_revisions = _migration_head_revisions()
    engine = session_module.get_engine()
    with engine.connect() as conn:
        # ``alembic_version`` is created by alembic on first upgrade.
        # If the table does not yet exist, the readiness probe MUST
        # fail closed — "no migrations applied" is a deployment error.
        rows = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='alembic_version'"
        ).fetchall()
        if not rows:
            return False
        version = conn.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    if version is None:
        return False
    return str(version[0]) in head_revisions


def _not_ready(detail: str) -> JSONResponse:
    """Return the standard 503 body for the readiness probe.

    All three failure modes (migration marker, DB unreachable, alembic
    not at head) produce the same shape — only the ``detail`` substring
    naming the failing side differs. Centralising the shape here keeps
    the bodies byte-identical so a client can parse one schema.

    [FR-10] The body carries the six RFC 7807 fields
    (``type`` / ``title`` / ``status`` / ``detail`` / ``instance`` /
    ``correlation_id``) plus the ``X-Correlation-Id`` header so the
    503 response is byte-shape-compatible with every other non-2xx
    emitted by the API (AC-10.1 + AC-10.3).
    """
    cid = correlation_id_for()
    body = {
        "type": "/errors/not-ready",
        "title": "Service not ready",
        "status": 503,
        "detail": detail,
        "instance": "/readyz",
        "correlation_id": cid,
    }
    return JSONResponse(
        status_code=503,
        content=body,
        media_type="application/problem+json",
        headers={"X-Correlation-Id": cid},
    )


@router.get("/healthz")
def healthz_route() -> dict[str, str]:
    """Liveness probe — always 200, no auth (FR-09 / AC-9.1).

    The probe MUST NOT touch the DB: a pod that has lost its DB
    connection is still alive and a liveness probe restart on a
    transient DB blip would make the outage worse.

    Citations: SPEC.md §3 FR-09 + §7 (no auth row for /healthz).
    """
    return {"status": "ok"}


@router.get("/readyz")
def readyz_route() -> Any:
    """Readiness probe — 200 if DB reachable AND alembic current == head (FR-09 / AC-9.2).

    Otherwise returns 503 with ``Content-Type: application/problem+json``
    whose ``detail`` names the failing side (``"db"`` for a DB outage,
    ``"migration"`` for an alembic head mismatch).

    The migration-failure marker file (written by the FR-07 lifecycle
    when ``TASKQ_MIGRATION_FORCE_FAIL=1`` aborts an upgrade) takes
    precedence over the DB probe — a half-applied migration is worse
    than a missing DB.

    Citations: SPEC.md §3 FR-09 + §7 row 503 + §8 #10 / #11; NFR-03
    (DB failure → 503 + explicit detail; no silent retry).
    """
    # 1. Surface a forced-failure marker as the highest-priority 503.
    home = get_settings().home
    if os.path.exists(os.path.join(home, _MIGRATION_FAILURE_MARKER)):
        return _not_ready("migration")

    # 2. DB reachability — must answer SELECT 1 without error.
    try:
        engine = session_module.get_engine()
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
    except Exception:
        return _not_ready("db")

    # 3. Alembic current must equal head — fail closed on a mismatch.
    try:
        if not _migration_is_at_head():
            return _not_ready("migration")
    except Exception:
        # An exception during the alembic probe (e.g. table missing
        # entirely) is a migration failure from the probe's perspective.
        return _not_ready("migration")

    return {"status": "ready"}


@router.get(
    "/v1/metrics",
    dependencies=[Depends(require_api_key_with_scope("admin"))],
)
def metrics_route() -> dict[str, Any]:
    """Admin-scope metrics view (FR-09 / AC-9.3).

    Returns five fields — the ``latency_p50/p95/p99`` triple and the
    ``rate_limit_denials`` counter — in the order documented in
    TEST_SPEC.md FR-09 row 4. The body MUST NOT echo the DB URL or any
    userinfo substring; the auth dependency strips those before this
    handler runs, and we do not interpolate settings here.

    Citations: SPEC.md §3 FR-09; FR-04 admin scope gate; SEC-T-05.
    """
    # [FR-09 / SEC-T-05] If the DB engine cannot be built at all (e.g.
    # the configured ``TASKQ_DB_URL`` names a driver that is not
    # installed), return an empty payload rather than letting the
    # ``create_engine`` failure propagate. Propagation would surface the
    # raw URL inside a stack-trace log line and inside any 500 body
    # the framework assembles — both of which are exactly the leaks
    # this endpoint is supposed to prevent.
    try:
        task_counts = dict(metrics_repo.task_counts_by_status())
    except Exception:  # noqa: BLE001 — metrics must never 500
        task_counts = {}
    try:
        p50, p95, p99 = metrics_repo.latency_percentiles()
    except Exception:  # noqa: BLE001 — metrics must never 500
        p50 = p95 = p99 = 0.0
    return {
        "task_counts": task_counts,
        "latency_p50": p50,
        "latency_p95": p95,
        "latency_p99": p99,
        "rate_limit_denials": ratelimit_service.denial_count,
    }


__all__ = [
    "router",
    "rate_limit_denials",
]