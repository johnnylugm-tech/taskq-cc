# Test Results — Phase 4

> Phase: **4 — Testing** | Run date: 2026-08-21 | Python: `/Users/johnny/projects/taskq-cc/.venv/bin/python`

## Scope of this run

| Item | Value | Source |
|------|-------|--------|
| Test target | `03-development/tests` | `core.quality_gate.test_suite_run.resolve_targets` (`ProjectLayout.active_test_dir`) |
| Coverage target | `03-development/src` | `core.quality_gate.test_suite_run.resolve_targets` (`ProjectLayout.active_src_dir`, no `.coveragerc` override) |
| Pytest | invoked via `python -m pytest <test_target> --cov=<cov_target> --cov-report=term-missing -q` | this document, run 2026-08-21 |

The run is intentionally scoped to the project's own test and source trees. Running pytest from the repository root without an explicit target also collects `harness/tests/*` (the vendored framework's own tests) — `harness/core/quality_gate/test_suite_run.py` documents this in its module docstring ("A bare `pytest` with no path also collects harness/tests/* when harness/ is vendored inside the project"). The scope used here keeps the numerator and denominator inside what this project delivers.

## Pytest summary (verbatim)

```
3 failed, 283 passed, 12 warnings in 89.18s (0:01:29)
```

Source: `04-testing/coverage_raw.txt`, last three lines (the line pytest itself printed at exit).

## Outcomes

| Metric | Count |
|--------|-------|
| Tests collected | 286 |
| Passed | 283 |
| Failed | 3 |
| Skipped (per pytest summary) | 0 |
| Errors | 0 |
| Wall time | 89.18 s (0:01:29) |

Pytest only prints a `skipped` segment in the summary when the count is non-zero (see `core.quality_gate.test_suite_run._parse_skipped`), so the absent segment on this run is `0`, not an unknown.

## Failures

### 1. `test_ac_n8_2_mutmut_score_at_least_70_over_service_and_repository`

- File: `03-development/tests/test_nfr_spec_coverage.py`
- Marker: NFR-08 / AC-N8.2
- Status: **failed**

```
E       AssertionError: assert None is not None
E        +  where None = <built-in method get of dict object at 0x10c320640>('score')
E        +    where <built-in method get of dict object at 0x10c320640> = {'could_not_measure': 'mutmut run failed (return code 14).\nSTDOUT:\n236  ⏰ 2  🤔 1  🙁 97  🔇 0\n...a': 'f6d984bc421b502ed104d9a328f053159e44f504', 'generated_at': '2026-08-20T23:51:52.642775+00:00', 'score': None, ...}.get
```

The test asserts `mutation_score.json["score"]` is a float ≥ 70. The recorded score is `None` because the mutmut subprocess exited with code 14 and the framework recorded `could_not_measure`. The mutmut baseline run itself reports `🎉 239  ⏰ 2  🤔 1  🙁 97  🔇 0` (out of 339) → kill rate ~70.5%, but the framework's `compute_mutation_score` did not capture it. Deferred; this is the same NFR-08 measurement gap flagged in `gate3_result.json` (`mutation_testing.tool_evidence`).

### 2. `test_ac_n8_3_mutation_scope_annotated_service_repository_with_rationale`

- File: `03-development/tests/test_nfr_spec_coverage.py`
- Marker: NFR-08 / AC-N8.3
- Status: **failed**

```
E       AssertionError: assert 'service' in ''
```

The test expects a non-empty rationale string (containing the substring `service`) in `mutation_score.json`. Because `compute_mutation_score` could not record a real score (see failure #1), the rationale field is empty. Same root cause as failure #1.

### 3. `test_ac_n12_2_make_verify_system_exits_zero_stdout_contains_pass`

- File: `03-development/tests/test_nfr_spec_coverage.py`
- Marker: NFR-12 / AC-N12.2
- Status: **failed**

```
E       assert 2 == 0
E        +  where 2 = CompletedProcess(args=['make', 'verify-system'], returncode=2, stdout='  OPS: Operations Per Second, computed as 1 / M...ale\n2 failed, 284 passed, 12 warnings in 44.12s\nverify-system: FAIL\n', stderr='make: *** [verify-system] Error 1\n').returncode
```

The test chains into `make verify-system`. The inner pytest invocation (running the same suite under `TASKQ_INSIDE_VERIFY_SYSTEM=1`) reports `2 failed, 284 passed` and `verify-system: FAIL`, so `make` exits 2. Both inner failures are the NFR-08 tests above — once those are fixed, this test will also pass. Not an independent defect.

## Deferred issues (none blocking Phase 4 exit)

- AC-N8.2 / AC-N8.3 mutmut baseline measurement (failures #1 and #2 above).
- AC-N12.2 chained verify-system (failure #3 — derivative of the above).

No infrastructure failures. No collection errors. No skip-driven regressions: every test that pytest saw was either passed or one of these three NFR-08/NFR-12 AC failures. The NFR-03 readiness test that failed in the prior snapshot (`test_ac_n3_6_failed_migration_rolls_back_readyz_503`) is **passing** on this run — the deferred fix landed between snapshots.

## Reconciliation note

`core.quality_gate.cross_artifact.check_test_count_reconciliation` compares this document's counts against the framework's own `run_suite` measurement for the same project. The framework's `_measure` (test_suite_run.py:284) runs the same command shape — `pytest <test_target> --cov=<cov_target> --cov-report=term-missing -q` — so the two runs converge on the same `286 / 283 / 3` split for this snapshot. Re-running pytest against the repository root would inflate the denominator into the thousands by adding `harness/tests/*`; that is the trap the framework's scope was added to avoid (test_suite_run.py:144–170).
