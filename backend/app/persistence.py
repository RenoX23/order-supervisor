"""Postgres persistence helpers (async, raw SQL over the asyncpg pool).

Used by Temporal activities (never by the workflow directly) and by the CLI/API
to create the supervisor + run rows. Kept as plain SQL so the ``model_config``
column name doesn't collide with anything.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from app.db import get_pool
from app.temporal.shared import TimelineEntry


async def create_supervisor(
    name: str,
    base_instruction: str,
    tools_enabled: list[str],
    wake_policy: dict[str, Any],
    model_config: dict[str, Any],
) -> str:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO supervisors (name, base_instruction, tools_enabled, wake_policy, model_config)
        VALUES ($1, $2, $3::jsonb, $4::jsonb, $5::jsonb)
        RETURNING id
        """,
        name,
        base_instruction,
        json.dumps(tools_enabled),
        json.dumps(wake_policy),
        json.dumps(model_config),
    )
    return str(row["id"])


async def create_run(supervisor_id: str, order_id: str, workflow_id: str, status: str = "pending") -> str:
    """Create the run row, or return the existing one for this workflow_id."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO runs (supervisor_id, order_id, workflow_id, status)
        VALUES ($1::uuid, $2, $3, $4)
        ON CONFLICT (workflow_id) DO UPDATE SET updated_at = now()
        RETURNING id
        """,
        supervisor_id,
        order_id,
        workflow_id,
        status,
    )
    return str(row["id"])


async def append_activity_logs(run_id: str, entries: list[TimelineEntry]) -> None:
    if not entries:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(
                "INSERT INTO activity_log (run_id, type, payload) VALUES ($1::uuid, $2, $3::jsonb)",
                [(run_id, e.type, json.dumps(e.payload)) for e in entries],
            )


async def update_run(
    run_id: str,
    *,
    memory_summary: Optional[str] = None,
    status: Optional[str] = None,
    next_wake_at: Optional[str] = None,
    final_summary: Optional[dict[str, Any]] = None,
) -> None:
    sets: list[str] = []
    args: list[Any] = []

    def add(column: str, value: Any, cast: str = "") -> None:
        args.append(value)
        sets.append(f"{column} = ${len(args)}{cast}")

    if memory_summary is not None:
        add("memory_summary", memory_summary)
    if status is not None:
        add("status", status)
    if next_wake_at is not None:
        # asyncpg wants a datetime for timestamptz, not an ISO string.
        add("next_wake_at", datetime.fromisoformat(next_wake_at))
    if final_summary is not None:
        add("final_summary", json.dumps(final_summary), "::jsonb")

    if not sets:
        return

    args.append(run_id)
    pool = await get_pool()
    await pool.execute(
        f"UPDATE runs SET {', '.join(sets)} WHERE id = ${len(args)}::uuid",
        *args,
    )
