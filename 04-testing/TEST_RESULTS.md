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
1 failed, 275 passed, 12 warnings in 99.00s (0:01:39)
```

Source: `04-testing/coverage_raw.txt`, last three lines (the line pytest itself printed at exit).

## Outcomes

| Metric | Count |
|--------|-------|
| Tests collected | 276 |
| Passed | 275 |
| Failed | 1 |
| Skipped (per pytest summary) | 0 |
| Errors | 0 |
| Wall time | 99.00 s (0:01:39) |

Pytest only prints a `skipped` segment in the summary when the count is non-zero (see `core.quality_gate.test_suite_run._parse_skipped`), so the absent segment on this run is `0`, not an unknown.

## Failures

### `test_ac_n3_6_failed_migration_rolls_back_readyz_503`

- File: `03-development/tests/test_nfr_spec_coverage.py:376`
- Marker: NFR-03 / AC-N3.6
- Status: **failed**

```
>           assert r.status_code == 503
E           assert 200 == 503
E            +  where 200 = <Response [200 OK]>.status_code

03-development/tests/test_nfr_spec_coverage.py:385: AssertionError
```

The test plants `$TASKQ_HOME/.migration_failure.json` (using `_MIGRATION_FAILURE_MARKER` from `taskq_api.api.health`) and then asserts that `GET /readyz` returns 503 with `detail == "migration"`. The test is correct per the AC description in its docstring (lines 377–384), but `/readyz` returned `200` in this run — `taskq_api.api.health.py` does read the marker (line 177), so the failure is in the path between marker-detection and the 503 response. Deferred to the next phase round; reproduction is one `pytest 03-development/tests/test_nfr_spec_coverage.py::test_ac_n3_6_failed_migration_rolls_back_readyz_503 -q`.

## Deferred issues (none blocking Phase 4 exit)

- AC-N3.6 readiness check under forced migration failure (above) — the only red result on the run.

No infrastructure failures. No collection errors. No skip-driven regressions: every test that pytest saw was either passed or the single NFR-03 AC failure.

## Reconciliation note

`core.quality_gate.cross_artifact.check_test_count_reconciliation` compares this document's counts against the framework's own `run_suite` measurement for the same project. The framework's `_measure` (test_suite_run.py:284) runs the same command shape — `pytest <test_target> --cov=<cov_target> --cov-report=term-missing -q` — so the two runs converge on the same `276 / 275 / 1` split for this snapshot. Re-running pytest against the repository root would inflate the denominator into the thousands by adding `harness/tests/*`; that is the trap the framework's scope was added to avoid (test_suite_run.py:144–170).
