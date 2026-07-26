"""The reasoning agent — the only place a real LLM is called.

Uses an OpenAI-compatible endpoint (Groq by default) with **tool/function
calling** so the model returns a structured decision, never free text that we
regex-parse (CLAUDE.md rule 6). This module is imported only by the Temporal
activity that runs it, so all network I/O stays out of the deterministic
workflow.
"""

from __future__ import annotations

import json

from openai import AsyncOpenAI

from app.config import settings
from app.temporal.shared import ACTION_TYPES, AgentAction, AgentDecision, AgentStepInput

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not settings.llm_api_key:
            raise RuntimeError(
                "LLM_API_KEY is not set. Add a (free) Groq key to .env as "
                "LLM_API_KEY=... before running the agent. See .env.example."
            )
        _client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=settings.llm_timeout_seconds,
            max_retries=2,
        )
    return _client


# ── decision tool schema (structured output) ─────────────────────────────────

# One-line description of each action, used to build the system prompt for
# whatever subset a supervisor has enabled.
_ACTION_DESCRIPTIONS = {
    "message_fulfillment_team": "coordinate order preparation.",
    "message_payments_team": "resolve payment problems (e.g. payment_failed).",
    "message_logistics_team": "chase shipping/delivery issues (e.g. shipment_delayed).",
    "message_customer": "proactively update or reassure the customer.",
    "create_internal_note": "record an observation for the audit trail.",
}


def _allowed_actions(inp: AgentStepInput) -> list[str]:
    """The actions this supervisor may take — its ``tools_enabled`` intersected
    with the known action set. Empty/unset means all actions are available."""
    allowed = [t for t in inp.tools_enabled if t in ACTION_TYPES]
    return allowed or list(ACTION_TYPES)


def _decision_tool(allowed: list[str]) -> dict:
    """Build the decision tool schema, constraining ``actions[].type`` to the
    supervisor's enabled actions so the model can only pick allowed tools."""
    return {
        "type": "function",
        "function": {
            "name": "submit_decision",
            "description": "Record your supervision decision for this step.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string",
                        "description": "Brief rationale for the actions and sleep choice.",
                    },
                    "actions": {
                        "type": "array",
                        "description": "Simulated actions to take now (may be empty).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": allowed},
                                "params": {
                                    "type": "object",
                                    "description": "Free-form details, e.g. a message body or note.",
                                },
                            },
                            "required": ["type"],
                        },
                    },
                    "memory_update": {
                        "type": "string",
                        "description": "The full, compact rolling memory summary to store going forward.",
                    },
                    "sleep_for_seconds": {
                        "type": "integer",
                        "description": "How long to sleep before the next scheduled check.",
                    },
                    "complete_recommendation": {
                        "type": "boolean",
                        "description": "Whether you believe the order is effectively resolved. "
                        "(The workflow — not you — decides when the run actually ends.)",
                    },
                },
                "required": ["reasoning", "actions", "sleep_for_seconds", "complete_recommendation"],
            },
        },
    }

_SUMMARY_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_summary",
        "description": "Record the final summary of the completed order run.",
        "parameters": {
            "type": "object",
            "properties": {
                "final_summary": {"type": "string", "description": "1-3 sentence summary of how the run went."},
                "key_learnings": {"type": "array", "items": {"type": "string"}},
                "feedback": {"type": "string", "description": "Feedback for improving future supervision."},
            },
            "required": ["final_summary", "key_learnings", "feedback"],
        },
    },
}


def _system_prompt(base_instruction: str, allowed: list[str]) -> str:
    action_lines = "\n".join(f"- {t}: {_ACTION_DESCRIPTIONS[t]}" for t in allowed)
    return (
        "You are an autonomous supervisor watching a single e-commerce order from "
        "creation to completion. You wake on order events and on a schedule, decide "
        "what to do, then sleep until the next check.\n\n"
        f"Supervisor instruction: {base_instruction}\n\n"
        "You may take ONLY these simulated actions (each is logged; nothing is really sent):\n"
        f"{action_lines}\n\n"
        "Guidance: act only when it helps; a routine check with nothing wrong needs no "
        "action. Keep the memory summary short and cumulative. Choose a sleep duration "
        "that fits the situation (shorter when actively handling a problem, longer when "
        "idle). You recommend completion, but the workflow decides when the run ends."
    )


def _step_user_message(inp: AgentStepInput) -> str:
    events = [{"type": e.type, "payload": e.payload} for e in inp.events]
    payload = {
        "trigger": inp.trigger,
        "order_id": inp.order_id,
        "new_events": events,
        "new_instructions": inp.new_instructions,
        "memory_summary_so_far": inp.memory_summary,
        "recent_timeline": inp.recent_timeline,
    }
    return (
        "Here is the current step context as JSON. Decide the actions to take now, "
        "an updated memory summary, and how long to sleep, then call submit_decision.\n\n"
        + json.dumps(payload, indent=2)
    )


def _extract_tool_args(message, tool_name: str) -> dict:
    tool_calls = getattr(message, "tool_calls", None) or []
    for call in tool_calls:
        if call.function.name == tool_name:
            return json.loads(call.function.arguments or "{}")
    # Fallback: some models return JSON in content; accept it defensively.
    if message.content:
        return json.loads(message.content)
    raise ValueError(f"model did not call {tool_name}")


# ── public API (called by the Temporal activity) ─────────────────────────────

async def decide(inp: AgentStepInput) -> AgentDecision:
    client = _get_client()
    allowed = _allowed_actions(inp)
    response = await client.chat.completions.create(
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        messages=[
            {"role": "system", "content": _system_prompt(inp.base_instruction, allowed)},
            {"role": "user", "content": _step_user_message(inp)},
        ],
        tools=[_decision_tool(allowed)],
        tool_choice={"type": "function", "function": {"name": "submit_decision"}},
    )
    args = _extract_tool_args(response.choices[0].message, "submit_decision")

    raw_actions = args.get("actions") or []
    # Belt-and-braces: the schema already constrains the enum, but filter the
    # returned actions against the supervisor's allowed set too.
    actions = [
        AgentAction(type=a["type"], params=a.get("params") or {})
        for a in raw_actions
        if a.get("type") in allowed
    ]
    sleep_for = args.get("sleep_for_seconds")
    return AgentDecision(
        reasoning=str(args.get("reasoning", "")),
        actions=actions,
        memory_update=args.get("memory_update"),
        sleep_for_seconds=int(sleep_for) if isinstance(sleep_for, (int, float)) else None,
        complete_recommendation=bool(args.get("complete_recommendation", False)),
    )


async def summarize(inp: AgentStepInput) -> dict:
    client = _get_client()
    response = await client.chat.completions.create(
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        messages=[
            {"role": "system", "content": _system_prompt(inp.base_instruction, _allowed_actions(inp))},
            {
                "role": "user",
                "content": (
                    "The run is ending. Produce a final summary, key learnings, and feedback "
                    "by calling submit_summary. Context JSON:\n\n"
                    + json.dumps(
                        {
                            "order_id": inp.order_id,
                            "reason": inp.trigger,
                            "memory_summary": inp.memory_summary,
                            "recent_timeline": inp.recent_timeline,
                        },
                        indent=2,
                    )
                ),
            },
        ],
        tools=[_SUMMARY_TOOL],
        tool_choice={"type": "function", "function": {"name": "submit_summary"}},
    )
    args = _extract_tool_args(response.choices[0].message, "submit_summary")
    return {
        "final_summary": str(args.get("final_summary", "")),
        "key_learnings": list(args.get("key_learnings") or []),
        "feedback": str(args.get("feedback", "")),
        "memory_summary": inp.memory_summary,
    }
