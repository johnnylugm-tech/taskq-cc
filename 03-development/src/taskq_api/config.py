"""[FR-01/FR-09] Configuration loader for taskq_api.

Reads ``TASKQ_*`` environment variables once at startup.
Independence module — no sibling imports.

[FR-09] ``Settings.__repr__`` redacts the userinfo substring from
``db_url`` so a logger or ``/v1/metrics`` response that picks up the
repr does not leak the password (SEC-T-05 / SEC-T-08). The raw
``db_url`` field stays intact for the engine builder.

Citations: SPEC.md §3 FR-06 (env-driven config) + FR-09 (DB URL
password redaction); SAD.md §2.2 L0 config.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Settings:
    """Read-once typed view of the ``TASKQ_*`` environment variables."""

    db_url: str
    home: str
    rate_per_sec: float
    rate_burst: int
    max_concurrent: int
    task_timeout: float
    drain_timeout: float
    cors_origins: tuple[str, ...]
    log_level: str
    log_format: str
    host: str
    port: int
    db_pool_size: int

    def __repr__(self) -> str:  # noqa: D401 — repr override for safety
        """Return a repr whose ``db_url`` has the userinfo password redacted.

        The raw ``db_url`` field keeps the password because the engine
        builder needs it; any code path that stringifies the Settings
        object (a logger formatting ``%r``, ``repr(settings)`` in a
        debugging session, the ``/v1/metrics`` exception path) sees the
        redacted form instead.
        """
        redacted_url = _REDACT_USERINFO.sub(r"\1[REDACTED]@", self.db_url)
        # Walk ``fields()`` rather than hand-listing every name so a
        # future field added to ``Settings`` is picked up here without
        # a separate edit to ``__repr__``.
        pairs = [
            f"{field.name}={redacted_url if field.name == 'db_url' else getattr(self, field.name)!r}"
            for field in fields(self)
        ]
        return f"Settings({', '.join(pairs)})"


# [FR-09] Match ``scheme://user:password@host`` and capture the user
# portion so the password substring can be replaced wholesale with
# ``[REDACTED]``. Only the password is replaced; the username stays so
# operators can still tell which credential is in use.
_REDACT_USERINFO = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*)://[^:@\s/]+:[^@\s/]+@")


def _tuple_env(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def get_settings() -> Settings:
    """Return a fresh ``Settings`` snapshot from current env state."""
    return Settings(
        db_url=os.environ.get("TASKQ_DB_URL", "sqlite:///./taskq.db"),
        home=os.environ.get("TASKQ_HOME", "."),
        rate_per_sec=float(os.environ.get("TASKQ_RATE_PER_SEC", "5")),
        rate_burst=int(os.environ.get("TASKQ_RATE_BURST", "20")),
        max_concurrent=int(os.environ.get("TASKQ_MAX_CONCURRENT", "8")),
        task_timeout=float(os.environ.get("TASKQ_TASK_TIMEOUT", "30")),
        drain_timeout=float(os.environ.get("TASKQ_DRAIN_TIMEOUT", "10")),
        cors_origins=_tuple_env("TASKQ_CORS_ORIGINS", ()),
        log_level=os.environ.get("TASKQ_LOG_LEVEL", "INFO"),
        log_format=os.environ.get("TASKQ_LOG_FORMAT", "text"),
        host=os.environ.get("TASKQ_HOST", "127.0.0.1"),
        port=int(os.environ.get("TASKQ_PORT", "8000")),
        db_pool_size=int(os.environ.get("TASKQ_DB_POOL_SIZE", "5")),
    )