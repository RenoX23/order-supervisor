"""Temporal client helpers used by the API layer.

Central home for the one non-negotiable rule: every event ingestion goes through
**signal-with-start** (``start_or_signal_event``), so creation and later
``/events`` share a single code path — no "create vs signal" branch.
"""

from __future__ import annotations

from temporalio.client import Client

from app.config import settings
from app.temporal.shared import OrderEvent, RunInput
from app.temporal.workflows import OrderSupervisorWorkflow


def workflow_id(order_id: str) -> str:
    return f"order-{order_id}"


async def get_client() -> Client:
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)


async def start_or_signal_event(client: Client, run_input: RunInput, event: OrderEvent) -> None:
    """Deliver an event via signal-with-start.

    If the workflow for this order doesn't exist yet it is created and the event
    is its first signal; if it's already running, Temporal just delivers the
    signal and ignores the start args. Same path for the first order_created
    event and every event injected later.
    """
    await client.start_workflow(
        OrderSupervisorWorkflow.run,
        run_input,
        id=workflow_id(run_input.order_id),
        task_queue=settings.temporal_task_queue,
        start_signal="submit_event",
        start_signal_args=[event],
    )


async def signal(client: Client, order_id: str, signal_method, *args) -> None:
    """Send a signal to an already-running workflow (instruction/interrupt/…)."""
    await client.get_workflow_handle(workflow_id(order_id)).signal(signal_method, *args)
