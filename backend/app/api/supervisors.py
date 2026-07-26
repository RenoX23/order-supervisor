"""Supervisor endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app import persistence
from app.schemas import SupervisorCreate

router = APIRouter(prefix="/supervisors", tags=["supervisors"])


@router.post("")
async def create_supervisor(body: SupervisorCreate) -> dict:
    supervisor_id = await persistence.create_supervisor(
        name=body.name,
        base_instruction=body.base_instruction,
        tools_enabled=body.tools_enabled,
        wake_policy=body.wake_policy,
        model_config=body.configuration,
    )
    return await persistence.get_supervisor(supervisor_id)


@router.get("/{supervisor_id}")
async def get_supervisor(supervisor_id: str) -> dict:
    supervisor = await persistence.get_supervisor(supervisor_id)
    if supervisor is None:
        raise HTTPException(status_code=404, detail="supervisor not found")
    return supervisor
