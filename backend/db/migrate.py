"""Minimal forward-only migration runner.

Applies every `*.sql` file in `db/migrations/` in filename order, each inside a
transaction, and records applied files in a `schema_migrations` table so re-runs
are idempotent.

Usage (from the `backend/` directory, with the venv active and Postgres up):

    python db/migrate.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import asyncpg

# Make `app` importable when run as a plain script (python db/migrate.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


async def _ensure_tracking_table(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename   TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


async def _already_applied(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch("SELECT filename FROM schema_migrations")
    return {r["filename"] for r in rows}


async def migrate() -> None:
    conn = await asyncpg.connect(dsn=settings.database_url)
    try:
        await _ensure_tracking_table(conn)
        applied = await _already_applied(conn)

        files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        if not files:
            print("No migration files found.")
            return

        pending = [f for f in files if f.name not in applied]
        if not pending:
            print(f"Up to date - {len(files)} migration(s) already applied.")
            return

        for path in pending:
            sql = path.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES ($1)", path.name
                )
            print(f"applied  {path.name}")

        print(f"Done - {len(pending)} migration(s) applied.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
