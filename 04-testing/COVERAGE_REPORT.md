# Coverage Report — Phase 4

> Phase: **4 — Testing** | Run date: 2026-08-21 | Python: `/Users/johnny/projects/taskq-cc/.venv/bin/python`

## Run

| Item | Value |
|------|-------|
| Test target | `03-development/tests` |
| Coverage target | `03-development/src` |
| Command | `.venv/bin/python -m pytest 03-development/tests --cov=03-development/src --cov-report=term-missing -q` |
| Raw output | `04-testing/coverage_raw.txt` |

Targets resolved via `core.quality_gate.test_suite_run.resolve_targets` (`ProjectLayout.active_test_dir` / `active_src_dir`). No `.coveragerc` is present in the repository root, so `cov_target` falls through to `active_src_dir` rather than to coverage's `"."` default — `_read_coveragerc_source` returns `None` for this project, and `test_suite_run.resolve_targets` line 168 only overrides when the declared value differs from `"."`.

## Overall coverage

```
TOTAL                                                         1005      5    99%
```

Read from `coverage report --format=total` (`04-testing/coverage_total.txt`). The exact `totals.percent_covered` reported by coverage's JSON backend is `99.50248756218906`; the table above reports the same number the framework's `_read_coverage` parses (`test_suite_run.py:422–433`).

**Gate 3 threshold: ≥ 80%. Result: PASS (99%).**

## Per-module breakdown

| Module | Stmts | Miss | Cover | Missing lines |
|---|---:|---:|---:|---|
| 03-development/src/migrations/__init__.py | 0 | 0 | 100% | — |
| 03-development/src/migrations/env.py | 51 | 0 | 100% | — |
| 03-development/src/migrations/versions/__init__.py | 0 | 0 | 100% | — |
| 03-development/src/migrations/versions/v1_initial.py | 13 | 0 | 100% | — |
| 03-development/src/migrations/versions/v2_tags.py | 15 | 0 | 100% | — |
| 03-development/src/migrations/versions/v3_split_results.py | 17 | 0 | 100% | — |
| 03-development/src/taskq_api/__init__.py | 0 | 0 | 100% | — |
| 03-development/src/taskq_api/__main__.py | 5 | 0 | 100% | — |
| 03-development/src/taskq_api/api/__init__.py | 4 | 0 | 100% | — |
| 03-development/src/taskq_api/api/deps.py | 33 | 0 | 100% | — |
| 03-development/src/taskq_api/api/health.py | 67 | 0 | 100% | — |
| 03-development/src/taskq_api/api/tasks.py | 62 | 2 | 97% | 149-153 |
| 03-development/src/taskq_api/app.py | 80 | 0 | 100% | — |
| 03-development/src/taskq_api/cli.py | 28 | 3 | 89% | 45-49 |
| 03-development/src/taskq_api/config.py | 37 | 0 | 100% | — |
| 03-development/src/taskq_api/errors.py | 20 | 0 | 100% | — |
| 03-development/src/taskq_api/models/__init__.py | 3 | 0 | 100% | — |
| 03-development/src/taskq_api/models/orm.py | 39 | 0 | 100% | — |
| 03-development/src/taskq_api/models/schemas.py | 35 | 0 | 100% | — |
| 03-development/src/taskq_api/repository/__init__.py | 4 | 0 | 100% | — |
| 03-development/src/taskq_api/repository/key_repo.py | 43 | 0 | 100% | — |
| 03-development/src/taskq_api/repository/metrics.py | 30 | 0 | 100% | — |
| 03-development/src/taskq_api/repository/rate_repo.py | 63 | 0 | 100% | — |
| 03-development/src/taskq_api/repository/session.py | 72 | 0 | 100% | — |
| 03-development/src/taskq_api/repository/task_repo.py | 83 | 0 | 100% | — |
| 03-development/src/taskq_api/service/__init__.py | 3 | 0 | 100% | — |
| 03-development/src/taskq_api/service/auth.py | 29 | 0 | 100% | — |
| 03-development/src/taskq_api/service/ratelimit.py | 8 | 0 | 100% | — |
| 03-development/src/taskq_api/service/run_state.py | 45 | 0 | 100% | — |
| 03-development/src/taskq_api/service/runner.py | 91 | 0 | 100% | — |
| 03-development/src/taskq_api/service/tasks.py | 25 | 0 | 100% | — |
| **TOTAL** | **1005** | **5** | **99%** | — |

## Uncovered lines

Five lines total, all in two modules:

- **`03-development/src/taskq_api/api/tasks.py:149–153`** (2 statements uncovered, 97% on a 62-stmt module).
- **`03-development/src/taskq_api/cli.py:45–49`** (3 statements uncovered, 89% on a 28-stmt module).

Every other module — including the architecture-constraint boundary modules `repository/session.py`, `service/runner.py`, `service/auth.py`, and the migration split module `migrations/versions/v3_split_results.py` — sits at 100%.

## Reconciliation note

`core.quality_gate.cross_artifact.check_coverage_reconciliation` re-measures the same scope at Gate 3 and compares its result against this document. Re-running `pytest 03-development/tests --cov=03-development/src` against an unchanged tree produces the same `99% / 5 missed` table because the run command, the targets, and the source/test trees all feed into the fingerprint `test_suite_run._fingerprint` builds before memoising the result. A change in any of those would change the fingerprint and trigger a re-measure; this document records the snapshot the framework will see on first run after it is written.
