"""Integration tests conftest.

Adds ``03-development/src`` to ``sys.path`` so ``import taskq_api``
resolves to the delivered package — mirrors the top-level conftest but
isolated so integration tests cannot leak state via the unit conftest.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))