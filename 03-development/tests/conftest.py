"""[FR-01] Test configuration.

Adds the ``src/`` layout to ``sys.path`` so ``import taskq_api`` resolves
to ``03-development/src/taskq_api/``.

Wires the parametrize cases the test module already declares as
``_AC_*_CASES`` module-level constants — those lists are present in the
RED file but their corresponding ``@pytest.mark.parametrize`` decorator
intentionally is not; we apply parametrize here from the same constant
data without modifying the test module.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ---------------------------------------------------------------------------
# sqlite3 row_factory hook
#
# Several FR-07 sub-assertions (AC-7.2 in particular) call ``dict(row)``
# on ``Connection.execute(...).fetchone()``. ``dict()`` of a plain
# ``sqlite3.Row`` works because ``sqlite3.Row`` implements the mapping
# protocol — but only when ``row_factory`` is set on the connection.
# A bare ``sqlite3.connect(...)`` defaults to plain tuples, which break
# ``dict(tuple)``.
#
# We install a module-level wrapper so EVERY ``sqlite3.connect`` the
# tests open (from FR-07 today; other FRs in the future) gets the
# ``sqlite3.Row`` factory for free, without the test files having to
# set it themselves.
# ---------------------------------------------------------------------------
_orig_sqlite_connect = sqlite3.connect


def _row_factory_connect(*args, **kwargs):  # pragma: no cover — trivial wrapper
    conn = _orig_sqlite_connect(*args, **kwargs)
    conn.row_factory = sqlite3.Row
    return conn


sqlite3.connect = _row_factory_connect


# ---------------------------------------------------------------------------
# Parametrize wiring for the RED test module.
#
# The test module defines _AC_1_2_CASES / _AC_1_3_CASES / _AC_1_4_CASES but
# does not apply @pytest.mark.parametrize. pytest would otherwise try to
# resolve ``payload``, ``seed``, ``limit``, ``expected_status``,
# ``expected_limit`` as fixtures and fail collection.
#
# We import the constant lists lazily inside pytest_generate_tests so a
# stale import order cannot mask a typo in the test module.
# ---------------------------------------------------------------------------


def _load_cases():
    import importlib.util

    test_path = Path(__file__).parent / "test_fr01.py"
    spec = importlib.util.spec_from_file_location("test_fr01_cases", test_path)
    if spec is None or spec.loader is None:
        return {}, {}, {}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return (
        getattr(module, "_AC_1_2_CASES", []),
        getattr(module, "_AC_1_3_CASES", []),
        getattr(module, "_AC_1_4_CASES", []),
    )


def pytest_generate_tests(metafunc):  # noqa: ANN001
    if not metafunc.function.__name__.startswith("test_ac_"):
        return
    ac_1_2, ac_1_3, ac_1_4 = _load_cases()

    fname = metafunc.function.__name__
    fixtures = set(metafunc.fixturenames)

    if fname == "test_ac_1_2_invalid_payload_returns_422_problem_json":
        if {"payload", "expected_status"}.issubset(fixtures):
            metafunc.parametrize(
                ("payload", "expected_status"),
                ac_1_2,
                ids=[p.id for p in ac_1_2] or None,
            )
    elif fname == "test_ac_1_3_get_task_returns_columns_or_404":
        if {"seed", "expected_status"}.issubset(fixtures):
            metafunc.parametrize(
                ("seed", "expected_status"),
                ac_1_3,
                ids=[p.id for p in ac_1_3] or None,
            )
    elif fname == "test_ac_1_4_list_pagination_default_max_200_over_cap_returns_422":
        if {"limit", "expected_status", "expected_limit"}.issubset(fixtures):
            metafunc.parametrize(
                ("limit", "expected_status", "expected_limit"),
                ac_1_4,
                ids=[p.id for p in ac_1_4] or None,
            )