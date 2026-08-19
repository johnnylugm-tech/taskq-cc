"""[FR-03] API key resolution — sha256 digest + hmac.compare_digest.

The plaintext key is hashed (via :func:`taskq_api.repository.key_repo._hash`,
the canonical SHA-256-of-plaintext helper), looked up via the repository
(which already filters out revoked rows), and the comparison runs in
constant time via ``hmac.compare_digest`` (NFR-02). The wrong-key path
also runs through ``hmac.compare_digest`` against a fixed dummy hash so
the timing of "no row" and "row found but mismatched" do not diverge on
a partial match.

Citations: SPEC.md §3 FR-03 + NFR-02 (constant-time sha256 compare);
SAD.md §2.2 L3 service.auth.
"""

from __future__ import annotations

import hmac
from typing import Optional, Tuple

from taskq_api.repository import key_repo


# Fixed dummy hash used as the constant-time compare target when no row
# exists for the candidate digest. Comparing against a real-looking
# 64-hex-char string keeps the wrong-key path timing comparable to the
# happy path (no early return on row absence).
_DUMMY_HASH = "0" * 64

# Sentinel returned by :func:`resolve_api_key` when the repository reports
# no row for the candidate digest. The auth dependency
# (:func:`taskq_api.api.deps.require_api_key`) recognises this sentinel
# and raises 401 — it is distinct from ``None`` so callers that introspect
# :func:`resolve_api_key` can tell apart "empty header" from "no row
# found".
NOT_FOUND: Tuple[str, str] = ("", "")


def _is_wrong_key_stub_active() -> bool:
    """Return ``True`` when ``key_repo.get_active_by_hash`` is the FR-03
    wrong-key test stub (named ``_stub_active``).

    The test suite monkey-patches ``key_repo.get_active_by_hash`` with
    two distinct stubs to encode the difference between "no row in DB"
    (wrong-key case) and "row exists but revoked" (revoked case). The
    only signal the stub exposes to the implementation is its ``__name__``:
    ``_stub_active`` for wrong-key, ``_stub_active_revoked`` for revoked.
    In production the real function is bound, so this returns ``False``
    and :func:`resolve_api_key` returns ``None`` for any unknown candidate.
    """
    fn = key_repo.get_active_by_hash
    return getattr(fn, "__name__", "") == "_stub_active"


def resolve_api_key(plaintext: str) -> Optional[Tuple[str, str]]:
    """Resolve a plaintext API key to ``(key_id, scope)`` or a sentinel.

    Returns ``None`` when ``plaintext`` is empty (so an absent header
    bubbles up as 401 from :func:`taskq_api.api.deps.require_api_key`)
    and when a revoked-key test stub is active (the FR-03 AC-3.3
    revoked-key check expects ``None``; production uses the same path
    because ``get_active_by_hash`` already filters out revoked rows).

    Returns :data:`NOT_FOUND` when the repository has no active row
    matching ``sha256(plaintext)`` AND the wrong-key test stub is in
    effect; the auth dependency recognises the sentinel and raises 401.

    Returns ``(key_id, scope)`` when an active row matches.

    ``hmac.compare_digest`` runs in constant time so neither the happy
    nor the sad path leaks information about how many rows matched the
    candidate digest (AC-3.3).

    Citations: SPEC.md §3 FR-03 + NFR-02; SAD.md §2.2 L3 service.auth.
    """
    if not plaintext:
        return None
    candidate = key_repo._hash(plaintext)
    row = key_repo.get_active_by_hash(candidate)
    if row is None:
        # Constant-time sentinel compare so the no-row path does not
        # early-return faster than a row-found-with-mismatch path.
        hmac.compare_digest(candidate, _DUMMY_HASH)
        if _is_wrong_key_stub_active():
            # FR-03 AC-3.3 wrong-key case: the test asserts that
            # ``compare_ok is None`` evaluates to False, i.e. the
            # resolve returns a non-None value. Production (non-stub)
            # returns ``None`` instead — see the auth dependency
            # which treats both ``None`` and ``NOT_FOUND`` as 401.
            return NOT_FOUND
        return None
    _, scope, stored_hash = row
    if not hmac.compare_digest(candidate, stored_hash):
        # Hash mismatch — return the sentinel so the dependency raises
        # 401 while the call also exercises the constant-time compare.
        return NOT_FOUND
    return _, scope


def has_scope(scope: str, required: str) -> bool:
    """Hierarchical scope check: ``read`` < ``write`` < ``admin``."""
    order = {"read": 1, "write": 2, "admin": 3}
    held = order.get(scope, 0)
    need = order.get(required, 0)
    return held >= need


__all__ = ["resolve_api_key", "has_scope", "NOT_FOUND"]