"""Deterministic proof of the sleep -> wake cycle using Temporal's time-skipping
test environment.

This exercises all three inference triggers (start, scheduled wake-up, important
signal), the classifier's log-only path, workflow-gated terminal completion, and
the graceful terminate signal — with no real clock waiting.
"""

import uuid

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.temporal.activities import agent_decide, build_final_summary
from app.temporal.shared import OrderEvent, RunInput, SupervisorConfig
from app.temporal.workflows import OrderSupervisorWorkflow

TASK_QUEUE = "test-order-supervisor"


def _supervisor() -> SupervisorConfig:
    return SupervisorConfig(
        name="Test Supervisor",
        base_instruction="Watch the order.",
        tools_enabled=["message_customer", "create_internal_note"],
        wake_policy={"default_sleep_seconds": 30, "max_age_seconds": 3600},
    )


async def _start(env: WorkflowEnvironment, order_id: str, first_event: str = "order_created"):
    run_input = RunInput(order_id=order_id, supervisor=_supervisor(), run_id=f"run-{order_id}")
    return await env.client.start_workflow(
        OrderSupervisorWorkflow.run,
        run_input,
        id=f"order-{order_id}-{uuid.uuid4()}",
        task_queue=TASK_QUEUE,
        start_signal="submit_event",
        start_signal_args=[OrderEvent(type=first_event)],
    )


def _triggers(state) -> list[str]:
    return [t.payload.get("trigger") for t in state.timeline if t.type == "wake_decision" and t.payload.get("woke_agent")]


def _action_types(state) -> list[str]:
    return [t.payload.get("action_type") for t in state.timeline if t.type == "agent_action" and "action_type" in t.payload]


@pytest.mark.asyncio
async def test_full_cycle_start_scheduled_signal_and_terminal_completion():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[OrderSupervisorWorkflow],
            activities=[agent_decide, build_final_summary],
        ):
            handle = await _start(env, "o1")

            # Trigger 1: the agent ran on start and reacted to order_created.
            state = await handle.query(OrderSupervisorWorkflow.get_state)
            assert "start" in _triggers(state)
            assert state.order_id == "o1"

            # Trigger 3: advance past the scheduled sleep so a scheduled wake fires.
            await env.sleep(40)
            state = await handle.query(OrderSupervisorWorkflow.get_state)
            assert "scheduled" in _triggers(state)

            # Trigger 2: an important signal wakes the agent and drives an action.
            await handle.signal(OrderSupervisorWorkflow.submit_event, OrderEvent(type="payment_failed"))
            await env.sleep(1)
            state = await handle.query(OrderSupervisorWorkflow.get_state)
            assert "signal" in _triggers(state)
            assert "message_payments_team" in _action_types(state)

            # Workflow-owned completion: a terminal event ends the run.
            await handle.signal(OrderSupervisorWorkflow.submit_event, OrderEvent(type="delivered"))
            result = await handle.result()
            assert "final_summary" in result

            final_state = await handle.query(OrderSupervisorWorkflow.get_state)
            assert final_state.terminal is True
            assert final_state.completion_reason == "terminal_event:delivered"
            assert final_state.status == "completed"


@pytest.mark.asyncio
async def test_benign_event_does_not_wake_agent():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[OrderSupervisorWorkflow],
            activities=[agent_decide, build_final_summary],
        ):
            handle = await _start(env, "o2")
            await handle.query(OrderSupervisorWorkflow.get_state)  # ensure start step done

            # A benign event should be logged but must NOT trigger an agent run.
            await handle.signal(OrderSupervisorWorkflow.submit_event, OrderEvent(type="shipment_created"))
            await env.sleep(1)
            state = await handle.query(OrderSupervisorWorkflow.get_state)

            logged_not_woken = [
                t for t in state.timeline
                if t.type == "wake_decision" and t.payload.get("woke_agent") is False
                and t.payload.get("type") == "shipment_created"
            ]
            assert logged_not_woken, "benign event should be logged as not waking the agent"
            # Only the start trigger should have woken the agent so far.
            assert _triggers(state) == ["start"]

            await handle.signal(OrderSupervisorWorkflow.terminate, "test done")
            result = await handle.result()
            assert "final_summary" in result
            final_state = await handle.query(OrderSupervisorWorkflow.get_state)
            assert final_state.status == "terminated"


@pytest.mark.asyncio
async def test_interrupt_and_resume():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[OrderSupervisorWorkflow],
            activities=[agent_decide, build_final_summary],
        ):
            handle = await _start(env, "o3")
            await handle.query(OrderSupervisorWorkflow.get_state)

            await handle.signal(OrderSupervisorWorkflow.interrupt)
            await env.sleep(1)
            state = await handle.query(OrderSupervisorWorkflow.get_state)
            assert state.interrupted is True
            assert state.status == "interrupted"

            await handle.signal(OrderSupervisorWorkflow.resume)
            await env.sleep(1)
            state = await handle.query(OrderSupervisorWorkflow.get_state)
            assert state.interrupted is False

            await handle.signal(OrderSupervisorWorkflow.terminate)
            result = await handle.result()
            assert "final_summary" in result
