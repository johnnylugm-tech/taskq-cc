"""[FR-05/FR-09] Token-bucket rate limiting.

Thin service layer over :mod:`taskq_api.repository.rate_repo`: the API
layer may not reach into the repository directly (SAD layering), so the
rate-limit decision is exposed here as :func:`check`.

[FR-09] The module also owns the process-local denial counter surfaced
by ``/v1/metrics`` as ``rate_limit_denials``. :func:`record_denial`
bumps it; :data:`denial_count` exposes the current value. The counter
sits here (rather than in ``api.health``) so neither ``api.health`` nor
``api.deps`` has to import the other, breaking the circular import.

Citations: SPEC.md §3 FR-05 + FR-09; ADR-007 (token bucket with
row-level lock); NFR-02 (rate-limit 429); SAD.md §2.2 L3
service.ratelimit.
"""

from __future__ import annotations

from taskq_api.repository import rate_repo

# [FR-09] Process-local gauge: number of token-bucket rejections since
# process start. A restart resets the value, which matches the
# "denied requests in this lifetime" semantics that /v1/metrics
# promises (SPEC.md §3 FR-09).
denial_count: int = 0


def record_denial() -> None:
    """[FR-05] Increment the rate-limit denial counter by one."""
    global denial_count
    denial_count += 1


def check(key_id: object) -> tuple[bool, int]:
    """[FR-05] Charge one request against ``key_id``'s bucket.

    Returns ``(allowed, retry_after)`` — ``retry_after`` is the number of
    whole seconds the caller should wait before retrying, and is 0 when
    the request is allowed.

    Citations: SPEC.md §3 FR-05 (per-token token bucket); AC-5.1.
    """
    return rate_repo.withdraw(key_id)


__all__ = ["check", "record_denial", "denial_count"]
