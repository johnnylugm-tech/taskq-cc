"""[FR-03] ``python -m taskq_api`` entry point.

Delegates to :mod:`taskq_api.cli.main`.

Citations: SPEC.md §3 FR-03 "python -m taskq_api key create --scope <scope>".
"""

# pragma: no error-handling  (pure delegation to cli.main — no I/O at this layer)

from __future__ import annotations

import sys

from taskq_api.cli import main


if __name__ == "__main__":
    sys.exit(main())