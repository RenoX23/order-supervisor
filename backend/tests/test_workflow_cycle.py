"""Deterministic proof of the sleep -> wake cycle using Temporal's time-skipping
test environment.

The real activities call an LLM (Groq) and Postgres, so these tests register
**mock activities under the same Temporal names** — the workflow dispatches by
name, so it exercises the real control flow (all three triggers, the classifier's
log-only path, workflow-gated completion, interrupt/resume, terminate, and the
persistence flush) with no network, no database, and no API key.
"""

import uuid

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.temporal.shared import (
    AgentAction,
    AgentDecision,
    AgentStepInput,
    OrderEvent,
    PersistStepInput,
    RunInput,
    SupervisorConfig,
)
from app.temporal.workflows import OrderSupervisorWorkflow

TASK_QUEUE = "test-order-supervisor"

# Records every persist_step call so a test can assert the flush is wired.
PERSISTED: list[PersistStepInput] = []

_ROUTING = {
    "payment_failed": ["message_payments_team", "create_internal_note"],
    "refund_requested": ["message_payments_team", "message_fulfillment_team"],
    "shipment_delayed": ["message_logistics_team", "message_customer"],
    "customer_message_received": ["message_customer"],
    "delivered": ["message_customer", "create_internal_note"],
    "order_created": ["create_internal_note"],
    "no_update_for_n_hours": ["create_internal_note"],
}


@activity.defn(name="agent_decide")
async def mock_agent_decide(inp: AgentStepInput) -> AgentDecision:
    event_types = [e.type for e in inp.events]
    actions: list[AgentAction] = []
    for et in event_types:
        for tool in _ROUTING.get(et, []):
            actions.append(AgentAction(type=tool, params={"reason": f"mock {et}"}))
    if not actions:
        actions.append(AgentAction(type="create_internal_note", params={"note": "routine check"}))
    return AgentDecision(
        reasoning=f"[mock] trigger={inp.trigger} events={event_types}",
        actions=actions,
        memory_update=(inp.memory_summary + f"\n- {inp.trigger}:{event_types}").strip()[-1000:],
        sleep_for_seconds=20 if event_types else 60,
        complete_recommendation=False,
    )


@activity.defn(name="build_final_summary")
async def mock_build_final_summary(inp: AgentStepInput) -> dict:
    return {"final_summary": f"[mock] {inp.order_id}", "key_learnings": [], "feedback": "", "memory_summary": inp.memory_summary}


@activity.defn(name="persist_step")
async def mock_persist_step(inp: PersistStepInput) -> None:
    PERSISTED.append(inp)


def _mock_worker(env: WorkflowEnvironment) -> Worker:
    return Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[OrderSupervisorWorkflow],
        activities=[mock_agent_decide, mock_build_final_summary, mock_persist_step],
    )


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
        async with _mock_worker(env):
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
        async with _mock_worker(env):
            handle = await _start(env, "o2")
            await handle.query(OrderSupervisorWorkflow.get_state)

            await handle.signal(OrderSupervisorWorkflow.submit_event, OrderEvent(type="shipment_created"))
            await env.sleep(1)
            state = await handle.query(OrderSupervisorWorkflow.get_state)

            logged_not_woken = [
                t for t in state.timeline
                if t.type == "wake_decision" and t.payload.get("woke_agent") is False
                and t.payload.get("type") == "shipment_created"
            ]
            assert logged_not_woken, "benign event should be logged as not waking the agent"
            assert _triggers(state) == ["start"]

            await handle.signal(OrderSupervisorWorkflow.terminate, "test done")
            result = await handle.result()
            assert "final_summary" in result
            final_state = await handle.query(OrderSupervisorWorkflow.get_state)
            assert final_state.status == "terminated"


@pytest.mark.asyncio
async def test_interrupt_and_resume():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with _mock_worker(env):
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


@pytest.mark.asyncio
async def test_persistence_flush_is_wired():
    PERSISTED.clear()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with _mock_worker(env):
            handle = await _start(env, "o4")
            await handle.signal(OrderSupervisorWorkflow.submit_event, OrderEvent(type="delivered"))
            await handle.result()

    # The workflow flushed timeline rows + run patches through persist_step.
    assert PERSISTED, "persist_step should have been called"
    assert all(p.run_id == "run-o4" for p in PERSISTED)
    all_entry_types = {e.type for p in PERSISTED for e in p.entries}
    assert "agent_action" in all_entry_types
    assert "final_output" in all_entry_types
    assert any(p.status == "completed" for p in PERSISTED)
