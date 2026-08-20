# TEST_PLAN.md — taskq-api

> P4 entry artifact. Runs **once** before per-FR testing. Authored from
> `01-requirements/SRS.md` (FR/NFR + AC stable IDs) and
> `.methodology/quality_manifest.json` (FR list + module traceability).
>
> **Coverage rule**: every FR-01 … FR-10 and every NFR-01 … NFR-12 carries at
> least one **positive**, **negative**, **boundary**, and **edge-case** test case
> where the AC surface supports it; priorities are P0 (release-blocking),
> P1 (gate-required), P2 (advisory). Each TC links back to one or more
> AC IDs from the SRS so traceability remains 1-to-1.

---

## 0. Conventions

| Tag | Meaning |
|-----|---------|
| `POS` | Positive — happy path with valid input |
| `NEG` | Negative — invalid input or hostile condition expected to be rejected |
| `BND` | Boundary — exact off-by-one limits (min, max, max+1, max-1) |
| `EDG` | Edge case — rare / unusual but legal condition (empty list, zero rows, single row, very long strings, unicode, concurrency) |
| `P0` | Release-blocking (AC explicitly tied to a SPEC §8 acceptance command) |
| `P1` | Gate-required (AC explicitly tied to a quality dimension) |
| `P2` | Advisory / hardening |

Module prefixes used in `module:` fields follow `fr_module_traceability` from
`.methodology/quality_manifest.json`.

---

## 1. FR-01 — 任務資源 CRUD API

| TC ID | Category | Priority | Module | Description | Input | Expected Output | Linked AC |
|-------|----------|----------|--------|-------------|-------|-----------------|-----------|
| TC-01-P01 | POS | P0 | api.tasks, service.tasks, repository.task_repo, models.schemas | POST /v1/tasks with valid body and write-scope key creates task | `{"name":"job-A","command":"echo hi"}` + `X-API-Key: write` | 201; body `{"id": <uuid>}`; row visible in DB | AC-1.1 |
| TC-01-P02 | POS | P1 | api.tasks, repository.task_repo | GET /v1/tasks/{id} returns all columns of a known task | write-scope create then read | 200; body matches DB row column-for-column | AC-1.3 |
| TC-01-P03 | POS | P1 | api.tasks | GET /v1/tasks paginated with default cursor | insert 60 tasks, hit `?limit=50` | 200; `items.length == 50`; `next_cursor` non-null | AC-1.4 |
| TC-01-N01 | NEG | P0 | api.tasks | POST /v1/tasks with **empty name** | `{"name":"","command":"x"}` | 422 + problem+json; type=about:blank or `/errors/validation` | AC-1.2 |
| TC-01-N02 | NEG | P0 | api.tasks | POST /v1/tasks with **>1000 chars** in name | 1001-char name | 422 + problem+json | AC-1.2 |
| TC-01-N03 | NEG | P0 | api.tasks, service.tasks | POST /v1/tasks with **injection character** (`;`/`\|`/`$`/backtick) | `{"name":"a;rm -rf /","command":"x"}` | 422 + problem+json | AC-1.2 |
| TC-01-N04 | NEG | P0 | api.tasks, repository.task_repo | POST /v1/tasks with **duplicate name** (unique constraint) | create `dup` then create `dup` again | 409 + problem+json; second row absent | AC-1.2, AC-10.4 |
| TC-01-N05 | NEG | P1 | api.tasks | GET /v1/tasks/{id} for unknown id | UUIDv4 that doesn't exist | 404 + problem+json | AC-1.3 |
| TC-01-B01 | BND | P1 | api.tasks | `limit=200` accepted; `limit=201` rejected | seed ≥200 rows; hit `?limit=200` then `?limit=201` | 200 / 422 + problem+json | AC-1.4 |
| TC-01-B02 | BND | P1 | api.tasks | `limit=1` accepted (minimum boundary) | `?limit=1` | 200; `items.length == 1` | AC-1.4 |
| TC-01-E01 | EDG | P1 | api.tasks | GET /v1/tasks supports `?status=` and `?cursor=`; **offset** keyword is absent from query schema | introspect `app.openapi()` | `status` and `cursor` present; `offset` absent | AC-1.5 |
| TC-01-E02 | EDG | P0 | api.tasks, repository.task_repo | DELETE /v1/tasks/{id} with admin key removes task + `task_results` row in **one transaction** | admin-scope create + run + delete; subsequent GET | 204; result GET → 404 | AC-1.6 |
| TC-01-E03 | EDG | P1 | api.tasks, repository.task_repo | SQL statement count is constant for 10, 100, 1000 rows (N+1 guard) | instrument `before_cursor_execute`; seed 10/100/1000 rows | identical statement counts across all three sizes | AC-1.7, AC-N1.3 |
| TC-01-E04 | EDG | P2 | api.tasks | POST /v1/tasks with **unicode** name (CJK) | `{"name":"任務-α","command":"echo"}` | 201; row readable; list returns same name | AC-1.1 |

---

## 2. FR-02 — 任務執行端點

| TC ID | Category | Priority | Module | Description | Input | Expected Output | Linked AC |
|-------|----------|----------|--------|-------------|-------|-----------------|-----------|
| TC-02-P01 | POS | P0 | api.tasks, service.runner | POST /v1/tasks/{id}/run with write key returns 202 + run_id | write-scope, valid task | 202; body `{"run_id": <uuid>}`; task→`running`→terminal | AC-2.1 |
| TC-02-P02 | POS | P1 | api.tasks, service.runner, repository.task_repo | Run completes; `task_results` row carries `exit_code`, `stdout_tail`, `stderr_tail`, `duration_ms`, `finished_at` | echo command run | row exists; all 5 columns non-null | AC-2.4 |
| TC-02-P03 | POS | P1 | api.tasks | GET /v1/tasks/{id}/runs returns reverse-chronological history | 3 sequential runs | list length=3; `finished_at` non-increasing | AC-2.5 |
| TC-02-N01 | NEG | P0 | service.runner | Runner uses `asyncio.create_subprocess_exec(*shlex.split(...))`; **never** `shell=True` | grep `03-development/src/` for `shell=True` | zero hits; unit test on call site | AC-2.2, AC-N2.1 |
| TC-02-N02 | NEG | P0 | service.runner | Task exceeding `TASKQ_TASK_TIMEOUT` is killed; final state=`timeout`; no orphan PIDs | `sleep 30` with `TASKQ_TASK_TIMEOUT=1` | state=`timeout`; `pgrep` returns nothing of the child | AC-2.3, AC-N3.5 |
| TC-02-B01 | BND | P1 | service.runner | `TASKQ_TASK_TIMEOUT=0.1` boundary (very short) | 200ms sleep | state=`timeout`; no orphan | AC-2.3 |
| TC-02-E01 | EDG | P1 | service.runner | `kill()` then `await process.wait()` actually reaped (PID enumeration before/after) | instrument runner; long sleep | `set(pids_before) == set(pids_after)` for children | AC-2.3, AC-8.2 |

---

## 3. FR-03 — API Key 認證

| TC ID | Category | Priority | Module | Description | Input | Expected Output | Linked AC |
|-------|----------|----------|--------|-------------|-------|-----------------|-----------|
| TC-03-P01 | POS | P0 | api.deps, service.auth, repository.key_repo | Request with valid X-API-Key (read-scope) reaches handler | `X-API-Key: <read-plaintext>` | 200/201 (handler response) | AC-3.1 |
| TC-03-N01 | NEG | P0 | api.deps | Missing X-API-Key on three distinct `/v1/*` endpoints | `/v1/tasks`, `/v1/tasks/x/runs`, `/v1/metrics` | each → 401 + problem+json | AC-3.1, AC-10.4 |
| TC-03-N02 | NEG | P0 | api.deps, service.auth | Invalid (non-existent) X-API-Key | bogus header | 401 + problem+json | AC-3.1 |
| TC-03-N03 | NEG | P0 | repository.key_repo, service.auth | Revoked key is rejected even if hash matches | mint → revoke → call | 401 + problem+json | AC-3.5 |
| TC-03-B01 | BND | P1 | repository.key_repo | `key_hash` is exactly 64-char lowercase hex SHA-256 digest | mint then SELECT `key_hash` | len=64; regex `^[0-9a-f]{64}$`; matches `hashlib.sha256(plaintext).hexdigest()` | AC-3.2, AC-N2.3 |
| TC-03-E01 | EDG | P0 | service.auth | Comparison uses `hmac.compare_digest`; unit test asserts success + constant-time for wrong key | auth service direct unit test | `compare_digest` referenced; wrong key → False | AC-3.3 |
| TC-03-E02 | EDG | P0 | taskq_api.__main__ | `python -m taskq_api key create --scope <scope>` prints plaintext **exactly once**; not in any log/metric sink | CLI invocation; capture stdout + post-run log grep | plaintext in stdout; plaintext absent from log/metric files | AC-3.4, AC-N4.3 |
| TC-03-E03 | EDG | P1 | api.health | `/healthz` and `/readyz` reachable **without** `X-API-Key` and return 200/503 not 401 | no header set | 200 or 503; never 401 | AC-3.6 |
| TC-03-E04 | EDG | P2 | repository.key_repo | Plaintext string **not** present anywhere in the `api_keys` table | mint key, full-table dump for substring | zero plaintext occurrences | AC-3.2, AC-N4.3 |

---

## 4. FR-04 — Scope 授權

| TC ID | Category | Priority | Module | Description | Input | Expected Output | Linked AC |
|-------|----------|----------|--------|-------------|-------|-----------------|-----------|
| TC-04-P01 | POS | P1 | api.deps, service.auth | write-scope key accepted on POST /v1/tasks | write key | 201 | AC-4.1 (inverse) |
| TC-04-P02 | POS | P1 | api.deps | admin-scope key accepted on DELETE /v1/tasks/{id} | admin key | 204 | AC-4.1 (inverse) |
| TC-04-N01 | NEG | P0 | api.deps, service.auth | write key against admin-only DELETE /v1/tasks/{id} | write key + valid id | 403 + problem+json | AC-4.1, AC-N2.4 |
| TC-04-N02 | NEG | P0 | api.deps | read key against POST /v1/tasks (write required) | read key | 403 + problem+json | AC-4.1 |
| TC-04-N03 | NEG | P0 | api.deps | 403 body **must not** leak whether resource exists; bodies for existing vs non-existing id are byte-indistinguishable on the existence axis | write key vs write key + bogus id on admin endpoint | both 403; bodies equal modulo correlation_id | AC-4.2, AC-N2.4 |
| TC-04-B01 | BND | P1 | service.auth | Scope precedence: read < write < admin (inclusive) — every adjacent pair | 3 keys | each strictly weaker key → 403 | AC-4.1 |
| TC-04-E01 | EDG | P0 | api.deps | All `/v1/*` routes resolve through **one** FastAPI dependency; static check on `app.openapi()` route → dependencies | introspect routes | auth dep present in every `/v1` route's `dependencies` | AC-4.3 |

---

## 5. FR-05 — 流量控制

| TC ID | Category | Priority | Module | Description | Input | Expected Output | Linked AC |
|-------|----------|----------|--------|-------------|-------|-----------------|-----------|
| TC-05-P01 | POS | P1 | api.deps, service.ratelimit, repository.rate_repo | First N requests under `TASKQ_RATE_BURST` succeed | N=20 burst | 200/201 | AC-5.1 (inverse) |
| TC-05-N01 | NEG | P0 | api.deps, service.ratelimit | N+1th request against same key returns 429 + `Retry-After` ≥ 0 | N+1 | 429; `Retry-After` header present; value ≥ 0 | AC-5.1, AC-10.4 |
| TC-05-N02 | NEG | P1 | api.health | `/healthz` and `/readyz` **never** return 429 | 100 calls with bucket=1 | all 200/503, no 429 | AC-5.3 |
| TC-05-B01 | BND | P1 | repository.rate_repo | Bucket update runs in single transaction with row-level lock | instrument `Session.execute` | exactly one `SELECT ... FOR UPDATE` and one commit per call | AC-5.2 |
| TC-05-E01 | EDG | P1 | service.ratelimit | Concurrent requests against same key: deny count = burst overage (no race admission) | 50 concurrent calls; bucket=10 | exactly 10 admitted, 40 denied | AC-5.2, AC-N3.1 |
| TC-05-E02 | EDG | P2 | service.ratelimit | Bucket refill: after waiting `TASKQ_RATE_PER_SEC`, 1 token regenerates | bucket=1, sleep 1/REFILL_SEC | next call → 200, subsequent → 429 | AC-5.1 |

---

## 6. FR-06 — 持久化層與交易邊界

| TC ID | Category | Priority | Module | Description | Input | Expected Output | Linked AC |
|-------|----------|----------|--------|-------------|-------|-----------------|-----------|
| TC-06-P01 | POS | P1 | repository.session | Successful request commits exactly once | insert happy path | `Session.commit()` called exactly 1× | AC-6.2 |
| TC-06-N01 | NEG | P0 | migrations.env, repository.session | `service/` and `api/` contain no `from sqlalchemy ...` / `import sqlalchemy` | grep | zero hits; `lint-imports` exit 0 | AC-6.1, AC-N6.1, AC-N6.2 |
| TC-06-N02 | NEG | P0 | repository.session | Raise inside context manager → rollback, no commit | trigger `RuntimeError` mid-transaction | `Session.commit()` never called; `Session.rollback()` called once | AC-6.2, AC-N3.1 |
| TC-06-N03 | NEG | P0 | repository/*, service/*, api/* | SQL string-concatenation patterns (f-string / `%` / `+`) over `03-development/src/` | CI grep | zero matches | AC-6.3, AC-N2.2 |
| TC-06-B01 | BND | P1 | repository.task_repo | Relationship loads use `selectinload`/`joinedload` explicitly — SQL count constant at 10/100/1000 rows | seed 10/100/1000; instrument | constant statement count | AC-6.4, AC-N1.3 |
| TC-06-E01 | EDG | P1 | repository.session | Engine configured with `pool_size=TASKQ_DB_POOL_SIZE` and `pool_pre_ping=True` | inspect `engine.pool` | `pool.size() == TASKQ_DB_POOL_SIZE`; `_pre_ping` enabled | AC-6.5 |

---

## 7. FR-07 — Schema Migration (Alembic 三步演進)

| TC ID | Category | Priority | Module | Description | Input | Expected Output | Linked AC |
|-------|----------|----------|--------|-------------|-------|-----------------|-----------|
| TC-07-P01 | POS | P0 | migrations.env, migrations.versions.v1_initial, v2_tags, v3_split_results | `alembic upgrade head` exits 0 | clean DB | exit 0; `tasks`, `api_keys`, `tags`, `task_tags`, `task_results` tables exist | AC-7.1 |
| TC-07-N01 | NEG | P0 | migrations.env | `alembic downgrade base` exits 0; no residual tables | `upgrade head` → `downgrade base` | exit 0; only `alembic_version` remains | AC-7.1 |
| TC-07-N02 | NEG | P0 | migrations.versions.v3_split_results | Round-trip: `upgrade head → write sample → downgrade -1 → upgrade head` leaves every column byte-identical | real SQLite file with seeded `result_json` | every column equals original after round-trip | AC-7.2, AC-N9.4 |
| TC-07-N03 | NEG | P0 | migrations.versions.v3_split_results | v3 `downgrade()` reverse-migrates data — no `op.execute("DROP TABLE ...")` shortcut | static scan + offline-SQL | no destructive shortcut; offline-SQL contains reverse DML | AC-7.3 |
| TC-07-N04 | NEG | P0 | migrations.env | Migration that fails rolls back atomically; `/readyz` returns 503 with failure detail | introduce failing migration; run | DB unchanged; `/readyz` 503 | AC-7.5, AC-N3.6 |
| TC-07-B01 | BND | P1 | migrations.versions.* | Offline SQL for each revision contains expected tables/columns in expected order | `alembic upgrade head --sql` and `downgrade` | each table appears in expected order | AC-7.4 |
| TC-07-E01 | EDG | P1 | migrations.versions.v1_initial, v2_tags, v3_split_results | All migration files are themselves under test coverage | run tests against migrations/* | coverage > 0 on the revision files | AC-7.4 |
| TC-07-E02 | EDG | P1 | migrations.versions.v3_split_results | v3 data migration handles NULL/empty `result_json` | seed mixed rows | all rows survive round-trip | AC-7.2 |

---

## 8. FR-08 — 非同步執行器

| TC ID | Category | Priority | Module | Description | Input | Expected Output | Linked AC |
|-------|----------|----------|--------|-------------|-------|-----------------|-----------|
| TC-08-P01 | POS | P1 | service.runner | Background execution uses `asyncio.TaskGroup` (not bare `gather` or fire-and-forget `create_task`) | AST/static on `service.runner` | `TaskGroup` referenced; no bare `gather`/fire-and-forget | AC-8.5 |
| TC-08-P02 | POS | P1 | service.runner | Per-task timeout via `asyncio.wait_for`; on timeout, child `process.kill()` + `await process.wait()` | timeout test | child gone; state=`timeout` | AC-2.3, AC-8.2 |
| TC-08-N01 | NEG | P0 | service.runner | Tasks beyond `TASKQ_MAX_CONCURRENT` queue (not spawn ad-hoc) | stress to cap+5 | at most `cap + 1` live `subprocess_exec` calls | AC-8.1 |
| TC-08-N02 | NEG | P0 | service.runner | `asyncio.CancelledError` propagates; not swallowed by `except Exception` | inject `CancelledError` into handler | error surfaces; no `except Exception: pass` | AC-8.4, AC-N3.3 |
| TC-08-N03 | NEG | P0 | service.runner | On shutdown, in-flight tasks drain up to `TASKQ_DRAIN_TIMEOUT`; tasks still running → `interrupted`; no orphans | hold long task, trigger shutdown | state=`interrupted`; `pgrep` returns nothing | AC-8.3 |
| TC-08-B01 | BND | P1 | service.runner | `TASKQ_MAX_CONCURRENT=1` boundary (single-concurrency) | single-cap stress | exactly one live at a time | AC-8.1 |
| TC-08-E01 | EDG | P1 | service.runner | PID enumeration before/after timeout shows child reaped | instrument | `set(pids_before) == set(pids_after)` | AC-8.2, AC-N3.5 |

---

## 9. FR-09 — 健康檢查與可觀測性

| TC ID | Category | Priority | Module | Description | Input | Expected Output | Linked AC |
|-------|----------|----------|--------|-------------|-------|-----------------|-----------|
| TC-09-P01 | POS | P0 | api.health | GET /healthz returns 200 `{"status":"ok"}` with no `X-API-Key` | no header | 200; body matches | AC-9.1 |
| TC-09-P02 | POS | P1 | api.health, repository.session, config | GET /readyz returns 200 when DB reachable **and** `alembic current == head` | healthy DB at head | 200 | AC-9.2 |
| TC-09-N01 | NEG | P0 | api.health | GET /readyz returns 503 with `detail` naming DB when DB unreachable | closed DB file | 503; body names `database` | AC-9.2, AC-N3.4, AC-10.4 |
| TC-09-N02 | NEG | P0 | api.health | GET /readyz returns 503 with `detail` naming migration when DB OK but `alembic current != head` | `downgrade -1` | 503; body names `migration` | AC-9.2, AC-7.5, AC-10.4 |
| TC-09-N03 | NEG | P1 | api.health | GET /v1/metrics with write-scope key → 403 | write key | 403 + problem+json | AC-9.3, AC-4.1 |
| TC-09-B01 | BND | P1 | api.health, repository.session | `/readyz` boundary: `alembic current == head` exactly (just-upgraded) returns 200; immediately after `downgrade -1` returns 503 | upgrade→readyz→downgrade→readyz | first 200, second 503 | AC-9.2, AC-7.5 |
| TC-09-E01 | EDG | P1 | api.health | /v1/metrics returns task counts by status, latency percentiles, rate-limit denial counts | admin key after activity | keys present; numeric values | AC-9.3 |
| TC-09-E02 | EDG | P2 | api.health | `/healthz` response carries a `correlation_id` field for log stitching | no header | field present in body | AC-9.1, AC-10.3 |

---

## 10. FR-10 — 錯誤契約 (RFC 7807)

| TC ID | Category | Priority | Module | Description | Input | Expected Output | Linked AC |
|-------|----------|----------|--------|-------------|-------|-----------------|-----------|
| TC-10-P01 | POS | P0 | errors, app | Every non-2xx response has `Content-Type: application/problem+json` and fields `type`/`title`/`status`/`detail`/`instance`/`correlation_id` | sweep 8 status rows | every response carries `application/problem+json`; fields present | AC-10.1 |
| TC-10-N01 | NEG | P0 | errors, app | 500 `detail` carries **no** stack trace / SQL / file path | trigger 500 | substring allow-list passes | AC-10.2, AC-N2.5 |
| TC-10-N02 | NEG | P0 | app | Every non-2xx response has `X-Correlation-Id` header that also appears in server log for the same request | any non-2xx | header present; log grep finds correlation_id | AC-10.3 |
| TC-10-B01 | BND | P0 | errors | Status code coverage: 422, 401, 403, 404, 409, 429, 503, 500 each triggered by at least one integration test | enumerated | each code appears in suite | AC-10.4 |
| TC-10-E01 | EDG | P0 | service.runner, errors | `asyncio.CancelledError` does **not** become a 500; propagates upward | inject `CancelledError` | no 500 produced; error surfaces to caller | AC-10.5, AC-N3.3 |

---

## 11. NFR-01 — 效能與查詢效率

| TC ID | Category | Priority | Module | Description | Input | Expected Output | Linked AC |
|-------|----------|----------|--------|-------------|-------|-----------------|-----------|
| TC-N01-P01 | POS | P1 | repository.task_repo | GET /v1/tasks/{id} p95 latency < 30 ms at 10,000 rows | `pytest-benchmark` on 10k seeded table | p95 < 30 ms | AC-N1.1 |
| TC-N01-P02 | POS | P1 | repository.task_repo | GET /v1/tasks?limit=50 p95 latency < 80 ms at 10,000 rows | `pytest-benchmark` | p95 < 80 ms | AC-N1.2 |
| TC-N01-N01 | NEG | P0 | repository.task_repo | List endpoint SQL statement count constant regardless of returned row count | event listener at 10/100/1000 | identical counts | AC-N1.3, AC-1.7, AC-6.4 |
| TC-N01-B01 | BND | P2 | repository.task_repo | Latency at exactly 10,000 rows meets p95 target (boundary of scale) | 10k seeded | p95 < target | AC-N1.1, AC-N1.2 |
| TC-N01-E01 | EDG | P2 | repository.task_repo | Latency at 50,000 rows (above target scale) still within reason (graceful degradation, not collapse) | 50k seeded | monotonic or bounded p95 increase vs 10k baseline | AC-N1.1, AC-N1.2 |

---

## 12. NFR-02 — HTTP 與資料層安全

| TC ID | Category | Priority | Module | Description | Input | Expected Output | Linked AC |
|-------|----------|----------|--------|-------------|-------|-----------------|-----------|
| TC-N02-N01 | NEG | P0 | repository/*, service/*, api/* | Repository-wide grep for `shell=True` / `eval(` / `exec(` over `03-development/src/` | CI grep gate | zero hits | AC-N2.1, AC-2.2 |
| TC-N02-N02 | NEG | P0 | repository/*, service/*, api/* | Repository-wide grep for SQL string-concat (f-string / `%` / `+`) | CI grep | zero hits | AC-N2.2, AC-6.3 |
| TC-N02-N03 | NEG | P0 | service.auth, repository.key_repo | API key stored as 64-char lowercase hex SHA-256; no plaintext | unit + integration | regex match; no plaintext anywhere | AC-N2.3, AC-3.2 |
| TC-N02-N04 | NEG | P0 | api.deps, service.auth | 403 bodies indistinguishable on existence axis | compare for existing vs missing id | byte-equal modulo correlation_id | AC-N2.4, AC-4.2 |
| TC-N02-N05 | NEG | P0 | errors | 500 body has no stack trace / SQL / path | trigger 500 | substring allow-list passes | AC-N2.5, AC-10.2 |
| TC-N02-P01 | POS | P1 | app | CORS denies all origins by default; `TASKQ_CORS_ORIGINS` is the only allowlist | empty + non-empty env | empty → no `Access-Control-Allow-Origin`; allowlist → only listed origins | AC-N2.6 |
| TC-N02-B01 | BND | P1 | repository/*, service/*, api/* | SQL string-concat boundary: f-string used outside SQL context (e.g. log messages) is allowed; the grep gate is SQL-context-aware | CI grep with context | no SQL-context f-string hits; non-SQL f-strings pass | AC-N2.2, AC-6.3 |
| TC-N02-E01 | EDG | P0 | repository/*, service/*, api/* | `bandit -r 03-development/src/` reports 0 HIGH / 0 MEDIUM | CI | zero findings | AC-N2.7 |

---

## 13. NFR-03 — 錯誤處理、交易與非同步正確性

| TC ID | Category | Priority | Module | Description | Input | Expected Output | Linked AC |
|-------|----------|----------|--------|-------------|-------|-----------------|-----------|
| TC-N03-P01 | POS | P1 | repository.session | Context manager wraps every transaction; success → exactly one commit | unit test | one commit | AC-N3.1, AC-6.2 |
| TC-N03-N01 | NEG | P0 | repository/*, service/*, api/* | Codebase contains no bare `except:` / `except Exception: pass` | `ast-error-handling` scan | zero hits | AC-N3.2 |
| TC-N03-N02 | NEG | P0 | service.runner | `asyncio.CancelledError` propagates out of task handlers | inject | surfaces upward | AC-N3.3, AC-8.4 |
| TC-N03-N03 | NEG | P0 | api.health, repository.session | DB connection failure → `/readyz` 503 naming DB | closed DB | 503 with `detail`; no infinite retry | AC-N3.4, AC-9.2 |
| TC-N03-N04 | NEG | P0 | service.runner | Timeout kills child; PID enumeration before/after | instrument | children reaped | AC-N3.5, AC-2.3 |
| TC-N03-N05 | NEG | P0 | migrations.env | Failed migration rolls back atomically; `/readyz` 503 with detail | failing migration | atomicity + `/readyz` 503 | AC-N3.6, AC-7.5 |
| TC-N03-E01 | EDG | P1 | repository.session | Connection-pool exhaustion is observable (does not silently hang) | saturate pool | bounded error within timeout | AC-N3.4 |
| TC-N03-B01 | BND | P1 | service.runner | Timeout boundary at `TASKQ_TASK_TIMEOUT=0.05` (50 ms); child is still killed and reaped, not orphaned | `sleep 1` with 50 ms timeout | state=`timeout`; `pgrep` finds nothing | AC-N3.5, AC-2.3 |

---

## 14. NFR-04 — 敏感資料遮蔽

| TC ID | Category | Priority | Module | Description | Input | Expected Output | Linked AC |
|-------|----------|----------|--------|-------------|-------|-----------------|-----------|
| TC-N04-P01 | POS | P0 | config, errors | `stdout_tail`/`stderr_tail`/logs/error bodies are redacted against `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)` | unit test on redaction helper | each match replaced with `[REDACTED]` | AC-N4.1 |
| TC-N04-N01 | NEG | P0 | config, errors | DB URL with password does not appear in any log/error/metrics response | seed URL with password | password absent from every sink | AC-N4.2 |
| TC-N04-N02 | NEG | P0 | taskq_api.__main__ | API key plaintext printed exactly once at `key create`; absent from logs/DB/metrics | CLI run + post-run sink grep | plaintext in stdout; absent from all persistent stores | AC-N4.3, AC-3.4 |
| TC-N04-B01 | BND | P1 | config | Pattern boundary: `sk-1234567` (7 chars) NOT redacted; `sk-12345678` (8 chars) IS redacted | unit test | boundary respected | AC-N4.1 |
| TC-N04-E01 | EDG | P1 | config | `postgres://` vs `postgresql://` both redacted; multi-line error bodies redacted on every line | unit test | both schemes redacted | AC-N4.1 |

---

## 15. NFR-05 — 文件覆蓋

| TC ID | Category | Priority | Module | Description | Input | Expected Output | Linked AC |
|-------|----------|----------|--------|-------------|-------|-----------------|-----------|
| TC-N05-P01 | POS | P0 | api/*, service/*, repository/*, models/* | 100% of public functions/classes carry docstring with `[FR-XX]`/`[NFR-XX]` reference | `ast-docstrings` CI | 0 missing | AC-N5.1 |
| TC-N05-P02 | POS | P1 | api.tasks, api.deps, api.health | Every FastAPI endpoint appears in `/openapi.json` with `summary` + `description` | integration test fetch `/openapi.json` | field present for every route | AC-N5.2 |
| TC-N05-N01 | NEG | P1 | api.tasks | Endpoint missing `summary` fails CI scan | deliberate removal | CI fails | AC-N5.2 |
| TC-N05-B01 | BND | P2 | api.tasks | Docstring `[FR-01]` reference is present at handler-level | inspect | reference present | AC-N5.1 |
| TC-N05-E01 | EDG | P1 | api/* | Even error-only endpoints (e.g. 401/404) have `summary`/`description` | introspection | field present | AC-N5.2 |

---

## 16. NFR-06 — 架構分層契約

| TC ID | Category | Priority | Module | Description | Input | Expected Output | Linked AC |
|-------|----------|----------|--------|-------------|-------|-----------------|-----------|
| TC-N06-P01 | POS | P0 | migrations.env | `.importlinter` exists; declares `api > service > repository > models`; `config` and `errors` are independence modules | `lint-imports` exit 0 | exit 0 | AC-N6.1 |
| TC-N06-N01 | NEG | P0 | migrations.env | `.importlinter` forbids `sqlalchemy` outside `repository/`; deliberate import in `service/` rejected | negative test | non-zero exit | AC-N6.2, AC-6.1 |
| TC-N06-N02 | NEG | P0 | migrations.env | `lint-imports` runs in CI; exits 0 | CI gate | exit 0 | AC-N6.3 |
| TC-N06-N03 | NEG | P0 | migrations.env | No degradation: deleting `.importlinter`, replacing with `ignore_imports`, or downgrading contract cannot pass | policy check | rejected | AC-N6.4 |
| TC-N06-B01 | BND | P1 | api/*, service/* | Layer boundary: `api` may import `service`; `service` may not import `api` | static check | invariant holds | AC-N6.1 |

---

## 17. NFR-07 — 依賴與授權合規

| TC ID | Category | Priority | Module | Description | Input | Expected Output | Linked AC |
|-------|----------|----------|--------|-------------|-------|-----------------|-----------|
| TC-N07-P01 | POS | P0 | config | Every runtime dep in `requirements.txt` pinned with `==`; transitive fully locked in `requirements.lock` | file inspection | all direct pins exact | AC-N7.1 |
| TC-N07-N01 | NEG | P0 | config | Allowlist = `MIT / BSD-2-Clause / BSD-3-Clause / Apache-2.0 / PSF`; every dep (direct + transitive) ∈ set | `pip-licenses --format=json --with-system` | zero violations | AC-N7.2, AC-N7.3 |
| TC-N07-N02 | NEG | P1 | config | License scan covers full dep tree, not only `requirements.txt` | `--with-system` flag set | full tree scanned | AC-N7.3 |
| TC-N07-P02 | POS | P1 | config | `08-config/SBOM.json` carries `name`/`version`/`license`/`direct|transitive` per record | shape test | fields present | AC-N7.4 |
| TC-N07-B01 | BND | P1 | config | A dep whose license is **just outside** the allowlist (e.g. GPL-3) is rejected | inject fake | gate fails | AC-N7.2 |
| TC-N07-E01 | EDG | P1 | config | System packages are still scanned; `--with-system` required | run scan | included | AC-N7.3 |

---

## 18. NFR-08 — 變異測試

| TC ID | Category | Priority | Module | Description | Input | Expected Output | Linked AC |
|-------|----------|----------|--------|-------------|-------|-----------------|-----------|
| TC-N08-P01 | POS | P0 | config | `.methodology/harness_config.json` has `features.mutation_testing: true`; not modified to lower threshold | file inspection | field present | AC-N8.1 |
| TC-N08-N01 | NEG | P0 | service/*, repository/* | `mutmut run` then `mutmut results` ≥ 70 score over `service/` + `repository/` | CI | score ≥ 70 | AC-N8.2 |
| TC-N08-N02 | NEG | P0 | config | Mutation scope annotated in `harness_config.json` limited to `service/` + `repository/` with rationale | file inspection | rationale present | AC-N8.3 |
| TC-N08-B01 | BND | P1 | service/*, repository/* | Mutation score at threshold (≥70) — boundary | CI | passes at exactly 70 | AC-N8.2 |
| TC-N08-E01 | EDG | P1 | service.tasks | Surviving mutants are listed and triaged (mutation survivors json) | `mutmut results` | survivors enumerated | AC-N8.2 |

---

## 19. NFR-09 — 驗證真實性 (零 skip 鐵律)

| TC ID | Category | Priority | Module | Description | Input | Expected Output | Linked AC |
|-------|----------|----------|--------|-------------|-------|-----------------|-----------|
| TC-N09-P01 | POS | P0 | tests/* | `pytest 03-development/tests -q` reports skipped count = 0 | CI | skipped = 0 | AC-N9.1 |
| TC-N09-P02 | POS | P0 | tests/* | Every test function has ≥ 1 `assert` (or non-skipped `raises`) | `ast-assertions` | 0 zero-assert tests | AC-N9.2 |
| TC-N09-N01 | NEG | P0 | tests/* | No test excluded via `--ignore` / `-k` / `--deselect` / `collect_ignore` / removed `testpaths` | CI config inspection | full suite runs | AC-N9.3 |
| TC-N09-N02 | NEG | P0 | tests/integration, migrations.versions.v3_split_results | FR-07 round-trip tested against **real SQLite file** (not in-memory) | integration test | uses temp-file DB | AC-N9.4, AC-7.2 |
| TC-N09-P03 | POS | P1 | traceability_matrix | `TRACEABILITY_MATRIX.md` `VERIFIED` marks only after test actually ran; matrix is generator output | generator | no hand-edits | AC-N9.5 |
| TC-N09-B01 | BND | P2 | tests/* | Zero-assert threshold: a deliberate zero-assert test is detected | `ast-assertions` | detected | AC-N9.2 |
| TC-N09-E01 | EDG | P2 | tests/* | `--ignore` / `-k` / `--deselect` arguments removed; CI uses defaults | CI config | no exclusions | AC-N9.3 |

---

## 20. NFR-10 — 整合覆蓋

| TC ID | Category | Priority | Module | Description | Input | Expected Output | Linked AC |
|-------|----------|----------|--------|-------------|-------|-----------------|-----------|
| TC-N10-P01 | POS | P0 | tests/integration/* | `03-development/tests/integration/` line coverage ≥ 80% | `pytest-cov-integration` | ≥ 80% | AC-N10.1 |
| TC-N10-N01 | NEG | P0 | tests/integration/* | Integration tests driven via `httpx.AsyncClient(transport=ASGITransport(app))`; no direct handler calls | static check | no direct handler calls | AC-N10.2 |
| TC-N10-N02 | NEG | P0 | tests/integration/* | Integration suite covers each error code (401, 403, 404, 409, 422, 429, 503) + migration round-trip + rate-limit trigger/recovery + graceful drain | enumeration | each present | AC-N10.3 |
| TC-N10-B01 | BND | P1 | tests/integration/* | Integration coverage at exactly 80% (boundary) | `pytest-cov-integration` | passes | AC-N10.1 |
| TC-N10-E01 | EDG | P1 | tests/integration/test_cli_entry | CLI entry integration test exists for `key create` | run | covered | AC-N10.3 |
| TC-N10-P02 | POS | P1 | tests/integration/* | Integration test for `key create` CLI entry-point | run CLI via subprocess | stdout contains plaintext; sink scan clean | AC-N10.3, AC-3.4 |

---

## 21. NFR-11 — 可讀性

| TC ID | Category | Priority | Module | Description | Input | Expected Output | Linked AC |
|-------|----------|----------|--------|-------------|-------|-----------------|-----------|
| TC-N11-P01 | POS | P0 | service.utils | Project MI (LLOC-weighted) ≥ 80; per-function CC ≤ 10 | `readability-v2` (radon-mi) | pass | AC-N11.1 |
| TC-N11-N01 | NEG | P0 | repository/*, service/*, api/* | No file > 400 lines; no directory > 15 files | file-system gate | pass | AC-N11.2 |
| TC-N11-N02 | NEG | P0 | api/* | No API handler > 40 lines (business logic sunk to `service/`) | per-handler line-count | pass | AC-N11.3 |
| TC-N11-B01 | BND | P1 | service.utils | MI at exactly 80 (boundary) | `radon-mi` | passes | AC-N11.1 |
| TC-N11-E01 | EDG | P2 | api/* | OpenAPI docstring-heavy handler excluded from the 40-line ceiling (per NFR-99-04 grey zone) | per-handler line-count excluding docstring | passes | AC-N11.3 |

---

## 22. NFR-12 — 系統驗證目標

| TC ID | Category | Priority | Module | Description | Input | Expected Output | Linked AC |
|-------|----------|----------|--------|-------------|-------|-----------------|-----------|
| TC-N12-P01 | POS | P0 | app | `Makefile` defines `verify-system` chaining: (1) `alembic upgrade head`, (2) full tests, (3) start service + `/healthz` + `/readyz` smoke, (4) `downgrade base` + `upgrade head` | run target | exit 0; stdout contains `verify-system: PASS` | AC-N12.1, AC-N12.2 |
| TC-N12-N01 | NEG | P0 | app | A deliberately-broken step in `verify-system` chain causes non-zero exit | inject failure | exit ≠ 0 | AC-N12.1 |
| TC-N12-B01 | BND | P1 | app | Migration round-trip step in `verify-system` is atomic; `/readyz` flips back to 200 after round-trip | run | 200 after | AC-N12.1 |
| TC-N12-E01 | EDG | P1 | app | stdout contains literal `verify-system: PASS` only on full success | run with partial failure | literal absent on failure | AC-N12.2 |

---

## 23. Cross-FR/NFR Coverage Matrix

Maps every FR + every NFR to at least one TC. (Used by Gate 3 + Gate 4 to confirm no
FR/NFR slipped.)

| Req | TCs |
|-----|-----|
| FR-01 | TC-01-P01..E04 |
| FR-02 | TC-02-P01..E01 |
| FR-03 | TC-03-P01..E04 |
| FR-04 | TC-04-P01..E01 |
| FR-05 | TC-05-P01..E02 |
| FR-06 | TC-06-P01..E01 |
| FR-07 | TC-07-P01..E02 |
| FR-08 | TC-08-P01..E01 |
| FR-09 | TC-09-P01..E01 |
| FR-10 | TC-10-P01..E01 |
| NFR-01 | TC-N01-P01..B01 |
| NFR-02 | TC-N02-N01..E01 |
| NFR-03 | TC-N03-P01..E01 |
| NFR-04 | TC-N04-P01..E01 |
| NFR-05 | TC-N05-P01..E01 |
| NFR-06 | TC-N06-P01..B01 |
| NFR-07 | TC-N07-P01..E01 |
| NFR-08 | TC-N08-P01..E01 |
| NFR-09 | TC-N09-P01..E01 |
| NFR-10 | TC-N10-P01..E01 |
| NFR-11 | TC-N11-P01..E01 |
| NFR-12 | TC-N12-P01..E01 |

> All 10 FRs and all 12 NFRs from `quality_manifest.json::fr_ids` and
> `nfr_dimension_mapping` are covered.

---

## 24. Category Coverage Summary

| Req | POS | NEG | BND | EDG |
|-----|-----|-----|-----|-----|
| FR-01 | 3 | 5 | 2 | 4 |
| FR-02 | 3 | 2 | 1 | 1 |
| FR-03 | 1 | 3 | 1 | 4 |
| FR-04 | 2 | 3 | 1 | 1 |
| FR-05 | 1 | 2 | 1 | 2 |
| FR-06 | 1 | 3 | 1 | 1 |
| FR-07 | 1 | 4 | 1 | 2 |
| FR-08 | 2 | 3 | 1 | 1 |
| FR-09 | 2 | 3 | 1 | 2 |
| FR-10 | 1 | 2 | 1 | 1 |
| NFR-01 | 2 | 1 | 1 | 1 |
| NFR-02 | 1 | 5 | 1 | 1 |
| NFR-03 | 1 | 5 | 1 | 1 |
| NFR-04 | 1 | 2 | 1 | 1 |
| NFR-05 | 2 | 1 | 1 | 1 |
| NFR-06 | 1 | 3 | 1 | 0 |
| NFR-07 | 2 | 2 | 1 | 1 |
| NFR-08 | 1 | 2 | 1 | 1 |
| NFR-09 | 3 | 2 | 1 | 1 |
| NFR-10 | 2 | 2 | 1 | 1 |
| NFR-11 | 1 | 2 | 1 | 1 |
| NFR-12 | 1 | 1 | 1 | 1 |

Every FR + every NFR carries at least one entry in each of POS/NEG/BND/EDG
where the AC surface supports it (e.g. NFR-06 is a configuration-only check
where EDG has no additional surface beyond the BND boundary on the layers
contract itself).

---

## 25. Test Runner & Tooling Anchors

- Runner: `pytest -q 03-development/tests/`
- Integration runner: `pytest -q 03-development/tests/integration/` via
  `httpx.AsyncClient(transport=ASGITransport(app))`
- Mutation: `mutmut run` + `mutmut results` over `service/` + `repository/`
- Architecture: `lint-imports` (from `.importlinter`)
- Security: `bandit -r 03-development/src/`
- Licensing: `pip-licenses --format=json --with-system`
- Performance: `pytest-benchmark` on seeded 10k-row table
- N+1 guard: SQLAlchemy `before_cursor_execute` event listener

---

## 26. Self-Review

**Possible errors**:
1. The category counts above are computed by hand; a copy-paste mistake could
   understate one row. Mitigation: the cross-reference matrix (§23) names
   TCs per FR/NFR and is the binding check.
2. Some `module:` fields reference modules whose exact file path may not
   match `fr_module_traceability` 1-to-1 (e.g. `taskq_api.api.tasks` vs
   `taskq_api.api.deps`). Each TC only requires the module to be one of
   the modules in the FR's traceability list.

**Unverified assumptions**:
- None blocking. TC ordering follows SRS AC ordering; per-FR gates are
  expected to reference this plan directly.

**Confidence**: High. Each FR/NFR in `quality_manifest.json::fr_ids` and
`nfr_dimension_mapping` appears in §23's matrix.