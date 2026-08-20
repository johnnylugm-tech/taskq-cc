# Test Results — Phase 4 (P4 · Per-FR Delta)

> Generated 2026-08-20 from live pytest execution scoped to project tests.

## Scope of the run

| Aspect | Value |
|---|---|
| Test directory | `03-development/tests/` |
| Repository root | `/Users/johnny/projects/taskq-cc` |
| Test runner | `pytest 8.4.2` via `.venv/bin/python -m pytest` |
| Coverage invocation | `pytest 03-development/tests/ --cov=03-development/src --cov-report=term-missing -q` |
| Scope intent | Only the project's own test tree (10 FR suites + `tests/integration/` + `test_nfr_spec_coverage.py` + `test_bug_hunt_regressions.py` + `test_perf_benchmarks.py`). NO harness vendored tests were collected. |

Running from the repository root without scoping would also pick up the framework's vendored copy inside `harness/` and inflate collected test counts into the thousands (measured at `4 failed, 7563 passed, 3 skipped` in a 349-test project tree last cycle). That figure is unrelated to this project's deliverable, so the run is explicitly scoped to `03-development/tests/`.

## Verbatim pytest summary line

Bare run (no `--cov`):

```
22 failed, 236 passed, 1 skipped, 12 warnings in 408.47s (0:06:48)
```

With coverage instrumentation enabled (the run `--cov` records come from):

```
22 failed, 236 passed, 1 skipped, 12 warnings in 365.08s (0:06:05)
```

The two runs diverge in wall time because the per-test failure collection truncates the FAILED panel earlier (the second invocation crashes on `test_ac_n12_2_make_verify_system_…`'s 300-second `subprocess.run` timeout and the bare run reports it as a normal failure). Case counts are identical across both runs.

## Pass / fail / skipped tally

| Outcome | Count |
|---|---|
| Passed | **236** |
| Failed | **22** |
| Errored | **0** |
| Skipped | **1** |
| XFailed (expected failure, not raised) | 0 |
| XPassed (unexpected pass of xfail) | 0 |
| Warnings emitted | 12 (deprecation noise from third-party libs) |

**All 22 failures are concentrated in one file** — `03-development/tests/test_nfr_spec_coverage.py` (the NFR acceptance-check suite). The 10 FR-acceptance suites, the integration suite, the bug-hunt regression suite, and the perf benchmarks all pass.

## Failed tests (verbatim, from short test summary)

```
FAILED 03-development/tests/test_nfr_spec_coverage.py::test_ac_n1_3_list_sql_count_constant_at_10_100_1000_rows
FAILED 03-development/tests/test_nfr_spec_coverage.py::test_ac_n2_1_grep_shell_true_eval_exec_zero_hits
FAILED 03-development/tests/test_nfr_spec_coverage.py::test_ac_n2_3_api_keys_sha256_hmac_constant_time_no_plaintext
FAILED 03-development/tests/test_nfr_spec_coverage.py::test_ac_n2_4_403_bodies_indistinguishable_for_existing_and_nonexistent_ids
FAILED 03-development/tests/test_nfr_spec_coverage.py::test_ac_n2_5_500_error_body_no_stack_sql_or_paths
FAILED 03-development/tests/test_nfr_spec_coverage.py::test_ac_n3_1_transaction_context_manager_rollback_or_single_commit
FAILED 03-development/tests/test_nfr_spec_coverage.py::test_ac_n3_5_timeout_kills_child_awaits_exit_no_orphan_pid
FAILED 03-development/tests/test_nfr_spec_coverage.py::test_ac_n3_6_failed_migration_rolls_back_readyz_503
FAILED 03-development/tests/test_nfr_spec_coverage.py::test_ac_n4_1_redaction_helper_replaces_sk_token_bearer_postgres
FAILED 03-development/tests/test_nfr_spec_coverage.py::test_ac_n5_1_public_functions_classes_have_fr_or_nfr_tagged_docstrings
FAILED 03-development/tests/test_nfr_spec_coverage.py::test_ac_n6_2_importlinter_forbidden_sqlalchemy_outside_repository
FAILED 03-development/tests/test_nfr_spec_coverage.py::test_ac_n6_3_lint_imports_ci_exits_zero
FAILED 03-development/tests/test_nfr_spec_coverage.py::test_ac_n6_4_no_degradation_no_ignore_imports_or_downgrade
FAILED 03-development/tests/test_nfr_spec_coverage.py::test_ac_n7_1_requirements_pinned_with_equals_and_lock_present
FAILED 03-development/tests/test_nfr_spec_coverage.py::test_ac_n8_2_mutmut_score_at_least_70_over_service_and_repository
FAILED 03-development/tests/test_nfr_spec_coverage.py::test_ac_n8_3_mutation_scope_annotated_service_repository_with_rationale
FAILED 03-development/tests/test_nfr_spec_coverage.py::test_ac_n9_1_pytest_reports_skipped_count_zero
FAILED 03-development/tests/test_nfr_spec_coverage.py::test_ac_n9_4_fr07_migration_real_sqlite_file_not_in_memory_mock
FAILED 03-development/tests/test_nfr_spec_coverage.py::test_ac_n10_2_integration_tests_use_asgi_transport_no_direct_handler_calls
FAILED 03-development/tests/test_nfr_spec_coverage.py::test_ac_n10_3_integration_suite_covers_each_error_code_and_flows
FAILED 03-development/tests/test_nfr_spec_coverage.py::test_ac_n11_2_no_file_over_400_lines_no_dir_over_15_files
FAILED 03-development/tests/test_nfr_spec_coverage.py::test_ac_n12_2_make_verify_system_exits_zero_stdout_contains_pass
```

## Deferred issues (failing NFR acceptance checks)

| NFR | Test | Failure mode |
|---|---|---|
| NFR-01 | `test_ac_n1_3_list_sql_count_constant…` | `list_paginated` emits 3 SQL statements per call instead of 1 (counts=[3,3,3]); N+1 leakage at scale. |
| NFR-02 | `test_ac_n2_1_grep_shell_true_eval_exec_zero_hits` | `service/runner.py` contains the literal token `exec(` (the policy bans `shell=True`/`eval(`/`exec(` everywhere in source). |
| NFR-02 | `test_ac_n2_3_api_keys_sha256_hmac_constant_time_no_plaintext` | `key_repo.create` returns a digest that does not match `sha256(plaintext)`. Implementation is using HMAC with a secret prefix instead of plain SHA-256 over the key. |
| NFR-02 | `test_ac_n2_4_403_bodies_indistinguishable_for_existing_and_nonexistent_ids` | Deleting a non-existent task id returns 404 instead of 403 — 404 leaks ID existence. |
| NFR-02 | `test_ac_n2_5_500_error_body_no_stack_sql_or_paths` | 500 envelope includes the literal error message, which the test seeded with `INTERNAL_SECRET_PATH=/etc/passwd SELECT * FROM users`. The body must be sanitised. |
| NFR-03 | `test_ac_n3_1_transaction_context_manager_rollback_or_single_commit` | `session_scope` rolls back on exception but the next `task_repo.get_by_id(before.id)` raises `DetachedInstanceError` — the row re-fetch is touched via an expired instance attribute on a session that was already closed. |
| NFR-03 | `test_ac_n3_5_timeout_kills_child_awaits_exit_no_orphan_pid` | Test imports `psutil`, which is not in the project's `requirements.txt` (transitive unmets). |
| NFR-03 | `test_ac_n3_6_failed_migration_rolls_back_readyz_503` | Setting `TASKQ_MIGRATION_AUTO_FAIL=1` does not cause `/readyz` to return 503 — the failure path is not wired into the readiness probe. |
| NFR-04 | `test_ac_n4_1_redaction_helper_replaces_sk_token_bearer_postgres` | Redaction helper does not strip `sk-…` / `Bearer …` / `postgres://…` substrings before they reach logs. |
| NFR-05 | `test_ac_n5_1_public_functions_classes_have_fr_or_nfr_tagged_docstrings` | Some public functions/classes lack an `[FR-NN]` / `[NFR-NN]` docstring tag. |
| NFR-06 | `test_ac_n6_2_importlinter_forbidden_sqlalchemy_outside_repository` | Import-linter enforces `sqlalchemy` only in `repository/`; current layer violates it somewhere. |
| NFR-06 | `test_ac_n6_3_lint_imports_ci_exits_zero` | `ruff check` / `lint-imports` step exits non-zero. |
| NFR-06 | `test_ac_n6_4_no_degradation_no_ignore_imports_or_downgrade` | `# noqa` / `# type: ignore` markers discovered in source. |
| NFR-07 | `test_ac_n7_1_requirements_pinned_with_equals_and_lock_present` | `requirements.txt` does not use `==` pins for every line or no `requirements.lock` is present. |
| NFR-08 | `test_ac_n8_2_mutmut_score_at_least_70_over_service_and_repository` | Mutmut score below the 70% threshold over `service/` + `repository/`. |
| NFR-08 | `test_ac_n8_3_mutation_scope_annotated_service_repository_with_rationale` | Mutation scope annotations are missing rationale. |
| NFR-09 | `test_ac_n9_1_pytest_reports_skipped_count_zero` | `pytest` reports `1 skipped` (the deterministic skip in this run). |
| NFR-09 | `test_ac_n9_4_fr07_migration_real_sqlite_file_not_in_memory_mock` | `test_fr07.py` uses an in-memory SQLite mock where the spec requires a real on-disk file. |
| NFR-10 | `test_ac_n10_2_integration_tests_use_asgi_transport_no_direct_handler_calls` | Some integration tests reach into the FastAPI handler directly instead of going through the ASGI transport. |
| NFR-10 | `test_ac_n10_3_integration_suite_covers_each_error_code_and_flows` | Integration suite does not cover every error code path. |
| NFR-11 | `test_ac_n11_2_no_file_over_400_lines_no_dir_over_15_files` | `service/runner.py` is over 400 lines (the spec caps source files at 400). |
| NFR-12 | `test_ac_n12_2_make_verify_system_exits_zero_stdout_contains_pass` | `make verify-system` did not complete within the 300-second timeout — the verify target is too slow / hangs. |

Total: **22 deferred NFR acceptance checks**. All 10 FR suites, the integration suite, the bug-hunt regression suite, and the perf benchmarks pass.

### Warnings classified (informational, non-blocking)

- `StarletteDeprecationWarning` (`fastapi/testclient.py:1`): "Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead." — third-party; deferred to dependency upgrade, not in scope for P4.
- `DeprecationWarning` from `alembic/config.py:604`: 11 occurrences in `test_fr07.py`. "No `path_separator` found in configuration; falling back to legacy splitting…" — alembic env-config-level concern; P5 hardening candidate, not a test defect.

Neither warning originates from project source. Counts are stable and do not represent test failures.

## Reconciliation against `run_suite`

`cross_artifact.check_test_count_reconciliation` compares the test count recorded here against the harness's own measurement of `run_suite` output. Both sides must match.

This document records `236 passed + 22 failed + 1 skipped = 259 collected cases`. The harness's own `run_suite` invocation must produce the same total. The skipped+1 case is the deterministic `pytest.skip` introduced in the latest test set; the 22 failures are all in `test_nfr_spec_coverage.py` and not in any other FR/integration suite.

No mismatch expected. If a CRITICAL is raised by `cross_artifact.py`, the responsible divergence is the scope (tests collected from the repo root instead of `03-development/tests/`); that path was deliberately avoided here.

## How to reproduce

```bash
cd /Users/johnny/projects/taskq-cc
./venv/bin/python -m pytest 03-development/tests/ -q
```

Expected final line: `22 failed, 236 passed, 1 skipped, 12 warnings in 408.47s (0:06:48)`.
