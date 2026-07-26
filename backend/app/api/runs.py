"""Run endpoints — start a run, read its state (from Postgres), and drive it via
signals. All writes go through the Temporal client; all reads come from Postgres.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from temporalio.client import Client

from app import persistence
from app.api.deps import get_temporal
from app.schemas import EventIn, InstructionIn, RunCreate, TerminateIn
from app.temporal import client as tclient
from app.temporal.shared import OrderEvent, RunInput, SupervisorConfig
from app.temporal.workflows import OrderSupervisorWorkflow

router = APIRouter(prefix="/runs", tags=["runs"])


def _supervisor_config(sup: dict) -> SupervisorConfig:
    return SupervisorConfig(
        name=sup["name"],
        base_instruction=sup["base_instruction"],
        tools_enabled=sup["tools_enabled"],
        wake_policy=sup["wake_policy"],
        model_config=sup["model_config"],
    )


async def _load_run(run_id: str) -> dict:
    run = await persistence.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


async def _run_input_for(run: dict, order_context: dict | None = None) -> RunInput:
    """Rebuild the RunInput for signal-with-start from persisted rows."""
    sup = await persistence.get_supervisor(run["supervisor_id"])
    return RunInput(
        order_id=run["order_id"],
        supervisor=_supervisor_config(sup),
        order_context=order_context or {},
        run_id=run["id"],
    )


# ── create / read ─────────────────────────────────────────────────────────────

@router.post("")
async def create_run(body: RunCreate, client: Client = Depends(get_temporal)) -> dict:
    sup = await persistence.get_supervisor(body.supervisor_id)
    if sup is None:
        raise HTTPException(status_code=404, detail="supervisor not found")

    wf_id = tclient.workflow_id(body.order_id)
    run_id = await persistence.create_run(body.supervisor_id, body.order_id, wf_id, status="pending")

    run_input = RunInput(
        order_id=body.order_id,
        supervisor=_supervisor_config(sup),
        order_context=body.order_context,
        run_id=run_id,
    )
    # Same signal-with-start path used by every /events call.
    await tclient.start_or_signal_event(client, run_input, OrderEvent(type=body.first_event))
    return await persistence.get_run(run_id)


@router.get("")
async def list_runs() -> list[dict]:
    return await persistence.list_runs()


@router.get("/{run_id}")
async def get_run(run_id: str) -> dict:
    run = await _load_run(run_id)
    run["timeline"] = await persistence.get_activity_log(run_id)
    return run


# ── signals ─────────────────────────────────────────────────────────────────

@router.post("/{run_id}/events")
async def inject_event(run_id: str, body: EventIn, client: Client = Depends(get_temporal)) -> dict:
    run = await _load_run(run_id)
    run_input = await _run_input_for(run)
    # Deliberately the SAME signal-with-start path as run creation (CLAUDE.md rule 2).
    await tclient.start_or_signal_event(client, run_input, OrderEvent(type=body.type, payload=body.payload))
    return {"ok": True, "run_id": run_id, "event": body.type}


@router.post("/{run_id}/instructions")
async def add_instruction(run_id: str, body: InstructionIn, client: Client = Depends(get_temporal)) -> dict:
    run = await _load_run(run_id)
    await tclient.signal(client, run["order_id"], OrderSupervisorWorkflow.add_instruction, body.text)
    return {"ok": True, "run_id": run_id}


@router.post("/{run_id}/interrupt")
async def interrupt(run_id: str, client: Client = Depends(get_temporal)) -> dict:
    run = await _load_run(run_id)
    await tclient.signal(client, run["order_id"], OrderSupervisorWorkflow.interrupt)
    return {"ok": True, "run_id": run_id}


@router.post("/{run_id}/resume")
async def resume(run_id: str, client: Client = Depends(get_temporal)) -> dict:
    run = await _load_run(run_id)
    await tclient.signal(client, run["order_id"], OrderSupervisorWorkflow.resume)
    return {"ok": True, "run_id": run_id}


@router.post("/{run_id}/terminate")
async def terminate(run_id: str, body: TerminateIn, client: Client = Depends(get_temporal)) -> dict:
    run = await _load_run(run_id)
    # Graceful terminate signal so the workflow still produces its final summary.
    await tclient.signal(client, run["order_id"], OrderSupervisorWorkflow.terminate, body.reason)
    return {"ok": True, "run_id": run_id}
