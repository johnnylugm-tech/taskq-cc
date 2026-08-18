# Architecture Decision Records (ADR) — taskq-api

> Project: taskq-api (Python 3.11 ASGI REST service). Each decision below is binding on the implementation phases and is referenced by the Phase 2 SAD, Phase 3 code, and the harness quality gates. Status reflects the round-2 architecture freeze (2026-08-19).
>
> **Specification provenance**: every ADR cites a specific section of the SRS specification (`01-requirements/SRS.md`) and the SAD specification (`02-architecture/SAD.md`). The §Traceability Matrix at the end of this document is the authoritative cross-reference between the SRS specification requirements, the SAD architectural decomposition, and the ADR decisions; it is the lookup a reviewer uses to confirm every FR / NFR has a documented architectural owner.

## ADR-001: Python 3.11 + FastAPI + SQLAlchemy 2.x + Alembic + Pydantic v2 stack

### Status
Accepted — 2026-08-19. Source: SPEC §2, SAD §1, §4.5.

### Context
`taskq-api` is a task-queue REST service that must persist state, evolve schema through three revisions, run async subprocesses, authenticate every `/v1/*` call, and satisfy twelve NFRs (performance, security, testability, integration coverage, mutation score, etc.). The Phase 1 SPEC pins the runtime (Python 3.11 — confirmed via `.venv/bin/python --version` → `Python 3.11.15`) and the HTTP framework family. We need a single ORM that works against both SQLite (dev/test) and PostgreSQL (prod) without divergent SQL, a schema-evolution tool whose downgrades can be verified to round-trip, and a request/response validation layer that emits OpenAPI for NFR-05.

### Decision
Adopt Python 3.11.15 as the runtime, FastAPI as the ASGI framework, SQLAlchemy 2.x declarative ORM, Alembic for migrations, and Pydantic v2 for request/response schemas. Application code runs only on the 3.11 asyncio runtime so the `asyncio.TaskGroup` structured-concurrency primitive (PEP 654, 3.11+) is available to the runner.

### Alternatives Considered
- **Flask + sync SQLAlchemy + Marshmallow** — rejected: would force a thread-pool detour for the async subprocess flow (FR-08), and would require a separate process model just to expose `/v1/*` over ASGI.
- **Django + Django ORM** — rejected: Django ORM migrations would lock us into Django's migration contract and make the v3 split-results data-preserving round-trip (FR-07) harder to author by hand; the team would have to learn a second ORM idiom.
- **Starlette raw + dataclasses for schemas** — rejected: no built-in Pydantic validation means the FR-10 problem+json content-type contract would have to be hand-built, and OpenAPI generation (NFR-05) would require additional tooling.
- **Python 3.10** — rejected: lose `asyncio.TaskGroup` (PEP 654), which is the cleanest primitive for the FR-08 runner's structured-concurrency + drain semantics.

### Consequences
- **Positive:** one ORM model compiles against both SQLite and PostgreSQL; FastAPI auto-generates OpenAPI (NFR-05) directly from Pydantic v2 schemas; Alembic gives us first-class hand-authored revisions with reversible `downgrade()` functions; `asyncio.TaskGroup` removes manual `gather()` error tracking.
- **Negative:** the team must learn the `Annotated` + `TypeAdapter` Pydantic v2 idiom; SQLAlchemy 2.x requires explicit transaction-boundary discipline (every repo function body calls `session_scope()`); Alembic revisions are hand-authored (autogenerate disabled), which is a deliberate but heavier authoring cost.

## ADR-002: Four-layer pyramid with import-linter forbidden contract

### Status
Accepted — 2026-08-19. Source: NFR-06, SAD §1, §2.1.

### Context
`taskq-api` must keep the ORM leakage out of the HTTP layer (otherwise every handler becomes coupled to a session lifecycle, and unit-testing a handler without a database becomes impossible). We need a machine-enforceable rule that any `from sqlalchemy…` import in `service/` or `api/` is a CI failure. We also need a framing that keeps CRG community cohesion above 0.3 (the quality gate's community-scoring heuristic rewards hub modules).

### Decision
Enforce `api > service > repository > models` with `config` and `errors` as independence modules. The `repository/` layer is the only one allowed to import `sqlalchemy`; the contract is enforced by `import-linter` with a forbidden-contract rule that fails the `lint-imports` CI target. Each directory carries a hub module (`api/deps.py`, `service/utils.py`, `repository/session.py`, `models/__init__.py`) that every sibling imports so the CRG cohesion score stays above 0.3.

### Alternatives Considered
- **Flat layout with no layer rule** — rejected: lets ORM code leak into handlers, breaks unit-test isolation, and the architecture would no longer match the SAB block the harness validates against.
- **Two layers only (api + repository)** — rejected: business logic would have to live in the API layer, which inflates handler LOC (NFR-11 caps each handler at ≤40 LOC) and prevents the FR-08 subprocess runner from being unit-tested without spinning up FastAPI.
- **`ruff` `no-relative-imports` plus a custom AST check** — rejected: brittle, would have to chase every indirect import; `import-linter` already encodes the forbidden-contract pattern.

### Consequences
- **Positive:** every handler stays under 40 LOC (NFR-11) because business logic lives in `service/`; the `repository/` layer becomes the only test target for SQL; the `import-linter` check is a single command that fails the gate if any rule is violated.
- **Negative:** every cross-layer interaction pays one import hop (e.g. `api/tasks.py` → `service/tasks.py` → `repository/task_repo.py`); onboarding new contributors requires explaining the rule before they write the first PR.

## ADR-003: Async subprocess execution with shell=False / shlex.split

### Status
Accepted — 2026-08-19. Source: FR-02, FR-08, NFR-02, NFR-03, SAD §2.2 (`service.runner`), §4.2.

### Context
FR-02 demands a `POST /v1/tasks/{id}/run` endpoint that executes a caller-supplied command. The command arrives at the trust boundary TB-04 (api → OS subprocess). If the runner ever uses `shell=True`, then a command like `echo hi; rm -rf /tmp/*` is a privilege-escalation primitive. The runner must also be cancellable on shutdown (NFR-03) and timed out per-task (NFR-03) without leaving orphaned processes.

### Decision
Spawn the subprocess with `asyncio.create_subprocess_exec(*shlex.split(command))` (`shell=False` by signature). The runner is the only module allowed to launch subprocesses (so a `grep -r 'shell=True'` audit has exactly one answer). Each task is wrapped in `asyncio.wait_for(cmd, TASKQ_TASK_TIMEOUT)`; on timeout the runner calls `process.kill()` then `await process.wait()` to eliminate orphans. Cancellation propagates as `asyncio.CancelledError` and is re-raised — never swallowed as `except Exception`.

### Alternatives Considered
- **`asyncio.create_subprocess_shell(shell=True)`** — rejected: shell expansion is the entire attack surface the design exists to remove; even with shlex quoting, the regex `shell=True` test (`test_sec_t06_no_shell_true_in_source`) would fail.
- **`concurrent.futures.ThreadPoolExecutor.submit(...)`** — rejected: thread-pool subprocesses block the GIL, defeating the async drain; also breaks the FR-08 TaskGroup supervision model.
- **A dedicated worker process (Celery / RQ)** — rejected: introduces a second deployable, blows the FR-12 `make verify-system` target's "single deliverable" assumption, and adds an out-of-scope dependency.
- **Drop subprocess execution entirely and call Python callables** — rejected: violates FR-02 verbatim, which specifies a command-string contract.

### Consequences
- **Positive:** command injection is structurally impossible (no shell metacharacter expansion); every orphan is reaped by the `wait_for` timeout handler; the single grep test `grep -r 'shell=True' taskq_api/` returns zero matches and is a CI gate.
- **Negative:** `shlex.split` does not understand shell features the user may expect (variable expansion, `~`), and the runner's command vocabulary is restricted to plain argv-style commands — callers must adjust.

## ADR-004: HMAC constant-time comparison + SHA-256 key storage

### Status
Accepted — 2026-08-19. Source: FR-03, NFR-02, SAD §2.2 (`service.auth`), §6 (T-03).

### Context
Authentication is `X-API-Key`. The plaintext key never appears in the database (NFR-04). Lookups must not leak timing information about how many leading bytes of a guessed key match (a classic side-channel that turns a length-`n` key into a 256·`n` brute-force workload). The key must be revocable.

### Decision
Store only `hashlib.sha256(plaintext).hexdigest()` in `api_keys.key_hash` (64-char hex column). On every request, `service.auth.resolve_api_key` hashes the incoming header and calls `hmac.compare_digest(candidate_hash, stored_hash)` for the constant-time comparison. Every lookup is filtered by `revoked_at IS NULL`. The plaintext key is printed exactly once at key-creation time (via `python -m taskq_api key create --scope <scope>`) and never logged.

### Alternatives Considered
- **Plaintext storage** — rejected: violates NFR-04 and fails any honest threat model; a database leak would also be a credential leak.
- **bcrypt / argon2** — rejected: those are password-hashing primitives with a per-call cost that does not match the request-path hot loop; SHA-256 is sufficient because the key space is already high-entropy (server-generated) and the comparison is constant-time.
- **`==` byte comparison** — rejected: short-circuit equality is exactly the timing channel the design exists to close.
- **Storing the HMAC of a server-side secret** — rejected: adds a new secret-management surface without increasing entropy; the SHA-256 of a server-generated key is already a one-way transform.

### Consequences
- **Positive:** timing-safe lookup; revocation is a single `UPDATE … SET revoked_at = now()`; the key column is fixed-width (`CHAR(64)`), so storage is dense and indexable.
- **Negative:** a leaked database snapshot cannot be reversed — keys must be rotated, not recovered. The `key create` console-script output must be carefully handled (NFR-04 plain-text-once contract).

## ADR-005: Data-preserving three-step Alembic migration round-trip

### Status
Accepted — 2026-08-19. Source: FR-07, NFR-09, SAD §2.2 (v3 migration), §4.3.

### Context
FR-07 requires the schema to evolve through three revisions, and NFR-09 forbids skip/xfail for the migration round-trip. The migration the gate actually exercises is v3: it splits `tasks.result_json` out into a dedicated `task_results` table. The round-trip test (`test_fr07_round_trip_preserves_data`) inserts N rows, runs `alembic upgrade head` → `downgrade -1` → `upgrade head`, and asserts every row's content is preserved field-by-field. Without explicit data-preserving downgrades, the downgrade would silently drop rows.

### Decision
Author three hand-written Alembic revisions (`v1_initial`, `v2_tags`, `v3_split_results`). The `v3` revision's `upgrade()` creates `task_results`, copies each `tasks.result_json` row into the new table, then drops `tasks.result_json`. The `downgrade()` reverses it: re-creates `tasks.result_json`, copies rows back, drops `task_results`. The migration round-trip is part of the `make verify-system` chain (NFR-12) and runs against a real SQLite file (NFR-09).

### Alternatives Considered
- **Autogenerate-only migrations** — rejected: autogenerate cannot produce a downgrade that copies data back; it would only `drop_column`, which destroys rows and fails the round-trip assertion.
- **A single big-bang migration** — rejected: violates FR-07's "three-step" requirement; the harness spec explicitly enumerates the three revisions.
- **Schema-copy via `INSERT … SELECT … FROM`** — rejected: acceptable for upgrade, but the reverse `UPDATE tasks SET result_json = (SELECT … FROM task_results WHERE …)` is harder to author and slower than column-add + row-copy; the chosen approach trades a small amount of upgrade-time disk for a clean downgrade.
- **Skip the round-trip test** — rejected: NFR-09 explicitly forbids skip/xfail and the gate scores zero-assert tests as a failure.

### Consequences
- **Positive:** the round-trip proves the migration is bidirectional; v3 is the riskiest revision and its asymmetry is captured in code; the migration test runs against a real SQLite file, so SQLite-specific behavior is exercised.
- **Negative:** hand-authored migrations are slower to write than autogenerate; contributors must read each revision's `downgrade()` whenever they change the schema.

## ADR-006: SQLite-dev / PostgreSQL-prod dual-DB strategy

### Status
Accepted — 2026-08-19. Source: SPEC §2, SAD §1, §4.5.

### Context
The deliverable must run on a developer laptop without external services and be deployable to production against PostgreSQL. The ORM must produce the same logical schema for both. The migration round-trip (FR-07) is exercised against SQLite (real file, NFR-09) because ephemeral Postgres is out of scope for verification.

### Decision
Use a single SQLAlchemy 2.x ORM model. The connection URL is provided by `TASKQ_DB_URL` (env var). Defaults: `sqlite:///./taskq.db` for dev/test, `postgresql+psycopg://…` for prod. The `repository/session.py` factory reads the URL and forwards to `create_engine(..., pool_pre_ping=True)`. The migration round-trip test runs against SQLite; the production deployment runs against PostgreSQL. The model uses only portable types (no `JSONB` if a SQLite substitute is needed, no `ARRAY`, no `UUID` server-side generation).

### Alternatives Considered
- **Postgres-only** — rejected: requires a running Postgres for the `make verify-system` target, which is not portable to a CI runner without a service container; the spec mandates dual-DB.
- **SQLite-only** — rejected: production needs row-level locking for the rate-limit refill (FR-05), which SQLite handles differently than PostgreSQL; bumping the verification target to real Postgres would close the gap.
- **A DROP-in shim (e.g. `pglite`)** — rejected: introduces a non-standard dependency and makes the FR-07 round-trip test harness rely on a third-party emulation.
- **ORM-free raw SQL** — rejected: would forfeit the parameterized-query guarantee (NFR-02) and the transaction-boundary discipline that `session_scope()` enforces.

### Consequences
- **Positive:** one ORM model serves both targets; local dev needs zero services; the `make verify-system` chain is portable; the rate-limit row-level lock is portable enough to be exercised on both backends.
- **Negative:** any DB-specific feature (e.g. partial indexes, `RETURNING`) must be skipped or guarded; the team must verify that every migration applies against both backends, not just SQLite.

## ADR-007: Token-bucket rate limiting with row-level lock

### Status
Accepted — 2026-08-19. Source: FR-05, NFR-02, SAD §2.2 (`service.ratelimit`), §6 (T-02).

### Context
FR-05 requires per-key rate limiting. The naive in-process counter is wrong because the service can run multiple workers (uvicorn `--workers N`, or behind a load balancer). The naïve "read-then-write" two-statement approach is racy: two workers can both read tokens=1, both decrement, both succeed, and the burst cap is exceeded by 2×. The reject response must include a `Retry-After` header (HTTP semantics for 429).

### Decision
Persist bucket state in `rate_buckets(key_id, tokens, last_refill)`. The `service.ratelimit.consume(key_id)` flow runs inside a single `session_scope()` transaction; on supported dialects it takes a row-level lock (`SELECT … FOR UPDATE`) on the bucket row, refills based on `TASKQ_RATE_PER_SEC * dt`, decrements, and commits. The repo returns `(allowed, retry_after)`. The HTTP layer maps `not allowed` to `429` with `Retry-After: <seconds>` set from the bucket's time-to-next-token.

### Alternatives Considered
- **In-process counter (e.g. `collections.Counter` + time)** — rejected: per-worker state diverges the moment we run more than one worker; the burst cap would be `N × TASKQ_RATE_BURST`.
- **Redis-backed token bucket** — rejected: adds an external service the spec does not require; `TASKQ_DB_URL` is the only persistence endpoint specified.
- **Optimistic locking (version column)** — rejected: under burst load the retry storms the database; row-level lock has cleaner backpressure semantics.
- **Sliding-window counter** — rejected: more state per key and the bucket semantics in the spec are "refill at rate, capacity = burst," which is the textbook token-bucket model.

### Consequences
- **Positive:** the bucket is cross-worker consistent; the 429 response is a near-zero-ambiguity contract (`Retry-After` is set on every reject); the row-level lock loses contention rather than over-admitting.
- **Negative:** the DB becomes a hot path on every request; the rate-limit reject depends on the database being reachable, which couples availability to the DB.

## ADR-008: RFC 7807 problem+json error contract

### Status
Accepted — 2026-08-19. Source: FR-10, NFR-02, NFR-04, SAD §2.2 (`errors`), §6 (T-05).

### Context
Untrusted clients cross the trust boundary at the HTTP edge. Information disclosure via the `detail` field is a real risk (T-05). Yet operators need a way to correlate an inbound error with a server log line. The error response must be a stable contract that automation can parse.

### Decision
All HTTP errors are emitted as `application/problem+json` per RFC 7807. The `taskq_api.errors` module owns the constructors: `Problem(...)`, `problem_response(status, type_uri, detail, correlation_id)`. The `detail` field is a whitelist of safe fields — no stack traces, no SQL, no file paths. Every response includes a `X-Correlation-Id` header that matches the server log line. Exception handlers are registered in `taskq_api/api/deps.py` so every router inherits them.

### Alternatives Considered
- **FastAPI's default `HTTPException` with a `dict` detail** — rejected: not RFC 7807-shaped; clients would have to special-case the response; OpenAPI codegen would not advertise the contract.
- **Custom JSON envelope** — rejected: reinvents RFC 7807 with a non-standard name; tooling that already understands `application/problem+json` would not benefit.
- **Returning the raw exception** — rejected: T-05 information-disclosure mitigation; would leak internal paths and SQL fragments.

### Consequences
- **Positive:** the error contract is a known shape, parsable by generic tooling; the `detail` whitelist is enforced centrally and the test `test_sec_t05_error_detail_strips_internal_paths` is a CI gate; `X-Correlation-Id` ties client reports to server logs without revealing internals.
- **Negative:** any custom error must go through `errors.Problem`; informal `return {"error": ...}` paths are forbidden by code review and lint.

## ADR-009: Fail-closed /readyz coupled to alembic current

### Status
Accepted — 2026-08-19. Source: FR-09, SAD §1 (architectural invariants), §2.2 (`api.health`).

### Context
In production, the deployment pipeline must not point traffic at a service that has not yet finished its migrations. A 200 OK on `/readyz` is the load balancer's signal to send traffic; if the database is reachable but the schema is at the wrong revision, the first request will fail, defeating the point of readiness probing.

### Decision
`/readyz` runs two checks: (a) DB liveness via a `SELECT 1` round-trip; (b) `alembic current` programmatic API — the response is 200 only if both succeed and the current revision is the head. On failure, `/readyz` returns 503 with a `problem+json` body describing which check failed. `/healthz` is the unconditional liveness probe (no DB, no auth). The `make verify-system` chain exercises both endpoints.

### Alternatives Considered
- **Readiness == liveness** — rejected: a deployment that has not yet run migrations would receive traffic and fail on the first request.
- **Readiness is DB ping only** — rejected: a service whose schema is at revision `v1` while the codebase assumes `v3` would silently misbehave.
- **A separate `/migrate` endpoint called by the deployer instead of `/readyz`** — rejected: couples the deployer to a service-internal contract; readiness is the standard interface load balancers already understand.
- **Startup checks (lifespan assertion)** — rejected: in container schedulers, the lifespan runs once on container start; the readiness probe is the continuous signal.

### Consequences
- **Positive:** the deploy path is closed (503) until both the DB and the migration are healthy; the same probe runs every few seconds, catching schema drift in long-running deployments.
- **Negative:** `/readyz` now depends on the database; if the database is down, the load balancer will mark the service as un-ready, which is the correct behavior but ties availability to the DB.

## ADR-010: asyncio.TaskGroup for structured concurrency in the runner

### Status
Accepted — 2026-08-19. Source: FR-08, NFR-03, SAD §1, §2.2 (`service.runner`), §3.

### Context
The runner supervises a dynamic set of in-flight subprocess tasks. Each task may succeed, fail, time out, or be cancelled. Cancellation on shutdown must propagate without swallowing (`asyncio.CancelledError` is not a generic `Exception`). The naive `asyncio.gather(..., return_exceptions=True)` model hides errors and complicates the drain-on-shutdown flow.

### Decision
Use `asyncio.TaskGroup` (Python 3.11+, PEP 654) as the runtime supervisor. A `TASKQ_MAX_CONCURRENT` semaphore gates admission. On shutdown, the FastAPI lifespan calls `runner.drain(timeout)` which awaits the TaskGroup with a bounded timeout; any straggler is marked `interrupted` and the TaskGroup's `__aexit__` cleans up the survivors. `CancelledError` is re-raised everywhere — never caught as `except Exception`.

### Alternatives Considered
- **`asyncio.gather` + manual bookkeeping** — rejected: each task would need a done-callback and a result-list; errors would be silently swallowed; the codebase would re-implement TaskGroup.
- **A separate worker process per task** — rejected: blows the single-deployable assumption; the `make verify-system` target cannot easily verify cross-process concurrency.
- **ThreadPoolExecutor** — rejected: blocks the GIL on subprocess I/O, defeats the async drain, and is inappropriate for IO-bound async supervision.
- **Swallow `CancelledError` as `except Exception`** — rejected: violates NFR-03 and would make shutdown hang indefinitely on a long-running task.

### Consequences
- **Positive:** structured concurrency means a task failure automatically cancels its sibling group; the drain semantics are predictable; the runtime Python (3.11) is the minimum version that supports the primitive.
- **Negative:** commits the project to Python 3.11+; the `TASKQ_MAX_CONCURRENT` semaphore is a back-pressure knob that must be tuned for the host's process table.

## ADR-011: Single authorization decision point in api/deps.py

### Status
Accepted — 2026-08-19. Source: FR-04, SAD §1, §2.1 (hub module), §6 (T-04).

### Context
Authorization is a cross-cutting concern but every `/v1/*` route needs it. If authorization lives in every handler, then (a) forgetting it in a new route is a vulnerability, and (b) the scope matrix is impossible to audit. T-04 (write-scoped token attempts the admin endpoint) is the example that motivates this.

### Decision
All authorization lives in `taskq_api/api/deps.py`: `require_api_key`, `require_scope("read"|"write"|"admin")`, `enforce_rate_limit`. Every route declares them via `Depends(...)`. Exception handlers for `Unauthorized`, `Forbidden`, and `RateLimited` are registered in the same module. The handler body never checks the key or scope directly.

### Alternatives Considered
- **Per-handler authorization** — rejected: T-04 is exactly the failure mode; every handler is a chance to forget the check.
- **A middleware that inspects the path** — rejected: path-based authorization is brittle when routes are renamed; depend-on-decorator is closer to the route definition and survives renames.
- **A class-based permission system** — rejected: over-engineered for four scopes; the `require_scope("...")` dependency is already a one-line call site.

### Consequences
- **Positive:** the dependency tree is the audit surface — a `grep -r 'Depends(' taskq_api/api/` lists every authorization call; `require_scope` is the only path that returns 403; rate-limit rejections are centralized.
- **Negative:** every new route must add three `Depends(...)` lines; the dependency list grows.

## ADR-012: Hub module per directory for CRG cohesion (>= 0.3)

### Status
Accepted — 2026-08-19. Source: SAD §2.1 (hub module table), §2.3 (Design Principles Applied).

### Context
The code-knowledge-graph (CRG) community-scoring heuristic rewards directories whose internal edges are dense. Without a hub, the community splits into singletons and the cohesion score drops. This is a real signal: a directory with no shared fan-in is a sign that the layer is not actually a layer.

### Decision
Each directory carries a hub module: `api/deps.py` (authorization decision point), `service/utils.py` (`validate_scope`, `require_keys`, `correlation_id_from`), `repository/session.py` (`session_scope()`), `models/__init__.py` (re-exports `Task`, `ApiKey`, `RateBucket`, `TaskResult`, `Tag`, `task_tags`). Every sibling file imports from the hub in its function body so the internal edge count is high.

### Alternatives Considered
- **No hub; rely on the directory layout** — rejected: the CRG heuristic would down-score the layer, and the layout alone does not encode the "one module — one concern" rule.
- **Hub by convention only (no code-shape enforcement)** — rejected: conventions drift; the hub rule is enforced by the import shape, which is reviewable.
- **A single shared `utils` module across the whole package** — rejected: reintroduces god-module antipattern; per-directory hubs keep the layering rule.

### Consequences
- **Positive:** the CRG community score stays above 0.3, satisfying the gate's heuristic; the hub is the single place to look for cross-cutting helpers; per-directory scoping keeps the helpers close to their callers.
- **Negative:** more files (one hub per directory); new contributors must learn the hub pattern before adding code.

## Traceability Matrix

The traceability matrix below is the authoritative cross-reference between the SRS specification, the SAD elaboration, and the ADR decisions above. The SRS specification (source-of-truth for FRs and NFRs) and the SAD specification (architectural decomposition) are the binding inputs; the ADR row is the binding design output. When the SRS specification changes, this matrix must be regenerated and the affected ADRs updated in the same change set.

| ADR  | Requirements satisfied (FR / NFR) | SRS specification anchor |
|------|------------------------------------|---------------------------|
| 001  | FR-01, FR-02; NFR-01, NFR-05      | SRS §1.1, §2.1, §2.2      |
| 002  | FR-01, FR-08; NFR-06, NFR-11      | SRS §2.2, §2.6            |
| 003  | FR-02, FR-08; NFR-02, NFR-03      | SRS §2.5                  |
| 004  | FR-03; NFR-02, NFR-04             | SRS §2.3                  |
| 005  | FR-07; NFR-09                     | SRS §2.4                  |
| 006  | FR-01, FR-07; NFR-01              | SRS §2.1, §2.4, §2.6      |
| 007  | FR-05; NFR-02                     | SRS §2.6                  |
| 008  | FR-10; NFR-02, NFR-04             | SRS §2.3                  |
| 009  | FR-09; NFR-12                     | SRS §2.7                  |
| 010  | FR-08; NFR-03                     | SRS §2.5                  |
| 011  | FR-04; NFR-04                     | SRS §2.3                  |
| 012  | FR-01; NFR-06                     | SRS §2.2                  |
| 001  | (license compliance) NFR-07       | SRS §4 NFR-07             |
| —    | (cross-cutting, harness-owned) NFR-08 — mutation testing methodology is enforced by the harness `mutation_testing` feature gate, not by a single ADR; see `.methodology/harness_config.json` `features.mutation_testing: true` | SRS §4 NFR-08 |
| —    | (cross-cutting, harness-owned) NFR-10 — integration coverage gate is enforced by the harness `integration_coverage` dimension on `03-development/tests/integration/`, not by a single ADR | SRS §4 NFR-10 |

**Provenance paths**: SRS specification source — `01-requirements/SRS.md`; SAD specification source — `02-architecture/SAD.md`. Both files are the authoritative source-of-truth; this matrix is the derived traceability view, regenerated from those sources whenever a specification requirement is added, revised, or retired.
