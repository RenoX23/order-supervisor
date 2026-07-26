"""Temporal activities — the only place real I/O happens.

- ``agent_decide`` / ``build_final_summary`` call the LLM (Groq by default) via the
  agent module and return **structured** output.
- ``persist_step`` writes new timeline entries to ``activity_log`` and patches the
  ``runs`` row in one transaction.

The deterministic workflow invokes all three through ``execute_activity`` and never
imports these dependencies itself.
"""

from __future__ import annotations

from temporalio import activity

from app import persistence
from app.agent import agent
from app.temporal.shared import AgentDecision, AgentStepInput, PersistStepInput


@activity.defn
async def agent_decide(inp: AgentStepInput) -> AgentDecision:
    """Call the LLM for one reasoning step; returns a structured decision."""
    return await agent.decide(inp)


@activity.defn
async def build_final_summary(inp: AgentStepInput) -> dict:
    """Call the LLM to produce the end-of-run summary/learnings/feedback."""
    return await agent.summarize(inp)


@activity.defn
async def persist_step(inp: PersistStepInput) -> None:
    """Append new timeline rows and patch the run row (no-op without a run_id)."""
    if not inp.run_id:
        return
    await persistence.append_activity_logs(inp.run_id, inp.entries)
    await persistence.update_run(
        inp.run_id,
        memory_summary=inp.memory_summary,
        status=inp.status,
        next_wake_at=inp.next_wake_at,
        final_summary=inp.final_summary,
    )
