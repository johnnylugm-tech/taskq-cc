"""[FR-05] Token-bucket rate limiting.

Thin service layer over :mod:`taskq_api.repository.rate_repo`: the API
layer may not reach into the repository directly (SAD layering), so the
rate-limit decision is exposed here as :func:`check`.

Citations: SPEC.md §3 FR-05; ADR-007 (token bucket with row-level lock);
NFR-02 (rate-limit 429); SAD.md §2.2 L3 service.ratelimit.
"""

from __future__ import annotations

from taskq_api.repository import rate_repo


def check(key_id: object) -> tuple[bool, int]:
    """Charge one request against ``key_id``'s bucket.

    Returns ``(allowed, retry_after)`` — ``retry_after`` is the number of
    whole seconds the caller should wait before retrying, and is 0 when
    the request is allowed.

    Citations: SPEC.md §3 FR-05 (per-token token bucket); AC-5.1.
    """
    return rate_repo.withdraw(key_id)


__all__ = ["check"]
