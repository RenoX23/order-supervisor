"""Async Postgres access via a shared asyncpg connection pool.

The pool is created lazily on first use and reused for the process lifetime.
FastAPI and Temporal activities acquire connections from it; nothing here does
any schema work — migrations live in `db/migrate.py`.
"""

from __future__ import annotations

import asyncpg

from .config import settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Return the process-wide connection pool, creating it on first call."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn=settings.database_url)
    return _pool


async def close_pool() -> None:
    """Close the pool on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
