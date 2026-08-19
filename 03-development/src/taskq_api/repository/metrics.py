"""[FR-09] Metrics aggregation queries.

The two queries the ``/v1/metrics`` handler needs are deliberately
isolated here so :mod:`taskq_api.api.health` never imports
``sqlalchemy`` — NFR-06 forbids SQLAlchemy outside the repository
layer.

Citations: SPEC.md §3 FR-09 (task counts + latency percentiles);
SAD.md §2.2 L2 repository.metrics.
"""

from __future__ import annotations

from typing import Mapping

from sqlalchemy import func, select

from taskq_api.models.orm import Task, TaskResult
from taskq_api.repository.session import session_scope


def task_counts_by_status() -> Mapping[str, int]:
    """Return ``{status: row_count}`` for every status with at least one task."""
    with session_scope() as session:
        stmt = select(Task.status, func.count(Task.id)).group_by(Task.status)
        return {row[0]: int(row[1]) for row in session.execute(stmt).all()}


def _percentile(sorted_values: list[int], pct: float) -> float:
    """Compute a percentile on a pre-sorted ascending list.

    Nearest-rank — the result is always one of the observed values.
    Empty input yields ``0.0`` (a stable default rather than a NaN that
    breaks JSON serialisation).
    """
    if not sorted_values:
        return 0.0
    if pct <= 0:
        return float(sorted_values[0])
    if pct >= 100:
        return float(sorted_values[-1])
    rank = max(0, int(round(pct / 100.0 * (len(sorted_values) - 1))))
    return float(sorted_values[rank])


def latency_percentiles() -> tuple[float, float, float]:
    """Return ``(p50, p95, p99)`` of completed-task durations in ms.

    Reads only ``task_results.duration_ms`` rows that have a recorded
    duration (i.e. the run reached a terminal state). Sorted in SQL so
    we never have to sort the full set in Python.
    """
    with session_scope() as session:
        stmt = (
            select(TaskResult.duration_ms)
            .where(TaskResult.duration_ms.is_not(None))
            .order_by(TaskResult.duration_ms.asc())
        )
        values = [
            int(v)
            for v in session.execute(stmt).scalars().all()
            if v is not None
        ]
    return (
        _percentile(values, 50.0),
        _percentile(values, 95.0),
        _percentile(values, 99.0),
    )


__all__ = ["task_counts_by_status", "latency_percentiles"]