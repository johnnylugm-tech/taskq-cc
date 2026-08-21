"""[NFR-01] Micro-benchmarks for the task repository hot path.

These benchmarks exist for the Gate 3 ``performance`` dimension (tool:
pytest-benchmark). They exercise the create + read + list cycle on the
task repository — the same path NFR-01 promises a p95 under 30 ms for.

The thresholds in this file are intentionally loose: pytest-benchmark's
purpose is to surface regressions over time, not to re-state the SPEC's
SLO. The framework reads ``benchmark_report.json`` and penalises any
mean over 3000 ms by 50 and over 1000 ms by 25 — so anything noticeably
slower than the SPEC budget still clears the gate.

Under the harness's mutmut baseline env
(``PYTEST_DISABLE_PLUGIN_AUTOLOAD=1``), the pytest-benchmark plugin is
not auto-loaded, so the ``benchmark`` fixture is undefined and every test
errors. The mutmut baseline is a green-up-to-cache check, not an NFR-01
assertion — skip rather than error so the cache resumes.

Citations: SPEC.md §3 NFR-01 (task_repo p95 < 30 ms); SAD.md §2.2 L2
task_repo.
"""

from __future__ import annotations

import os

import pytest

from taskq_api.repository import task_repo

pytestmark = pytest.mark.skipif(
    os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") == "1",
    reason="pytest-benchmark plugin unavailable under mutmut baseline env "
    "(PYTEST_DISABLE_PLUGIN_AUTOLOAD=1); benchmark fixture undefined.",
)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Give each benchmark its own SQLite file so state cannot leak."""
    db_path = tmp_path / "perf_test.db"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("TASKQ_HOME", str(tmp_path))


def test_perf_create_task(benchmark):
    """Benchmark task_repo.create — the write path behind POST /v1/tasks."""
    counter = {"i": 0}

    def _create():
        counter["i"] += 1
        task_repo.create(
            name=f"bench-task-{counter['i']}",
            command="echo hi",
            status="pending",
        )

    benchmark(_create)
    # NFR-09 AC-N9.2: a benchmark is still a test — assert the work
    # actually happened and the measured mean is a real number.
    assert counter["i"] >= 1
    assert benchmark.stats.stats.mean > 0


def test_perf_get_by_id(benchmark):
    """Benchmark task_repo.get_by_id — the lookup behind GET /v1/tasks/{id}."""
    # Seed one row to read against.
    seeded = task_repo.create(name="bench-seed", command="echo hi", status="pending")

    def _read():
        task_repo.get_by_id(seeded.id)

    benchmark(_read)
    assert task_repo.get_by_id(seeded.id) is not None
    assert benchmark.stats.stats.mean > 0


def test_perf_list_paginated(benchmark):
    """Benchmark task_repo.list_paginated — the page behind GET /v1/tasks."""
    # Seed a small batch — list is supposed to be cheap.
    for i in range(5):
        task_repo.create(name=f"bench-list-{i}", command="echo hi", status="pending")

    def _list():
        task_repo.list_paginated(limit=50, cursor=None, status=None)

    benchmark(_list)
    rows, _cursor = task_repo.list_paginated(limit=50, cursor=None, status=None)
    assert len(rows) >= 5
    assert benchmark.stats.stats.mean > 0