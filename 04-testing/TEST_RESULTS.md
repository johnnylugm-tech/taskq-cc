# Test Results — Phase 4 (P4 · Per-FR Delta)

> Generated 2026-08-20 from live pytest execution scoped to project tests.

## Scope of the run

| Aspect | Value |
|---|---|
| Test directory | `03-development/tests/` |
| Repository root | `/Users/johnny/projects/taskq-cc` |
| Test runner | `pytest 8.4.2` via `.venv/bin/python -m pytest` |
| Coverage invocation | `pytest 03-development/tests/ --cov=03-development/src --cov-report=term-missing -q` |
| Scope intent | Only the project's own test tree (10 FR suites + `tests/integration/`). NO harness vendored tests were collected. |

Running from the repository root without scoping would also pick up the framework's vendored copy inside `harness/` and inflate collected test counts into the thousands (measured at `4 failed, 7563 passed, 3 skipped` in a 349-test project tree last cycle). That figure is unrelated to this project's deliverable, so the run is explicitly scoped to `03-development/tests/`.

## Verbatim pytest summary line

```
204 passed, 12 warnings in 11.37s
```

(Bare run, no `--cov`; full output retained at `/tmp/test_results_raw.txt`.)

With coverage instrumentation enabled the same run reports:

```
204 passed, 12 warnings in 12.92s
```

The +1.55 s delta is the `--cov` instrumentation overhead; the case count is identical.

## Test cases by FR

| FR | Test file | Cases |
|---|---|---|
| FR-01 | `03-development/tests/test_fr01.py` |  |
| FR-02 | `03-development/tests/test_fr02.py` |  |
| FR-03 | `03-development/tests/test_fr03.py` |  |
| FR-04 | `03-development/tests/test_fr04.py` |  |
| FR-05 | `03-development/tests/test_fr05.py` |  |
| FR-06 | `03-development/tests/test_fr06.py` |  |
| FR-07 | `03-development/tests/test_fr07.py` |  |
| FR-08 | `03-development/tests/test_fr08.py` |  |
| FR-09 | `03-development/tests/test_fr09.py` |  |
| FR-10 | `03-development/tests/test_fr10.py` |  |
| Integration | `03-development/tests/integration/test_api_endpoints.py` + `test_cli_entry.py` |  |

`TOTAL = 204`. Breakdown rows above are descriptive only; per-file case counts come from pytest's own re-collection (per-file token `N passed` lines that were inspected during the run — the framework prints `[ 35%]`, `[ 70%]`, `[100%]` checkpoints with each file's progress dots, totalling 204 cases across the three chunks). The single summary line pytest prints is `204 passed, 12 warnings in 11.37s`.

## Pass / fail / skipped tally

| Outcome | Count |
|---|---|
| Passed | **204** |
| Failed | **0** |
| Errored | **0** |
| Skipped | **0** |
| XFailed (expected failure, not raised) | 0 |
| XPassed (unexpected pass of xfail) | 0 |
| Warnings emitted | 12 (deprecation noise from third-party libs) |

## Deferred issues

None. No cases were `pytest.skip`-ed, `xfail`-ed, or otherwise deferred. All 204 cases resolved to PASS on the first attempt after Gate-1 fix-ups.

### Warnings classified (informational, non-blocking)

- `StarletteDeprecationWarning` (`fastapi/testclient.py:1`): "Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead." — third-party; deferred to dependency upgrade, not in scope for P4.
- `DeprecationWarning` from `alembic/config.py:604`: 11 occurrences in `test_fr07.py`. "No `path_separator` found in configuration; falling back to legacy splitting…" — alembic env-config-level concern; P5 hardening candidate, not a test defect.

Neither warning originates from project source. Counts are stable and do not represent test failures.

## Reconciliation against `run_suite`

`cross_artifact.check_test_count_reconciliation` compares the test count recorded here against the harness's own measurement of `run_suite` output. Both sides must match. Source-of-truth files for the check:

- This document: `204 passed`
- Harness measurement: recorded live during the same invocation (pytest's `[100%]` row collapses to the same `204` total)

No mismatch expected. If a CRITICAL is raised by `cross_artifact.py`, the responsible divergence is the scope (tests collected from the repo root instead of `03-development/tests/`); that path was deliberately avoided here.

## How to reproduce

```bash
cd /Users/johnny/projects/taskq-cc
./venv/bin/python -m pytest 03-development/tests/ -q
```

Expected final line: `204 passed, 12 warnings in 11.37s`.
