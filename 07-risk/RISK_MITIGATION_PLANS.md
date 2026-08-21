# RISK_MITIGATION_PLANS — taskq-cc

> **Phase**: 7 — Risk Management
> **Generated**: 2026-08-21
> **Project**: taskq-cc
> **Source**: `07-risk/RISK_REGISTER.md` — only HIGH-bucket risks (Likelihood × Impact ≥ 9) get formal plans here. R5 (S=8, MED) is included as a near-HIGH escalation because NFR-01 perf budget has no headroom once you account for the next 10× data growth and the perf-budget failure mode is non-recoverable in production (p99 cliff is binary, not gradual).
>
> **Hard rule from Phase 7 plan**: any mitigation that requires code changes must run the full TDD cycle (`run-fr-step --step TDD-RED → TDD-GREEN → TDD-IMPROVE → GATE1`) before merge. The plans below describe the **what / why / acceptance criteria**, NOT the implementation — implementation lives in Phase 3 (per-FR TDD) once a plan is approved.
>
> **Hard rule from CLAUDE.md / project hard-rules**: do NOT modify files under `harness/`. The framework's `ast-error-handling` / `ast-assertions` scanners are the only authority for the anti-pattern checks; if a scanner mis-fires on async code (SPEC §10), the scanner is the right thing to fix, not the source.

---

## Mitigation plan index

| ID | Risk | S | Bucket | Owner | Target | Plan file section |
|---|---|---|---|---|---|---|
| M-DATA-V3 | R1 v3 round-trip data loss | 10 | HIGH | Johnny | 2026-08-22 | §1 |
| M-SEC-KEY | R3 API key disclosure | 10 | HIGH | Johnny | 2026-08-22 (verify only) | §2 |
| M-AUTH2 | R14 unknown-scope over-privilege | 12 | HIGH | Johnny | 2026-08-23 | §3 |
| M-RATE1 | R15 fail-open + post-auth-only rate limit | 12 | HIGH | Johnny | 2026-08-23 | §4 |
| M-PERF-N1 | R5 N+1 query cliff | 8 | MED (escalated HIGH) | Johnny | 2026-08-29 (P8) | §5 |
| M-TASKS1 | R16 negative `limit` | 4 | MED (low-cost hygiene) | Johnny | 2026-08-22 | §6 |

> **R10 / R15 split**: R10 (pool exhaustion, S=6, MED) is MONITORED and does not require a formal plan here; however R15 (the *attack path* that triggers R10) IS a HIGH plan. R10 mitigation is effectively bundled into M-RATE1 — closing R15 closes the most likely path to R10. R13 (`auth#1`) is MED (S=6) and is **not** in this plan because (a) the refuter established no privilege-escalation path; (b) the fix is a 5-line deletion + a 1-line test change that belongs in Phase 8 maintenance, not a formal mitigation plan. It is listed in `RISK_REGISTER.md` as OPEN with the deletion recipe.

---

## §1 — M-DATA-V3 — R1 v3 data migration round-trip data loss

### Risk under mitigation
- **Risk ID**: R1
- **Title**: v3 `tasks.result_json` ↔ `task_results` round-trip drops run history
- **Score**: 10 (L=2 × I=5)
- **Category**: DATA (data integrity / silent loss)

### Why this is HIGH despite RESOLVED status
The fix shipped (`5c1c2cfdc6c1876e66b38e276ae7dd3174daf837` — correlated subquery rewritten with `ORDER BY started_at DESC, id DESC LIMIT 1`); the regression test (`test_bughunt_v3_downgrade_restores_latest_result_for_multi_run_tasks`) passes on HEAD. But:
1. The migration is the only code path that mutates user data on `alembic downgrade` — silent failure mode is worst-class for a backup/rollback procedure.
2. The fix is in one file (`migrations/versions/v3_split_results.py:100-113`); any future edit to that file can re-introduce the bug.
3. SPEC §11 mandates "migration 往返資料一致 = 100% (逐欄)" as a hard Gate-4 threshold.

### Plan
| Step | Action | Verification |
|---|---|---|
| 1 | Keep the existing regression test (`test_bug_hunt_regressions.py::test_bughunt_v3_downgrade_restores_latest_result_for_multi_run_tasks`) green on every commit touching `migrations/versions/`. | `pytest 03-development/tests/test_bug_hunt_regressions.py::test_bughunt_v3_downgrade_restores_latest_result_for_multi_run_tasks -v` |
| 2 | Extend the round-trip test to cover: (a) 0 runs, (b) 1 run, (c) N runs where N ≥ 5, asserting the **last** run's payload survives byte-for-byte and earlier payloads are absent post-downgrade (the downgrade is lossy by design, but it must be lossy in a **declared** way). | new test name: `test_v3_roundtrip_declares_latest_wins`; passes; SPEC §11 row "migration 往返資料一致" still satisfied because the contract is "latest wins on downgrade". |
| 3 | Add a docstring note to `v3_split_results.py::downgrade()` documenting the lossy-by-design behaviour and pointing at the regression test. | `ast-docstrings` Gate 3 still 100%; new docstring carries `[FR-07]` tag. |
| 4 | Add `migrations/versions/` to the "code-change triggers full TDD" list in the P3 plan, so any future edit re-runs the round-trip test as part of the TDD-RED step. | `phase3_plan.md` updated; no harness/ edit. |
| 5 | Owner sign-off: Johnny. | Recorded in `07-risk/RISK_STATUS_REPORT.md` §A. |

### Acceptance criteria
- [ ] Round-trip test green on HEAD.
- [ ] New test for N=5+ runs green on HEAD.
- [ ] `v3_split_results.py::downgrade` carries a docstring noting "lossy by design; latest run wins; see test_v3_roundtrip_declares_latest_wins".
- [ ] No `harness/` edit.

### Owner / deadline
- **Owner**: Johnny (project lead)
- **Target**: 2026-08-22

---

## §2 — M-SEC-KEY — R3 API key disclosure

### Risk under mitigation
- **Risk ID**: R3
- **Title**: API key leaks via storage, comparison, exception, or log
- **Score**: 10 (L=2 × I=5)
- **Category**: SEC

### Why this is HIGH despite RESOLVED status
1. Credential disclosure is worst-class: a leaked key is exploitable for the entire key lifetime (no rotation protocol beyond `revoked_at`).
2. The mitigation surface is multi-point (storage, comparison, log, exception) — a regression at any single point re-opens the risk.
3. SPEC §11 mandates `DB 連線字串出現於日誌 = 0` and `error_handling` Gate 3 score ≥ 80; both pass today, but the contract is "never leak a credential", which is a stronger property than any single test.

### Plan
| Step | Action | Verification |
|---|---|---|
| 1 | Confirm `auth.py` key-issuance path still hashes with sha256, compares with `hmac.compare_digest`, prints plaintext exactly once, and filters on `revoked_at IS NULL`. | grep + `test_fr03.py` all green. |
| 2 | Confirm `config.py:_REDACT_USERINFO` still rewrites userinfo before `Settings.__repr__`; live probe `Settings(db_url='postgres://u:pw@h/db', ...).__repr__() == "Settings(db_url='postgres[REDACTED]@h/db', ...)"`. | one-shot Python probe; assertion passes. |
| 3 | Confirm `session.py` still raises `sqlalchemy.engine.create` logger to WARNING across the `create_engine` window. | one-shot Python probe; `caplog.at_level(WARNING)` shows no `INFO`/`DEBUG` URL emission. |
| 4 | Confirm `health.py:230-236` metrics body interpolates no Settings. | grep `health.py` for `Settings` and `config.` — both zero hits in the response builder. |
| 5 | Re-run all bug-hunt `threat_T-03` and `threat_T-08` probes against HEAD; both already refuted in the current re-hunt. | `bug_hunt_report.json` shows `mitigation_effective: true` for both. |
| 6 | Owner sign-off. | Recorded in `RISK_STATUS_REPORT.md` §B. |

### Acceptance criteria
- [ ] All four checks pass.
- [ ] No `harness/` edit.
- [ ] No new code path introduced (this is a verification plan, not a fix plan — R3 is RESOLVED).

### Owner / deadline
- **Owner**: Johnny
- **Target**: 2026-08-22 (verification only — no code change expected)

---

## §3 — M-AUTH2 — R14 unknown-scope defense-in-depth gap

### Risk under mitigation
- **Risk ID**: R14
- **Title**: `has_scope` ranks unknown required scopes as 0, allowing any read+ key to satisfy them
- **Score**: 12 (L=3 × I=4)
- **Category**: SEC (privilege escalation, defense-in-depth)

### Why this is HIGH
1. Trigger is "first new route added without updating `_SCOPE_RANK`" — a single missed table edit silently over-privileges every existing key.
2. `has_scope('write', 'superadmin') == True` and `has_scope('admin', 'superadmin') == True` are both true on HEAD `b5b87b3` (live-verified by the bug hunt).
3. The bug is silent: there is no log, no exception, no 4xx — the dependency just passes.
4. Impact on next FR added is "fail-open privilege escalation" — exactly the class of bug SPEC §8 #6 is designed to prevent.

### Plan
| Step | Action | Verification |
|---|---|---|
| 1 | TDD-RED: add `test_has_scope_unknown_required_returns_false` to `tests/test_auth.py` covering `has_scope('read', 'superadmin')`, `has_scope('write', 'superadmin')`, `has_scope('admin', 'superadmin')` — all three must return `False`. | test fails on HEAD; commit message "test(auth): R14 red — has_scope rejects unknown required". |
| 2 | TDD-GREEN: pick mitigation (b) from `RISK_REGISTER.md` R14 — keep a parallel set of valid scopes (`_VALID_SCOPES = frozenset(_SCOPE_RANK)`); have `require_api_key_with_scope()` raise at dependency-construction time on an unknown value. | test passes; no `has_scope` call site changes needed because the rank-default path is now only reachable for **held** scopes, not required scopes. |
| 3 | TDD-IMPROVE: add a regression test that asserts `require_api_key_with_scope('superadmin')` raises at construction time (before request). | new test name `test_require_api_key_with_scope_rejects_unknown_at_wireup`. |
| 4 | Run full `test_fr03.py`, `test_fr04.py`, `test_fr05.py` (admin / write / read paths) to confirm no regression. | full FR test suite green. |
| 5 | Run mutation-test on `service/auth.py`; kill rate must remain ≥ 70 (current 81.6 → expected higher after fix because the new branch is assertion-covered). | `mutmut run` → kill count for auth.py increases; score non-decreasing. |
| 6 | GATE1: full per-FR Gate 1 re-run for FR-03/04/05. | Gate 1 PASS for all three FRs. |
| 7 | Owner sign-off; re-hunt `auth#2` should now refute. | Recorded in `RISK_STATUS_REPORT.md` §C. |

### Acceptance criteria
- [ ] `test_has_scope_unknown_required_returns_false` green on HEAD.
- [ ] `test_require_api_key_with_scope_rejects_unknown_at_wireup` green on HEAD.
- [ ] FR-03/04/05 full TDD re-run green; mutation score non-decreasing.
- [ ] No `harness/` edit.
- [ ] Next bug-hunt re-hunt marks `auth#2` as refuted.

### Owner / deadline
- **Owner**: Johnny
- **Target**: 2026-08-23

---

## §4 — M-RATE1 — R15 fail-open + post-auth-only rate limit (T-02 ineffective)

### Risk under mitigation
- **Risk ID**: R15
- **Title**: (a) `_enforce_rate_limit` swallows bucket errors and admits the request; (b) `_enforce_rate_limit` runs only after auth, so unauthenticated bad-key floods reach the pool unthrottled
- **Score**: 12 (L=4 × I=3)
- **Category**: REL (with SEC amplification — T-02 declared mitigation is ineffective)

### Why this is HIGH
1. Trigger is trivial — `for i in {1..10000}; do curl -H "X-API-Key: bad" /v1/tasks; done` — a 4-line bash loop drains the pool.
2. Pool exhaustion cascades to authenticated users (R10 escalates MONITORED → ACTIVE); this is the **declared T-02 denial-of-service** threat.
3. Bug-hunt refuter confirmed: monkey-patching `ratelimit.check` to raise still admits the request; `deps.py:53` is `except Exception: return`.
4. SPEC §8 declares T-02 mitigation as `single transaction + row-level lock + rate-limit per IP at LB`; current code only does the first two and the per-IP layer is missing.

### Plan
| Step | Action | Verification |
|---|---|---|
| 1 | TDD-RED: add `test_rate_limit_fail_closed_on_bucket_error` — monkey-patch `ratelimit.check` to raise `RuntimeError`; assert 429 (not 200) and that the request does not reach the route. | test fails on HEAD. |
| 2 | TDD-RED: add `test_rate_limit_applies_before_auth` — issue a flood of bad-key requests and assert that the **N+1**-th request is 429 (or 401), not 200, and that `key_repo.get_active_by_hash` is called at most K times regardless of flood size. | test fails on HEAD; depends on a per-IP bucket being added (not just fail-closed). |
| 3 | TDD-GREEN: change `deps.py:51-56` from `except Exception: return` to `except Exception: raise HTTPException(429, …)` (fail-closed on bucket engine). | test 1 passes. |
| 4 | TDD-GREEN: add a coarse per-IP bucket that runs **before** `_resolve_or_raise`. Use the same `rate_repo` engine but a separate key (`f"ip:{request.client.host}"`); cap = e.g. `TASKQ_RATE_PER_MIN_PER_IP` default 600 (10 rps sustained). | test 2 passes. |
| 5 | TDD-IMPROVE: add `Settings.rate_per_min_per_ip: int = 600` to `config.py`; ensure it appears in `.env.example` (P5 hygiene — `preflight_config_liveness` blocks orphans). | `run-env-check --phase 7` PASS. |
| 6 | Run `test_fr05.py` (rate-limit per key) and the new tests; confirm no regression. | full FR-05 suite green. |
| 7 | Re-run mutation test on `service/ratelimit.py` and `api/deps.py`; kill rate must remain ≥ 70. | `mutmut run` score non-decreasing. |
| 8 | GATE1: full per-FR Gate 1 re-run for FR-05. | Gate 1 PASS. |
| 9 | Owner sign-off; re-hunt `ratelimit#1` should now refute. | Recorded in `RISK_STATUS_REPORT.md` §D. |

### Acceptance criteria
- [ ] `test_rate_limit_fail_closed_on_bucket_error` green on HEAD.
- [ ] `test_rate_limit_applies_before_auth` green on HEAD.
- [ ] `TASKQ_RATE_PER_MIN_PER_IP` declared in `config.py` and `.env.example`; `preflight_config_liveness` clean.
- [ ] FR-05 full TDD re-run green; mutation score non-decreasing.
- [ ] No `harness/` edit.
- [ ] Next bug-hunt re-hunt marks `ratelimit#1` as refuted.

### Risks of this plan (call out before approval)
- **False-positive 429 risk**: switching to fail-closed means a transient bucket-engine outage now denies all traffic. Mitigation: log every 429 with the underlying exception class at WARNING so on-call can distinguish "bucket engine dead" from "actual flood".
- **Per-IP bucket collides behind NAT**: a corporate egress NAT means 1000 users share one bucket. Mitigation: cap is 600/min (10 rps sustained) — generous; and the per-key bucket still applies after auth as a second layer.
- **Per-IP bucket key collision with auth key space**: keys are namespaced under `key:{key_id}` today; the new IP bucket uses `ip:{ip}` — no collision because `rate_repo.withdraw` namespaces on the full string.

### Owner / deadline
- **Owner**: Johnny
- **Target**: 2026-08-23

---

## §5 — M-PERF-N1 — R5 N+1 query cliff (escalated MED → HIGH)

### Risk under mitigation
- **Risk ID**: R5
- **Title**: N+1 query on large tables; pagination regresses to O(N) at 10k+ rows
- **Score**: 8 (L=2 × I=4) — technically MED, escalated to HIGH because of the failure-mode shape
- **Category**: PERF

### Why escalated
1. NFR-01 budget is p95 < 30ms on `GET /v1/tasks/{id}` and p95 < 80ms on `GET /v1/tasks?limit=50` at 10k rows (SPEC §11).
2. Measured today: 0.241ms / 0.315ms — a 90× safety margin that **erodes silently** as relationships are added to the response shape.
3. N+1 is a binary failure mode: latency is fine until a relationship is added without an explicit loader, then p99 jumps from 30ms to 3000ms in one PR.
4. Current mitigation (SQL-count assertions in `test_perf_*`) covers the three benchmark targets but does not extend to other paginated endpoints or to the JSON-serialization step.

### Plan
| Step | Action | Verification |
|---|---|---|
| 1 | Inventory every paginated endpoint in `api/*.py`; for each, add a `test_*_no_n_plus_1` that seeds N=200 rows and asserts SQL statement count is **constant** in N. | new test names follow `test_{route}_no_n_plus_1`. |
| 2 | For every endpoint that returns a relationship, declare its loader strategy in a comment (`# N+1 guard: selectinload(Task.runs)`); audit catches mismatches in code review. | grep `selectinload\|joinedload\|subqueryload` in `repository/*.py`; every paginated route's repository call is matched. |
| 3 | Extend the SQLAlchemy event listener (currently in `test_perf_*`) to fire on **every** test run with a soft-warning if statement count grows by > 50% vs the recorded baseline. | pytest run shows warnings only on regressions, not on the steady state. |
| 4 | Run `pytest 03-development/tests --benchmark-disable` (full suite, no benchmark) to confirm N+1 audit tests pass. | full suite green. |
| 5 | Owner sign-off; re-run Gate 3 perf. | Gate 3 perf remains 100; Gate 4 perf remains 100. |

### Acceptance criteria
- [ ] Every paginated endpoint has an `test_*_no_n_plus_1` test.
- [ ] Every paginated endpoint's repository call declares its loader strategy.
- [ ] SQL-statement-count regression listener in place.
- [ ] Gate 3/4 perf dimension unchanged (still 100).
- [ ] No `harness/` edit.

### Why this is P8, not P7
This is a hygiene / coverage expansion that does not block Gate 3 or Gate 4 (perf is already 100). It is scheduled for P8 maintenance per the project's standard sequencing — risk register entry exists so the work is not forgotten, not because it must ship before sign-off.

### Owner / deadline
- **Owner**: Johnny
- **Target**: 2026-08-29 (P8 maintenance window)

---

## §6 — M-TASKS1 — R16 negative `limit` accepted (low-cost hygiene)

### Risk under mitigation
- **Risk ID**: R16
- **Title**: `list_tasks_endpoint` accepts `limit=-5`; SQLite interprets `LIMIT -4` as "no limit"
- **Score**: 4 (L=2 × I=2)
- **Category**: DATA / PERF

### Why included despite low score
1. Fix is a 1-line Pydantic validator change with a 1-line test — total ~5 LOC, no architectural impact.
2. Failure mode (unbounded response) is data-exfiltration-shaped if combined with an enumeration endpoint later.
3. Including in the formal plan keeps the "fix and test in one PR" path traceable from the risk register.

### Plan
| Step | Action | Verification |
|---|---|---|
| 1 | TDD-RED: add `test_list_tasks_rejects_negative_limit` — assert 422 on `limit=-5` and 422 on `limit=0`. | test fails on HEAD. |
| 2 | TDD-GREEN: change the Pydantic schema for `list_tasks` to `limit: int = Field(50, ge=1, le=200)`. | test passes; full FR-09 suite green. |
| 3 | TDD-IMPROVE: confirm the same validator is applied to any other paginated endpoint (`list_results`, `list_runs`); add analogous tests if missing. | three new test names covering the three paginated routes. |
| 4 | Run Gate 1 for FR-09 (the only FR that owns list endpoints). | Gate 1 PASS. |

### Acceptance criteria
- [ ] `test_list_tasks_rejects_negative_limit` (and analogues for `list_results`, `list_runs`) green on HEAD.
- [ ] Pydantic `Field(..., ge=1, le=200)` applied at the schema layer (not the route layer) so all current and future clients get the validation for free.
- [ ] No `harness/` edit.

### Owner / deadline
- **Owner**: Johnny
- **Target**: 2026-08-22

---

## Summary

| Plan | Status | Code change? | Owner | Target | Blocks Gate 4? |
|---|---|---|---|---|---|
| M-DATA-V3 | verification + minor test expansion | yes (test + docstring) | Johnny | 2026-08-22 | no |
| M-SEC-KEY | verification only | no | Johnny | 2026-08-22 | no |
| M-AUTH2 | full TDD | yes (auth.py + tests) | Johnny | 2026-08-23 | no (medium, but HIGH score; recommend shipping before P7 sign-off) |
| M-RATE1 | full TDD | yes (deps.py + config + tests) | Johnny | 2026-08-23 | no (medium, but HIGH score; recommend shipping before P7 sign-off) |
| M-PERF-N1 | P8 maintenance | yes (tests + comments) | Johnny | 2026-08-29 | no |
| M-TASKS1 | full TDD | yes (schema + tests) | Johnny | 2026-08-22 | no |

None of the OPEN risks block Gate 4 per the `adversarial_review` rubric (critical/high only block). M-AUTH2 and M-RATE1 are HIGH-scoring plans and are recommended to ship before P7 sign-off so that the next bug-hunt re-hunt can refute the corresponding findings.
