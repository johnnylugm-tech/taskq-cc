"""[FR-01] API layer re-exports.

Citations: SPEC.md §3 FR-01; SAD.md §2.2 L4 api.
"""

from taskq_api.api import deps as deps_module  # noqa: F401
from taskq_api.api import tasks as tasks_module  # noqa: F401

__all__ = ["deps_module", "tasks_module"]