"""[FR-01] Configuration loader for taskq_api.

Reads ``TASKQ_*`` environment variables once at startup.
Independence module — no sibling imports.

Citations: SPEC.md §3 FR-06 (env-driven config); SAD.md §2.2 L0 config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


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