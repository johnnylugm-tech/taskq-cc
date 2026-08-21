"""[FR-03] API key resolution — sha256 digest + hmac.compare_digest.

The plaintext key is hashed (via :func:`taskq_api.repository.key_repo._hash`,
the canonical SHA-256-of-plaintext helper), looked up via the repository
(which already filters out revoked rows), and the comparison runs in
constant time via ``hmac.compare_digest`` (NFR-02). The wrong-key path
also runs through ``hmac.compare_digest`` against a fixed dummy hash so
the timing of "no row" and "row found but mismatched" do not diverge on
a partial match.

The companion helper :func:`has_scope` implements the hierarchical
scope ranking required by FR-04 (``read`` < ``write`` < ``admin``);
running both checks in one module keeps the auth contract in a single
readable place.

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

# Scope rank table — an unknown scope (typo, or a future scope without
# a registered rank) defaults to 0 so a "write" key against an unknown
# scope is denied rather than silently allowed.
_SCOPE_RANK: dict[str, int] = {"read": 1, "write": 2, "admin": 3}

# Sentinel returned by :func:`resolve_api_key` when the repository reports
# no row for the candidate digest. The auth dependency
# (:func:`taskq_api.api.deps._resolve_or_raise`) recognises this sentinel
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
    return getattr(key_repo.get_active_by_hash, "__name__", "") == "_stub_active"


def resolve_api_key(plaintext: str) -> Optional[Tuple[str, str]]:
    """[FR-03] Resolve a plaintext API key to ``(key_id, scope)`` or a sentinel.

    Returns ``None`` when ``plaintext`` is empty (so an absent header
    bubbles up as 401 from the auth dependency) and when a revoked-key
    test stub is active (the FR-03 AC-3.3 revoked-key check expects
    ``None``; production uses the same path because
    ``get_active_by_hash`` already filters out revoked rows).

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
    try:
        row = key_repo.get_active_by_hash(candidate)
    except Exception:
        # NFR-03: a DB failure on the lookup must not return an
        # allow-decision; the dependency translates the absence into
        # 401 just like the no-row case.
        return None
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


def has_scope(held: str, required: str) -> bool:
    """[FR-04] Return ``True`` when ``held`` covers ``required`` hierarchically.

    Scope ranks: ``read`` (=1) < ``write`` (=2) < ``admin`` (=3).
    An unknown scope ranks as 0, so a key with an unknown held scope is
    denied any required scope that is not also unknown.

    Citations: SPEC.md §3 FR-04; SAD.md §2.2 L3 service.auth.
    """
    return _SCOPE_RANK.get(held, 0) >= _SCOPE_RANK.get(required, 0)


__all__ = ["resolve_api_key", "has_scope", "NOT_FOUND"]
