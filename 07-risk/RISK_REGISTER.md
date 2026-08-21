# RISK_REGISTER — taskq-cc

> **Phase**: 7 — Risk Management
> **Generated**: 2026-08-21
> **Project**: taskq-cc
> **Source seeds**:
> 1. `SPEC.md` §9 risk matrix (R1–R12) — the project's authoritative risk register baseline.
> 2. `.methodology/bug_hunt_report.json` — adversarial 4-lens re-hunt on `b5b87b3`, 15 raw / 9 confirmed / 6 refuted.
> 3. `.methodology/gate3_result.json` — 17 dimensions, all ≥ thresholds (quality_complete=true).
> 4. `.methodology/gate4_result.json` — Gate 4 PASS (overall_score=null but quality_complete=true with all 5 devil-advocate dimensions answered).
> 5. `.methodology/lessons/{680919069f8f,ac58bf107d8e,faa0341d2c1d,fc0e27c889f7}.md` — most recent failures all in the infra-failure / tool-evidence family, NOT risk-content; no risk-register rows are derived from them.

> **Scope rules followed**: no `advance-phase` / `push-milestone` calls; no edits under `harness/`; no FR re-implementation. This register is the only output of Phase 7 — Gate 4 (not Gate 3) closes P7, so this register must be non-trivial and traceable to existing source artefacts.

---

## Scoring legend

| Field | Range | Meaning |
|---|---|---|
| **Likelihood (L)** | 1–5 | 1 = remote, 2 = unlikely, 3 = possible, 4 = likely, 5 = near-certain given current code path |
| **Impact (I)** | 1–5 | 1 = cosmetic, 2 = data quality, 3 = operational, 4 = security, 5 = data loss / outage |
| **Score (S)** | L × I | Drives HIGH / MED / LOW bucket |
| **Bucket** | HIGH: S ≥ 9, MED: 4 ≤ S ≤ 8, LOW: S ≤ 3 | Used by `RISK_MITIGATION_PLANS.md` to scope formal plans |
| **Status** | OPEN / MONITORED / RESOLVED / ACCEPTED | RESOLVED = fix shipped and regression-tested. ACCEPTED = residual risk accepted with rationale. |

**Categories**: SEC = security, REL = reliability/availability, DATA = data integrity, PERF = performance, OPS = operational/observability, COMP = compliance, ARCH = architecture/maintainability.

---

## R1 — v3 data migration loses data (downgrade non-determinism)

| Field | Value |
|---|---|
| **ID** | R1 (SPEC §9) |
| **Name** | v3 `tasks.result_json` ↔ `task_results` round-trip drops run history |
| **Likelihood** | 2 (was 3 pre-fix) — the correlated subquery is now `ORDER BY started_at DESC, id DESC LIMIT 1`, regression-tested |
| **Impact** | 5 — silent run-history loss on downgrade; AC-7.2 byte-identical round-trip violated |
| **Score** | 10 |
| **Bucket** | HIGH |
| **Category** | DATA |
| **Status** | RESOLVED (commit `5c1c2cf`) |
| **Mitigation approach** | Real SQLite round-trip test (`tests/test_bug_hunt_regressions.py::test_bughunt_v3_downgrade_restores_latest_result_for_multi_run_tasks`) inserts 3 `task_results` rows and asserts `{"run": 2}` survives both upgrade and downgrade; SPEC §9 mitigation in force. **Residual**: monitor any future migration authored against this tree; `migrations.versions.v3_split_results` is in `High-Risk Modules` (CLAUDE.md) — keep under per-FR TDD. |
| **Source** | SPEC §9 R1; bug-hunt `v3_split_results#1` (critical, RESOLVED fix_commit `5c1c2cfdc6c1876e66b38e276ae7dd3174daf837`). |

## R2 — SQL injection via string-concatenated SQL

| Field | Value |
|---|---|
| **ID** | R2 (SPEC §9) |
| **Name** | SQL injection through raw `text()` calls or f-strings into the DBAPI |
| **Likelihood** | 1 — `import-linter` enforces sqlalchemy-only-in-repository; CI grep gate forbids string-concatenated SQL anywhere in source tree |
| **Impact** | 5 — full DB read/write compromise |
| **Score** | 5 |
| **Bucket** | MED |
| **Category** | SEC |
| **Status** | MONITORED |
| **Mitigation approach** | (a) ORM-bound parameters for every interactive query (FR-04/06/08). (b) CI grep gate `SQL 字串拼接命中 = 0` (SPEC §11). (c) bandit HIGH/MED = 0 (Gate 3 result: 0/0). **Residual**: new migrations are the only place `sa.text()` is allowed — author migration SQL with bind parameters, never with f-strings; lint-imports and grep gate must remain green at Gate 4. |
| **Source** | SPEC §9 R2; SPEC §11 threshold; `lint-imports` + bandit Gate 3 evidence. |

## R3 — API key disclosure (plaintext at rest / in logs / in 500 detail)

| Field | Value |
|---|---|
| **ID** | R3 (SPEC §9) |
| **Name** | API key leaks via storage, comparison, exception, or log |
| **Likelihood** | 2 — sha256 + `hmac.compare_digest` + one-shot plaintext print + `revoked_at` filter, regression-tested in `test_fr03.py` |
| **Impact** | 5 — credential disclosure enables full authenticated access for the lifetime of the key |
| **Score** | 10 |
| **Bucket** | HIGH |
| **Category** | SEC |
| **Status** | RESOLVED |
| **Mitigation approach** | FR-03 design: hash-at-rest + constant-time comparison + single plaintext print + revocation timestamp filter. **Residual**: NFR-04 (DB URL redaction) is satisfied by `_REDACT_USERINFO` in `config.py:51` + sqlalchemy logger raised to WARNING during `create_engine` (live-verified in bug-hunt T-08). Maintain the one-shot plaintext contract in any new key-issuance code path; `health.py:230-236` already excludes Settings from `/v1/metrics`. |
| **Source** | SPEC §9 R3; bug-hunt `threat_T-03` (low/refuted, mitigation verified effective); bug-hunt `threat_T-08` (low/refuted, mitigation verified effective). |

## R4 — 403/404 leak resource existence (enumeration)

| Field | Value |
|---|---|
| **ID** | R4 (SPEC §9) |
| **Name** | Authorisation decision happens after resource lookup, so timing/response distinguishes "exists but not yours" from "doesn't exist" |
| **Likelihood** | 2 — `deps.py` runs `_resolve_or_raise` (auth) before the route's resource query; FR-04 §8 #6 contract |
| **Impact** | 3 — information disclosure enabling targeted enumeration |
| **Score** | 6 |
| **Bucket** | MED |
| **Category** | SEC |
| **Status** | MONITORED |
| **Mitigation approach** | Auth-before-fetch is wired in `deps.py` and covered by `test_fr04.py`. **Residual**: any new route that bypasses `_resolve_or_raise` and queries the repository directly would re-introduce the leak — lint-imports + per-route code review must keep the auth-before-fetch invariant. No bug-hunt finding for R4 in current re-hunt; keep as a re-hunt seed. |
| **Source** | SPEC §9 R4; SPEC §8 #6. |

## R5 — N+1 query on large tables (performance cliff)

| Field | Value |
|---|---|
| **ID** | R5 (SPEC §9) |
| **Name** | Unbounded `selectinload` / lazy-load regresses to O(N) queries on 10k-row tables |
| **Likelihood** | 2 — `sqlalchemy.event.listen` SQL-count assertions in `test_perf_*` enforce constant statement count vs row count |
| **Impact** | 4 — endpoint latency climbs from p95 < 30ms to seconds; rate-limit denial cascades |
| **Score** | 8 |
| **Bucket** | MED (just below HIGH; treat as HIGH for mitigation plans) |
| **Category** | PERF |
| **Status** | MONITORED |
| **Mitigation approach** | Explicit eager-loading + SQL-count assertions + pytest-benchmark p95 thresholds (SPEC §11). Gate 3 perf: `get_by_id` 241µs, `list_paginated` 315µs, `create_task` 368µs (all ≪ 1000ms). **Residual**: any new endpoint that adds a relationship must declare its loader strategy in code review; consider extending the SQL-count listener to all paginated endpoints in P8 maintenance. |
| **Source** | SPEC §9 R5; SPEC §11 perf thresholds; Gate 3 perf evidence. |

## R6 — 500 body leaks internal structure (paths, SQL, stack traces)

| Field | Value |
|---|---|
| **ID** | R6 (SPEC §9) |
| **Name** | Unhandled exception's `detail` field echoes paths, SQL fragments, or stack traces |
| **Likelihood** | 1 — `app.py` now uses three regex classes (`_TRACEBACK_MARKER`, `_SQL_FRAGMENT`, `_ABSOLUTE_PATH`) + `config.redact()` fallback; live-probed against Linux-style absolute path, SQL fragment, stack-trace marker (all three → "Internal server error.") |
| **Impact** | 4 — reconnaissance + targeted exploit chain |
| **Score** | 4 |
| **Bucket** | MED |
| **Category** | SEC |
| **Status** | RESOLVED (commit `c1351e5`) |
| **Mitigation approach** | RFC 7807 fixed-field envelope + allowlisted `detail` + regex-class denylist + secret-redaction pass. **Residual**: `test_nfr_spec_coverage.py` exercises the matrix; on any future addition to the regex denylist, re-run the probe suite. If a new attack vector is found in re-hunt (T-05 family), the denylist is the right place — not a global try/except. |
| **Source** | SPEC §9 R6; bug-hunt `errors#1` (medium, RESOLVED fix_commit `c1351e550258f4570e32f174fdc6fb6d048e723c`). |

## R7 — CancelledError swallowed → shutdown hangs

| Field | Value |
|---|---|
| **ID** | R7 (SPEC §9) |
| **Name** | Broad `except Exception` or `except BaseException` swallows the asyncio cancellation signal and strands subprocesses / task groups |
| **Likelihood** | 1 — `ast-error-handling` Gate 3 score 86.7 (13/15 with handlers, 0 anti-patterns); explicit forbidden contract in CLAUDE.md ("CancelledError must propagate, never be swallowed as a generic Exception"); `test_*.py` regression coverage |
| **Impact** | 4 — graceful-shutdown hangs until SIGKILL; orchestrator restart loop |
| **Score** | 4 |
| **Bucket** | MED |
| **Category** | REL |
| **Status** | MONITORED |
| **Mitigation approach** | Architectural constraint in CLAUDE.md + `ast-error-handling` tool detects anti-patterns (`except Exception` containing `pass` / `return` / `continue`). **Residual**: the tool may mis-detect on async-only patterns (SPEC §10 calls this out as "P4 bug-hunt discovery"); if a future async-specific false negative emerges, raise the regex in the framework's scanner — do not patch source. |
| **Source** | SPEC §9 R7; SPEC §10 async note; Gate 3 error_handling evidence. |

## R8 — Task timeout leaves orphan subprocess(es)

| Field | Value |
|---|---|
| **ID** | R8 (SPEC §9) |
| **Name** | `asyncio.wait_for` fires but `proc.kill()` / `proc.wait()` are skipped on the TimeoutError path |
| **Likelihood** | 1 — `runner.py:113-119` (TimeoutError) and `runner.py:120-128` (CancelledError) both `proc.kill()` + `await proc.wait()`; `drain()` (`runner.py:293-342`) bounds shutdown wait and marks stragglers `interrupted`; live-probed by `test_bughunt_runner_unspawnable_command_reaches_terminal_state` (passes) |
| **Impact** | 4 — process-table exhaustion; container PID cap hit; no graceful drain |
| **Score** | 4 |
| **Bucket** | MED |
| **Category** | REL |
| **Status** | RESOLVED (with `runner#2` in same fix wave) |
| **Mitigation approach** | FR-08 / SPEC §8 #25. Reap on both timeout and cancellation. **Residual**: spawn-failure branch (`runner.py:158-171`) catches `(OSError, ValueError)` and writes `STATE_FAILED`; this was the `runner#2` critical-severity finding — regression test passes. Any new subprocess path must mirror both reaping branches. |
| **Source** | SPEC §9 R8; bug-hunt `runner#2` (high, RESOLVED); bug-hunt `threat_T-07` (low/refuted, verified). |

## R9 — Deploy without running migrations → schema drift / 500s

| Field | Value |
|---|---|
| **ID** | R9 (SPEC §9) |
| **Name** | New deploy forgets `alembic upgrade head`; service comes up against pre-migration schema |
| **Likelihood** | 2 — `/readyz` returns 503 on stale `alembic_version` (FR-09 / SPEC §8 #11); container entrypoint runs `alembic upgrade head` before uvicorn |
| **Impact** | 3 — partial outage, traffic shed by load balancer |
| **Score** | 6 |
| **Bucket** | MED |
| **Category** | OPS |
| **Status** | MONITORED |
| **Mitigation approach** | Fail-closed `/readyz` + container entrypoint sequence. **Residual**: in-memory SQLite test path uses `Base.metadata.create_all` for speed and is the only path where `/readyz` would not catch a missing migration — production uses file-based SQLite via `TASKQ_DB_URL`, so this is contained. Document the entrypoint sequence in `08-config/DEPLOY.md` (P5 deliverable). |
| **Source** | SPEC §9 R9; SPEC §8 #11. |

## R10 — Connection-pool exhaustion

| Field | Value |
|---|---|
| **ID** | R10 (SPEC §9) |
| **Name** | Concurrent load drains the SQLAlchemy pool; new requests block until timeout |
| **Likelihood** | 2 — `pool_pre_ping=True` (FR-06) + admission-gate cap `TASKQ_MAX_CONCURRENT` (FR-08) + slot-release in `run_state.py:132-141` |
| **Impact** | 3 — degraded latency under burst |
| **Score** | 6 |
| **Bucket** | MED |
| **Category** | REL |
| **Status** | MONITORED |
| **Mitigation approach** | Pool pre-ping + concurrency cap + per-request session lifecycle. **Residual**: `bug-hunt ratelimit#1` (medium, OPEN) calls out that the per-key bucket is fail-open and is only applied **after** auth — an unauthenticated flood of bad keys reaches `key_repo.get_active_by_hash` for each request without consuming a bucket. A burst of bad keys can therefore drain the pool before any authenticated request is throttled. Track in mitigation plan as a HIGH sub-risk because it converts R10 from "monitored" to "exploitable." |
| **Source** | SPEC §9 R10; bug-hunt `ratelimit#1` (medium, OPEN); bug-hunt `runner#1` (critical, RESOLVED) — admission gate now correctly releases slots. |

## R11 — Transitive dependency introduces non-allowlist license

| Field | Value |
|---|---|
| **ID** | R11 (SPEC §9) |
| **Name** | New `pip install` (direct or transitive) drags in GPL/AGPL/SSPL/etc., breaking the license allowlist |
| **Likelihood** | 1 — `pip-licenses` Gate 3 result: 89 files scanned, 0 licenses outside MIT/BSD/Apache-2.0/PSF allowlist; `requirements.txt` is fully pinned |
| **Impact** | 3 — legal/compliance blocker for redistribution |
| **Score** | 3 |
| **Bucket** | LOW |
| **Category** | COMP |
| **Status** | MONITORED |
| **Mitigation approach** | Lock file + full-tree scan + allowlist gate (NFR-07). **Residual**: any `pip install <pkg>` in a feature branch must be followed by `pip-licenses --allow-only …` and a Gate 3 re-run before merge; CI currently has no per-PR license gate — this is a candidate for P8 maintenance. |
| **Source** | SPEC §9 R11; Gate 3 license_compliance evidence. |

## R12 — Rate-bucket race overshoots the per-key cap

| Field | Value |
|---|---|
| **ID** | R12 (SPEC §9) |
| **Name** | Token bucket read-modify-write is non-atomic; concurrent withdraws can both succeed past the cap |
| **Likelihood** | 1 — `rate_repo.withdraw` (FR-05) wraps the read + write in a single transaction with `SELECT … FOR UPDATE` (or SQLite equivalent); regression-tested |
| **Impact** | 2 — minor over-admission under high concurrency |
| **Score** | 2 |
| **Bucket** | LOW |
| **Category** | REL |
| **Status** | MONITORED |
| **Mitigation approach** | Single transaction + row-level lock. **Residual**: `ratelimit#1` (OPEN, medium) is a *different* bucket-engine risk (fail-open on exception + ordering against auth) — see R10; R12 itself remains GREEN. |
| **Source** | SPEC §9 R12. |

## R13 — `auth#1`: production auth branches on a test stub's `__name__`

| Field | Value |
|---|---|
| **ID** | R13 |
| **Name** | `service/auth.py:_is_wrong_key_stub_active` inspects `key_repo.get_active_by_hash.__name__ == "_stub_active"` and returns NOT_FOUND vs None based on that — production behaviour coupled to a test double's identifier |
| **Likelihood** | 3 — any monkeypatch / decorator wrapping of `get_active_by_hash` with a function of that name flips the branch (low barrier; many test setups do this) |
| **Impact** | 2 — currently capped at medium because `deps.py:87` treats NOT_FOUND and None identically as 401 (refuter established no privilege-escalation); defect is test-shaped code in the production decision tree |
| **Score** | 6 |
| **Bucket** | MED |
| **Category** | ARCH |
| **Status** | OPEN |
| **Mitigation approach** | (a) Delete `_is_wrong_key_stub_active` and always return NOT_FOUND on the no-row path; (b) adjust the FR-03 test to match. Until removed, every new auth test must avoid monkeypatching `get_active_by_hash` to a callable literally named `_stub_active`. **Owner / deadline**: see `RISK_MITIGATION_PLANS.md` §M-AUTH1. |
| **Source** | bug-hunt `auth#1` (medium, OPEN, confirmed, refuter established 401 outcome identical either way). |

## R14 — `auth#2`: defense-in-depth gap in `has_scope` ranks unknown required scopes as 0

| Field | Value |
|---|---|
| **ID** | R14 |
| **Name** | `auth.py:37` `_SCOPE_RANK` is a 3-entry table; `auth.py:123` reads the required scope with `.get(..., 0)`, so a required scope not registered in the table ranks below `read`; a held `read` (rank 1) or higher satisfies any unknown required scope |
| **Likelihood** | 3 — live probe on HEAD `b5b87b3`: `has_scope('write', 'superadmin') == True`, `has_scope('admin', 'superadmin') == True`. Latent today (no route uses an unregistered scope), so the trigger is "first new route added without updating `_SCOPE_RANK`" |
| **Impact** | 4 — any future route added with an unranked required scope is silently over-privileged for every existing key (read+ passes unknown required); privilege escalation on a single missed table edit |
| **Score** | 12 |
| **Bucket** | HIGH |
| **Category** | SEC |
| **Status** | OPEN |
| **Mitigation approach** | Two candidate fixes — pick one: (a) deny when `required not in _SCOPE_RANK` (`has_scope` raises `UnknownScope`); (b) keep a parallel set of valid scopes and have `require_api_key_with_scope()` raise at dependency-construction time on an unknown value. (b) is preferred because it fails at dependency wire-up rather than at request time. **Owner / deadline**: see `RISK_MITIGATION_PLANS.md` §M-AUTH2. |
| **Source** | bug-hunt `auth#2` (medium, OPEN, live-verified on HEAD); `deps.py:136-164` per-route dependency factory. |

## R15 — `ratelimit#1`: rate limiting is fail-open and never applies before auth (T-02 ineffective)

| Field | Value |
|---|---|
| **ID** | R15 |
| **Name** | (a) `deps.py:51-56` swallows every bucket failure and admits the request; (b) `_enforce_rate_limit` is only invoked **after** `_resolve_or_raise` succeeds — an unauthenticated flood of bad `X-API-Key` values is never throttled while each attempt still runs `key_repo.get_active_by_hash` against the pool |
| **Likelihood** | 4 — trigger is "first burst of bad keys" (low barrier; trivial to script); pool drain path is direct |
| **Impact** | 3 — pool exhaustion / DB connection starvation → R10 escalates from MONITORED to ACTIVE; cascading 5xx on authenticated traffic |
| **Score** | 12 |
| **Bucket** | HIGH |
| **Category** | REL (with SEC amplification because unauthenticated floods bypass rate-limit entirely, weakening T-02) |
| **Status** | OPEN |
| **Mitigation approach** | Two changes: (1) fail **closed** (429) on bucket errors — replace `except Exception: return` with re-raise / explicit 429; (2) add a coarse per-IP bucket **before** `_resolve_or_raise`, sized to absorb credential-stuffing at the LB rather than at the DB. **Owner / deadline**: see `RISK_MITIGATION_PLANS.md` §M-RATE1. |
| **Source** | bug-hunt `ratelimit#1` (medium, OPEN, attack_vector=T-02 denial_of_service, mitigation_effective=false); live-verified `deps.py:53` bare except + `deps.py:149-153` ordering. |

## R16 — `tasks#1`: `list_tasks_endpoint` accepts a negative `limit`

| Field | Value |
|---|---|
| **ID** | R16 |
| **Name** | `api/tasks.py:108` checks only the upper bound (`> 200`); `limit=-5` reaches `list_paginated` and issues `LIMIT -4`; on SQLite a negative LIMIT means "no limit", defeating the bounded-page-size guard |
| **Likelihood** | 2 — negative `limit` is unusual in client libraries, but curl / direct API users can pass it |
| **Impact** | 2 — full-table scan served in a single response; small operational impact at current 10k-row scale, but unbounded as data grows |
| **Score** | 4 |
| **Bucket** | MED |
| **Category** | DATA / PERF |
| **Status** | OPEN |
| **Mitigation approach** | One-line Pydantic validator: `limit: int = Field(50, ge=1, le=200)` at the request schema, so `limit=-5` is rejected at 422 before reaching the repository. **Owner / deadline**: see `RISK_MITIGATION_PLANS.md` §M-TASKS1. |
| **Source** | bug-hunt `tasks#1` (low, OPEN). |

---

## Risk heat-map (L × I grid)

| L ↓ \ I → | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| **5** | — | — | — | — | — |
| **4** | — | — | R15 | — | — |
| **3** | — | R13 | — | R14 | — |
| **2** | — | R16 | R9, R10 | R3, R5, R7, R8 | R1 |
| **1** | — | R12 | R11 | R2, R6 | — |

## Bucket counts

| Bucket | Count | IDs |
|---|---|---|
| HIGH (S ≥ 9) | 4 | R1, R3, R14, R15 |
| MED (4 ≤ S ≤ 8) | 9 | R2, R4, R5, R6, R7, R8, R9, R10, R13, R16 |
| LOW (S ≤ 3) | 2 | R11, R12 |

> Note: R5 (S=8) is treated as HIGH for mitigation planning because NFR-01 perf budget has no headroom (p95 < 30ms vs measured 0.27ms is not the same as "perf risk is bounded") — see `RISK_MITIGATION_PLANS.md`.

## OPEN risks at end of Phase 7

| ID | Title | Severity | S |
|---|---|---|---|
| R13 | `auth#1` test-shaped backdoor in auth branch | medium | 6 |
| R14 | `auth#2` unknown-scope defense-in-depth gap | medium | 12 |
| R15 | `ratelimit#1` fail-open + post-auth-only rate limit | medium | 12 |
| R16 | `tasks#1` negative `limit` accepted | low | 4 |

All four are seeded from the bug-hunt re-hunt on `b5b87b3`. Per the `adversarial_review` rubric (Gate 3: critical/high only block; medium/low do not), none of these block Gate 3/4 — but all four are formal mitigation-plan entries in `RISK_MITIGATION_PLANS.md` because R14 and R15 have HIGH bucket scores and the user has explicitly asked for HIGH mitigation plans.
