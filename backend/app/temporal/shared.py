"""Serializable data types shared between the workflow, its activities, and (in
later stages) the API layer.

These are plain dataclasses so Temporal's default JSON data converter can
round-trip them without extra configuration. Keep them free of behaviour and
free of non-deterministic defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# ── Event catalogue ───────────────────────────────────────────────────────────

# Every event type the generator can fire (see CLAUDE.md).
EVENT_TYPES: tuple[str, ...] = (
    "order_created",
    "payment_confirmed",
    "payment_failed",
    "shipment_created",
    "shipment_delayed",
    "delivered",
    "refund_requested",
    "customer_message_received",
    "no_update_for_n_hours",
)

# Event types that put the order into a terminal state. This is a WORKFLOW-owned
# completion rule — the agent never ends the run by itself (see CLAUDE.md rule 4).
TERMINAL_EVENT_TYPES: frozenset[str] = frozenset({"delivered"})

# ── activity_log.type values (mirror the DB CHECK constraint in 0001_init.sql) ─
LOG_EVENT = "event"
LOG_AGENT_ACTION = "agent_action"
LOG_WAKE_DECISION = "wake_decision"
LOG_SLEEP_DECISION = "sleep_decision"
LOG_INSTRUCTION = "instruction"
LOG_FINAL_OUTPUT = "final_output"


# ── Inputs ────────────────────────────────────────────────────────────────────

@dataclass
class OrderEvent:
    """An event delivered into the workflow as a signal payload."""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: Optional[str] = None  # ISO-8601, set by the caller/API


@dataclass
class SupervisorConfig:
    """A supervisor template's runtime config, passed into the workflow.

    ``model_config`` mirrors the DB column name verbatim; it is a plain dict here
    (these are dataclasses, not Pydantic models, so the name is safe).
    """

    name: str
    base_instruction: str
    tools_enabled: list[str] = field(default_factory=list)
    wake_policy: dict[str, Any] = field(default_factory=dict)
    model_config: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunInput:
    """Initial arguments for the workflow's ``run`` method."""

    order_id: str
    supervisor: SupervisorConfig
    order_context: dict[str, Any] = field(default_factory=dict)
    run_id: str = ""  # our Postgres run id; empty for direct/script starts


# ── Agent decision (structured output; stubbed in Stage 2, LLM in Stage 4) ────

@dataclass
class AgentAction:
    type: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentDecision:
    reasoning: str
    actions: list[AgentAction] = field(default_factory=list)
    memory_update: Optional[str] = None
    sleep_for_seconds: Optional[int] = None
    complete_recommendation: bool = False


@dataclass
class AgentStepInput:
    """Everything the decision activity needs for one reasoning step."""

    trigger: str  # start | signal | scheduled | finalize
    order_id: str
    base_instruction: str
    memory_summary: str
    events: list[OrderEvent] = field(default_factory=list)
    new_instructions: list[str] = field(default_factory=list)
    recent_timeline: list[dict[str, Any]] = field(default_factory=list)


# ── Timeline + queryable state ────────────────────────────────────────────────

@dataclass
class TimelineEntry:
    seq: int
    ts: str  # ISO-8601 (deterministic workflow time)
    type: str  # one of the LOG_* values above
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowStateView:
    """Snapshot returned by the ``get_state`` query."""

    run_id: str
    order_id: str
    supervisor_name: str
    status: str
    sleeping: bool
    interrupted: bool
    terminal: bool
    memory_summary: str
    next_wake_at: Optional[str]
    pending_event_count: int
    instruction_count: int
    completion_reason: Optional[str]
    last_reasoning: Optional[str]
    final_summary: Optional[dict[str, Any]]
    timeline: list[TimelineEntry] = field(default_factory=list)
