"""Temporal activities.

**Stage 2 note:** ``agent_decide`` and ``build_final_summary`` are STUBS. They
return structured decisions using simple rules, with **no LLM call and no
external I/O**. Stage 4 replaces their bodies with a real structured LLM call and
database persistence — the workflow that invokes them does not change.

Keeping these as activities from the start means the deterministic workflow never
has to be restructured when real I/O is introduced.
"""

from __future__ import annotations

from temporalio import activity

from app.temporal.shared import AgentAction, AgentDecision, AgentStepInput

# Simple event -> action routing for the stub, so the demo produces believable
# activity. Replaced by the LLM's judgement in Stage 4.
_STUB_ROUTING: dict[str, list[str]] = {
    "payment_failed": ["message_payments_team", "create_internal_note"],
    "refund_requested": ["message_payments_team", "message_fulfillment_team"],
    "shipment_delayed": ["message_logistics_team", "message_customer"],
    "customer_message_received": ["message_customer"],
    "delivered": ["message_customer", "create_internal_note"],
    "order_created": ["create_internal_note"],
    "no_update_for_n_hours": ["create_internal_note"],
}


@activity.defn
async def agent_decide(inp: AgentStepInput) -> AgentDecision:
    """STUB decision function — deterministic rules, no LLM, no I/O."""
    event_types = [e.type for e in inp.events]

    actions: list[AgentAction] = []
    for et in event_types:
        for tool in _STUB_ROUTING.get(et, []):
            actions.append(
                AgentAction(
                    type=tool,
                    params={"reason": f"stub response to {et}", "order_id": inp.order_id},
                )
            )

    if inp.new_instructions:
        actions.append(
            AgentAction(
                type="create_internal_note",
                params={
                    "note": "acknowledged new run instruction(s)",
                    "instructions": inp.new_instructions,
                },
            )
        )

    if not actions:
        actions.append(
            AgentAction(
                type="create_internal_note",
                params={"note": f"[stub] routine check on {inp.trigger}; nothing to do"},
            )
        )

    reasoning = (
        f"[stub decision] trigger={inp.trigger}, "
        f"events={event_types or 'none'} -> {[a.type for a in actions]}"
    )

    # Compact, rolling memory update (bounded length).
    memory_update = None
    if event_types or inp.new_instructions:
        note = f"- {inp.trigger}: events={event_types or 'none'}, instr={inp.new_instructions or 'none'}"
        memory_update = (inp.memory_summary + "\n" + note).strip()[-1000:]

    # Sleep shorter while actively handling events, longer when idle.
    sleep_for = 20 if event_types else 60

    # The stub never *forces* completion; terminal handling is the workflow's job.
    return AgentDecision(
        reasoning=reasoning,
        actions=actions,
        memory_update=memory_update,
        sleep_for_seconds=sleep_for,
        complete_recommendation=False,
    )


@activity.defn
async def build_final_summary(inp: AgentStepInput) -> dict:
    """STUB end-of-run summary. Stage 4 replaces this with an LLM final step."""
    return {
        "final_summary": f"[stub] Run for order {inp.order_id} ended (trigger={inp.trigger}).",
        "important_actions": [],
        "key_learnings": ["Stubbed summary; real learnings arrive in Stage 4."],
        "feedback": "Stub feedback.",
        "memory_summary": inp.memory_summary,
    }
