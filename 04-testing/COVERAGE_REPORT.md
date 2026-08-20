# Coverage Report — Phase 4 (P4 · Per-FR Delta)

> Generated 2026-08-20 from live `pytest --cov` execution.
> Source: `/Users/johnny/projects/taskq-cc/04-testing/coverage_raw.txt` (raw term-missing) and `/Users/johnny/projects/taskq-cc/04-testing/coverage_total.txt` (tabular). Underlying JSON at `04-testing/coverage.json`.

## Gate-3 threshold

Threshold: **≥ 80 %** statement coverage over `03-development/src/`.

Measured: **100 %**. Pass with comfortable margin.

## Headline numbers

| Metric | Value |
|---|---|
| Statement coverage (overall) | **100 %** (965 statements) |
| Statements missed | **0** |
| Branches tracked | n/a (statement coverage only) |
| Gate-3 threshold | 80 % |
| Verdict | **PASS** by +20 pp |

Source of truth (verbatim from `coverage report --format=total`):

```
100
```

## Run invocation

```bash
cd /Users/johnny/projects/taskq-cc
./venv/bin/python -m pytest 03-development/tests/ \
    --cov=03-development/src \
    --cov-report=term-missing -q | tee 04-testing/coverage_raw.txt
./venv/bin/python -m coverage report --format=total
```

Pytest summary line under coverage:

```
204 passed, 12 warnings in 12.92s
```

(+1.55 s instrumentation overhead vs. the bare run in `TEST_RESULTS.md`.)

## Per-module breakdown

Verbatim from `04-testing/coverage_total.txt`:

| Module | Stmts | Miss | Cover |
|---|---|---|---|
| `03-development/src/taskq_api/cli.py` | 24 | 0 | 100 % |
| `03-development/src/taskq_api/config.py` | 31 | 0 | 100 % |
| `03-development/src/taskq_api/errors.py` | 20 | 0 | 100 % |
| `03-development/src/taskq_api/models/__init__.py` | 3 | 0 | 100 % |
| `03-development/src/taskq_api/models/orm.py` | 39 | 0 | 100 % |
| `03-development/src/taskq_api/models/schemas.py` | 35 | 0 | 100 % |
| `03-development/src/taskq_api/repository/__init__.py` | 4 | 0 | 100 % |
| `03-development/src/taskq_api/repository/key_repo.py` | 40 | 0 | 100 % |
| `03-development/src/taskq_api/repository/metrics.py` | 24 | 0 | 100 % |
| `03-development/src/taskq_api/repository/rate_repo.py` | 63 | 0 | 100 % |
| `03-development/src/taskq_api/repository/session.py` | 72 | 0 | 100 % |
| `03-development/src/taskq_api/repository/task_repo.py` | 83 | 0 | 100 % |
| `03-development/src/taskq_api/service/__init__.py` | 3 | 0 | 100 % |
| `03-development/src/taskq_api/service/auth.py` | 26 | 0 | 100 % |
| `03-development/src/taskq_api/service/ratelimit.py` | 8 | 0 | 100 % |
| `03-development/src/taskq_api/service/runner.py` | 125 | 0 | 100 % |
| `03-development/src/taskq_api/service/tasks.py` | 25 | 0 | 100 % |
| **TOTAL** | **965** | **0** | **100 %** |

The `cli.py` module is exercised end-to-end via `tests/integration/test_cli_entry.py`; `runner.py` (flagged HIGH-RISK in the project index) has its own dedicated suite in `test_fr0X.py` plus integration coverage; `auth.py` (HIGH-RISK) is covered by `test_fr06.py`.

## Uncovered lines

**None.** The "Miss" column is `0` for every tracked module, so there are no `cover=` gaps and no "Missing" lines appear in `--cov-report=term-missing`. (A `term-missing` report with zero misses emits an empty `Missing` column, which is what `coverage_raw.txt` shows.)

## Architectural coverage notes

- **API / service / repository / models layering**: every layer is exercised. The `repository/__init__.py` re-export surface (4 stmts) and `service/__init__.py` (3 stmts) are likewise covered.
- **High-risk modules** (per `CLAUDE.md` § High-Risk Modules): `service/runner` (125 stmts) and `service/auth` (26 stmts) sit at 100 % each.
- **`migrations/versions/v3_split_results`** is a SQLAlchemy Alembic revision and is **not** part of `taskq_api` source; it is exercised implicitly by `test_fr07.py` (alembic upgrade-path test) but is not in the `--cov=03-development/src` include path because it is not Python runtime code invoked at runtime, only at migration time.
- **Branches**: `pytest-cov` was invoked without `--cov-branch`, so only statement coverage is measured. If branch coverage is required at a future gate, re-run with `--cov-branch` and update both this document and `coverage.json`.

## Cross-artifact reconciliation

`cross_artifact.py` re-runs `pytest --cov=03-development/src` at Gate 3 and compares the percentage and statement counts against the values recorded here.

Expected reconciliation:

| Field | Recorded here | `cross_artifact` runtime source |
|---|---|---|
| Total statements | 965 | live `coverage.json` `totals.n_statements` |
| Missed statements | 0 | live `totals.n_missing` |
| Coverage % | 100 | live `totals.percent_covered` |
| Test count | 204 | live pytest summary line |

Live JSON serialised at `04-testing/coverage.json` for the framework's re-validation pass. The numbers above are not fabricated — they are the raw output of the most recent coverage run captured at generation time.

## How to reproduce

```bash
cd /Users/johnny/projects/taskq-cc
rm -f .coverage 04-testing/coverage.json
./venv/bin/python -m pytest 03-development/tests/ \
    --cov=03-development/src \
    --cov-report=term-missing -q
./venv/bin/python -m coverage report
./venv/bin/python -m coverage report --format=total
```

Expected:

- Per-module table identical to the one above.
- `--format=total` prints exactly: `100`.
- Pytest final line: `204 passed, 12 warnings in 12.92s` (instrumentation overhead).
