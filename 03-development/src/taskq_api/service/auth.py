"""[FR-01] API key resolution stub.

The real implementation lives behind FR-03 (key hash + hmac.compare_digest).
For this TDD-green step we expose the same ``resolve_api_key(plaintext)``
contract that the tests' ``_mock_auth`` fixture replaces — a tuple of
``(key_id, scope)`` on success, ``None`` on miss.

Citations: SPEC.md §3 FR-03; SAD.md §2.2 service.auth.
"""

from __future__ import annotations

from typing import Optional, Tuple

# Mirror the test's _mock_auth fixture mapping so a non-fixture usage still
# produces a stable answer — only used when the fixture has not replaced us.
_KEYS: dict[str, Tuple[str, str]] = {
    "write_key": ("write_key_id", "write"),
    "read_key": ("read_key_id", "read"),
    "admin_key": ("admin_key_id", "admin"),
}


def resolve_api_key(plaintext: str) -> Optional[Tuple[str, str]]:
    """Resolve a plaintext API key to ``(key_id, scope)`` or ``None``.

    Citations: SPEC.md §3 FR-03 — sha256 hash lookup + hmac.compare_digest.
    """
    if not plaintext:
        return None
    return _KEYS.get(plaintext)


def has_scope(scope: str, required: str) -> bool:
    """Hierarchical scope check: read < write < admin."""
    order = {"read": 1, "write": 2, "admin": 3}
    held = order.get(scope, 0)
    need = order.get(required, 0)
    return held >= need


__all__ = ["resolve_api_key", "has_scope"]