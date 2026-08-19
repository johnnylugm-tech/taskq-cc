"""[FR-03/FR-06] API key repository — only the SHA-256 digest is ever persisted.

The plaintext is minted here and returned to the caller once; only the
hex digest lands in ``api_keys.key_hash``. The repository never logs or
echoes the plaintext. ``get_active_by_hash`` filters out rows whose
``revoked_at`` is non-null so the auth service can rely on a single
lookup to decide accept/deny.

All writes go through :func:`session_scope` /
:func:`insert_scope` — the FR-06 transactional boundary
(SAD.md §2.2 L2 repository.key_repo; SPEC.md §3 FR-06 + FR-03;
NFR-02 sha256-only).
"""

from __future__ import annotations

import hashlib
import secrets
from types import SimpleNamespace
from typing import Optional, Tuple

from sqlalchemy import select

from taskq_api.models.orm import ApiKey
from taskq_api.repository import session as session_module


def _hash(plaintext: str) -> str:
    """Return the lowercase hex SHA-256 digest of ``plaintext``."""
    return hashlib.sha256(plaintext.encode()).hexdigest()


def _generate_plaintext() -> str:
    """Mint a fresh opaque API key plaintext.

    Uses 24 random bytes (192 bits) encoded as URL-safe base64 — ample
    entropy for a service API key while keeping the printable form
    short enough for a one-shot stdout reveal.
    """
    return secrets.token_urlsafe(24)


def create(scope: str) -> Tuple[int, str, str]:
    """Mint a new key, persist its SHA-256 digest, return ``(id, plaintext, key_hash)``.

    The plaintext is returned to the caller once and is never written to
    any persistent sink. ``ApiKey.key_hash`` is the 64-character
    lowercase hex of ``hashlib.sha256(plaintext.encode()).hexdigest()``;
    no plaintext column exists in the schema (AC-3.2).
    """
    plaintext = _generate_plaintext()
    key_hash = _hash(plaintext)
    with session_module.insert_scope() as session:
        row = ApiKey(scope=scope, key_hash=key_hash)
        session.add(row)
        session.flush()
        session.expunge(row)
    return row.id, plaintext, key_hash


def get_active_by_hash(key_hash: str) -> Optional[Tuple[str, str, str]]:
    """Return ``(key_id, scope, key_hash)`` for an active row, else ``None``.

    "Active" means ``revoked_at IS NULL`` (AC-3.5). The repo returns the
    stored hash as the third element so the auth service can run
    ``hmac.compare_digest`` against the candidate digest — constant-time,
    no early return on a partial match.
    """
    with session_module.session_scope() as session:
        stmt = (
            select(ApiKey)
            .where(ApiKey.key_hash == key_hash)
            .where(ApiKey.revoked_at.is_(None))
        )
        row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        return None
    return str(row.id), row.scope, row.key_hash


def revoke(key_hash: str) -> bool:
    """Mark the row with ``key_hash`` as revoked (sets ``revoked_at`` to now).

    Idempotent — a second call on an already-revoked row is a no-op.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    with session_module.session_scope() as session:
        stmt = (
            select(ApiKey)
            .where(ApiKey.key_hash == key_hash)
            .where(ApiKey.revoked_at.is_(None))
        )
        row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            return False
        row.revoked_at = now
    return True


# Module-level instance exposing the canonical functions as attributes so
# callers can write ``key_repo.create(...)`` (the binding shape declared
# in ``.methodology/SAB.json``).
key_repo = SimpleNamespace(
    create=create,
    get_active_by_hash=get_active_by_hash,
    revoke=revoke,
)


__all__ = [
    "create",
    "get_active_by_hash",
    "revoke",
    "key_repo",
]