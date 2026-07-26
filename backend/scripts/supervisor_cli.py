"""Command-line client for driving OrderSupervisorWorkflow against a live
Temporal server — handy for manual testing, the Stage 3 sleep/wake demo, and the
walkthrough video.

Run from ``backend/`` (venv active, ``docker compose up -d`` and the worker
running):

    python -m scripts.supervisor_cli start   --order-id demo-001
    python -m scripts.supervisor_cli event   --order-id demo-001 --type payment_failed
    python -m scripts.supervisor_cli instruct --order-id demo-001 --text "Prioritise VIP"
    python -m scripts.supervisor_cli interrupt --order-id demo-001
    python -m scripts.supervisor_cli resume    --order-id demo-001
    python -m scripts.supervisor_cli terminate --order-id demo-001 --reason "done"
    python -m scripts.supervisor_cli state   --order-id demo-001
    python -m scripts.supervisor_cli history --order-id demo-001
    python -m scripts.supervisor_cli demo    --order-id demo-001   # scripted full cycle
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import sys
from pathlib import Path

# Make `app` importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from temporalio.client import Client, WorkflowHandle  # noqa: E402

from app.config import settings  # noqa: E402
from app.temporal.shared import OrderEvent, RunInput, SupervisorConfig  # noqa: E402
from app.temporal.workflows import OrderSupervisorWorkflow  # noqa: E402


def workflow_id(order_id: str) -> str:
    return f"order-{order_id}"


def ui_url(wf_id: str) -> str:
    return f"http://localhost:8233/namespaces/{settings.temporal_namespace}/workflows/{wf_id}"


async def _client() -> Client:
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)


def _handle(client: Client, order_id: str) -> WorkflowHandle:
    return client.get_workflow_handle(workflow_id(order_id))


# ── commands ──────────────────────────────────────────────────────────────────

async def cmd_start(args) -> None:
    client = await _client()
    supervisor = SupervisorConfig(
        name=args.supervisor,
        base_instruction="Watch this order from creation to completion and keep it on track.",
        tools_enabled=[
            "message_fulfillment_team",
            "message_payments_team",
            "message_logistics_team",
            "message_customer",
            "create_internal_note",
        ],
        wake_policy={"default_sleep_seconds": args.sleep, "max_age_seconds": args.max_age},
    )
    run_input = RunInput(order_id=args.order_id, supervisor=supervisor, run_id=f"run-{args.order_id}")

    # signal-with-start: the first order_created event both creates the workflow
    # and is delivered as its first signal.
    handle = await client.start_workflow(
        OrderSupervisorWorkflow.run,
        run_input,
        id=workflow_id(args.order_id),
        task_queue=settings.temporal_task_queue,
        start_signal="submit_event",
        start_signal_args=[OrderEvent(type=args.event)],
    )
    print(f"started workflow: {handle.id}")
    print(f"web UI:          {ui_url(handle.id)}")


async def cmd_event(args) -> None:
    client = await _client()
    await _handle(client, args.order_id).signal(
        OrderSupervisorWorkflow.submit_event, OrderEvent(type=args.type)
    )
    print(f"sent event '{args.type}' to {workflow_id(args.order_id)}")


async def cmd_instruct(args) -> None:
    client = await _client()
    await _handle(client, args.order_id).signal(OrderSupervisorWorkflow.add_instruction, args.text)
    print(f"sent instruction to {workflow_id(args.order_id)}: {args.text!r}")


async def cmd_interrupt(args) -> None:
    client = await _client()
    await _handle(client, args.order_id).signal(OrderSupervisorWorkflow.interrupt)
    print(f"interrupted {workflow_id(args.order_id)}")


async def cmd_resume(args) -> None:
    client = await _client()
    await _handle(client, args.order_id).signal(OrderSupervisorWorkflow.resume)
    print(f"resumed {workflow_id(args.order_id)}")


async def cmd_terminate(args) -> None:
    client = await _client()
    await _handle(client, args.order_id).signal(OrderSupervisorWorkflow.terminate, args.reason)
    print(f"terminate signalled to {workflow_id(args.order_id)} (reason={args.reason!r})")


async def cmd_state(args) -> None:
    client = await _client()
    state = await _handle(client, args.order_id).query(OrderSupervisorWorkflow.get_state)
    _print_state(state)


async def cmd_history(args) -> None:
    client = await _client()
    await _print_history(_handle(client, args.order_id))


async def cmd_demo(args) -> None:
    """Scripted end-to-end sleep/wake cycle with evidence printed at each step."""
    client = await _client()
    oid = args.order_id
    wf = workflow_id(oid)

    print("=" * 72)
    print(f"DEMO: sleep -> wake cycle for order '{oid}'")
    print("=" * 72)

    supervisor = SupervisorConfig(
        name="Demo Supervisor",
        base_instruction="Watch this order from creation to completion.",
        wake_policy={"default_sleep_seconds": args.sleep, "max_age_seconds": args.max_age},
    )
    run_input = RunInput(order_id=oid, supervisor=supervisor, run_id=f"run-{oid}")
    handle = await client.start_workflow(
        OrderSupervisorWorkflow.run,
        run_input,
        id=wf,
        task_queue=settings.temporal_task_queue,
        start_signal="submit_event",
        start_signal_args=[OrderEvent(type="order_created")],
    )
    print(f"\n[1] started via signal-with-start (order_created).  UI: {ui_url(wf)}")

    # After the start step the agent should be asleep with a scheduled wake.
    state = await _wait_for(handle, lambda s: s.sleeping, "workflow to go to sleep")
    print(f"    -> agent ran on START, now SLEEPING until {state.next_wake_at}")

    # [2] Scheduled wake: let the sleep timer fire on its own.
    print("\n[2] waiting for the SCHEDULED wake-up timer to fire (no signals)...")
    state = await _wait_for(
        handle, lambda s: "scheduled" in _triggers(s), "a scheduled wake-up", timeout=args.sleep + 20
    )
    print("    -> woke on SCHEDULE (timer fired), agent ran, back to sleep")

    # [3] Signal wake: an important event wakes it early.
    print("\n[3] sending an important event (payment_failed) to wake it early...")
    await handle.signal(OrderSupervisorWorkflow.submit_event, OrderEvent(type="payment_failed"))
    state = await _wait_for(
        handle, lambda s: "message_payments_team" in _actions(s), "the agent to act on payment_failed"
    )
    print("    -> woke on SIGNAL (timer cancelled early), took action: message_payments_team")

    # [4] Terminal event -> workflow-gated completion.
    print("\n[4] sending a terminal event (delivered) -> workflow completes...")
    await handle.signal(OrderSupervisorWorkflow.submit_event, OrderEvent(type="delivered"))
    result = await handle.result()
    final = await handle.query(OrderSupervisorWorkflow.get_state)
    print(f"    -> COMPLETED. reason={final.completion_reason}, status={final.status}")
    print(f"    -> final summary: {result.get('final_summary')}")

    print("\n[5] wake/sleep triggers observed, in order:")
    for t in _triggers(final):
        print(f"      - {t}")

    print("\n[6] Temporal event history (the same data the Web UI shows):")
    await _print_history(handle, highlight=True)

    print("\nDONE. Open the workflow in the Web UI to see it there too:")
    print(f"  {ui_url(wf)}")


# ── helpers ───────────────────────────────────────────────────────────────────

def _triggers(state) -> list[str]:
    return [
        t.payload.get("trigger")
        for t in state.timeline
        if t.type == "wake_decision" and t.payload.get("woke_agent")
    ]


def _actions(state) -> list[str]:
    return [
        t.payload.get("action_type")
        for t in state.timeline
        if t.type == "agent_action" and "action_type" in t.payload
    ]


async def _wait_for(handle, predicate, description, timeout: float = 30.0, interval: float = 1.0):
    """Poll the get_state query until predicate holds (client-side polling only)."""
    waited = 0.0
    while waited <= timeout:
        state = await handle.query(OrderSupervisorWorkflow.get_state)
        if predicate(state):
            return state
        await asyncio.sleep(interval)
        waited += interval
    raise TimeoutError(f"timed out after {timeout}s waiting for {description}")


def _print_state(state) -> None:
    d = dataclasses.asdict(state)
    timeline = d.pop("timeline", [])
    print("-- state " + "-" * 54)
    for k, v in d.items():
        print(f"  {k:22}: {v}")
    print(f"  timeline ({len(timeline)} entries):")
    for entry in timeline:
        print(f"    #{entry['seq']:<3} {entry['type']:<14} {entry['payload']}")


# Temporal history event types that make the sleep/wake cycle visible.
_KEY_EVENTS = {
    "EVENT_TYPE_TIMER_STARTED": "sleep: timer started",
    "EVENT_TYPE_TIMER_FIRED": "SCHEDULED wake: timer fired",
    "EVENT_TYPE_TIMER_CANCELED": "SIGNAL wake: timer cancelled early",
    "EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED": "signal received",
    "EVENT_TYPE_ACTIVITY_TASK_COMPLETED": "activity (agent step) completed",
    "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED": "workflow started",
    "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED": "workflow completed",
}


def _event_type_name(event) -> str:
    try:
        from temporalio.api.enums.v1 import EventType

        return EventType.Name(event.event_type)
    except Exception:
        return getattr(event.event_type, "name", str(event.event_type))


async def _print_history(handle, highlight: bool = False) -> None:
    async for event in handle.fetch_history_events():
        name = _event_type_name(event)
        if highlight:
            note = _KEY_EVENTS.get(name)
            if note:
                print(f"    #{event.event_id:<3} {name:<48} <- {note}")
        else:
            print(f"    #{event.event_id:<3} {name}")


# ── argparse wiring ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Drive OrderSupervisorWorkflow from the CLI.")
    sub = p.add_subparsers(dest="command", required=True)

    def add_order_id(sp):
        sp.add_argument("--order-id", required=True)

    sp = sub.add_parser("start", help="signal-with-start a new run")
    add_order_id(sp)
    sp.add_argument("--supervisor", default="CLI Supervisor")
    sp.add_argument("--event", default="order_created")
    sp.add_argument("--sleep", type=int, default=30, help="default sleep seconds")
    sp.add_argument("--max-age", type=int, default=86400)
    sp.set_defaults(func=cmd_start)

    sp = sub.add_parser("event", help="send an event signal")
    add_order_id(sp)
    sp.add_argument("--type", required=True)
    sp.set_defaults(func=cmd_event)

    sp = sub.add_parser("instruct", help="add an instruction")
    add_order_id(sp)
    sp.add_argument("--text", required=True)
    sp.set_defaults(func=cmd_instruct)

    sp = sub.add_parser("interrupt", help="pause the agent")
    add_order_id(sp)
    sp.set_defaults(func=cmd_interrupt)

    sp = sub.add_parser("resume", help="resume the agent")
    add_order_id(sp)
    sp.set_defaults(func=cmd_resume)

    sp = sub.add_parser("terminate", help="gracefully terminate the run")
    add_order_id(sp)
    sp.add_argument("--reason", default="manual_terminate")
    sp.set_defaults(func=cmd_terminate)

    sp = sub.add_parser("state", help="query and print current state")
    add_order_id(sp)
    sp.set_defaults(func=cmd_state)

    sp = sub.add_parser("history", help="print the Temporal event history")
    add_order_id(sp)
    sp.set_defaults(func=cmd_history)

    sp = sub.add_parser("demo", help="scripted full sleep/wake cycle with evidence")
    add_order_id(sp)
    sp.add_argument("--sleep", type=int, default=15, help="default sleep seconds")
    sp.add_argument("--max-age", type=int, default=86400)
    sp.set_defaults(func=cmd_demo)

    return p


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
