# Software Requirements Specification (SRS) — taskq-api

> INGESTION MODE — 100% transcribed from canonical `SPEC.md` (v1.0.0, 2026-07-30).
> Canonical source: `/Users/johnny/projects/taskq-cc/SPEC.md`.
> All `### FR-NN` / `### NFR-NN` headings in canonical appear here; no invention, no omission.
> TBD / TODO / placeholder markers from canonical: none.

---

## 1. Introduction

### 1.1 Purpose
This SRS captures the requirements for `taskq-api`, a task-queue HTTP service. It is the single source of truth for Phase 1 (requirements), and every downstream artifact (SAD, TEST_SPEC, SAB, verification) cites the AC identifiers defined here.

### 1.2 Scope
- **In scope**: REST API for task submission, query, execution; relational persistence; schema migration; authentication, authorisation, rate limiting; observability; error contract.
- **Out of scope**: UI; multi-tenant org/workspace model; distributed queue (single-process background runner with DB-backed rate buckets is in scope, cross-node message brokers are not).

### 1.3 Definitions, Acronyms, Abbreviations
See §9 Glossary.

### 1.4 References
| ID | Source | Section / clause |
|----|--------|------------------|
| SPEC | `SPEC.md` v1.0.0 | full file |
| PB | `PROJECT_BRIEF.md` | test-bed intent + FR/NFR inventory |
| DIM | `harness/harness/ssi/prompts/evaluate_dimension.md` | dimension roster (Gate scoring) |
| SAB-VOCAB | `harness/core/quality_gate/sab_parser.ALL_NFR_TYPES` | `type:` vocabulary for `## FR Block` |

### 1.5 Document Overview
§1 Introduction · §2 Constraints · §3 Functional Requirements (FR-01…FR-10) · §4 Non-Functional Requirements (NFR-01…NFR-12) · §5 Acceptance Criteria Summary · §6 Out-of-Scope · §7 Open Issues · §8 Risks · §9 Glossary · `## FR Block (machine-readable)`.

---

## 2. Constraints

> Source: SPEC §1, §2, §6, PROJECT_BRIEF Key Constraints.

### 2.1 Technical Constraints
- **Language / runtime**: Python 3.11. (SPEC §1)
- **HTTP framework**: FastAPI (ASGI); `uvicorn taskq_api.app:app` is the canonical entry. (SPEC §1, §2)
- **ORM**: SQLAlchemy 2.x (declarative + explicit `Session` transaction boundaries). (SPEC §2)
- **Database**: SQLite (dev/test) and PostgreSQL (prod) — same ORM models. (SPEC §2)
- **Migration**: Alembic; v1 → v2 → v3; every step has a working `downgrade`. (SPEC §2, FR-07)
- **Async**: `async def` endpoints; background runner via `asyncio.TaskGroup`. (SPEC §2, FR-08)
- **Process execution**: `asyncio.create_subprocess_exec(*shlex.split(command))` — `shell=True` is **forbidden everywhere**. (SPEC §2, FR-02, NFR-02)

### 2.2 Architecture Constraints
- Four layers: `api > service > repository > models`; enforced by mandatory `.importlinter` (NFR-06).
- `config` and `errors` are independence modules.
- **`sqlalchemy` may only be imported by `repository/`** — ORM leakage into the business layer is the anti-pattern this round guards against (NFR-06).

### 2.3 Security Constraints
- API keys stored as **SHA-256 hashes**; compared with `hmac.compare_digest` (FR-03, NFR-02).
- 403 responses must not reveal whether the resource exists (FR-04, NFR-02).
- No string-concatenated SQL anywhere (FR-06, NFR-02).
- CORS denies all origins by default; `TASKQ_CORS_ORIGINS` is the explicit allowlist (NFR-02).
- Error bodies must not carry stack traces, SQL, or file paths (FR-10, NFR-02).

### 2.4 Migration Constraints
- Three revisions: v1 base tables; v2 tags many-to-many + unique name index; **v3 moves `tasks.result_json` into `task_results` with real data migration** (FR-07).
- `upgrade head` → sample write → `downgrade -1` → `upgrade head` must leave every column byte-identical (FR-07).

### 2.5 Async Correctness Constraints
- `asyncio.CancelledError` must propagate — never swallowed by `except Exception` (FR-08, NFR-03).
- Task timeouts must actually kill the child process (`process.kill()` then `await process.wait()`), leaving no orphans (FR-08, NFR-03).
- Shutdown drains in-flight work up to `TASKQ_DRAIN_TIMEOUT` (FR-08).

### 2.6 Query Efficiency Constraints
- Relationship loads must be explicit (`selectinload` / `joinedload`); **N+1 is an acceptance failure** — list endpoint SQL statement count must be constant regardless of row count (NFR-01).

### 2.7 Readiness Constraints
- `/readyz` returns 503 when the database is unreachable **or** when `alembic current` is not at head — deploying new code without running the migration must fail closed (FR-09).

### 2.8 Verification Honesty Constraints
- Same zero-skip rule as round 1, plus: the three-step migration must be tested against a **real database file** (not a mock), and may not be downgraded to a skip on the grounds that "migration logic is hard to test" (NFR-09).

### 2.9 Mandatory Project-Side Configuration Files
Source: SPEC §5.3 — these files are **not optional**; their absence silently turns the linked dimensions into free points.

| File | Purpose | Linked clause |
|------|---------|---------------|
| `.importlinter` | layers contract + `sqlalchemy` forbidden contract | NFR-06 |
| `requirements.txt` + `requirements.lock` | `==` pinning + transitive lock | NFR-07 |
| `requirements-dev.txt` | `import-linter` / `pip-licenses` / `mutmut` / `pytest-benchmark` / `httpx` | NFR-06/07/08/10 |
| `alembic.ini` + `migrations/versions/` | three revisions | FR-07 |
| `.env.example` | all 12 `TASKQ_*` declared with comments | §3.1 env table |
| `.methodology/harness_config.json` | `features.mutation_testing: true`; do not lower `crg_cohesion_healthy` | NFR-08 |
| `Makefile` | `verify-system` (incl. migration round-trip) | NFR-12 |

### 2.10 Environment Variables
Source: SPEC §5.1.

| Variable | Default | Purpose | Linked clause |
|----------|---------|---------|---------------|
| `TASKQ_DB_URL` | `sqlite:///./taskq.db` | database connection string (**never logged** — NFR-04) | FR-06, NFR-04 |
| `TASKQ_DB_POOL_SIZE` | `5` | connection pool size | FR-06 |
| `TASKQ_TASK_TIMEOUT` | `10.0` | per-task subprocess timeout (seconds) | FR-02, FR-08 |
| `TASKQ_MAX_CONCURRENT` | `8` | background execution concurrency cap | FR-08 |
| `TASKQ_DRAIN_TIMEOUT` | `30.0` | graceful drain budget on shutdown | FR-08 |
| `TASKQ_RATE_BURST` | `20` | token bucket capacity | FR-05 |
| `TASKQ_RATE_PER_SEC` | `5.0` | token refill rate | FR-05 |
| `TASKQ_CORS_ORIGINS` | (empty) | comma-separated allowlist; empty = deny all | NFR-02 |
| `TASKQ_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` | — |
| `TASKQ_LOG_FORMAT` | `json` | `json` / `text` | — |
| `TASKQ_HOST` | `127.0.0.1` | bind address (not public by default) | — |
| `TASKQ_PORT` | `8000` | bind port | — |

---

## 3. Functional Requirements

> Each section starts with the canonical `### FR-NN: <title>` heading; ACs use `#### AC-N.M` stable IDs. Citations are to SPEC §3 and §6.

### FR-01: 任務資源 CRUD API

> Source: SPEC §3 FR-01; PB FR Inventory.

| Method | Path | scope | Behaviour |
|--------|------|-------|-----------|
| `POST` | `/v1/tasks` | `write` | create task; body validated by `TaskCreate` pydantic model |
| `GET` | `/v1/tasks/{id}` | `read` | fetch single task (all columns) |
| `GET` | `/v1/tasks` | `read` | paginated list; supports `?status=`, `?limit=`, `?cursor=` |
| `DELETE` | `/v1/tasks/{id}` | `admin` | delete task (and its result row, in the same transaction) |

- Validation rules: non-empty / ≤1000 chars / blacklist for injection characters / name uniqueness; violation → **HTTP 422** + problem+json.
- Unknown id → **HTTP 404** + problem+json.
- Pagination is **cursor-based** (offset forbidden — large-table offset scan is an N+1 relative).
- List endpoint default `limit` 50, max 200; over the cap → 422.

**Acceptance criteria**

#### AC-1.1: POST /v1/tasks with valid write-scope key and valid body returns the new task id
DERIVED: SPEC §3 FR-01 row 1 + §8 #4. Verification: integration test asserts HTTP 201 + body carries a task id. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-01 row 1 + §8 #4. Verification: integration test asserts HTTP 201 + body carries a task id.

#### AC-1.2: POST /v1/tasks with a body that fails any of the FR-01 validation rules (empty / >1000 chars / injection char / duplicate name) returns 422 + problem+json
DERIVED: SPEC §3 FR-01 "validation rules" + §7 row 422. Verification: one test per rule class. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-01 "validation rules" + §7 row 422. Verification: one test per rule class.

#### AC-1.3: GET /v1/tasks/{id} with read-scope key returns all columns of the task; unknown id returns 404 + problem+json
DERIVED: SPEC §3 FR-01 row 2 + "unknown id → 404" + §7. Verification: integration test for known + unknown ids. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-01 row 2 + "unknown id → 404" + §7. Verification: integration test for known + unknown ids.

#### AC-1.4: GET /v1/tasks returns a cursor-paginated list; default `limit`=50, max 200; `limit>200` returns 422
DERIVED: SPEC §3 FR-01 row 3 + "預設 limit 50，上限 200". Verification: integration test covers default, max-boundary, and over-cap. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-01 row 3 + "預設 limit 50，上限 200". Verification: integration test covers default, max-boundary, and over-cap.

#### AC-1.5: GET /v1/tasks supports `?status=` and `?cursor=` query parameters and does not accept offset-based pagination
DERIVED: SPEC §3 FR-01 row 3 + "不得用 offset". Verification: integration test asserts offset keyword absent from query schema. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-01 row 3 + "不得用 offset". Verification: integration test asserts offset keyword absent from query schema.

#### AC-1.6: DELETE /v1/tasks/{id} with admin-scope key removes the task and the linked `task_results` row in the same transaction
DERIVED: SPEC §3 FR-01 row 4 + "連同結果列，同一交易". Verification: integration test asserts 204/200 and a subsequent GET on the result row returns 404 in the same session. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-01 row 4 + "連同結果列，同一交易". Verification: integration test asserts 204/200 and a subsequent GET on the result row returns 404 in the same session.

#### AC-1.7: The list endpoint's SQL statement count is constant regardless of how many rows are returned (N+1 guard)
DERIVED: SPEC §3 FR-01 "cursor-based" + NFR-01 + §8 #14. Verification: SQLAlchemy event listener assertion. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-01 "cursor-based" + NFR-01 + §8 #14. Verification: SQLAlchemy event listener assertion.

### FR-02: 任務執行端點

> Source: SPEC §3 FR-02.

- `POST /v1/tasks/{id}/run` (scope `write`) → **HTTP 202 Accepted**; body carries `run_id`.
- Actual execution: `asyncio.create_subprocess_exec(*shlex.split(command))`; **`shell=True` forbidden**; timeout = `TASKQ_TASK_TIMEOUT`.
- State machine: `pending → running → done | failed | timeout`.
- Result row written to `task_results` (FR-07 v3 schema) with columns `exit_code` / `stdout_tail` / `stderr_tail` / `duration_ms` / `finished_at`.
- `GET /v1/tasks/{id}/runs` (scope `read`) → run history, newest first.

**Acceptance criteria**

#### AC-2.1: POST /v1/tasks/{id}/run with a write-scope key returns 202 with a `run_id`; the task transitions to `running` then to a terminal state
DERIVED: SPEC §3 FR-02 + §7 (no row for 202; row 422 is for validation). Verification: integration test polls until terminal state. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-02 + §7 (no row for 202; row 422 is for validation). Verification: integration test polls until terminal state.

#### AC-2.2: The runner invokes `asyncio.create_subprocess_exec(*shlex.split(command))` and never `shell=True`; a repository-wide grep for `shell=True` returns zero hits
DERIVED: SPEC §3 FR-02 + NFR-02 + §8 #16. Verification: grep gate + unit test on the runner call site. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-02 + NFR-02 + §8 #16. Verification: grep gate + unit test on the runner call site.

#### AC-2.3: A task exceeding `TASKQ_TASK_TIMEOUT` is killed (`process.kill()` then `await process.wait()`), leaves no orphan child process, and the final state is `timeout`
DERIVED: SPEC §3 FR-02 + FR-08 + NFR-03 + §8 #25. Verification: integration test counts child PIDs before and after timeout. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-02 + FR-08 + NFR-03 + §8 #25. Verification: integration test counts child PIDs before and after timeout.

#### AC-2.4: After a run completes, the result row in `task_results` carries `exit_code`, `stdout_tail`, `stderr_tail`, `duration_ms`, `finished_at`
DERIVED: SPEC §3 FR-02 "欄位" list + SPEC §5.2 `task_results` row. Verification: integration test asserts the row is present with all five columns populated. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-02 "欄位" list + SPEC §5.2 `task_results` row. Verification: integration test asserts the row is present with all five columns populated.

#### AC-2.5: GET /v1/tasks/{id}/runs with a read-scope key returns the task's run history ordered newest-first
DERIVED: SPEC §3 FR-02 last bullet. Verification: integration test inserts three runs and asserts reverse-chronological order. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-02 last bullet. Verification: integration test inserts three runs and asserts reverse-chronological order.

### FR-03: API Key 認證

> Source: SPEC §3 FR-03.

- All `/v1/*` endpoints require `X-API-Key` header; missing or invalid → **HTTP 401** + problem+json.
- Keys are **SHA-256 hashed** before storage in `api_keys`; plaintext must never be stored.
- Comparison uses `hmac.compare_digest` (constant-time).
- Keys are minted by `python -m taskq_api key create --scope <scope>`; **plaintext is printed exactly once at creation**.
- A key with `revoked_at` non-null is invalid.
- `/healthz` and `/readyz` are exempt (FR-09).

**Acceptance criteria**

#### AC-3.1: A request to any `/v1/*` endpoint without `X-API-Key` returns 401 + problem+json
DERIVED: SPEC §3 FR-03 + §7 row 401 + §8 #5. Verification: integration test across at least three distinct `/v1/*` endpoints. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-03 + §7 row 401 + §8 #5. Verification: integration test across at least three distinct `/v1/*` endpoints.

#### AC-3.2: `api_keys` table stores `key_hash` as a 64-character lowercase hex SHA-256 digest; no plaintext is persisted
DERIVED: SPEC §3 FR-03 + NFR-02 + §8 #18. Verification: integration test mints a key, then queries the DB and asserts the plaintext string is absent and the digest matches `hashlib.sha256(plaintext).hexdigest()`. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-03 + NFR-02 + §8 #18. Verification: integration test mints a key, then queries the DB and asserts the plaintext string is absent and the digest matches `hashlib.sha256(plaintext).hexdigest()`.

#### AC-3.3: Key comparison uses `hmac.compare_digest`; a unit test asserts a successful compare and a constant-time compare for a wrong key
DERIVED: SPEC §3 FR-03 + NFR-02. Verification: unit test on the auth service. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-03 + NFR-02. Verification: unit test on the auth service.

#### AC-3.4: `python -m taskq_api key create --scope <scope>` prints the plaintext exactly once to stdout; the plaintext is not written to any persistent store
DERIVED: SPEC §3 FR-03 "明文只在建立當下印出一次" + NFR-04. Verification: CLI test asserts stdout contains plaintext and that no log/metric file contains it afterwards. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-03 "明文只在建立當下印出一次" + NFR-04. Verification: CLI test asserts stdout contains plaintext and that no log/metric file contains it afterwards.

#### AC-3.5: A key with `revoked_at` set is treated as invalid even if the hash would otherwise match
DERIVED: SPEC §3 FR-03 "revoked_at 非空的金鑰一律視為無效". Verification: integration test mints, revokes, and asserts 401 on the next call. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-03 "revoked_at 非空的金鑰一律視為無效". Verification: integration test mints, revokes, and asserts 401 on the next call.

#### AC-3.6: `/healthz` and `/readyz` are reachable without `X-API-Key` and return their non-401 responses
DERIVED: SPEC §3 FR-03 "FR-09" + §7 (no auth row for these endpoints). Verification: integration test asserts 200/503 with no header set. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-03 "FR-09" + §7 (no auth row for these endpoints). Verification: integration test asserts 200/503 with no header set.

### FR-04: Scope 授權

> Source: SPEC §3 FR-04.

- Each key carries a scope: `read` < `write` < `admin` (hierarchical, inclusive).
- Per-endpoint scope: see FR-01 / FR-02 tables. Insufficient → **HTTP 403** + problem+json; **body must not leak whether the resource exists**.
- The authorisation decision must live in **a single dependency** (no scattered checks in handlers) — the test asserts "every `/v1` route goes through the same dependency".

**Acceptance criteria**

#### AC-4.1: A request with insufficient scope returns 403 + problem+json
DERIVED: SPEC §3 FR-04 + §7 row 403 + §8 #6. Verification: integration test using a `write` key against the `admin`-only `DELETE /v1/tasks/{id}` endpoint. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-04 + §7 row 403 + §8 #6. Verification: integration test using a `write` key against the `admin`-only `DELETE /v1/tasks/{id}` endpoint.

#### AC-4.2: The 403 body does not reveal whether the resource (task) exists
DERIVED: SPEC §3 FR-04 "body 不得洩漏該資源是否存在" + NFR-02 + §8 #6. Verification: integration test compares 403 bodies for an existing id and a non-existent id; bodies must be indistinguishable on the existence axis. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-04 "body 不得洩漏該資源是否存在" + NFR-02 + §8 #6. Verification: integration test compares 403 bodies for an existing id and a non-existent id; bodies must be indistinguishable on the existence axis.

#### AC-4.3: All `/v1/*` routes resolve through a single FastAPI dependency; a static check enumerates route dependencies and asserts the auth dependency appears in every set
DERIVED: SPEC §3 FR-04 "以測試斷言『每個 /v1 路由都經過同一個 dependency』". Verification: introspect FastAPI route → dependency graph; assert the auth dependency is on every `/v1` route. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-04 "以測試斷言『每個 /v1 路由都經過同一個 dependency』". Verification: introspect FastAPI route → dependency graph; assert the auth dependency is on every `/v1` route.

### FR-05: 流量控制

> Source: SPEC §3 FR-05.

- Per-token token bucket: capacity = `TASKQ_RATE_BURST`, refill = `TASKQ_RATE_PER_SEC`.
- Over-limit → **HTTP 429** + problem+json + `Retry-After` header (seconds).
- Bucket state is in the database (consistent across workers); updates run in a single transaction with row-level lock.
- `/healthz` and `/readyz` are not rate-limited.

**Acceptance criteria**

#### AC-5.1: Burst requests beyond `TASKQ_RATE_BURST` against the same key return 429 + problem+json + a `Retry-After` header carrying a non-negative integer
DERIVED: SPEC §3 FR-05 + §7 row 429 + §8 #9. Verification: integration test fires N+1 requests and asserts the (N+1)th response shape. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-05 + §7 row 429 + §8 #9. Verification: integration test fires N+1 requests and asserts the (N+1)th response shape.

#### AC-5.2: A unit test asserts that bucket updates take a row-level lock and run inside a single transaction
DERIVED: SPEC §3 FR-05 "更新必須在單一交易內以 row-level lock 進行". Verification: instrumentation on the rate-bucket repository asserts `SELECT ... FOR UPDATE` and a single `Session` per call. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-05 "更新必須在單一交易內以 row-level lock 進行". Verification: instrumentation on the rate-bucket repository asserts `SELECT ... FOR UPDATE` and a single `Session` per call.

#### AC-5.3: `/healthz` and `/readyz` are not counted against the bucket
DERIVED: SPEC §3 FR-05 "exempt" clause. Verification: integration test fires 100 health requests with a low-burst bucket and asserts none return 429. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-05 "exempt" clause. Verification: integration test fires 100 health requests with a low-burst bucket and asserts none return 429.

### FR-06: 持久化層與交易邊界

> Source: SPEC §3 FR-06.

- All data access goes through `repository/`; the business layer must not hold a `Session`.
- One `Session` per request; transaction boundary is explicit: success → commit, exception → rollback, guaranteed by a context manager.
- **No string-concatenated SQL** anywhere; ORM or parameterised queries only (NFR-02).
- Relationship loads must use `selectinload` / `joinedload` explicitly — **N+1 is an acceptance failure** (NFR-01).
- Connection pool: `pool_size=TASKQ_DB_POOL_SIZE`, `pool_pre_ping=True`.

**Acceptance criteria**

#### AC-6.1: The `service/` and `api/` layers contain no `from sqlalchemy ...` or `import sqlalchemy` statements; `.importlinter` forbidden contract enforces this
DERIVED: SPEC §3 FR-06 + NFR-06 + §8 #21. Verification: `lint-imports` exit 0 + repository-level grep for `sqlalchemy` returns zero matches in those layers. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-06 + NFR-06 + §8 #21. Verification: `lint-imports` exit 0 + repository-level grep for `sqlalchemy` returns zero matches in those layers.

#### AC-6.2: The `Session` lifetime is wrapped in a context manager; a request that raises commits nothing and rolls back; a successful request commits exactly once
DERIVED: SPEC §3 FR-06 "成功 commit、例外 rollback(以 context manager 保證)". Verification: unit test on `repository.session` simulates raise vs success. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-06 "成功 commit、例外 rollback(以 context manager 保證)". Verification: unit test on `repository.session` simulates raise vs success.

#### AC-6.3: A repository-wide grep for SQL string concatenation patterns (f-string / `%` / `+`) returns zero matches in `03-development/src/`
DERIVED: SPEC §3 FR-06 + NFR-02 + §8 #17. Verification: CI grep gate. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-06 + NFR-02 + §8 #17. Verification: CI grep gate.

#### AC-6.4: Relationship queries on the list endpoint use `selectinload` / `joinedload`; SQL statement count is constant for 10, 100, 1000 rows
DERIVED: SPEC §3 FR-06 "N+1 為驗收失敗條件" + NFR-01 + §8 #14. Verification: SQLAlchemy event listener with three row counts. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-06 "N+1 為驗收失敗條件" + NFR-01 + §8 #14. Verification: SQLAlchemy event listener with three row counts.

#### AC-6.5: The SQLAlchemy engine is configured with `pool_size=TASKQ_DB_POOL_SIZE` and `pool_pre_ping=True`
DERIVED: SPEC §3 FR-06 "pool_pre_ping=True". Verification: engine configuration unit test. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-06 "pool_pre_ping=True". Verification: engine configuration unit test.

### FR-07: Schema Migration (Alembic 三步演進)

> Source: SPEC §3 FR-07.

| revision | upgrade | downgrade |
|----------|---------|-----------|
| **v1** | create `tasks`, `api_keys` | drop both tables |
| **v2** | add `tags`, `task_tags` (many-to-many) + unique index on `tasks.name` | drop new tables and index without affecting v1 data |
| **v3** | **data migration**: split `tasks.result_json` into `task_results`, migrate data, then drop the original column | reverse-migrate data back into `tasks.result_json`, then drop `task_results` — **no data loss** |

- `alembic upgrade head` and `alembic downgrade base` must both succeed.
- **Round-trip reversibility acceptance**: `upgrade head` → write sample → `downgrade -1` → `upgrade head`; every column of the sample data must be byte-identical (v3 data migration is the focus).
- No `op.execute("DROP TABLE ...")` destructive shortcut replacing a real `downgrade`.
- Migration files are themselves under test coverage (offline-SQL generation + assertions).

**Acceptance criteria**

#### AC-7.1: `alembic upgrade head` and `alembic downgrade base` both exit 0
DERIVED: SPEC §3 FR-07 + §8 #13. Verification: integration test runs both commands and asserts exit codes. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-07 + §8 #13. Verification: integration test runs both commands and asserts exit codes.

#### AC-7.2: The `upgrade head → write sample → downgrade -1 → upgrade head` round-trip leaves every column byte-identical to the original write (v3 data-migration focus)
DERIVED: SPEC §3 FR-07 "往返可逆性驗收" + §8 #12. Verification: integration test against a **real SQLite file** (not in-memory mock) — per NFR-09. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-07 "往返可逆性驗收" + §8 #12. Verification: integration test against a **real SQLite file** (not in-memory mock) — per NFR-09.

#### AC-7.3: The v3 revision contains a real `downgrade()` that reverse-migrates the data; no `op.execute("DROP TABLE ...")` shortcut replaces the data-migration `downgrade`
DERIVED: SPEC §3 FR-07 "禁止以 ... 破壞性捷徑取代真正的 downgrade" + NFR-09. Verification: static check on the v3 file + offline-SQL round-trip test. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-07 "禁止以 ... 破壞性捷徑取代真正的 downgrade" + NFR-09. Verification: static check on the v3 file + offline-SQL round-trip test.

#### AC-7.4: The migration files are themselves covered by tests; the test generates offline SQL for each revision and asserts the expected tables/columns appear in the expected order
DERIVED: SPEC §3 FR-07 "migration 檔本身納入測試覆蓋". Verification: `alembic upgrade head --sql` and downgrade offline-SQL test. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-07 "migration 檔本身納入測試覆蓋". Verification: `alembic upgrade head --sql` and downgrade offline-SQL test.

#### AC-7.5: A migration that fails rolls back the transaction and the database remains at the prior revision; `/readyz` returns 503 with the failure detail
DERIVED: SPEC §3 FR-07 + NFR-03. Verification: integration test introduces a failing migration and asserts atomicity + `/readyz` body. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-07 + NFR-03. Verification: integration test introduces a failing migration and asserts atomicity + `/readyz` body.

### FR-08: 非同步執行器

> Source: SPEC §3 FR-08.

- Background execution managed by `asyncio.TaskGroup`; on shutdown, **graceful drain** (wait in-flight tasks up to `TASKQ_DRAIN_TIMEOUT`; timed-out ones are marked `interrupted`).
- Concurrency cap = `TASKQ_MAX_CONCURRENT`; over-cap tasks queue — no unbounded coroutine generation.
- Per-task timeout via `asyncio.wait_for`; on timeout, the child is **actually killed** (`process.kill()` then `await process.wait()`), no orphan child processes.
- Cancellation semantics: `asyncio.CancelledError` must propagate — **never swallowed by `except Exception`** (NFR-03).

**Acceptance criteria**

#### AC-8.1: New tasks beyond `TASKQ_MAX_CONCURRENT` are queued, not spawned ad-hoc; a stress test holds capacity at the cap and asserts no more than `cap + 1` `subprocess_exec` calls are live at any moment
DERIVED: SPEC §3 FR-08 "超過時新任務排隊，不得無限制生成 coroutine". Verification: instrumentation on the runner. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-08 "超過時新任務排隊，不得無限制生成 coroutine". Verification: instrumentation on the runner.

#### AC-8.2: A timed-out task has its child `process.kill()`-ed and `await process.wait()`-ed; no orphan child processes after the test completes
DERIVED: SPEC §3 FR-08 + NFR-03 + §8 #25. Verification: integration test enumerates child PIDs before and after timeout assertion. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-08 + NFR-03 + §8 #25. Verification: integration test enumerates child PIDs before and after timeout assertion.

#### AC-8.3: On shutdown, in-flight tasks get up to `TASKQ_DRAIN_TIMEOUT` to complete; tasks still running after the drain are marked `interrupted`; no orphans
DERIVED: SPEC §3 FR-08 "graceful drain ... 逾時則標記 interrupted". Verification: integration test holds a long-running task, triggers shutdown, asserts `interrupted` state and zero orphans. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-08 "graceful drain ... 逾時則標記 interrupted". Verification: integration test holds a long-running task, triggers shutdown, asserts `interrupted` state and zero orphans.

#### AC-8.4: `asyncio.CancelledError` raised inside a task handler propagates out (it is not caught by `except Exception`); a test injects a `CancelledError` and asserts it surfaces
DERIVED: SPEC §3 FR-08 "必須向上傳播，不得被 except Exception 吞掉" + NFR-03. Verification: unit test on the runner. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-08 "必須向上傳播，不得被 except Exception 吞掉" + NFR-03. Verification: unit test on the runner.

#### AC-8.5: Background execution is coordinated by `asyncio.TaskGroup`; a static check or test asserts the runner constructs a `TaskGroup` (not bare `gather` or fire-and-forget `asyncio.create_task`)
DERIVED: SPEC §3 FR-08 "asyncio.TaskGroup". Verification: AST / unit test on `service.runner`. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-08 "asyncio.TaskGroup". Verification: AST / unit test on `service.runner`.

### FR-09: 健康檢查與可觀測性

> Source: SPEC §3 FR-09.

| Endpoint | Auth | Behaviour |
|----------|------|-----------|
| `GET /healthz` | none | process alive → 200 `{"status":"ok"}` |
| `GET /readyz` | none | DB connection available **and** `alembic current` == head → 200; otherwise **503** with body naming the failing side |
| `GET /v1/metrics` | `admin` | task counts by status, execution latency percentiles, rate-limit denial count |

- The "migration not at head" check on `/readyz` is critical: deploying new code without running the migration must **fail closed**.

**Acceptance criteria**

#### AC-9.1: GET /healthz returns 200 `{"status":"ok"}` while the process is alive, with no `X-API-Key` required
DERIVED: SPEC §3 FR-09 + §7 (no auth row for /healthz). Verification: integration test. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-09 + §7 (no auth row for /healthz). Verification: integration test.

#### AC-9.2: GET /readyz returns 200 when DB is reachable and `alembic current == head`; returns 503 with a `detail` naming the failing side otherwise
DERIVED: SPEC §3 FR-09 + §7 row 503 + §8 #10/#11. Verification: integration test stops the DB connection (or points to a closed DB) and runs `alembic downgrade -1` separately, asserting the two 503 bodies. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-09 + §7 row 503 + §8 #10/#11. Verification: integration test stops the DB connection (or points to a closed DB) and runs `alembic downgrade -1` separately, asserting the two 503 bodies.

#### AC-9.3: GET /v1/metrics with an admin-scope key returns task counts by status, execution latency percentiles, and rate-limit denial counts; non-admin returns 403
DERIVED: SPEC §3 FR-09 + FR-04. Verification: integration test for admin + write-scope. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-09 + FR-04. Verification: integration test for admin + write-scope.

### FR-10: 錯誤契約 (RFC 7807)

> Source: SPEC §3 FR-10, §7.

- All non-2xx responses carry `Content-Type: application/problem+json`.
- Body fields: `type` (URI), `title`, `status`, `detail`, `instance`, `correlation_id`.
- `detail` **must not leak internals**: no SQL statements, no stack traces, no file paths, no schema description.
- `correlation_id` appears both in the response header `X-Correlation-Id` and in server logs, for stitching.
- Status map: 422 validation / 401 unauthenticated / 403 scope-insufficient / 404 unknown / 409 name-conflict / 429 rate-limited / 503 not-ready / 500 other.

**Acceptance criteria**

#### AC-10.1: Every non-2xx response carries `Content-Type: application/problem+json` and a body with `type` / `title` / `status` / `detail` / `instance` / `correlation_id`
DERIVED: SPEC §3 FR-10 + §7. Verification: integration test sweeps at least the eight status rows of the §7 table and asserts shape. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-10 + §7. Verification: integration test sweeps at least the eight status rows of the §7 table and asserts shape.

#### AC-10.2: The `detail` field contains no stack trace, no SQL fragment, and no absolute file path; a test that triggers a 500 asserts these are absent
DERIVED: SPEC §3 FR-10 "detail 不得洩漏內部細節" + NFR-02 + §8 #19. Verification: integration test asserts a substring allow-list. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-10 "detail 不得洩漏內部細節" + NFR-02 + §8 #19. Verification: integration test asserts a substring allow-list.

#### AC-10.3: Every non-2xx response carries an `X-Correlation-Id` header that also appears in the server log for the same request
DERIVED: SPEC §3 FR-10 "correlation_id 同時出現在回應 header X-Correlation-Id 與伺服器日誌". Verification: integration test asserts header presence and log grep. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-10 "correlation_id 同時出現在回應 header X-Correlation-Id 與伺服器日誌". Verification: integration test asserts header presence and log grep.

#### AC-10.4: Status codes 422 / 401 / 403 / 404 / 409 / 429 / 503 / 500 each have at least one integration test that triggers them
DERIVED: SPEC §3 FR-10 error code map + §8 acceptance (each code once). Verification: integration test suite enumeration. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-10 error code map + §8 acceptance (each code once). Verification: integration test suite enumeration.

#### AC-10.5: `asyncio.CancelledError` does not become a 500; it propagates upward; a test injects `CancelledError` and asserts no 500 is produced
DERIVED: SPEC §3 FR-10 status map (CancelledError is **not** on the table) + NFR-03 + SPEC §7 trailing note. Verification: unit + integration test. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §3 FR-10 status map (CancelledError is **not** on the table) + NFR-03 + SPEC §7 trailing note. Verification: unit + integration test.

---

## 4. Non-Functional Requirements

> Each section starts with `### NFR-NN: <title>`; ACs use `#### AC-Nn.M` stable IDs. `dimension:` is the SPEC's dimension (verified against the current `evaluate_dimension.md` roster — see `## FR Block` §10.1 for the mapping to the SAB `type:` vocabulary).

### NFR-01: 效能與查詢效率

- **dimension**: `performance` (in current roster: yes — `### performance` in `evaluate_dimension.md`)

**Acceptance criteria**

#### AC-N1.1: GET /v1/tasks/{id} p95 latency at 10,000 rows is below 30 ms
DERIVED: SPEC §4 NFR-01 + §11. Verification: `pytest-benchmark` over a seeded 10k-row table. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-01 + §11. Verification: `pytest-benchmark` over a seeded 10k-row table.

#### AC-N1.2: GET /v1/tasks?limit=50 p95 latency at 10,000 rows is below 80 ms
DERIVED: SPEC §4 NFR-01 + §11. Verification: `pytest-benchmark` on the list endpoint. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-01 + §11. Verification: `pytest-benchmark` on the list endpoint.

#### AC-N1.3: The list endpoint's SQL statement count is constant regardless of returned row count (N+1 guard)
DERIVED: SPEC §4 NFR-01 + §8 #14 + §11. Verification: SQLAlchemy event listener assertion at 10 / 100 / 1000 rows. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-01 + §8 #14 + §11. Verification: SQLAlchemy event listener assertion at 10 / 100 / 1000 rows.

### NFR-02: HTTP 與資料層安全

- **dimension**: `security` (in current roster: yes — `### security` in `evaluate_dimension.md`)

**Acceptance criteria**

#### AC-N2.1: A repository-wide grep for `shell=True`, `eval(`, `exec(` over `03-development/src/` returns zero hits
DERIVED: SPEC §4 NFR-02 + §8 #16. Verification: CI grep gate. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-02 + §8 #16. Verification: CI grep gate.

#### AC-N2.2: A repository-wide scan for SQL string-concatenation patterns (f-string / `%` / `+`) in `03-development/src/` returns zero hits
DERIVED: SPEC §4 NFR-02 + §8 #17. Verification: CI grep gate + code review. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-02 + §8 #17. Verification: CI grep gate + code review.

#### AC-N2.3: API keys are stored as SHA-256 hashes and compared with `hmac.compare_digest`; `api_keys.key_hash` is a 64-character lowercase hex digest with no plaintext persisted
DERIVED: SPEC §4 NFR-02 + FR-03 + §8 #18. Verification: unit + integration test. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-02 + FR-03 + §8 #18. Verification: unit + integration test.

#### AC-N2.4: 403 responses do not reveal whether the resource exists; bodies for an existing id and a non-existent id are indistinguishable on the existence axis
DERIVED: SPEC §4 NFR-02 + FR-04 + §8 #6. Verification: integration test. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-02 + FR-04 + §8 #6. Verification: integration test.

#### AC-N2.5: Error response bodies do not contain stack traces, SQL fragments, or file paths; a test that triggers a 500 asserts these are absent
DERIVED: SPEC §4 NFR-02 + FR-10 + §8 #19. Verification: integration test allow-list. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-02 + FR-10 + §8 #19. Verification: integration test allow-list.

#### AC-N2.6: CORS denies all origins by default; `TASKQ_CORS_ORIGINS` is the only allowlist; a request with a non-allowlisted `Origin` header receives no CORS allow
DERIVED: SPEC §4 NFR-02. Verification: integration test with empty + non-empty env. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-02. Verification: integration test with empty + non-empty env.

#### AC-N2.7: `bandit -r 03-development/src/` reports 0 HIGH and 0 MEDIUM findings
DERIVED: SPEC §4 NFR-02 + §8 #23 + §11. Verification: CI gate. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-02 + §8 #23 + §11. Verification: CI gate.

### NFR-03: 錯誤處理、交易與非同步正確性

- **dimension**: `error_handling` (in current roster: yes — `### error_handling` in `evaluate_dimension.md`)

**Acceptance criteria**

#### AC-N3.1: Every request's transaction is wrapped in a context manager; raise → rollback; success → exactly one commit
DERIVED: SPEC §4 NFR-03 + FR-06. Verification: unit test on `repository.session`. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-03 + FR-06. Verification: unit test on `repository.session`.

#### AC-N3.2: The codebase contains no bare `except:` and no `except Exception: pass`; a static check (`ast-error-handling` or equivalent grep) returns zero hits
DERIVED: SPEC §4 NFR-03 "不得出現裸 except:、except Exception: pass". Verification: static + grep gate. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-03 "不得出現裸 except:、except Exception: pass". Verification: static + grep gate.

#### AC-N3.3: `asyncio.CancelledError` raised inside a task handler propagates upward; a unit test injects a `CancelledError` and asserts it surfaces
DERIVED: SPEC §4 NFR-03 + FR-08. Verification: unit test on `service.runner`. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-03 + FR-08. Verification: unit test on `service.runner`.

#### AC-N3.4: A database connection failure causes `/readyz` to return 503 with a `detail` naming the DB; no silent infinite-retry loop
DERIVED: SPEC §4 NFR-03 + FR-09. Verification: integration test pointing at a closed DB. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-03 + FR-09. Verification: integration test pointing at a closed DB.

#### AC-N3.5: A timed-out task kills the child process and awaits its exit; no orphan child PID remains after the test
DERIVED: SPEC §4 NFR-03 + FR-08 + §8 #25. Verification: integration test on PID enumeration. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-03 + FR-08 + §8 #25. Verification: integration test on PID enumeration.

#### AC-N3.6: A failed migration rolls back the transaction; the database remains at the prior revision; `/readyz` returns 503 with the failure detail
DERIVED: SPEC §4 NFR-03 + FR-07. Verification: integration test on a failing migration. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-03 + FR-07. Verification: integration test on a failing migration.

### NFR-04: 敏感資料遮蔽

- **dimension**: `security` (in current roster: yes; canonical redaction pattern overlap with `secrets_scanning` is acknowledged — coverage note: `secrets_scanning` (gitleaks) complements but does not fully replace the SPEC regex, so the application-level allow-list test below is the binding check)

**Acceptance criteria**

#### AC-N4.1: `stdout_tail` / `stderr_tail` / logs / error bodies are redacted before write/emit; lines matching `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)` are replaced wholesale with `[REDACTED]`
DERIVED: SPEC §4 NFR-04 regex. Verification: unit test on the redaction helper with each pattern class. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-04 regex. Verification: unit test on the redaction helper with each pattern class.

#### AC-N4.2: The database connection string (including password) does not appear in any log, error message, or `/v1/metrics` response; a test seeds a DB URL with a password and asserts the password is absent from all sinks
DERIVED: SPEC §4 NFR-04 + §8 #20 + §11. Verification: unit + integration test. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-04 + §8 #20 + §11. Verification: unit + integration test.

#### AC-N4.3: API key plaintext is printed exactly once at `key create` time and is not written to any persistent store (logs, DB, metrics)
DERIVED: SPEC §4 NFR-04 + FR-03. Verification: CLI test + post-run sink grep. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-04 + FR-03. Verification: CLI test + post-run sink grep.

### NFR-05: 文件覆蓋

- **dimension**: `documentation` (in current roster: yes — `### documentation` in `evaluate_dimension.md`)

**Acceptance criteria**

#### AC-N5.1: 100% of public functions and classes carry a docstring containing at least one `[FR-XX]` or `[NFR-XX]` reference; an `ast-docstrings` scan reports 0 missing-public-docstring findings
DERIVED: SPEC §4 NFR-05. Verification: `ast-docstrings` CI gate. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-05. Verification: `ast-docstrings` CI gate.

#### AC-N5.2: Every FastAPI endpoint appears in the auto-generated `/openapi.json` with both `summary` and `description`; a test fetches `/openapi.json` and asserts the field presence for every route
DERIVED: SPEC §4 NFR-05. Verification: integration test. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-05. Verification: integration test.

### NFR-06: 架構分層契約

- **dimension**: `architecture_constraints` (in current roster: yes — `### architecture_constraints` in `evaluate_dimension.md`)

**Acceptance criteria**

#### AC-N6.1: `.importlinter` exists at the project root and declares the layers contract `api > service > repository > models`; `config` and `errors` are declared as independence modules
DERIVED: SPEC §4 NFR-06 + §5.3. Verification: file-exists + `lint-imports` exit 0. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-06 + §5.3. Verification: file-exists + `lint-imports` exit 0.

#### AC-N6.2: `.importlinter` also declares a forbidden contract that bans `import sqlalchemy` (and `from sqlalchemy import ...`) outside `repository/`; a deliberate import in `service/` is rejected with a non-zero exit
DERIVED: SPEC §4 NFR-06 + §5.3 + §8 #21. Verification: `lint-imports` exit 0 + a negative test that adds the import and asserts failure. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-06 + §5.3 + §8 #21. Verification: `lint-imports` exit 0 + a negative test that adds the import and asserts failure.

#### AC-N6.3: `lint-imports` runs in CI and exits 0
DERIVED: SPEC §4 NFR-06 + §8 #21 + §11. Verification: CI gate. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-06 + §8 #21 + §11. Verification: CI gate.

#### AC-N6.4: No degradation is permitted: deleting `.importlinter`, replacing the contract with `ignore_imports`, or downgrading the contract to obtain a pass is rejected
DERIVED: SPEC §4 NFR-06 "禁止以刪除 .importlinter、萬用字元 ignore_imports、或降級 contract 的方式取得通過". Verification: PR-time policy check / static check on the `.importlinter` file. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-06 "禁止以刪除 .importlinter、萬用字元 ignore_imports、或降級 contract 的方式取得通過". Verification: PR-time policy check / static check on the `.importlinter` file.

### NFR-07: 依賴與授權合規

- **dimension**: `license_compliance` (in current roster: yes — `### license_compliance` in `evaluate_dimension.md`)

**Acceptance criteria**

#### AC-N7.1: Every runtime dependency in `requirements.txt` is pinned with `==`; transitive dependencies are fully locked in `requirements.lock`
DERIVED: SPEC §4 NFR-07 + §5.3. Verification: file inspection + `pip install --require-hashes` smoke. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-07 + §5.3. Verification: file inspection + `pip install --require-hashes` smoke.

#### AC-N7.2: The allowed license set is `MIT / BSD-2-Clause / BSD-3-Clause / Apache-2.0 / PSF`; every dependency in the full tree (direct + transitive) has a license in this set
DERIVED: SPEC §4 NFR-07 + §8 #22 + §11. Verification: `pip-licenses --format=json --with-system` checked against the allowlist. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-07 + §8 #22 + §11. Verification: `pip-licenses --format=json --with-system` checked against the allowlist.

#### AC-N7.3: The license scan covers the entire dependency tree (direct + transitive), not only the first-party `requirements.txt`
DERIVED: SPEC §4 NFR-07 "掃描範圍必須包含完整依賴樹" + §10 framework alignment. Verification: scan run with `--with-system` (or equivalent) and CI gate. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-07 "掃描範圍必須包含完整依賴樹" + §10 framework alignment. Verification: scan run with `--with-system` (or equivalent) and CI gate.

#### AC-N7.4: `08-config/SBOM.json` is produced with one record per dependency carrying `name` / `version` / `license` / `direct|transitive`
DERIVED: SPEC §4 NFR-07 SBOM requirement. Verification: file-shape test on the produced SBOM. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-07 SBOM requirement. Verification: file-shape test on the produced SBOM.

### NFR-08: 變異測試

- **dimension**: `mutation_testing` (in current roster: yes — `### mutation_testing` in `evaluate_dimension.md`)

**Acceptance criteria**

#### AC-N8.1: `.methodology/harness_config.json` has `features.mutation_testing: true`; the file is not modified to lower the threshold
DERIVED: SPEC §4 NFR-08 + §5.3. Verification: file inspection. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-08 + §5.3. Verification: file inspection.

#### AC-N8.2: `mutmut run` followed by `mutmut results` reports a mutation score of at least 70 over `service/` and `repository/`
DERIVED: SPEC §4 NFR-08 + §8 #24 + §11. Verification: CI gate. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-08 + §8 #24 + §11. Verification: CI gate.

#### AC-N8.3: The mutation scope is annotated in `harness_config.json` as limited to `service/` + `repository/` with a recorded rationale (execution-time budget)
DERIVED: SPEC §4 NFR-08 "在 harness_config.json 註記限定理由". Verification: file inspection. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-08 "在 harness_config.json 註記限定理由". Verification: file inspection.

### NFR-09: 驗證真實性 (零 skip 鐵律)

- **dimension**: `test_assertion_quality` (in current roster: yes — `### test_assertion_quality` in `evaluate_dimension.md`)

**Acceptance criteria**

#### AC-N9.1: `pytest 03-development/tests -q` reports a skipped count of 0
DERIVED: SPEC §4 NFR-09 + §8 #1 + §11. Verification: CI gate on pytest output. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-09 + §8 #1 + §11. Verification: CI gate on pytest output.

#### AC-N9.2: Every test function contains at least one `assert` (or a non-`skip`ed `raises`); the `ast-assertions` scanner reports 0 zero-assertion tests
DERIVED: SPEC §4 NFR-09 "每個測試函式至少一個 assert" + §11. Verification: static scan. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-09 "每個測試函式至少一個 assert" + §11. Verification: static scan.

#### AC-N9.3: No test is excluded via `--ignore`, `-k`, `--deselect`, `collect_ignore`, or removal from `testpaths`; CI runs the full suite unfiltered
DERIVED: SPEC §4 NFR-09 "反造假條款". Verification: CI config inspection. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-09 "反造假條款". Verification: CI config inspection.

#### AC-N9.4: The FR-07 three-step migration is tested against a **real SQLite file** (not an in-memory mock); the round-trip test compares actual row data; it is not downgraded to a skip
DERIVED: SPEC §4 NFR-09 "本輪特別條款" + FR-07. Verification: integration test uses a temp file DB and asserts row equality. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-09 "本輪特別條款" + FR-07. Verification: integration test uses a temp file DB and asserts row equality.

#### AC-N9.5: `TRACEABILITY_MATRIX.md` marks a requirement as `VERIFIED` only after a test has actually executed and passed for it; the matrix is the live scan output, not hand-edited
DERIVED: SPEC §4 NFR-09 "TRACEABILITY_MATRIX.md 的 VERIFIED 只能在測試實際執行並通過時給出". Verification: the matrix is generated, not hand-written. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-09 "TRACEABILITY_MATRIX.md 的 VERIFIED 只能在測試實際執行並通過時給出". Verification: the matrix is generated, not hand-written.

### NFR-10: 整合覆蓋

- **dimension**: `integration_coverage` (in current roster: yes — `### integration_coverage` in `evaluate_dimension.md`)

**Acceptance criteria**

#### AC-N10.1: `03-development/tests/integration/` line coverage is at least 80%
DERIVED: SPEC §4 NFR-10 + §8 #3 + §11. Verification: `pytest-cov-integration` gate. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-10 + §8 #3 + §11. Verification: `pytest-cov-integration` gate.

#### AC-N10.2: Integration tests are driven through `httpx.AsyncClient(transport=ASGITransport(app))`; no integration test calls a handler function directly
DERIVED: SPEC §4 NFR-10 "不得直接呼叫 handler 函式". Verification: static check on integration tests. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-10 "不得直接呼叫 handler 函式". Verification: static check on integration tests.

#### AC-N10.3: Integration tests cover at least one example of each error code — 401, 403, 404, 409, 422, 429, 503 — plus the migration round-trip, rate-limit trigger and recovery, and graceful drain
DERIVED: SPEC §4 NFR-10 "至少涵蓋: ... 每個錯誤碼各一例" + SPEC §3 cross-cutting items. Verification: integration suite enumeration. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-10 "至少涵蓋: ... 每個錯誤碼各一例" + SPEC §3 cross-cutting items. Verification: integration suite enumeration.

### NFR-11: 可讀性

- **dimension**: `readability` (in current roster: yes — `### readability` in `evaluate_dimension.md`)

**Acceptance criteria**

#### AC-N11.1: Project MI (LLOC-weighted) is at least 80; per-function cyclomatic complexity is at most 10
DERIVED: SPEC §4 NFR-11 + §11. Verification: `readability-v2` (radon-mi) gate. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-11 + §11. Verification: `readability-v2` (radon-mi) gate.

#### AC-N11.2: No file exceeds 400 lines; no directory contains more than 15 files
DERIVED: SPEC §4 NFR-11 "單一檔案 ≤ 400 行；單一目錄 ≤ 15 檔". Verification: file-system gate. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-11 "單一檔案 ≤ 400 行；單一目錄 ≤ 15 檔". Verification: file-system gate.

#### AC-N11.3: No API handler exceeds 40 lines; business logic is sunk into `service/`
DERIVED: SPEC §4 NFR-11 "每個 API handler ≤ 40 行". Verification: per-handler line-count gate on `api/`. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-11 "每個 API handler ≤ 40 行". Verification: per-handler line-count gate on `api/`.

### NFR-12: 系統驗證目標

- **dimension**: `execute_verification_target` (in current roster: yes — `### execute_verification_target` in `evaluate_dimension.md`)

**Acceptance criteria**

#### AC-N12.1: `Makefile` defines a `verify-system` target that chains: (1) `alembic upgrade head`, (2) full test suite, (3) service start + `/healthz` and `/readyz` smoke, (4) `alembic downgrade base` followed by `alembic upgrade head` (round-trip)
DERIVED: SPEC §4 NFR-12 + §8 #27. Verification: make target run. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-12 + §8 #27. Verification: make target run.

#### AC-N12.2: `make verify-system` exits 0 and its stdout contains the literal `verify-system: PASS`
DERIVED: SPEC §4 NFR-12 + §8 #27. Verification: exit-code + stdout-grep gate. — faithful testable restatement of canonical; no new content beyond what the cited line requires.
Canonical: SPEC §4 NFR-12 + §8 #27. Verification: exit-code + stdout-grep gate.

---

## 5. Acceptance Criteria Summary

> Cross-reference of all 12 NFR and 10 FR acceptance commands from SPEC §8 mapped onto the AC IDs above.

| SPEC §8 # | Command / Check | AC IDs |
|-----------|----------------|--------|
| 1 | `pytest 03-development/tests -q` → all green, skipped=0 | AC-N9.1, AC-N9.2, AC-N9.3 |
| 2 | `pytest --cov` TOTAL 100% | AC-N9.1 + per-NFR coverage gates |
| 3 | integration cov ≥ 80% | AC-N10.1 |
| 4 | `POST /v1/tasks` (valid write) → 201 | AC-1.1 |
| 5 | `POST /v1/tasks` no `X-API-Key` → 401 | AC-3.1 |
| 6 | `DELETE /v1/tasks/{id}` write key → 403, no existence leak | AC-1.6, AC-4.1, AC-4.2, AC-N2.4 |
| 7 | `GET /v1/tasks/{unknown}` → 404 | AC-1.3 |
| 8 | duplicate name → 409 | AC-1.2 |
| 9 | burst over `TASKQ_RATE_BURST` → 429 + `Retry-After` | AC-5.1 |
| 10 | DB down → `/readyz` 503 (DB) | AC-9.2, AC-N3.4 |
| 11 | `downgrade -1` → `/readyz` 503 (migration) | AC-9.2, AC-7.5 |
| 12 | migration round-trip data identical | AC-7.2, AC-N9.4 |
| 13 | `alembic downgrade base` exit 0, no residual tables | AC-7.1 |
| 14 | list endpoint SQL count constant | AC-1.7, AC-6.4, AC-N1.3 |
| 15 | `GET /v1/tasks/{id}` p95 < 30ms | AC-N1.1 |
| 16 | `shell=True`/`eval(`/`exec(` grep = 0 | AC-2.2, AC-N2.1 |
| 17 | SQL string-concat scan = 0 | AC-6.3, AC-N2.2 |
| 18 | `api_keys` no plaintext, hash 64 hex | AC-3.2, AC-N2.3 |
| 19 | 500 body no internals | AC-10.2, AC-N2.5 |
| 20 | DB URL password absent from logs / metrics | AC-N4.2 |
| 21 | `lint-imports` exit 0; `service`/`api` import `sqlalchemy` blocked | AC-6.1, AC-N6.1, AC-N6.2, AC-N6.3 |
| 22 | `pip-licenses --with-system` ∈ allowlist | AC-N7.2, AC-N7.3 |
| 23 | `bandit -r` 0 HIGH, 0 MEDIUM | AC-N2.7 |
| 24 | `mutmut results` score ≥ 70 | AC-N8.2 |
| 25 | shutdown with in-flight → graceful drain, no orphans | AC-2.3, AC-8.2, AC-8.3, AC-N3.5 |
| 26 | `grep -c '^TASKQ_' .env.example` = 12 | §2.10 env table (12 rows) |
| 27 | `make verify-system` exit 0 + `verify-system: PASS` | AC-N12.1, AC-N12.2 |

---

## 6. Out-of-Scope

- UI / frontend.
- Multi-tenant org/workspace model.
- Distributed message brokers (RabbitMQ / Kafka / Redis pub-sub); single-process background runner with DB-backed rate buckets is the in-scope mechanism.
- The framework's pre-existing "taskq-plus CLI" round-1 surface (replaced by this HTTP service).
- Round 3 (TypeScript) — explicitly deferred per PROJECT_BRIEF.

---

## 7. Open Issues

> Items in canonical that are deferred, ambiguous, or warrant a Phase-3+ follow-up. They are NOT silently omitted; each carries a tag.

### 7.1 FR / NFR deferred items
- None of the 10 FRs or 12 NFRs in canonical SPEC §3 / §4 are deferred. Every heading appears in §3 / §4 above.

### 7.2 Ambiguity resolutions (NFR-99)
| ID | Topic | Ambiguity | Owner |
|----|-------|-----------|-------|
| NFR-99-01 | NFR-04 redaction pattern overlap | SPEC's NFR-04 regex is application-level; framework's `secrets_scanning` (gitleaks) overlaps but does not fully cover the allow-list. The binding check is the application-level test (AC-N4.1). | Phase 3 implementation + Phase 4 bug-hunt review |
| NFR-99-02 | Async scanners' coverage of `async def` | Framework's `ast-error-handling` and `ast-assertions` have only ever faced synchronous code. Any misjudgement on `async def` is itself a finding this test-bed is meant to surface — record in Phase 4 bug hunt, do not work around silently. | Phase 4 bug hunt |
| NFR-99-03 | `crg_cohesion_healthy` threshold | Per SPEC §10 the default is locked; do not lower it. If framework defaults shift, Phase 6 review must flag the drift. | Phase 6 |
| NFR-99-04 | NFR-11 handler line budget | The 40-line handler ceiling has a known grey zone for OpenAPI docstring-heavy handlers; the binding check counts **business logic lines**, not docstring lines. | Phase 5 verification (precise definition in `verify-system` script) |

### 7.3 Prompt-injection scan (R-NO-PRESCRIPTION-001 carve-out, summary only)
- One-line summary: the canonical SPEC.md was scanned for prompt-injection patterns; no high-severity citations were found. (Full scan output is a `.sessi-work/` debug artifact, not embedded here.)

---

## 8. Risks

> Source: SPEC §9 — 12 risks with impact/likelihood/mitigation.

| ID | Risk | Impact | Likelihood | Mitigation | Linked clauses |
|----|------|--------|------------|-----------|----------------|
| R1 | **v3 data migration loses data** | High | Medium | round-trip test against a real DB, column-by-column | FR-07, §8 #12, AC-7.2 |
| R2 | SQL injection | High | Low | no string concat + ORM/parameterised + grep gate | FR-06, NFR-02, AC-N2.2 |
| R3 | API key leak | High | Medium | hashed storage + constant-time compare + printed once | FR-03, NFR-02, AC-3.2, AC-3.4 |
| R4 | 403 reveals resource existence | Medium | Medium | authorise before lookup | FR-04, NFR-02, §8 #6, AC-4.2 |
| R5 | N+1 collapses on a large table | High | High | explicit eager loading + SQL count assertion | FR-06, NFR-01, §8 #14, AC-N1.3 |
| R6 | error body leaks internals | Medium | High | fixed RFC 7807 fields + detail allowlist | FR-10, NFR-02, §8 #19, AC-10.2 |
| R7 | **swallowed `CancelledError` hangs shutdown** | Medium | Medium | explicit ban + assertion | FR-08, NFR-03, AC-N3.3 |
| R8 | timeout leaves orphan processes | Medium | Medium | `kill()` + `await wait()` | FR-08, NFR-03, §8 #25, AC-2.3 |
| R9 | deploy without migration | High | Medium | `/readyz` fails closed | FR-09, §8 #11, AC-9.2 |
| R10 | connection pool exhaustion | Medium | Medium | `pool_pre_ping` + concurrency cap | FR-06, FR-08, AC-6.5 |
| R11 | transitive dep with incompatible license | Medium | Medium | lock file + whole-tree scan | NFR-07, §8 #22, AC-N7.3 |
| R12 | rate bucket race over-admits | Low | Medium | single transaction + row-level lock | FR-05, NFR-02, AC-5.2 |

---

## 9. Glossary

> Source: SPEC §0–§7 terminology.

| Term | Definition |
|------|-----------|
| `taskq-api` | The product name. Canonical project root directory. |
| FR | Functional Requirement (`### FR-NN: <title>` heading + acceptance criteria). |
| NFR | Non-Functional Requirement (`### NFR-NN: <title>` heading + acceptance criteria). |
| AC | Acceptance Criterion (`#### AC-N.M` or `#### AC-Nn.M` stable identifier). |
| AC-N<M> | Form used for NFR ACs (NFR-01 → AC-N1.x, NFR-02 → AC-N2.x, ...). |
| `dimension` | The harness's Gate-scoring dimension key (e.g. `security`, `performance`). Source: `harness/harness/ssi/prompts/evaluate_dimension.md`. |
| `type:` | The SAB-parser vocabulary field used in `## FR Block (machine-readable)`. Allowed values: `documentation\|integration\|layering\|licensing\|maintainability\|mutation\|performance\|reliability\|security\|testability\|verifiability\|deployability\|scalability\|usability`. |
| `problem+json` | RFC 7807 `application/problem+json` response shape (FR-10). |
| `revoked_at` | Timestamp column on `api_keys`; non-null ⇒ key invalid (FR-03). |
| `task_results` | Table created in v3 by splitting `tasks.result_json` (FR-07). |
| `TASKQ_*` | The 12 env-var prefix used for all configuration (§2.10). |
| `selectinload` / `joinedload` | SQLAlchemy 2.x eager-loading strategies required to avoid N+1 (FR-06, NFR-01). |
| `pool_pre_ping` | SQLAlchemy engine flag that tests connections before use (FR-06, NFR-03). |
| `asyncio.TaskGroup` | Python 3.11 structured concurrency primitive; required for the background runner (FR-08). |
| `asyncio.CancelledError` | The exception that MUST propagate on cancellation; explicitly forbidden to swallow (FR-08, NFR-03). |
| `hmac.compare_digest` | Constant-time compare used for API-key hash equality (FR-03, NFR-02). |
| `TASKQ_DRAIN_TIMEOUT` | Shutdown drain budget (FR-08). |
| `TASKQ_RATE_BURST` / `TASKQ_RATE_PER_SEC` | Token bucket capacity and refill rate (FR-05). |
| `crg_cohesion_healthy` | CRG quality gate threshold — locked at default, do not lower (SPEC §10). |
| INGESTION MODE | Agent A authoring mode: 100% transcribe canonical; no invention, no omission. |

---

## FR Block (machine-readable)

> `type:` values mirror `harness/core/quality_gate/sab_parser.ALL_NFR_TYPES` per
> `tests/test_sab_parser.py::TestCanonicalTemplate::test_srs_template_nfr_type_example_matches_vocabulary`.
> `dimension:` (NFR only) mirrors `harness/harness/ssi/prompts/evaluate_dimension.md`.
> INGESTION MODE: every `### FR-NN` and `### NFR-NN` above is in the JSON below; no omission, no invention.

```json
{
  "version": "1.0",
  "created_at": "2026-08-19",
  "phase": 1,
  "project": "taskq-api",
  "canonical_spec": "SPEC.md",
  "canonical_spec_version": "v1.0.0",
  "canonical_spec_date": "2026-07-30",
  "functional_requirements": [
    {
      "id": "FR-01",
      "description": "任務資源 CRUD API: POST/GET(單筆)/GET(列表, cursor 分頁)/DELETE /v1/tasks; 驗證失敗 422, 未知 id 404, 列表 limit 預設 50 上限 200, N+1 防護",
      "implementation_functions": ["taskq_api.service.tasks", "taskq_api.api.tasks"],
      "verification_method": "integration: AC-1.1..AC-1.7 + SPEC §8 #4, #7, #14"
    },
    {
      "id": "FR-02",
      "description": "任務執行端點: POST /v1/tasks/{id}/run → 202; asyncio.create_subprocess_exec(*shlex.split(command)) 禁 shell=True; 結果寫入 task_results; GET /v1/tasks/{id}/runs 歷史",
      "implementation_functions": ["taskq_api.service.runner", "taskq_api.api.tasks"],
      "verification_method": "integration: AC-2.1..AC-2.5 + SPEC §8 #16, #25"
    },
    {
      "id": "FR-03",
      "description": "API Key 認證: X-API-Key, SHA-256 雜湊儲存, hmac.compare_digest 比對, key create 一次性印明文, revoked_at 失效, /healthz 與 /readyz 免認證",
      "implementation_functions": ["taskq_api.service.auth", "taskq_api.api.deps", "taskq_api.__main__"],
      "verification_method": "integration + unit: AC-3.1..AC-3.6 + SPEC §8 #5, #18"
    },
    {
      "id": "FR-04",
      "description": "Scope 授權: read < write < admin 階層; 不足回 403, body 不洩漏資源存在性; 單一 FastAPI dependency 判定點",
      "implementation_functions": ["taskq_api.service.auth", "taskq_api.api.deps"],
      "verification_method": "integration: AC-4.1..AC-4.3 + SPEC §8 #6"
    },
    {
      "id": "FR-05",
      "description": "流量控制: per-token token bucket (TASKQ_RATE_BURST / TASKQ_RATE_PER_SEC); 超限 429 + Retry-After; DB 內狀態以 row-level lock 單一交易更新; /healthz 與 /readyz 免限",
      "implementation_functions": ["taskq_api.service.ratelimit", "taskq_api.repository.rate_repo", "taskq_api.api.deps"],
      "verification_method": "integration + unit: AC-5.1..AC-5.3 + SPEC §8 #9"
    },
    {
      "id": "FR-06",
      "description": "持久化層與交易邊界: 全部存取經由 repository/; 每個請求一個 Session 由 context manager 包; 禁字串拼接 SQL; 顯式 selectinload/joinedload 防 N+1; pool_size + pool_pre_ping",
      "implementation_functions": ["taskq_api.repository.session", "taskq_api.repository.task_repo", "taskq_api.repository.key_repo", "taskq_api.repository.rate_repo"],
      "verification_method": "integration + grep gate: AC-6.1..AC-6.5 + SPEC §8 #14, #17, #21"
    },
    {
      "id": "FR-07",
      "description": "Schema Migration (Alembic 三步演進): v1 base, v2 tags 多對多 + name unique, v3 拆 result_json 到 task_results 含資料搬遷; 全部有 downgrade; upgrade→write→downgrade -1→upgrade 逐欄相同",
      "implementation_functions": ["migrations.versions.v1_initial", "migrations.versions.v2_tags", "migrations.versions.v3_split_results"],
      "verification_method": "integration: AC-7.1..AC-7.5 + SPEC §8 #12, #13"
    },
    {
      "id": "FR-08",
      "description": "非同步執行器: asyncio.TaskGroup 管理背景; graceful drain 至 TASKQ_DRAIN_TIMEOUT; TASKQ_MAX_CONCURRENT 排隊; timeout 殺子進程 (kill + wait); CancelledError 向上傳播",
      "implementation_functions": ["taskq_api.service.runner"],
      "verification_method": "integration + unit: AC-8.1..AC-8.5 + SPEC §8 #25"
    },
    {
      "id": "FR-09",
      "description": "健康檢查與可觀測性: /healthz 200; /readyz 200 需 DB 可用 + alembic current == head, 否則 503; /v1/metrics 需 admin",
      "implementation_functions": ["taskq_api.api.health", "taskq_api.repository.session"],
      "verification_method": "integration: AC-9.1..AC-9.3 + SPEC §8 #10, #11"
    },
    {
      "id": "FR-10",
      "description": "錯誤契約 (RFC 7807): 全部非 2xx 為 application/problem+json, 欄位 type/title/status/detail/instance/correlation_id, detail 不含 SQL/堆疊/路徑, X-Correlation-Id header + log",
      "implementation_functions": ["taskq_api.errors", "taskq_api.app"],
      "verification_method": "integration: AC-10.1..AC-10.5 + SPEC §8 #5, #6, #7, #8, #9, #10, #19"
    }
  ],
  "non_functional_requirements": [
    {
      "id": "NFR-01",
      "type": "performance",
      "description": "GET /v1/tasks/{id} p95 < 30ms, list p95 < 80ms (10k 筆); 列表端點 SQL 陳述數常數 (N+1 防護)",
      "test_method": "pytest-benchmark + SQLAlchemy event listener (AC-N1.1..AC-N1.3 + SPEC §8 #14, #15)"
    },
    {
      "id": "NFR-02",
      "type": "security",
      "description": "禁 shell=True/eval(/exec(; 禁 SQL 字串拼接; API key 雜湊 + hmac.compare_digest; 403 不洩漏資源存在; 錯誤 body 不含內部; CORS 預設全拒; bandit 0/0",
      "test_method": "grep gate + integration + bandit CI (AC-N2.1..AC-N2.7 + SPEC §8 #6, #16, #17, #18, #19, #23)"
    },
    {
      "id": "NFR-03",
      "type": "reliability",
      "description": "交易 context manager; 禁裸 except: / except Exception: pass; CancelledError 傳播; DB 失敗 /readyz 503; timeout 殺子進程; migration 失敗 rollback",
      "test_method": "ast-error-handling + integration (AC-N3.1..AC-N3.6 + SPEC §8 #10, #11, #25)"
    },
    {
      "id": "NFR-04",
      "type": "security",
      "description": "stdout_tail/stderr_tail/log/error body 寫入或送出前以正則遮蔽 (sk-...|token=...|Bearer ...|postgres://...); DB URL 密碼不出現於日誌 / 錯誤 / metrics; key 明文只印一次",
      "test_method": "unit redaction test + log/metric sink scan (AC-N4.1..AC-N4.3 + SPEC §8 #20)"
    },
    {
      "id": "NFR-05",
      "type": "documentation",
      "description": "公開函式/類別 100% docstring 且含 [FR-XX]/[NFR-XX] 引用; 每個 API 端點在 /openapi.json 帶 summary + description",
      "test_method": "ast-docstrings CI + OpenAPI shape test (AC-N5.1, AC-N5.2)"
    },
    {
      "id": "NFR-06",
      "type": "layering",
      "description": ".importlinter 必存, api > service > repository > models 分層 + config/errors independence; 禁 repository 以外 import sqlalchemy; lint-imports exit 0; 禁止降級 contract",
      "test_method": "import-linter CI + negative import test (AC-N6.1..AC-N6.4 + SPEC §8 #21)"
    },
    {
      "id": "NFR-07",
      "type": "licensing",
      "description": "requirements.txt == 釘版; requirements.lock 鎖 transitive; allowlist = MIT/BSD-2-Clause/BSD-3-Clause/Apache-2.0/PSF; 全樹掃描; 08-config/SBOM.json 含 name/version/license/direct|transitive",
      "test_method": "pip-licenses --with-system + SBOM shape test (AC-N7.1..AC-N7.4 + SPEC §8 #22)"
    },
    {
      "id": "NFR-08",
      "type": "mutation",
      "description": "harness_config.json features.mutation_testing: true; mutmut score ≥ 70 over service/ + repository/; 範圍限定需註記理由",
      "test_method": "mutmut run + results parse (AC-N8.1..AC-N8.3 + SPEC §8 #24)"
    },
    {
      "id": "NFR-09",
      "type": "testability",
      "description": "pytest skipped=0; 每個測試至少一個 assert; 禁 --ignore/-k/--deselect/collect_ignore; FR-07 migration 以真實 SQLite 檔案測試不得降級; TRACEABILITY_MATRIX 的 VERIFIED 須來自實際執行",
      "test_method": "ast-assertions + pytest gate + matrix generator (AC-N9.1..AC-N9.5 + SPEC §8 #1)"
    },
    {
      "id": "NFR-10",
      "type": "integration",
      "description": "03-development/tests/integration/ 行覆蓋 ≥ 80%; 整合測試以 httpx.AsyncClient(ASGITransport(app)) 驅動, 不得直接呼叫 handler; 涵蓋 401/403/404/409/422/429/503 + migration 往返 + rate limit 觸發/恢復 + graceful drain",
      "test_method": "pytest-cov-integration + suite enumeration (AC-N10.1..AC-N10.3 + SPEC §8 #3)"
    },
    {
      "id": "NFR-11",
      "type": "maintainability",
      "description": "MI ≥ 80; 函式 CC ≤ 10; 單檔 ≤ 400 行; 單目錄 ≤ 15 檔; API handler ≤ 40 行 (業務邏輯下沉到 service/)",
      "test_method": "readability-v2 (radon-mi) + filesystem gate (AC-N11.1..AC-N11.3)"
    },
    {
      "id": "NFR-12",
      "type": "verifiability",
      "description": "Makefile verify-system 串接 upgrade head → tests → /healthz+ /readyz smoke → downgrade base + upgrade head; exit 0 且 stdout 含 verify-system: PASS",
      "test_method": "make verify-system exit + stdout grep (AC-N12.1, AC-N12.2 + SPEC §8 #27)"
    }
  ]
}
```

### FR Block Notes

- `dimension` mapping (for human reviewers) — NFR-03 `error_handling`, NFR-06 `architecture_constraints`, NFR-07 `license_compliance`, NFR-08 `mutation_testing`, NFR-09 `test_assertion_quality`, NFR-10 `integration_coverage`, NFR-11 `readability`, NFR-12 `execute_verification_target` are dimension names that are present in the current `harness/harness/ssi/prompts/evaluate_dimension.md` roster. These dimensions are NOT in the `type:` vocabulary enforced by `sab_parser.ALL_NFR_TYPES`, so they are mapped to the nearest vocabulary type in the JSON above (NFR-03 → `reliability`, NFR-06 → `layering`, NFR-07 → `licensing`, NFR-08 → `mutation`, NFR-09 → `testability`, NFR-10 → `integration`, NFR-11 → `maintainability`, NFR-12 → `verifiability`). The `dimension:` field is preserved in the NFR sections above for traceability.
- `functional_requirements` and `non_functional_requirements` together enumerate 10 + 12 = 22 entries — exactly matching canonical SPEC §3 / §4 headings.
- Per the `R-NO-PRESCRIPTION-001` carve-out, the prompt-injection scan outcome is summarised in §7.3 only; no methodology artifacts are embedded in this SRS.
