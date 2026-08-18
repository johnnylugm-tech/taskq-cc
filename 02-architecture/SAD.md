# Software Architecture Document (SAD) — taskq-api

> Round 2 harness-methodology validation bed: ASGI REST service with real persistence, real schema migration, async background execution, and API key authentication.

## 1. Architecture Overview

`taskq-api` is a Python 3.11 ASGI service that exposes a task queue over HTTP. The system accepts task definitions, persists them via SQLAlchemy ORM, evolves schema through Alembic across three revisions, executes tasks asynchronously via `asyncio`, and authenticates/authorizes every `/v1/*` call with hashed API keys.

The architecture is a strict four-layer pyramid:

```
api  ─┐
service ─┤   api > service > repository > models
repository ─┤
models  ─┘
```

`config` and `errors` are independence modules; **only `repository/` may import `sqlalchemy`**. `migrations/` ships alongside `models/` but is consumed by Alembic, not imported at runtime.

Key architectural invariants:

- **One module = one concern.** No god-modules; `service/runner.py` is the only module allowed to launch subprocesses; `service/auth.py` is the only module allowed to validate API keys; `api/deps.py` is the single authorization decision point.
- **Boundary-enforced ORM leakage prevention.** ORM imports in `service/` or `api/` are an import-linter forbidden-contract violation and a CI failure (NFR-06).
- **Real persistence, no doubles.** NFR-09 forbids skip/xfail for the migration round-trip; `migrations/versions/v3_split_results.py` performs a data-preserving move from `tasks.result_json` to `task_results`.
- **Fail-closed readiness.** `/readyz` returns 503 when the database is unreachable OR `alembic current` ≠ head — the deploy path is closed until migrations run.

### 1.1 System Verification Target

> **Every exit gate (2, 3 and 4)**: the harness executes `make verify-system`. A non-zero exit fails the gate. The target name is fixed — the harness always calls `make verify-system`.
>
> This is the only check in the whole framework that runs the delivered system. Everything else reads your source text or runs your test suite, both of which your test doubles configure. Two rules follow, and the gate enforces both:
>
> 1. At least one step must invoke the delivered entry point — `uvicorn taskq_api.app:app` or the `python -m taskq_api` console script. A target that chains `test lint coverage` re-runs dimensions the gate has already scored and verifies nothing further.
> 2. The step that does so must be able to fail. `|| true`, a leading `-`, and tool flags like `--exit-zero` all keep a failure out of make's exit code, which is the only thing the gate reads.

**Makefile target**: `verify-system`
**Exercises**: `taskq_api.app` (assembled FastAPI app), `taskq_api.repository.session` (real SQLite/Postgres connection + transaction), `taskq_api.service.runner` (asyncio subprocess runner against real processes), `migrations/versions/*` (Alembic `upgrade head` → `downgrade base` → `upgrade head` round-trip per FR-07 / NFR-12), `taskq_api.api.health` (`/healthz` + `/readyz` smoke), end-to-end CRUD + auth + rate-limit response cycles via the same `httpx.ASGITransport` stack the integration tests use.

## 2. Module Design

### 2.1 Directory Structure Design Principles

**Layer contract (NFR-06):** `api > service > repository > models`; `config` and `errors` are independence modules. Lower layers may not import upper layers. The `repository` layer is the only one allowed to import `sqlalchemy`; any `from sqlalchemy…` in `service/` or `api/` is a forbidden-contract violation and a CI gate failure.

**Source directories (4 + 2 independence + migrations):**

| Directory | Layer | Role |
|-----------|-------|------|
| `taskq_api/models/` | L1 | SQLAlchemy declarative tables + pydantic request/response schemas |
| `taskq_api/repository/` | L2 | The only `sqlalchemy` consumer; holds `Session` + repos + transaction context manager |
| `taskq_api/service/` | L3 | Business logic; no ORM imports; subprocess execution lives here |
| `taskq_api/api/` | L4 | FastAPI routes; thin handlers (≤40 LOC each — NFR-11); single authorization dependency |
| `taskq_api/config.py` | independence | Reads `TASKQ_*` env; no I/O at import time |
| `taskq_api/errors.py` | independence | RFC 7807 problem+json builders; imported by every layer that raises |
| `migrations/` | (out-of-runtime) | Alembic v1 → v2 → v3 with data-preserving downgrades |

**Each directory carries a hub module** so the CRG cohesion score stays above 0.3:

| Directory | Hub module | Hub role |
|-----------|-----------|----------|
| `taskq_api/api/` | `deps.py` | Single authorization dependency; called by every route — produces cross-sibling internal edges |
| `taskq_api/service/` | `service/utils.py` (added) | `validate_scope`, `require_keys` — called from every handler-body in `tasks.py` / `runner.py` / `auth.py` / `ratelimit.py` |
| `taskq_api/repository/` | `repository/session.py` | `session_scope()` context manager — called from every repo function body |
| `taskq_api/models/` | `models/__init__.py` | Re-exports `Task`, `ApiKey`, `RateBucket`, `TaskResult`, `Tag`, `task_tags` — used by every sibling |

**File-budget check** (NFR-11 — ≤15 files/dir, ≤400 LOC/file):

| Directory | Files | Under cap? |
|-----------|-------|-----------|
| `taskq_api/` (root) | `__init__.py`, `__main__.py`, `app.py`, `config.py`, `errors.py` = 5 | ✓ |
| `taskq_api/api/` | `__init__.py`, `deps.py`, `tasks.py`, `health.py` = 4 | ✓ |
| `taskq_api/service/` | `__init__.py`, `tasks.py`, `runner.py`, `auth.py`, `ratelimit.py`, `utils.py` = 6 | ✓ |
| `taskq_api/repository/` | `__init__.py`, `session.py`, `task_repo.py`, `key_repo.py`, `rate_repo.py` = 5 | ✓ |
| `taskq_api/models/` | `__init__.py`, `orm.py`, `schemas.py` = 3 | ✓ |
| `migrations/versions/` | `v1_initial.py`, `v2_tags.py`, `v3_split_results.py` = 3 | ✓ |

**FR → module mapping (10 FRs — one-to-one or one-to-many, no orphan FRs):**

| FR | Title | Primary module(s) |
|----|-------|-------------------|
| FR-01 | Task CRUD API | `taskq_api.api.tasks`, `taskq_api.service.tasks`, `taskq_api.repository.task_repo`, `taskq_api.models.orm`, `taskq_api.models.schemas` |
| FR-02 | Task execution endpoint | `taskq_api.api.tasks`, `taskq_api.service.runner`, `taskq_api.repository.task_repo`, `taskq_api.models.orm` |
| FR-03 | API key authentication | `taskq_api.api.deps`, `taskq_api.service.auth`, `taskq_api.repository.key_repo`, `taskq_api.models.orm` |
| FR-04 | Scope authorization | `taskq_api.api.deps` (single decision point), `taskq_api.service.auth` |
| FR-05 | Rate limiting | `taskq_api.api.deps`, `taskq_api.service.ratelimit`, `taskq_api.repository.rate_repo`, `taskq_api.models.orm` |
| FR-06 | Persistence + transaction boundary | `taskq_api.repository.session`, `taskq_api.repository.task_repo`, `taskq_api.repository.key_repo`, `taskq_api.repository.rate_repo` |
| FR-07 | Alembic 3-step migration | `migrations/env.py`, `migrations/versions/v1_initial.py`, `migrations/versions/v2_tags.py`, `migrations/versions/v3_split_results.py` |
| FR-08 | Async executor | `taskq_api.service.runner`, `taskq_api.app` (lifespan: TaskGroup + drain) |
| FR-09 | Health + observability | `taskq_api.api.health`, `taskq_api.app` (reads `alembic current`), `taskq_api.config` |
| FR-10 | RFC 7807 error contract | `taskq_api.errors`, `taskq_api.api.deps` (registers exception handlers) |

**Anti-circular-dependency check:** edges flow strictly downward (`api → service → repository → models`) plus two independence modules (`config`, `errors`) consumed by all layers. No layer imports a sibling layer upward; `repository` never imports `service`; `models` never imports `repository`. Verified by `lint-imports` (NFR-06).

### 2.2 Module Catalog

#### L0 — Entry & Independence

##### `taskq_api.__main__`

| Attribute | Value |
|-----------|-------|
| Responsibility | Console-script entry (`python -m taskq_api`). Routes subcommands: `migrate {up,down,head,base}`, `key create --scope <scope>`, `healthcheck`, `serve` (default = uvicorn). |
| External Interface | CLI subcommands (no HTTP) |
| Dependencies | `taskq_api.config`, `taskq_api.errors` |

##### `taskq_api.app`

| Attribute | Value |
|-----------|-------|
| Responsibility | FastAPI factory; mounts routers; registers exception handlers (FR-10 problem+json); configures CORS (NFR-02); wires FastAPI lifespan for `asyncio.TaskGroup` startup + graceful drain (FR-08). |
| External Interface | `app: FastAPI` — uvicorn entrypoint |
| Dependencies | `taskq_api.api.tasks`, `taskq_api.api.health`, `taskq_api.api.deps`, `taskq_api.errors`, `taskq_api.service.runner` (lifespan hooks), `taskq_api.config` |

##### `taskq_api.config`

| Attribute | Value |
|-----------|-------|
| Responsibility | Loads all 12 `TASKQ_*` env vars once at startup; provides typed accessors; never logs the DB URL (NFR-04). Independence — no sibling imports. |
| External Interface | `Settings` dataclass + module-level `get_settings()` |
| Dependencies | stdlib only (`os`, `dataclasses`) |

##### `taskq_api.errors`

| Attribute | Value |
|-----------|-------|
| Responsibility | RFC 7807 `application/problem+json` constructors; canonical field whitelist for `detail` (no stack traces, SQL, paths — FR-10 / NFR-04). Independence. |
| External Interface | `Problem(...)`, `problem_response(status, type_uri, detail, correlation_id)` |
| Dependencies | `taskq_api.config` (for `correlation_id` propagation) |

#### L4 — API

##### `taskq_api.api.deps`

| Attribute | Value |
|-----------|-------|
| Responsibility | **Single authorization decision point** (FR-04). Houses `require_api_key`, `require_scope("read"|"write"|"admin")`, `enforce_rate_limit` — each is a FastAPI dependency used by every `/v1/*` route. Exception handlers for `Unauthorized` / `Forbidden` / `RateLimited` registered here. Hub module — every route in `tasks.py` and `health.py` imports it. |
| External Interface | FastAPI `Depends(...)` callables |
| Dependencies | `taskq_api.service.auth`, `taskq_api.service.ratelimit`, `taskq_api.errors`, `taskq_api.config` |

##### `taskq_api.api.tasks`

| Attribute | Value |
|-----------|-------|
| Responsibility | HTTP route layer for FR-01/02. Handlers ≤ 40 LOC each (NFR-11); every handler body calls `service.utils.validate_scope` or another hub fn to keep CRG cohesion high. |
| External Interface | `POST /v1/tasks`, `GET /v1/tasks/{id}`, `GET /v1/tasks`, `DELETE /v1/tasks/{id}`, `POST /v1/tasks/{id}/run`, `GET /v1/tasks/{id}/runs` |
| Dependencies | `taskq_api.api.deps`, `taskq_api.service.tasks`, `taskq_api.service.runner`, `taskq_api.errors`, `taskq_api.service.utils` |

##### `taskq_api.api.health`

| Attribute | Value |
|-----------|-------|
| Responsibility | FR-09. `/healthz` (liveness, no auth), `/readyz` (DB ping + `alembic current == head`), `/v1/metrics` (admin-scope task counters + p50/p95/p99 latencies + rate-limit rejects). |
| External Interface | HTTP routes |
| Dependencies | `taskq_api.api.deps`, `taskq_api.repository.session`, `taskq_api.errors`, `taskq_api.config`, Alembic programmatic API for current head |

#### L3 — Service

##### `taskq_api.service.tasks`

| Attribute | Value |
|-----------|-------|
| Responsibility | FR-01 business logic: name-uniqueness, injection-character denylist, cursor pagination, status transitions. No SQL — delegates to `repository.task_repo`. |
| External Interface | `create_task(payload, session)`, `get_task(id, session)`, `list_tasks(filters, cursor, limit, session)`, `delete_task(id, session)` |
| Dependencies | `taskq_api.repository.task_repo`, `taskq_api.repository.session`, `taskq_api.errors`, `taskq_api.service.utils`, `taskq_api.models.schemas` |

##### `taskq_api.service.runner`

| Attribute | Value |
|-----------|-------|
| Responsibility | FR-02 + FR-08. `asyncio.create_subprocess_exec(*shlex.split(command))` (NEVER `shell=True` — NFR-02). `asyncio.wait_for` timeout with `process.kill()` + `await process.wait()` to eliminate orphans. Status transitions: `pending → running → done | failed | timeout`. Maintains the `asyncio.TaskGroup`; `TASKQ_MAX_CONCURRENT` semaphore gates admission. |
| External Interface | `submit(task_id, command, session)`, `drain(timeout)`, `cancel_all()` |
| Dependencies | `taskq_api.repository.task_repo`, `taskq_api.config`, `taskq_api.errors`, `taskq_api.service.utils` |

##### `taskq_api.service.auth`

| Attribute | Value |
|-----------|-------|
| Responsibility | FR-03. SHA-256 hash on lookup; `hmac.compare_digest` constant-time compare; `revoked_at IS NULL` filter. The `compare_digest` call is the single hub function called by every sibling in this directory. |
| External Interface | `resolve_api_key(plaintext) -> ApiKey`, `has_scope(key, required) -> bool` |
| Dependencies | `taskq_api.repository.key_repo`, `taskq_api.errors`, `taskq_api.service.utils` |

##### `taskq_api.service.ratelimit`

| Attribute | Value |
|-----------|-------|
| Responsibility | FR-05. Token-bucket refill `TASKQ_RATE_PER_SEC`, capacity `TASKQ_RATE_BURST`. State in DB (cross-worker) with single-transaction row-level lock to defeat the burst race. Returns `Retry-After` seconds on reject. |
| External Interface | `consume(key_id) -> (allowed, retry_after)` |
| Dependencies | `taskq_api.repository.rate_repo`, `taskq_api.repository.session`, `taskq_api.errors`, `taskq_api.service.utils` |

##### `taskq_api.service.utils`

| Attribute | Value |
|-----------|-------|
| Responsibility | Hub module for the directory. `validate_scope(required, held)`, `require_keys(d, *keys)`, `correlation_id_from(request)` — each called from multiple siblings' function bodies to multiply internal edges. |
| External Interface | Pure functions |
| Dependencies | stdlib only |

#### L2 — Repository

##### `taskq_api.repository.session`

| Attribute | Value |
|-----------|-------|
| Responsibility | FR-06. `session_scope()` context manager opens a `Session`, yields it, commits on success, rolls back on exception, always closes. `pool_size`, `pool_pre_ping=True`. Hub module — every repo function body calls `session_scope()`. |
| External Interface | `session_scope()` context manager, `get_engine()` |
| Dependencies | `sqlalchemy`, `taskq_api.config` |

##### `taskq_api.repository.task_repo`

| Attribute | Value |
|-----------|-------|
| Responsibility | FR-01/02 data access. **`selectinload` / `joinedload` mandatory** for any related-row fetch (NFR-01 N+1 guard). Pagination is cursor-based, never offset. |
| External Interface | `create`, `get_by_id`, `list_paginated`, `delete`, `update_status`, `append_result` |
| Dependencies | `taskq_api.repository.session`, `taskq_api.models.orm`, `sqlalchemy` |

##### `taskq_api.repository.key_repo`

| Attribute | Value |
|-----------|-------|
| Responsibility | FR-03 storage. Stores only `key_hash` (sha256 hex, 64 chars). Lookup filtered by `revoked_at IS NULL`. |
| External Interface | `create(hash, scope)`, `find_active(hash)` |
| Dependencies | `taskq_api.repository.session`, `taskq_api.models.orm`, `sqlalchemy` |

##### `taskq_api.repository.rate_repo`

| Attribute | Value |
|-----------|-------|
| Responsibility | FR-05 storage. Row-level lock on `rate_buckets.key_id` during refill-and-consume atomic operation. |
| External Interface | `withdraw(key_id) -> (tokens, retry_after)`, `refill(key_id, dt)` |
| Dependencies | `taskq_api.repository.session`, `taskq_api.models.orm`, `sqlalchemy` |

#### L1 — Models

##### `taskq_api.models.orm`

| Attribute | Value |
|-----------|-------|
| Responsibility | SQLAlchemy declarative: `Task`, `ApiKey`, `RateBucket`, `Tag`, `task_tags` association table, `TaskResult`. Mirrors §5.2 schema. The `ApiKey.key_hash` column is 64-char hex only. |
| External Interface | ORM classes |
| Dependencies | `sqlalchemy` |

##### `taskq_api.models.schemas`

| Attribute | Value |
|-----------|-------|
| Responsibility | Pydantic v2 request/response models: `TaskCreate`, `TaskRead`, `TaskList`, `RunRead`. Each has `model_config.json_schema_extra` with `summary` + `description` for OpenAPI (NFR-05). |
| External Interface | Pydantic models |
| Dependencies | `pydantic`, `taskq_api.models.orm` (for ORM-mode serialization) |

#### Out-of-runtime — Migrations

##### `migrations/env.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | Alembic environment: reads `TASKQ_DB_URL` from `taskq_api.config`; offline + online modes both supported; autogenerate disabled (revisions hand-authored). |
| External Interface | Alembic API |
| Dependencies | `sqlalchemy`, `alembic`, `taskq_api.config` |

##### `migrations/versions/v1_initial.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | FR-07 v1: creates `tasks`, `api_keys`, `rate_buckets`. `downgrade()` drops all three (no data preservation needed — baseline). |
| Dependencies | `alembic`, `sqlalchemy` |

##### `migrations/versions/v2_tags.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | FR-07 v2: adds `tags`, `task_tags` (composite PK), `tasks.name` unique index. `downgrade()` drops new tables + index without touching v1 data. |
| Dependencies | `alembic`, `sqlalchemy` |

##### `migrations/versions/v3_split_results.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | FR-07 v3 — **the migration the round-trip test targets**. Adds `task_results`, copies each row's `result_json` into the new table preserving every field, then drops `tasks.result_json`. `downgrade()` reverses it: creates `tasks.result_json` JSON column, copies rows back, drops `task_results`. The round-trip test (NFR-09 / §8 #12) compares row-by-row. |

### 2.3 Design Principles Applied

| Principle | Application |
|-----------|-------------|
| 1. Subdirectories control CRG communities | 4 subdirectories under `taskq_api/` + migrations separate → each a predictable community |
| 2. Hub module per directory | `deps.py` (api), `utils.py` (service), `session.py` (repository), `__init__.py` (models) |
| 3. Entry points in hub directory | `app.py` lives at root but imports from `service/utils.py` and `api/deps.py` from each handler body |
| 4. Every function body calls a hub function | Service handlers each call `service.utils.validate_scope` / `auth.compare_digest`; repo functions each call `session.session_scope()` |
| 5. Respect CRG edge-detection limits | Cross-file calls via standalone assignment (`row = session_scope()`); no `self.method()` chains across the layer; no dynamic `__getattr__` |
| 6. Community size ≤ 50 nodes | Largest dir has 6 files × ~6 functions = ~36 nodes; under cap |

## 3. Error Handling

| Level | Strategy | Application |
|-------|----------|-------------|
| Level 0 — Domain validation | Immediate 4xx via FR-10 problem+json | FR-01 schema validate, FR-04 scope, FR-05 rate-limit |
| Level 1 — Transient infra | Single retry with bounded backoff (e.g. DB connection acquire `pool_pre_ping`) | FR-06 connection acquisition |
| Level 2 — Task timeout | `asyncio.wait_for` → `process.kill()` → `await wait()`; mark `timeout` | FR-08 / NFR-03 |
| Level 3 — Migration failure | Alembic transaction rollback; DB stays at previous revision; `/readyz` returns 503 | FR-07 |
| Level 4 — Unhandled exception | Generic 500 problem+json; **`detail` field scrubbed** (no stack, no SQL, no path); `correlation_id` added to log + response header | FR-10 / NFR-02 / NFR-04 |
| Special — `asyncio.CancelledError` | **Re-raise; never catch as generic `Exception`** (NFR-03). Shutdown drains via `lifespan` → `runner.drain(TASKQ_DRAIN_TIMEOUT)` → stragglers marked `interrupted`. |

## 4. Interfaces & Data Flows

### 4.1 Request Flow (CRUD)

```
Client ──HTTP──▶ FastAPI Router (api/tasks.py)
                  │
                  ├─▶ api/deps.require_api_key        [FR-03] ─▶ service/auth ─▶ repository/key_repo
                  ├─▶ api/deps.require_scope("read")  [FR-04]
                  ├─▶ api/deps.enforce_rate_limit     [FR-05] ─▶ service/ratelimit ─▶ repository/rate_repo
                  │
                  └─▶ handler body (api/tasks.py)
                        │
                        ├─▶ service.tasks.create_task [FR-01]   ─▶ service.utils.validate_scope (hub call)
                        │     │
                        │     └─▶ repository.session.session_scope() ─▶ repository/task_repo.create
                        │                                              │
                        │                                              └─▶ models/orm.Task (insert)
                        │
                        └─▶ errors.Problem on any failure            [FR-10]
```

### 4.2 Async Execution Flow (FR-02 + FR-08)

```
Client ──POST /v1/tasks/{id}/run──▶ api/tasks.py
   │
   ├─▶ deps.require_api_key / require_scope("write") / enforce_rate_limit
   │
   └─▶ service.runner.submit(task_id, command)
         │
         ├─▶ repository.task_repo.update_status(task_id, RUNNING)       [transaction 1]
         │
         ├─▶ asyncio.TaskGroup.create_subprocess_exec(*shlex.split(cmd)) [NO shell=True]
         │       │
         │       └─▶ asyncio.wait_for(cmd, TASKQ_TASK_TIMEOUT)
         │             ├─ on normal exit  → done
         │             ├─ on timeout      → process.kill() + await wait(); no orphan  [NFR-03]
         │             └─ on CancelledError → re-raise (never swallowed)
         │
         └─▶ repository.task_repo.append_result(task_id, exit_code, …)    [transaction 2]
```

### 4.3 Migration Round-Trip (FR-07)

```
alembic upgrade head       ──(v1 → v2 → v3)──▶ task_results created; tasks.result_json removed
insert sample rows         ──▶ ORM Session.add(...) × N
alembic downgrade -1       ──(v3 → v2)──▶ tasks.result_json re-created; rows copied back row-by-row
alembic upgrade head       ──(v2 → v3)──▶ re-extracted; data preserved field-by-field
assert row == row_orig     [test_fr07_round_trip_preserves_data]
```

### 4.4 NFR Handling Matrix

| NFR | Title | Modules that handle it | Verification surface |
|-----|-------|------------------------|---------------------|
| NFR-01 | Performance / N+1 prevention | `repository.task_repo` (`selectinload`), `models.orm` (indexes on `tasks.name`, `tasks.created_at`), `config` (pool sizing) | pytest-benchmark `p95 < 30ms`; SQLAlchemy event listener counts statements per request (must be constant w.r.t. row count) |
| NFR-02 | HTTP + data-layer security | `service.auth`, `service.runner` (no shell=True), `repository.session` (parameterized only), `errors` (sanitized detail), `app` (CORS) | bandit 0/0; grep `shell=True\|eval(\|exec(` = 0; grep SQL string concat = 0 |
| NFR-03 | Errors / transactions / async correctness | `repository.session`, `service.runner`, `app` (lifespan drain), `errors` (CancelledError not classified) | ast-error-handling scan; explicit test for `CancelledError` re-raise; integration test for orphan kill |
| NFR-04 | Sensitive data masking | `errors` (detail whitelist), `config` (DB URL never logged), `api.health` (metrics response), `service.runner` (stdout/stderr redaction pre-write), `__main__` (key-create prints plaintext once only) | unit tests asserting no DB URL substring anywhere in logs/metrics; regex test for sk-/Bearer redaction |
| NFR-05 | Doc coverage | Every module | ast-docstrings scan: 100% coverage, every docstring cites `[FR-XX]` or `[NFR-XX]`; OpenAPI test asserts summary + description on every route |
| NFR-06 | Layering contract | `taskq_api/` layout + `.importlinter` | `lint-imports` exit 0; forbidden contract: `service`/`api` import `sqlalchemy` → fail |
| NFR-07 | Dependency compliance | `requirements.txt` + `requirements.lock` | `pip-licenses --with-system` JSON; every license in allowlist; SBOM in `08-config/SBOM.json` |
| NFR-08 | Mutation testing | `service/`, `repository/` only (harness_config.json documented) | `mutmut run` → score ≥ 70 |
| NFR-09 | Test assertion realism | All tests; `migrations/versions/*` exercised against real SQLite file | `pytest -q` skipped = 0; `pytest --collect-only` zero-assert = 0; no `xfail` / `skip` / `--ignore` exit |
| NFR-10 | Integration coverage | `03-development/tests/integration/` via `httpx.AsyncClient(transport=ASGITransport(app))` | pytest-cov integration ≥ 80%; covered codes: 201/401/403/404/409/422/429/503 |
| NFR-11 | Readability | All modules | LLOC-weighted MI ≥ 80; per-fn CC ≤ 10; file ≤ 400 LOC; dir ≤ 15 files; per-handler ≤ 40 LOC |
| NFR-12 | System verification target | `Makefile` | `make verify-system` chain: alembic up → test → uvicorn smoke + /healthz + /readyz → alembic down then up; stdout contains `verify-system: PASS` |

### 4.5 Technology Choices

| Technology | Rationale | Justification |
|-----------|-----------|---------------|
| FastAPI | Spec-mandated (SPEC §2) | All FR routes live under `/v1/*`; OpenAPI auto-gen satisfies NFR-05 |
| SQLAlchemy 2.x declarative | Spec-mandated; explicit transaction boundary required | FR-06 |
| Alembic | Spec-mandated for FR-07 three-step evolution; downgrades must round-trip | FR-07 / NFR-09 special clause |
| SQLite (dev/test) + Postgres (prod) | SPEC §2; one ORM model both targets | Avoids divergent SQL |
| `asyncio.TaskGroup` | Python 3.11+ structured concurrency; replaces manual gather/error tracking | FR-08 / NFR-03 |
| `hmac.compare_digest` | Constant-time comparison | NFR-02 |
| `pytest-benchmark` + SQLAlchemy event listener | Direct measurement of NFR-01 thresholds | NFR-01 |
| `httpx.ASGITransport` | Spec-mandated; no in-process double for the wire layer | NFR-10 |
| `import-linter` + `sqlalchemy` forbidden contract | Spec-mandated | NFR-06 |
| `mutmut` 2.x | Mutation score ≥ 70 against `service/` + `repository/` | NFR-08 |
| `bandit` | 0 HIGH / 0 MEDIUM | NFR-02 |
| `pip-licenses --with-system` | Full transitive scan including system packages | NFR-07 |

---

## 5. SAB Block (machine-readable — BINDING CONTRACT)

> **CONTRACT**: Field names, types, `sab:` root key, and `phase` as int must match `core/quality_gate/sab_parser.py:render_canonical_sab_template()`. Validate before committing: `python3 scripts/generate_sab.py --validate --project .`

<!-- SAB:START -->
```yaml
sab:
  version: "1.0"
  created_at: "2026-08-19"
  phase: 2  # MUST be int, NOT a string — parser raises on 'phase: "2"'
  project: "taskq-api"

  layers:
    - name: api
      modules:
        - name: "taskq_api.api.tasks"
        - name: "taskq_api.api.health"
        - name: "taskq_api.api.deps"
      allowed_dependencies: ["service", "independence"]
    - name: service
      modules:
        - name: "taskq_api.service.tasks"
        - name: "taskq_api.service.runner"
        - name: "taskq_api.service.auth"
        - name: "taskq_api.service.ratelimit"
        - name: "taskq_api.service.utils"
      allowed_dependencies: ["repository", "independence"]
    - name: repository
      modules:
        - name: "taskq_api.repository.session"
        - name: "taskq_api.repository.task_repo"
        - name: "taskq_api.repository.key_repo"
        - name: "taskq_api.repository.rate_repo"
      allowed_dependencies: ["models", "independence"]
    - name: models
      modules:
        - name: "taskq_api.models.orm"
        - name: "taskq_api.models.schemas"
      allowed_dependencies: []
    - name: independence
      modules:
        - name: "taskq_api.config"
        - name: "taskq_api.errors"
      allowed_dependencies: []
    - name: migrations
      modules:
        - name: "migrations.env"
        - name: "migrations.versions.v1_initial"
        - name: "migrations.versions.v2_tags"
        - name: "migrations.versions.v3_split_results"
      allowed_dependencies: ["models", "independence"]

  allowed_dependencies:
    - from: api
      to: service
    - from: api
      to: independence
    - from: service
      to: repository
    - from: service
      to: independence
    - from: repository
      to: models
    - from: repository
      to: independence
    - from: models
      to: independence
    - from: migrations
      to: models
    - from: migrations
      to: independence

  quality_targets:
    max_complexity: 10          # per FR-11 (CC <= 10)
    min_coverage: 100           # per §8 #2 (TOTAL 100%)
    max_coupling: 0.3           # 4-layer pyramid; forbidden contract raises hard fail

  nfr_dimension_mapping: {}

  nfr_traceability:
    NFR-01:
      type: performance
      target: "p95 < 30ms"
      module: taskq_api.repository.task_repo
    NFR-02:
      type: security
      target: "bandit 0 HIGH / 0 MEDIUM; grep shell=True = 0"
      module: taskq_api.errors
    NFR-03:
      type: reliability
      target: "CancelledError re-raised; orphan subprocesses = 0"
      module: taskq_api.service.runner
    NFR-04:
      type: security
      target: "DB URL absent from any log; secrets regex matches = 100% redacted"
      module: taskq_api.config
    NFR-05:
      type: documentation
      target: "100% docstring coverage; OpenAPI summary + description on every route"
      module: taskq_api.api.tasks
    NFR-06:
      type: layering
      target: "lint-imports exit 0; sqlalchemy imports outside repository = 0"
      module: migrations.env
    NFR-07:
      type: licensing
      target: "every direct + transitive license in allowlist"
      module: taskq_api.config
    NFR-08:
      type: mutation
      target: ">= 70"
      module: taskq_api.service.tasks
    NFR-09:
      type: testability
      target: "skipped = 0; zero-assert tests = 0; migration test runs against real SQLite"
      module: migrations.versions.v3_split_results
    NFR-10:
      type: integration
      target: "integration coverage >= 80%"
      module: taskq_api.app
    NFR-11:
      type: maintainability
      target: "MI >= 80; CC <= 10; file <= 400 LOC; dir <= 15 files; handler <= 40 LOC"
      module: taskq_api.service.utils
    NFR-12:
      type: verifiability
      target: "make verify-system exit 0 + stdout contains 'verify-system: PASS'"
      module: taskq_api.app

  advisory_only: []

  gate_score_overrides: {}

  fr_module_traceability:
    FR-01:
      - taskq_api.api.tasks
      - taskq_api.service.tasks
      - taskq_api.repository.task_repo
      - taskq_api.models.orm
      - taskq_api.models.schemas
    FR-02:
      - taskq_api.api.tasks
      - taskq_api.service.runner
      - taskq_api.repository.task_repo
      - taskq_api.models.orm
    FR-03:
      - taskq_api.api.deps
      - taskq_api.service.auth
      - taskq_api.repository.key_repo
      - taskq_api.models.orm
    FR-04:
      - taskq_api.api.deps
      - taskq_api.service.auth
    FR-05:
      - taskq_api.api.deps
      - taskq_api.service.ratelimit
      - taskq_api.repository.rate_repo
      - taskq_api.models.orm
    FR-06:
      - taskq_api.repository.session
      - taskq_api.repository.task_repo
      - taskq_api.repository.key_repo
      - taskq_api.repository.rate_repo
    FR-07:
      - migrations.env
      - migrations.versions.v1_initial
      - migrations.versions.v2_tags
      - migrations.versions.v3_split_results
    FR-08:
      - taskq_api.service.runner
      - taskq_api.app
    FR-09:
      - taskq_api.api.health
      - taskq_api.app
      - taskq_api.config
    FR-10:
      - taskq_api.errors
      - taskq_api.api.deps

  architecture_constraints:
    - "no_circular_dependencies"
    - "api > service > repository > models"
    - "sqlalchemy imports allowed only in repository layer"
    - "config and errors are independence modules with no upward imports"
    - "no shell=True anywhere in source tree"
    - "no string-concatenated SQL anywhere in source tree"
    - "CancelledError must propagate, never be swallowed as a generic Exception"

  high_risk_modules:
    - "taskq_api.service.runner"   # async subprocess + orphan kill (FR-02/08)
    - "taskq_api.service.auth"     # hmac.compare_digest + scope enforcement (FR-03/04)
    - "taskq_api.repository.session"  # transaction boundary (FR-06 / NFR-03)
    - "migrations.versions.v3_split_results"  # data-preserving round-trip (FR-07)
```
<!-- SAB:END -->

Note: Fill in the YAML above — it is used for Drift Detection and gate scoring.
Generate: `python3 scripts/generate_sab.py --project . [--overwrite]`

---

## 6. Security Design (STRIDE-lite — machine-readable, BINDING CONTRACT)

> **CONTRACT**: Field names and the `security_design:` root key are parsed by `core/quality_gate/security_design.py:extract_security_block()`. Validate: `python3 harness_cli.py check-artifact-consistency --project .`

<!-- SEC:START -->
```yaml
security_design:
  version: "1.0"
  applicability: full
  justification: ""
  trust_boundaries:
    - id: TB-01
      name: "external HTTP → api layer"
      description: "untrusted clients crossing into the FastAPI router; everything before X-API-Key validation"
    - id: TB-02
      name: "api → service"
      description: "validated requests entering business logic; command strings originate here"
    - id: TB-03
      name: "service → repository → SQL store"
      description: "ORM-mediated access to tasks/api_keys/rate_buckets; the only path that may touch sqlalchemy"
    - id: TB-04
      name: "service → OS subprocess"
      description: "asyncio.create_subprocess_exec into the host shell process tree; the only place the host kernel is reached"
  threats:
    - id: T-01
      boundary: TB-01
      category: tampering
      description: "malformed JSON body or injection-character command attempts to mutate task state"
      mitigation: "Pydantic v2 schema validation + name-uniqueness check + injection-character denylist; returns 422 problem+json on failure"
      owner_module: "taskq_api.service.tasks"
      nfr: NFR-02
      verified_by: "test_sec_t01_injection_payload_rejected"
    - id: T-02
      boundary: TB-01
      category: denial_of_service
      description: "excessive request volume exhausts DB connections or async slots"
      mitigation: "per-token token bucket (TASKQ_RATE_BURST / TASKQ_RATE_PER_SEC) consumed atomically with row-level lock; 429 + Retry-After"
      owner_module: "taskq_api.service.ratelimit"
      nfr: NFR-02
      verified_by: "test_sec_t02_rate_limit_returns_429_with_retry_after"
    - id: T-03
      boundary: TB-01
      category: spoofing
      description: "forged or revoked X-API-Key; replay attempts"
      mitigation: "SHA-256 hash lookup + hmac.compare_digest constant-time compare + revoked_at IS NULL filter"
      owner_module: "taskq_api.service.auth"
      nfr: NFR-02
      verified_by: "test_sec_t03_invalid_or_revoked_key_returns_401"
    - id: T-04
      boundary: TB-02
      category: elevation_of_privilege
      description: "write-scoped token attempts DELETE /v1/tasks/{id} or /v1/metrics"
      mitigation: "single dependency require_scope('admin') in api/deps.py; insufficient scope returns 403 without revealing resource existence"
      owner_module: "taskq_api.api.deps"
      nfr: NFR-02
      verified_by: "test_sec_t04_write_scope_denies_admin_endpoint_403"
    - id: T-05
      boundary: TB-02
      category: information_disclosure
      description: "unhandled exception leaks stack trace / SQL / file paths via detail field"
      mitigation: "RFC 7807 problem+json with whitelist-only detail; stack/SQL/path scrubbed at the errors boundary; X-Correlation-Id links to server log"
      owner_module: "taskq_api.errors"
      nfr: NFR-04
      verified_by: "test_sec_t05_error_detail_strips_internal_paths"
    - id: T-06
      boundary: TB-04
      category: tampering
      description: "command injection via shell metacharacters (e.g. '; rm -rf /')"
      mitigation: "asyncio.create_subprocess_exec with shell=False by signature; shlex.split on entry; no shell=True anywhere in tree (grep CI gate)"
      owner_module: "taskq_api.service.runner"
      nfr: NFR-02
      verified_by: "test_sec_t06_no_shell_true_in_source"
    - id: T-07
      boundary: TB-04
      category: denial_of_service
      description: "long-running command exhausts the host process table or blocks shutdown"
      mitigation: "asyncio.wait_for timeout fires process.kill() then await process.wait(); lifespan drains in-flight tasks up to TASKQ_DRAIN_TIMEOUT, stragglers marked interrupted"
      owner_module: "taskq_api.service.runner"
      nfr: NFR-03
      verified_by: "test_sec_t07_timeout_kills_subprocess_no_orphan"
    - id: T-08
      boundary: TB-03
      category: information_disclosure
      description: "TASKQ_DB_URL with embedded credentials leaks via logs or /v1/metrics response"
      mitigation: "config never logs the URL; metrics response is a typed counter object that lacks any URL field; structured logging redacts postgres:// / Bearer / sk- prefixes"
      owner_module: "taskq_api.config"
      nfr: NFR-04
      verified_by: "test_sec_t08_db_url_absent_from_logs_and_metrics"
```
<!-- SEC:END -->

Note: `owner_module` names a module declared in the §5 SAB block; `nfr` exists in SPEC.md §4; `verified_by` names the test that proves the mitigation — from Phase 5 onward, `check-artifact-consistency` blocks if that test doesn't exist yet. Threats also seed `bug-hunt-targets`' adversarial-review targeting and force NFR-pattern test cases in `derive_test_cases.md` Step 1c regardless of SRS keywords.
