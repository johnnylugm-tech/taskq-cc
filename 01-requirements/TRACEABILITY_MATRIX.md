# Traceability Matrix — taskq-api

> Bidirectional **FR ↔ SRS ↔ Code ↔ Test** traceability for `taskq-api` (Python 3.11 / FastAPI / SQLAlchemy 2.x / Alembic).
> Canonical spec: `SPEC.md` v1.0.0 (2026-07-30). SRS: `01-requirements/SRS.md` (APPROVED 2026-08-19). Tracking: `01-requirements/SPEC_TRACKING.md`.
> Phase: 1 (Requirements). Status column is machine-refreshed at `advance-phase` per SPEC_TRACKING protocol (DRAFT until code+test exist; VERIFIED after live scan). Hand-edit of Status is overwritten.
> Test Inventory: `01-requirements/TEST_INVENTORY.yaml` is not yet authored (Phase 2 deliverable per methodology); the right-hand `AC IDs` column below is the binding test-side reference until TEST_INVENTORY ships.

---

## 1. Overview

This matrix links every Functional Requirement (FR-01..FR-10) and Non-Functional Requirement (NFR-01..NFR-12) — defined canonically in `SPEC.md` §3 / §4 and transcribed into `SRS.md` §3 / §4 — through to:

1. The **SRS section** that carries the requirement text and its `#### AC-N.M` acceptance criteria.
2. The **design / implementation modules** planned for the requirement (from `SRS.md` FR Block §10).
3. The **test-side AC IDs** that prove the requirement is met (binding until `TEST_INVENTORY.yaml` exists; the file will then reference this matrix by AC ID, not duplicate it).

Coverage status is reported in §6. ASPICE SWE.3 / SYS.4 capability mapping is in §7.

---

## 2. FR ↔ SRS Mapping

> One row per FR/NFR. `SRS Section` is the `### FR-NN` / `### NFR-NN` heading anchor in `SRS.md`. `Acceptance Criteria` are the `#### AC-N.M` IDs that close the loop. `Status` is machine-refreshed.

| ID | Requirement (short) | SRS Section | Acceptance Criteria | Priority | Status |
|----|----------------------|-------------|---------------------|----------|--------|
| FR-01 | Task resource CRUD API (POST/GET/list/DELETE /v1/tasks; 422/404; cursor pagination; N+1 guard) | `SRS.md` §3 FR-01 | AC-1.1, AC-1.2, AC-1.3, AC-1.4, AC-1.5, AC-1.6, AC-1.7 | HIGH | DRAFT |
| FR-02 | Task execution endpoint (POST /v1/tasks/{id}/run → 202; subprocess via `shlex.split`; result row; runs history) | `SRS.md` §3 FR-02 | AC-2.1, AC-2.2, AC-2.3, AC-2.4, AC-2.5 | HIGH | DRAFT |
| FR-03 | API Key authentication (`X-API-Key`, SHA-256 hash, `hmac.compare_digest`, plaintext printed once, `/healthz`+`/readyz` exempt) | `SRS.md` §3 FR-03 | AC-3.1, AC-3.2, AC-3.3, AC-3.4, AC-3.5, AC-3.6 | HIGH | DRAFT |
| FR-04 | Scope authorisation (`read < write < admin`; 403 without existence leak; single dependency) | `SRS.md` §3 FR-04 | AC-4.1, AC-4.2, AC-4.3 | HIGH | DRAFT |
| FR-05 | Rate limit (per-token bucket; 429 + `Retry-After`; row-level lock; health endpoints exempt) | `SRS.md` §3 FR-05 | AC-5.1, AC-5.2, AC-5.3 | HIGH | DRAFT |
| FR-06 | Persistence layer & transaction boundary (repository-only; context manager; no SQL concat; eager loading; pool config) | `SRS.md` §3 FR-06 | AC-6.1, AC-6.2, AC-6.3, AC-6.4, AC-6.5 | HIGH | DRAFT |
| FR-07 | Schema migration (Alembic v1/v2/v3 with real data migration; reversible round-trip) | `SRS.md` §3 FR-07 | AC-7.1, AC-7.2, AC-7.3, AC-7.4, AC-7.5 | HIGH | DRAFT |
| FR-08 | Async runner (`asyncio.TaskGroup`, graceful drain, concurrency cap, timeout-kills-child, `CancelledError` propagation) | `SRS.md` §3 FR-08 | AC-8.1, AC-8.2, AC-8.3, AC-8.4, AC-8.5 | HIGH | DRAFT |
| FR-09 | Health & observability (`/healthz`, `/readyz` fail-closed on DB or migration, `/v1/metrics` admin) | `SRS.md` §3 FR-09 | AC-9.1, AC-9.2, AC-9.3 | HIGH | DRAFT |
| FR-10 | Error contract (RFC 7807 `application/problem+json`, six fields, detail allowlist, `X-Correlation-Id` stitching) | `SRS.md` §3 FR-10 | AC-10.1, AC-10.2, AC-10.3, AC-10.4, AC-10.5 | HIGH | DRAFT |
| NFR-01 | Performance & query efficiency (p95 budgets; SQL-count constant) — `dimension: performance` | `SRS.md` §4 NFR-01 | AC-N1.1, AC-N1.2, AC-N1.3 | HIGH | DRAFT |
| NFR-02 | HTTP & data-layer security (no `shell=True`/`eval`/`exec`; no SQL concat; hashed keys; CORS deny-by-default; `bandit` 0/0) — `dimension: security` | `SRS.md` §4 NFR-02 | AC-N2.1, AC-N2.2, AC-N2.3, AC-N2.4, AC-N2.5, AC-N2.6, AC-N2.7 | HIGH | DRAFT |
| NFR-03 | Error handling, transactions, async correctness (context-manager; no bare except; `CancelledError` propagation; DB failure → 503; timeout kills child; migration rollback) — `dimension: error_handling` | `SRS.md` §4 NFR-03 | AC-N3.1, AC-N3.2, AC-N3.3, AC-N3.4, AC-N3.5, AC-N3.6 | HIGH | DRAFT |
| NFR-04 | Sensitive data redaction (`stdout_tail`/`stderr_tail`/log/error; DB URL password never logged; key plaintext printed once) — `dimension: security` | `SRS.md` §4 NFR-04 | AC-N4.1, AC-N4.2, AC-N4.3 | HIGH | DRAFT |
| NFR-05 | Documentation coverage (public docstrings carry `[FR-XX]`/`[NFR-XX]`; OpenAPI `summary`+`description`) — `dimension: documentation` | `SRS.md` §4 NFR-05 | AC-N5.1, AC-N5.2 | MEDIUM | DRAFT |
| NFR-06 | Architecture layering contract (`.importlinter`, `api > service > repository > models`, `sqlalchemy` confined to `repository/`) — `dimension: architecture_constraints` | `SRS.md` §4 NFR-06 | AC-N6.1, AC-N6.2, AC-N6.3, AC-N6.4 | HIGH | DRAFT |
| NFR-07 | Dependency & license compliance (`requirements.txt` `==`-pinned + lock; whole-tree license allowlist; SBOM) — `dimension: license_compliance` | `SRS.md` §4 NFR-07 | AC-N7.1, AC-N7.2, AC-N7.3, AC-N7.4 | MEDIUM | DRAFT |
| NFR-08 | Mutation testing (`features.mutation_testing: true`; `mutmut` ≥ 70 over `service/`+`repository/`) — `dimension: mutation_testing` | `SRS.md` §4 NFR-08 | AC-N8.1, AC-N8.2, AC-N8.3 | MEDIUM | DRAFT |
| NFR-09 | Verification honesty (zero-skip rule; every test asserts; FR-07 uses real SQLite; matrix `VERIFIED` from live scan) — `dimension: test_assertion_quality` | `SRS.md` §4 NFR-09 | AC-N9.1, AC-N9.2, AC-N9.3, AC-N9.4, AC-N9.5 | HIGH | DRAFT |
| NFR-10 | Integration coverage (integration cov ≥ 80%; ASGI-transport-driven; covers 401/403/404/409/422/429/503 + migration round-trip + rate-limit trigger/recover + graceful drain) — `dimension: integration_coverage` | `SRS.md` §4 NFR-10 | AC-N10.1, AC-N10.2, AC-N10.3 | HIGH | DRAFT |
| NFR-11 | Readability (MI ≥ 80; CC ≤ 10; file ≤ 400 LOC; dir ≤ 15 files; API handler ≤ 40 LOC) — `dimension: readability` | `SRS.md` §4 NFR-11 | AC-N11.1, AC-N11.2, AC-N11.3 | MEDIUM | DRAFT |
| NFR-12 | System verification target (`make verify-system` chains upgrade→test→smoke→downgrade→upgrade; exit 0 + `verify-system: PASS`) — `dimension: execute_verification_target` | `SRS.md` §4 NFR-12 | AC-N12.1, AC-N12.2 | HIGH | DRAFT |

**FR/NFR coverage check**: 10 FR + 12 NFR = 22 rows; matches `SPEC.md` §3 / §4 headings and `SPEC_TRACKING.md` Completeness Check.

---

## 3. SRS ↔ Code Mapping

> Planned implementation modules (from `SRS.md` FR Block §10 + `SPEC_TRACKING.md` Notes column). Module paths are forward-looking and bind via `implementation_functions`; concrete file paths are not yet authored (Phase 3). `Status` flips to `IN_PROGRESS` once the module file exists, `VERIFIED` once the matching tests pass.

| SRS Section | Implementation Module(s) | Module Role | Acceptance Criteria Bound | Status |
|-------------|---------------------------|-------------|---------------------------|--------|
| §3 FR-01 | `taskq_api.service.tasks`, `taskq_api.api.tasks` | CRUD service + HTTP handler | AC-1.1..AC-1.7 | DRAFT |
| §3 FR-02 | `taskq_api.service.runner`, `taskq_api.api.tasks` | subprocess runner + `/run` and `/runs` handlers | AC-2.1..AC-2.5 | DRAFT |
| §3 FR-03 | `taskq_api.service.auth`, `taskq_api.api.deps`, `taskq_api.__main__` | key hashing/comparison + FastAPI dependency + `key create` CLI | AC-3.1..AC-3.6 | DRAFT |
| §3 FR-04 | `taskq_api.service.auth`, `taskq_api.api.deps` | scope-hierarchy check inside the single auth dependency | AC-4.1..AC-4.3 | DRAFT |
| §3 FR-05 | `taskq_api.service.ratelimit`, `taskq_api.repository.rate_repo`, `taskq_api.api.deps` | bucket logic + row-locked state update + dependency | AC-5.1..AC-5.3 | DRAFT |
| §3 FR-06 | `taskq_api.repository.session`, `taskq_api.repository.task_repo`, `taskq_api.repository.key_repo`, `taskq_api.repository.rate_repo` | session context manager + three repositories; engine config | AC-6.1..AC-6.5 | DRAFT |
| §3 FR-07 | `migrations.versions.v1_initial`, `migrations.versions.v2_tags`, `migrations.versions.v3_split_results` | three Alembic revisions with working `downgrade()` each | AC-7.1..AC-7.5 | DRAFT |
| §3 FR-08 | `taskq_api.service.runner` | `asyncio.TaskGroup` + queue + drain + child kill semantics | AC-8.1..AC-8.5 | DRAFT |
| §3 FR-09 | `taskq_api.api.health`, `taskq_api.repository.session` | `/healthz`, `/readyz`, `/v1/metrics`; readiness DB + alembic check | AC-9.1..AC-9.3 | DRAFT |
| §3 FR-10 | `taskq_api.errors`, `taskq_api.app` | RFC 7807 problem+json formatter + global exception handler | AC-10.1..AC-10.5 | DRAFT |
| §4 NFR-01 | cross-cutting — instrumented via SQLAlchemy event listener + `pytest-benchmark` | no new module | AC-N1.1..AC-N1.3 | DRAFT |
| §4 NFR-02 | cross-cutting — grep gate + CI `bandit`; enforcement via `.importlinter` | no new module | AC-N2.1..AC-N2.7 | DRAFT |
| §4 NFR-03 | cross-cutting — `repository.session` context manager + `service.runner` cancellation discipline | no new module | AC-N3.1..AC-N3.6 | DRAFT |
| §4 NFR-04 | redaction helper (lives under `taskq_api.errors` or a dedicated `taskq_api.redaction`) | redaction utility | AC-N4.1..AC-N4.3 | DRAFT |
| §4 NFR-05 | cross-cutting — docstring discipline + OpenAPI metadata on `taskq_api.api.*` | no new module | AC-N5.1, AC-N5.2 | DRAFT |
| §4 NFR-06 | cross-cutting — `.importlinter` config file + CI gate | config file | AC-N6.1..AC-N6.4 | DRAFT |
| §4 NFR-07 | cross-cutting — `requirements.txt`, `requirements.lock`, `08-config/SBOM.json` | config + artifact files | AC-N7.1..AC-N7.4 | DRAFT |
| §4 NFR-08 | cross-cutting — `.methodology/harness_config.json` toggle + `mutmut` scope annotation | config file | AC-N8.1..AC-N8.3 | DRAFT |
| §4 NFR-09 | cross-cutting — pytest config + `ast-assertions` + zero-skip CI gate | CI config | AC-N9.1..AC-N9.5 | DRAFT |
| §4 NFR-10 | cross-cutting — `03-development/tests/integration/` suite driven by `httpx.AsyncClient(ASGITransport(app))` | test suite | AC-N10.1..AC-N10.3 | DRAFT |
| §4 NFR-11 | cross-cutting — readability gate + `readability-v2` (radon-mi) | CI gate | AC-N11.1..AC-N11.3 | DRAFT |
| §4 NFR-12 | cross-cutting — `Makefile` `verify-system` target | build file | AC-N12.1, AC-N12.2 | DRAFT |

**Module uniqueness check**: `taskq_api.service.runner` carries FR-02 (subprocess call) + FR-08 (TaskGroup semantics); this is intentional — the runner is the single home for `asyncio.create_subprocess_exec` and the structured-concurrency owner. `taskq_api.api.deps` carries FR-03 + FR-04 + FR-05 because all three converge at the FastAPI dependency layer (single-dependency invariant per FR-04 / AC-4.3). `taskq_api.repository.session` is shared by FR-06 (boundary), FR-09 (readiness DB ping), and NFR-03 (transactional integrity).

---

## 4. Code ↔ Test Mapping

> Each module's AC IDs are the binding tests. The eventual `03-development/tests/` layout will reference these AC IDs (e.g., `tests/integration/test_fr01_tasks.py::test_ac_1_1_post_returns_201`). Until the test files exist, the AC ID column is the authoritative test-side pointer. The `Status` flips to `IN_PROGRESS` when the test file exists, `VERIFIED` only after live execution (per NFR-09 / AC-N9.5).

| Implementation Module | Test File (planned) | Bound AC IDs | Coverage Target | Status |
|----------------------|---------------------|--------------|-----------------|--------|
| `taskq_api.service.tasks` + `taskq_api.api.tasks` | `03-development/tests/integration/test_fr01_tasks.py` + `03-development/tests/integration/test_n10_3_error_code_sweep.py` (AC-1.2 422 case) | AC-1.1..AC-1.7 | integration cov ≥ 80% (NFR-10) | DRAFT |
| `taskq_api.service.runner` (FR-02) | `03-development/tests/integration/test_fr02_run.py` | AC-2.1..AC-2.5 | integration cov ≥ 80% | DRAFT |
| `taskq_api.service.auth` + `taskq_api.api.deps` + `taskq_api.__main__` (FR-03) | `03-development/tests/integration/test_fr03_auth.py` + `03-development/tests/unit/test_auth_hashing.py` + `03-development/tests/integration/test_fr03_healthz_no_auth.py` | AC-3.1..AC-3.6 | integration cov ≥ 80%; AC-3.3 unit | DRAFT |
| `taskq_api.service.auth` + `taskq_api.api.deps` (FR-04) | `03-development/tests/integration/test_fr04_scope.py` + `03-development/tests/unit/test_route_dependency_introspection.py` | AC-4.1..AC-4.3 | integration cov ≥ 80%; AC-4.3 static/unit | DRAFT |
| `taskq_api.service.ratelimit` + `taskq_api.repository.rate_repo` + `taskq_api.api.deps` (FR-05) | `03-development/tests/integration/test_fr05_ratelimit.py` + `03-development/tests/unit/test_ratelimit_lock.py` | AC-5.1..AC-5.3 | integration cov ≥ 80%; AC-5.2 unit | DRAFT |
| `taskq_api.repository.session` + `taskq_api.repository.task_repo` + `taskq_api.repository.key_repo` + `taskq_api.repository.rate_repo` (FR-06) | `03-development/tests/unit/test_session_context_manager.py` + `03-development/tests/integration/test_fr06_pool.py` + `03-development/tests/integration/test_fr06_n_plus_one.py` | AC-6.1..AC-6.5 | integration cov ≥ 80%; AC-6.2 + AC-6.5 unit | DRAFT |
| `migrations.versions.v1_initial` + `migrations.versions.v2_tags` + `migrations.versions.v3_split_results` (FR-07) | `03-development/tests/integration/test_fr07_migrations.py` (real SQLite file per NFR-09) | AC-7.1..AC-7.5 | integration cov ≥ 80%; AC-7.2 round-trip on real DB | DRAFT |
| `taskq_api.service.runner` (FR-08) | `03-development/tests/integration/test_fr08_async_runner.py` + `03-development/tests/unit/test_runner_cancellation.py` + `03-development/tests/integration/test_fr08_graceful_drain.py` | AC-8.1..AC-8.5 | integration cov ≥ 80%; AC-8.4 unit | DRAFT |
| `taskq_api.api.health` + `taskq_api.repository.session` (FR-09) | `03-development/tests/integration/test_fr09_healthz.py` + `03-development/tests/integration/test_fr09_readyz_db_down.py` + `03-development/tests/integration/test_fr09_readyz_migration_down.py` + `03-development/tests/integration/test_fr09_metrics.py` | AC-9.1..AC-9.3 | integration cov ≥ 80% | DRAFT |
| `taskq_api.errors` + `taskq_api.app` (FR-10) | `03-development/tests/integration/test_fr10_problem_json.py` (status sweep 422/401/403/404/409/429/503/500) + `03-development/tests/integration/test_fr10_correlation_id.py` + `03-development/tests/unit/test_fr10_cancelled_error.py` | AC-10.1..AC-10.5 | integration cov ≥ 80%; AC-10.5 unit | DRAFT |
| (NFR-01 perf instrumentation) | `03-development/tests/perf/test_n1_latency.py` + `03-development/tests/perf/test_n1_sql_count.py` | AC-N1.1..AC-N1.3 | `pytest-benchmark`; SQLAlchemy event listener | DRAFT |
| (NFR-02 grep / bandit gates) | `03-development/tests/gates/test_n2_grep_gates.py` + `.github/workflows/bandit.yml` | AC-N2.1..AC-N2.7 | grep + CI gate | DRAFT |
| (NFR-03 cross-cutting) | `03-development/tests/unit/test_n3_session_cm.py` + `03-development/tests/unit/test_n3_except_swallow.py` + `03-development/tests/integration/test_n3_readyz_db.py` + `03-development/tests/integration/test_n3_orphan_pid.py` + `03-development/tests/integration/test_n3_migration_rollback.py` | AC-N3.1..AC-N3.6 | integration cov ≥ 80%; AC-N3.1/N3.2/N3.3 static/unit | DRAFT |
| redaction helper (NFR-04) | `03-development/tests/unit/test_n4_redaction.py` + `03-development/tests/integration/test_n4_db_url_password.py` + `03-development/tests/integration/test_n4_key_plaintext_once.py` | AC-N4.1..AC-N4.3 | unit + integration | DRAFT |
| (NFR-05 docstrings + OpenAPI) | `03-development/tests/unit/test_n5_docstrings.py` (`ast-docstrings`) + `03-development/tests/integration/test_n5_openapi_shape.py` | AC-N5.1, AC-N5.2 | CI + integration | DRAFT |
| (NFR-06 layering contract) | `03-development/tests/gates/test_n6_importlinter.py` + negative-import test + `.importlinter` | AC-N6.1..AC-N6.4 | CI gate | DRAFT |
| (NFR-07 licenses) | `03-development/tests/unit/test_n7_lock_pin.py` + `03-development/tests/unit/test_n7_license_allowlist.py` + `03-development/tests/unit/test_n7_sbom_shape.py` | AC-N7.1..AC-N7.4 | unit + CI | DRAFT |
| (NFR-08 mutation) | `03-development/tests/gates/test_n8_harness_config_flag.py` + `mutmut` CI step | AC-N8.1..AC-N8.3 | CI gate | DRAFT |
| (NFR-09 zero-skip) | `03-development/tests/gates/test_n9_pytest_skipped.py` + `03-development/tests/unit/test_n9_assertions.py` + `03-development/tests/gates/test_n9_filter_flags.py` + (covered by FR-07 real-SQLite test) + matrix-generator CI step | AC-N9.1..AC-N9.5 | CI gate; AC-N9.4 covered via FR-07 | DRAFT |
| (NFR-10 integration driver) | `03-development/tests/integration/conftest.py` (ASGITransport) + suite-level coverage report | AC-N10.1..AC-N10.3 | `pytest-cov-integration` ≥ 80% | DRAFT |
| (NFR-11 readability) | `03-development/tests/gates/test_n11_readability.py` + `03-development/tests/gates/test_n11_filesystem.py` | AC-N11.1..AC-N11.3 | `readability-v2` + filesystem gate | DRAFT |
| (NFR-12 verify-system) | `Makefile` `verify-system` target (smoke); its own exit-code + stdout assertion in CI | AC-N12.1, AC-N12.2 | Makefile + CI | DRAFT |

**AC ↔ test bijection check**: every AC ID in §2 appears in §4 (no orphan ACs); every AC ID in §4 appears in §2 (no orphan tests). 92 AC IDs total — 47 from FRs (AC-1.1..AC-1.7 = 7, AC-2.1..AC-2.5 = 5, AC-3.1..AC-3.6 = 6, AC-4.1..AC-4.3 = 3, AC-5.1..AC-5.3 = 3, AC-6.1..AC-6.5 = 5, AC-7.1..AC-7.5 = 5, AC-8.1..AC-8.5 = 5, AC-9.1..AC-9.3 = 3, AC-10.1..AC-10.5 = 5) and 45 from NFRs (AC-N1=3, AC-N2=7, AC-N3=6, AC-N4=3, AC-N5=2, AC-N6=4, AC-N7=4, AC-N8=3, AC-N9=5, AC-N10=3, AC-N11=3, AC-N12=2). Final count: 92 AC IDs.

---

## 5. Risk ↔ Requirement Cross-Trace

> Pulled from `SRS.md` §8 Risks; included here so reviewers can trace each risk back to the AC IDs that mitigate it. Source-of-truth is `SRS.md` §8.

| Risk | Linked Requirement(s) | Mitigating AC IDs | Status |
|------|-----------------------|-------------------|--------|
| R1 (v3 data migration loses data) | FR-07, NFR-09 | AC-7.2, AC-N9.4 | DRAFT |
| R2 (SQL injection) | FR-06, NFR-02 | AC-6.3, AC-N2.2 | DRAFT |
| R3 (API key leak) | FR-03, NFR-02, NFR-04 | AC-3.2, AC-3.4, AC-N2.3, AC-N4.3 | DRAFT |
| R4 (403 reveals resource existence) | FR-04, NFR-02 | AC-4.2, AC-N2.4 | DRAFT |
| R5 (N+1 collapses on large table) | FR-06, NFR-01 | AC-1.7, AC-6.4, AC-N1.3 | DRAFT |
| R6 (error body leaks internals) | FR-10, NFR-02 | AC-10.2, AC-N2.5 | DRAFT |
| R7 (swallowed `CancelledError` hangs shutdown) | FR-08, NFR-03 | AC-8.4, AC-N3.3 | DRAFT |
| R8 (timeout leaves orphan processes) | FR-08, NFR-03 | AC-2.3, AC-8.2, AC-N3.5 | DRAFT |
| R9 (deploy without migration) | FR-09 | AC-9.2 | DRAFT |
| R10 (connection pool exhaustion) | FR-06, FR-08 | AC-6.5 | DRAFT |
| R11 (transitive dep with incompatible license) | NFR-07 | AC-N7.2, AC-N7.3 | DRAFT |
| R12 (rate bucket race over-admits) | FR-05, NFR-02 | AC-5.2 | DRAFT |

---

## 6. Completeness Verification

> All percentages below are reported against the 22-requirement scope (10 FR + 12 NFR) and the 92 AC IDs (§4). Status is machine-refreshed; the right-hand `Target` column is fixed by `SPEC.md` §8 / `SRS.md` §5 / NFR-09.

| Check | Target | Actual (Phase 1) | Status |
|-------|--------|------------------|--------|
| FR coverage in tracking matrix | 10 / 10 (100%) | 10 / 10 | OK |
| NFR coverage in tracking matrix | 12 / 12 (100%) | 12 / 12 | OK |
| Total requirement coverage (FR + NFR) | 22 / 22 (100%) | 22 / 22 | OK |
| AC ↔ requirement coverage | every FR/NFR carries ≥ 1 AC | 22 / 22 | OK |
| AC IDs enumerated in §4 | 92 / 92 | 92 / 92 | OK |
| FR → Code module mapping | 10 / 10 | 10 / 10 (planned modules) | OK (Phase 3 will flip to IN_PROGRESS) |
| NFR → Code/test artefact mapping | 12 / 12 | 12 / 12 (planned artefacts) | OK |
| Risk → AC linkage | 12 / 12 risks linked | 12 / 12 | OK |
| Unit test coverage (`pytest --cov`) | 100% | 0% (pre-implementation) | PENDING |
| Integration test coverage (`tests/integration/`) | ≥ 80% (NFR-10) | 0% (pre-implementation) | PENDING |
| Mutation score over `service/` + `repository/` | ≥ 70 (NFR-08) | n/a (pre-implementation) | PENDING |
| `bandit` HIGH/MEDIUM findings | 0 / 0 (NFR-02) | n/a (pre-implementation) | PENDING |
| `import-linter` exit code | 0 (NFR-06) | n/a (pre-implementation) | PENDING |
| `pytest ... skipped` count | 0 (NFR-09) | n/a (pre-implementation) | PENDING |
| `make verify-system` exit + stdout | 0 + `verify-system: PASS` (NFR-12) | n/a (pre-implementation) | PENDING |
| `TRACEABILITY_MATRIX.md` Status `VERIFIED` rows | 0 until live scan (NFR-09 / AC-N9.5) | 0 / 22 | EXPECTED (Phase 1; machine-refreshed at advance-phase) |

**Note on row counts**: §6's "Target 100% / Actual 100%" cells are populated now because Phase 1 only requires that every requirement be *referenced* from the matrix — the live `VERIFIED` flag is bound to actual test execution per AC-N9.5 and is intentionally deferred to the machine-refresh at `advance-phase`.

---

## 7. ASPICE Compliance Mapping

> Automotive SPICE 4.0 capability-level mapping for SWE.3 (Software Detailed Design & Unit Construction) and SYS.4 (System Integration & Integration Test). The matrix above supplies the bidirectional evidence.

| ASPICE Capability | Evidence in this Matrix | Status |
|-------------------|------------------------|--------|
| SWE.3.BP1: Specify software detailed design | §3 SRS ↔ Code Mapping (planned modules per requirement) | OK (planned) |
| SWE.3.BP2: Verify software detailed design against requirements | §2 FR ↔ SRS Mapping (every FR/NFR bound to an SRS section + AC set) | OK |
| SWE.3.BP3: Evaluate alternative designs | out of scope for Phase 1 (carried in `02-architecture/ADR.md`) | DEFERRED |
| SWE.3.BP4: Build software units | §3 Module column | OK (planned; Phase 3 implements) |
| SWE.3.BP5: Verify software units | §4 Code ↔ Test Mapping + NFR-09 zero-skip rule | OK (planned) |
| SYS.4.BP1: Develop system integration test specifications | §4 AC IDs per module + AC-N10.3 error-code enumeration | OK (planned) |
| SYS.4.BP2: Develop system integration test cases | §4 test-file column bound to AC IDs | OK (planned) |
| SYS.4.BP3: Verify system integration test specifications | §6 completeness check + §2/§4 bijection | OK |
| SYS.4.BP4: Integrate software items | §5 Risk ↔ Requirement cross-trace (integration risks R5/R8/R10/R12) | OK (planned) |
| SYS.4.BP5: Verify system integration | AC-9.2 (readiness), AC-N12.1/N12.2 (`verify-system`) | OK (planned) |

ASPICE note: SWE.3.BP3 evidence lives in `02-architecture/ADR.md` (Phase 2 deliverable) and is referenced here only for completeness; this matrix does not duplicate ADR content.

---

## 8. Update Log

| Date | Change | By |
|------|--------|----|
| 2026-08-19 | Initial creation (Round 1). Replaced placeholder template with full bidirectional FR ↔ SRS ↔ Code ↔ Test matrix for taskq-api, populated from APPROVED `SRS.md` (22 requirements, 92 AC IDs) and `SPEC_TRACKING.md`. Status column uniformly DRAFT; machine-refreshed at `advance-phase` per the tracking protocol. | Agent A — Requirements Engineer (Sub-Task 3/4 Round 1) |
