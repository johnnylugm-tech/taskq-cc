# RISK_STATUS_REPORT — taskq-cc

> **Phase**: 7 — Risk Management
> **Generated**: 2026-08-21
> **Project**: taskq-cc
> **Companion docs**:
> - `07-risk/RISK_REGISTER.md` — full register with L × I scores and mitigation approach per row.
> - `07-risk/RISK_MITIGATION_PLANS.md` — formal plans for HIGH-bucket risks.
>
> **Gate context**:
> - Gate 3 PASS (`quality_complete=true`, all 17 dimensions ≥ threshold; see `.methodology/gate3_result.json`).
> - Gate 4 PASS (`quality_complete=true`, all 5 devil-advocate dimensions answered; see `.methodology/gate4_result.json`).
> - Bug-hunt re-hunt on `b5b87b3`: 15 raw / 9 confirmed / 6 refuted; 0 critical/high open; 4 medium / 1 low open.
> - Per the `adversarial_review` rubric, critical/high only block; medium/low do not. So no OPEN risk in this register blocks Gate 4.

---

## Status legend

- **OPEN** — risk accepted as known; mitigation plan exists (HIGH) or scheduled (P8 for low-cost hygiene).
- **MONITORED** — mitigation in place; no active defect; re-hunt seed kept in case future changes regress.
- **RESOLVED** — fix shipped, regression-tested; verification cadence remains.
- **ACCEPTED** — residual risk consciously accepted with rationale (used sparingly; not currently any).

---

## §A — M-DATA-V3 (R1) — Status: RESOLVED, plan verification only

| Field | Value |
|---|---|
| **Risk ID** | R1 |
| **Plan ID** | M-DATA-V3 |
| **Owner** | Johnny |
| **Target date** | 2026-08-22 |
| **Current status** | RESOLVED — fix shipped (`5c1c2cfdc6c1876e66b38e276ae7dd3174daf837`); regression test green on HEAD `b5b87b3`. |
| **Open actions** | (1) extend round-trip test to N=5+ runs and assert "latest wins, declared lossy"; (2) add docstring note on `v3_split_results.py::downgrade`; (3) add `migrations/versions/` to "code-change triggers full TDD" list in `phase3_plan.md`. |
| **Blocker for Gate 4?** | No. |
| **Re-hunt seed?** | No. |

---

## §B — M-SEC-KEY (R3) — Status: RESOLVED, plan verification only

| Field | Value |
|---|---|
| **Risk ID** | R3 |
| **Plan ID** | M-SEC-KEY |
| **Owner** | Johnny |
| **Target date** | 2026-08-22 |
| **Current status** | RESOLVED — sha256 + `hmac.compare_digest` + one-shot plaintext + `revoked_at` filter + `_REDACT_USERINFO` + sqlalchemy logger raised to WARNING + `/v1/metrics` excludes Settings; bug-hunt T-03 and T-08 both `mitigation_effective: true`. |
| **Open actions** | Re-run all four live probes against HEAD; confirm no `Settings` or `config.` reference in `health.py:230-236` response builder; confirm `__repr__` redaction. |
| **Blocker for Gate 4?** | No. |
| **Re-hunt seed?** | No. |

---

## §C — M-AUTH2 (R14) — Status: OPEN, full TDD pending

| Field | Value |
|---|---|
| **Risk ID** | R14 |
| **Plan ID** | M-AUTH2 |
| **Owner** | Johnny |
| **Target date** | 2026-08-23 |
| **Current status** | OPEN — `auth.py:37` `_SCOPE_RANK` 3-entry table; `auth.py:123` uses `.get(..., 0)` for required scope; live-verified `has_scope('write', 'superadmin') == True` on HEAD. No current route uses an unregistered scope, so the bug is latent. |
| **Open actions** | (1) TDD-RED `test_has_scope_unknown_required_returns_false`; (2) TDD-GREEN: add `_VALID_SCOPES = frozenset(_SCOPE_RANK)` and have `require_api_key_with_scope()` raise at wire-up on unknown; (3) TDD-IMPROVE regression test for wire-up raise; (4) FR-03/04/05 full TDD re-run; (5) mutation score non-decreasing; (6) Gate 1 re-run for FR-03/04/05. |
| **Blocker for Gate 4?** | No — medium severity, but HIGH score (S=12). Recommend shipping before P7 sign-off so the next bug-hunt re-hunt refutes `auth#2`. |
| **Re-hunt seed?** | Yes — `auth#2` is currently OPEN in `.methodology/bug_hunt_report.json`; the next re-hunt should refute it after M-AUTH2 ships. |

---

## §D — M-RATE1 (R15) — Status: OPEN, full TDD pending

| Field | Value |
|---|---|
| **Risk ID** | R15 |
| **Plan ID** | M-RATE1 |
| **Owner** | Johnny |
| **Target date** | 2026-08-23 |
| **Current status** | OPEN — `deps.py:51-56` swallows bucket errors (admit on exception); `deps.py:149-153` runs rate limit only after auth. Live-verified `ratelimit.check` raising `RuntimeError` still admits the request. Bug-hunt `ratelimit#1` confirms `mitigation_effective: false`. |
| **Open actions** | (1) TDD-RED `test_rate_limit_fail_closed_on_bucket_error`; (2) TDD-RED `test_rate_limit_applies_before_auth`; (3) TDD-GREEN: change `except Exception: return` to `except Exception: raise HTTPException(429, ...)`; (4) TDD-GREEN: add coarse per-IP bucket `ip:{client.host}` running before `_resolve_or_raise`; (5) add `Settings.rate_per_min_per_ip: int = 600` to `config.py`; (6) update `.env.example`; (7) FR-05 full TDD re-run; (8) mutation score non-decreasing; (9) Gate 1 re-run for FR-05. |
| **Blocker for Gate 4?** | No — medium severity, but HIGH score (S=12). Recommend shipping before P7 sign-off so the next bug-hunt re-hunt refutes `ratelimit#1`. |
| **Re-hunt seed?** | Yes — `ratelimit#1` is currently OPEN with `mitigation_effective: false`. |

---

## §E — M-PERF-N1 (R5) — Status: MONITORED, P8 hygiene scheduled

| Field | Value |
|---|---|
| **Risk ID** | R5 |
| **Plan ID** | M-PERF-N1 |
| **Owner** | Johnny |
| **Target date** | 2026-08-29 (P8 maintenance window) |
| **Current status** | MONITORED — `test_perf_*` enforces SQL-count assertions on the three benchmark targets; measured 0.241ms / 0.315ms / 0.368ms vs 1000ms threshold (90× margin). Gate 3 perf 100; Gate 4 perf 100. |
| **Open actions** | (1) inventory every paginated endpoint; (2) add `test_*_no_n_plus_1` per route at N=200 rows; (3) audit `selectinload` / `joinedload` / `subqueryload` coverage in `repository/*.py`; (4) extend SQL-count regression listener to fire on every pytest run. |
| **Blocker for Gate 4?** | No. |
| **Re-hunt seed?** | No. |

---

## §F — M-TASKS1 (R16) — Status: OPEN, low-cost hygiene

| Field | Value |
|---|---|
| **Risk ID** | R16 |
| **Plan ID** | M-TASKS1 |
| **Owner** | Johnny |
| **Target date** | 2026-08-22 |
| **Current status** | OPEN — `api/tasks.py:108` checks only `> 200`; `limit=-5` reaches `list_paginated` and emits `LIMIT -4` which SQLite interprets as "no limit". |
| **Open actions** | (1) TDD-RED `test_list_tasks_rejects_negative_limit` (and analogues for `list_results`, `list_runs`); (2) TDD-GREEN: Pydantic `Field(50, ge=1, le=200)` on the list schema; (3) TDD-IMPROVE: apply same validator to all paginated schemas; (4) FR-09 Gate 1 re-run. |
| **Blocker for Gate 4?** | No — low severity (S=4); ship alongside M-DATA-V3 since they share the same TDD cycle. |
| **Re-hunt seed?** | Yes — `tasks#1` is currently OPEN. |

---

## §G — Risks with no formal plan (MED-bucket, MONITORED)

The following are tracked in `RISK_REGISTER.md` but do not have formal mitigation plans because their score is MED and they are not currently exploitable. They remain MONITORED and are candidates for re-hunt seeding.

| Risk | Title | L × I | Status | Notes |
|---|---|---|---|---|
| R2 | SQL injection | 1 × 5 = 5 | MONITORED | `import-linter` + grep gate + bandit 0/0 hold the line. |
| R4 | 403/404 leak resource existence | 2 × 3 = 6 | MONITORED | Auth-before-fetch in `deps.py`; FR-04 §8 #6 contract. |
| R6 | 500 body leaks internal structure | 1 × 4 = 4 | RESOLVED | Regex-class denylist + `config.redact()` fallback (commit `c1351e5`). |
| R7 | CancelledError swallowed | 1 × 4 = 4 | MONITORED | Architectural constraint in CLAUDE.md; `ast-error-handling` 86.7. |
| R8 | Task timeout leaves orphan subprocess | 1 × 4 = 4 | RESOLVED | `proc.kill() + await proc.wait()` on both TimeoutError and CancelledError paths. |
| R9 | Deploy without migrations | 2 × 3 = 6 | MONITORED | `/readyz` fail-closed + entrypoint sequence. |
| R10 | Connection-pool exhaustion | 2 × 3 = 6 | MONITORED | Pool pre-ping + admission gate; M-RATE1 closes the attack path. |
| R11 | Transitive license | 1 × 3 = 3 | MONITORED | Lock file + scancode full-tree scan. |
| R12 | Rate-bucket race | 1 × 2 = 2 | MONITORED | Single transaction + row-level lock. |
| R13 | `auth#1` test-shaped backdoor | 3 × 2 = 6 | OPEN (hygiene) | Fix is a 5-line deletion + 1-line test change; scheduled for P8 (no formal plan here because S=6 and refuter established no privilege-escalation). |

---

## §H — Roll-up

| Bucket | Count | OPEN | MONITORED | RESOLVED |
|---|---|---|---|---|
| HIGH (S ≥ 9) | 4 | 2 (R14, R15) | 0 | 2 (R1, R3) |
| MED (4 ≤ S ≤ 8) | 10 | 2 (R13, R16) | 7 | 1 (R6, R8) |
| LOW (S ≤ 3) | 2 | 0 | 2 (R11, R12) | 0 |
| **Total** | **16** | **4** | **9** | **3** |

> Note: R6 and R8 are technically in the MED bucket in the register; both are RESOLVED. R10 (MED) is MONITORED but its attack path is the OPEN R15 (HIGH). R13 (MED) is OPEN with a 5-line hygiene fix scheduled for P8 maintenance.

---

## §I — Open items blocking P7 sign-off

**None.** Per the `adversarial_review` rubric (critical/high only block), no OPEN risk blocks Gate 3 or Gate 4. The HIGH-score plans (M-AUTH2, M-RATE1) are **recommended** to ship before P7 sign-off but are not **required** — they are medium-severity bug-hunt findings whose mitigation improves the next re-hunt outcome but does not change any gate score.

Recommended sequencing (in order of cost/benefit):
1. **2026-08-22** — M-DATA-V3 (verification + minor test expansion) + M-SEC-KEY (verification only) + M-TASKS1 (5 LOC). All three are < 30 minutes of work; ship together.
2. **2026-08-23** — M-AUTH2 + M-RATE1 (both full TDD cycles). Higher cost but HIGH score.
3. **2026-08-29** — M-PERF-N1 (P8 hygiene).

---

## §J — Cross-references

- **Risk register**: `07-risk/RISK_REGISTER.md`
- **Mitigation plans**: `07-risk/RISK_MITIGATION_PLANS.md`
- **Bug-hunt report**: `.methodology/bug_hunt_report.json`
- **Gate 3 result**: `.methodology/gate3_result.json`
- **Gate 4 result**: `.methodology/gate4_result.json`
- **Quality manifest**: `.methodology/quality_manifest.json`
- **Phase 7 plan**: `.methodology/phase7_plan.md`
- **SPEC §9**: `/Users/johnny/projects/taskq-cc/SPEC.md` lines 441–456

---

## §K — Change log

| Date | Author | Change |
|---|---|---|
| 2026-08-21 | P7 Risk Author (this agent) | Initial generation — 16 risks (12 from SPEC §9 + 4 from bug-hunt re-hunt on `b5b87b3`); 6 formal mitigation plans; 4 OPEN items none of which block Gate 4. |
