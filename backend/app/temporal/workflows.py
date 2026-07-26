"""The long-running Order Supervisor workflow.

One workflow instance per order (id = ``order-{order_id}``). It is a strictly
deterministic Temporal workflow: it performs **no** I/O directly — every LLM call
or side effect goes through an activity. Its job is to model the order's lifecycle
and orchestrate three inference triggers:

    1. workflow start
    2. an incoming event / signal the classifier deems important
    3. a scheduled wake-up

between which it *sleeps* on ``workflow.wait_condition`` rather than polling.

Completion is workflow-owned (a terminal event, a terminate signal, or max age) —
never decided by the agent alone.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.temporal import policy
    from app.temporal.activities import agent_decide, build_final_summary, persist_step
    from app.temporal.shared import (
        LOG_AGENT_ACTION,
        LOG_EVENT,
        LOG_FINAL_OUTPUT,
        LOG_INSTRUCTION,
        LOG_SLEEP_DECISION,
        LOG_WAKE_DECISION,
        TERMINAL_EVENT_TYPES,
        AgentStepInput,
        OrderEvent,
        PersistStepInput,
        RunInput,
        TimelineEntry,
        WorkflowStateView,
    )

# Fallbacks when the supervisor's wake_policy doesn't specify them.
DEFAULT_SLEEP_SECONDS = 60
DEFAULT_MAX_AGE_SECONDS = 24 * 60 * 60  # 24h
MIN_SLEEP_SECONDS = 1

_ACTIVITY_RETRY = RetryPolicy(maximum_attempts=3)
_ACTIVITY_TIMEOUT = timedelta(seconds=30)


@workflow.defn
class OrderSupervisorWorkflow:
    def __init__(self) -> None:
        self._input: RunInput | None = None
        self._start_time: datetime | None = None

        # Inbound queues / run context.
        self._pending_events: list[OrderEvent] = []
        self._instructions: list[str] = []
        self._processed_instructions: int = 0

        # Timeline + compact memory.
        self._timeline: list[TimelineEntry] = []
        self._seq: int = 0
        self._persisted_seq: int = 0  # high-water mark flushed to Postgres
        self._memory_summary: str = ""
        self._last_reasoning: str | None = None

        # Lifecycle flags.
        self._status: str = "running"
        self._sleeping: bool = False
        self._interrupted: bool = False
        self._terminate_requested: bool = False
        self._terminal: bool = False
        self._completion_reason: str | None = None
        self._final_summary: dict | None = None

        # Scheduling.
        self._sleep_seconds: int = DEFAULT_SLEEP_SECONDS
        self._max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS
        self._next_wake_at: datetime | None = None

    # ── main loop ─────────────────────────────────────────────────────────────
    @workflow.run
    async def run(self, run_input: RunInput) -> dict:
        self._apply_input(run_input)
        self._start_time = workflow.now()
        self._log(LOG_EVENT, {"trigger": "workflow_start", "order_id": self._input.order_id})

        # Give signal-with-start's first event (order_created) a moment to be
        # delivered so the start step reasons about it. Returns immediately once
        # the event is queued; the short timeout only matters if none arrives.
        try:
            await workflow.wait_condition(
                lambda: bool(self._pending_events), timeout=timedelta(seconds=1)
            )
        except asyncio.TimeoutError:
            pass

        # Trigger 1 — run the agent on start.
        await self._run_agent("start")
        await self._flush()

        # Triggers 2 & 3 — important signals and scheduled wake-ups.
        while not self._terminal and not self._terminate_requested:
            if self._interrupted:
                await self._wait_while_paused()
                await self._flush()
                continue

            if self._exceeded_max_age():
                self._mark_terminal("max_age_reached")
                break

            remaining = self._seconds_until_wake()
            if remaining <= 0:
                await self._run_agent("scheduled")
                await self._flush()
                continue

            self._enter_sleep()
            woke_on_signal = True
            try:
                await workflow.wait_condition(
                    self._should_wake, timeout=timedelta(seconds=remaining)
                )
            except asyncio.TimeoutError:
                woke_on_signal = False  # the scheduled wake-up fired
            self._exit_sleep()

            if self._terminate_requested or self._terminal:
                break
            if self._interrupted:
                continue  # handle the pause at the top of the loop

            if woke_on_signal:
                await self._handle_signal_wake()
            else:
                await self._run_agent("scheduled")
            await self._flush()

        await self._finalize()
        await self._flush()
        return self._final_summary or {}

    # ── signals ───────────────────────────────────────────────────────────────
    @workflow.signal
    def submit_event(self, event: OrderEvent) -> None:
        self._pending_events.append(event)
        self._log(LOG_EVENT, {"type": event.type, "payload": event.payload})

    @workflow.signal
    def add_instruction(self, instruction: str) -> None:
        self._instructions.append(instruction)
        self._log(LOG_INSTRUCTION, {"instruction": instruction})

    @workflow.signal
    def interrupt(self) -> None:
        self._interrupted = True
        self._log(LOG_WAKE_DECISION, {"control": "interrupt"})

    @workflow.signal
    def resume(self) -> None:
        self._interrupted = False
        self._log(LOG_WAKE_DECISION, {"control": "resume"})

    @workflow.signal
    def terminate(self, reason: str = "") -> None:
        self._terminate_requested = True
        self._completion_reason = reason or "manual_terminate"
        self._log(LOG_WAKE_DECISION, {"control": "terminate", "reason": self._completion_reason})

    # ── query ─────────────────────────────────────────────────────────────────
    @workflow.query
    def get_state(self) -> WorkflowStateView:
        return WorkflowStateView(
            run_id=self._input.run_id if self._input else "",
            order_id=self._input.order_id if self._input else "",
            supervisor_name=self._input.supervisor.name if self._input else "",
            status=self._status,
            sleeping=self._sleeping,
            interrupted=self._interrupted,
            terminal=self._terminal,
            memory_summary=self._memory_summary,
            next_wake_at=self._next_wake_at.isoformat() if self._next_wake_at else None,
            pending_event_count=len(self._pending_events),
            instruction_count=len(self._instructions),
            completion_reason=self._completion_reason,
            last_reasoning=self._last_reasoning,
            final_summary=self._final_summary,
            timeline=list(self._timeline),
        )

    # ── input / scheduling helpers ──────────────────────────────────────────────
    def _apply_input(self, run_input: RunInput) -> None:
        self._input = run_input
        wp = run_input.supervisor.wake_policy or {}
        self._sleep_seconds = int(wp.get("default_sleep_seconds", DEFAULT_SLEEP_SECONDS))
        self._max_age_seconds = int(wp.get("max_age_seconds", DEFAULT_MAX_AGE_SECONDS))

    def _should_wake(self) -> bool:
        return (
            bool(self._pending_events)
            or self._has_new_instructions()
            or self._interrupted
            or self._terminate_requested
            or self._terminal
        )

    def _has_new_instructions(self) -> bool:
        return len(self._instructions) > self._processed_instructions

    def _seconds_until_wake(self) -> float:
        if self._next_wake_at is None:
            return 0.0
        return (self._next_wake_at - workflow.now()).total_seconds()

    def _exceeded_max_age(self) -> bool:
        if self._start_time is None:
            return False
        return (workflow.now() - self._start_time).total_seconds() >= self._max_age_seconds

    def _enter_sleep(self) -> None:
        self._sleeping = True
        self._status = "sleeping"

    def _exit_sleep(self) -> None:
        self._sleeping = False
        self._status = "running"

    async def _wait_while_paused(self) -> None:
        self._status = "interrupted"
        self._sleeping = False
        # Persist the paused status BEFORE blocking, otherwise the API (which
        # reads Postgres) would never observe "interrupted" — the wait below only
        # returns on resume, by which point the status is back to "running".
        await self._flush()
        await workflow.wait_condition(
            lambda: not self._interrupted or self._terminate_requested or self._terminal
        )
        if not (self._terminate_requested or self._terminal):
            self._status = "running"

    # ── agent step ──────────────────────────────────────────────────────────────
    async def _handle_signal_wake(self) -> None:
        events = self._drain_events()
        new_instructions = self._drain_instructions()

        wake = bool(new_instructions) or any(
            policy.classify(e.type, self._input.supervisor.wake_policy).wake for e in events
        )
        if wake:
            await self._run_agent("signal", events=events, new_instructions=new_instructions)
        else:
            # Not important enough — log the classifier's decision, stay asleep
            # (the same scheduled wake-up still stands).
            for e in events:
                a = policy.classify(e.type, self._input.supervisor.wake_policy)
                self._log(LOG_WAKE_DECISION, {"type": e.type, "woke_agent": False, "reason": a.reason})

    def _drain_events(self) -> list[OrderEvent]:
        events = list(self._pending_events)
        self._pending_events.clear()
        return events

    def _drain_instructions(self) -> list[str]:
        new = self._instructions[self._processed_instructions:]
        self._processed_instructions = len(self._instructions)
        return new

    async def _run_agent(
        self,
        trigger: str,
        events: list[OrderEvent] | None = None,
        new_instructions: list[str] | None = None,
    ) -> None:
        if events is None:
            events = self._drain_events()
        if new_instructions is None:
            new_instructions = self._drain_instructions()

        self._status = "running"
        self._log(
            LOG_WAKE_DECISION,
            {
                "trigger": trigger,
                "woke_agent": True,
                "event_types": [e.type for e in events],
                "new_instructions": new_instructions,
            },
        )

        step_input = AgentStepInput(
            trigger=trigger,
            order_id=self._input.order_id,
            base_instruction=self._input.supervisor.base_instruction,
            memory_summary=self._memory_summary,
            events=events,
            new_instructions=new_instructions,
            recent_timeline=[{"type": t.type, "payload": t.payload} for t in self._timeline[-10:]],
            tools_enabled=list(self._input.supervisor.tools_enabled),
        )

        decision = await workflow.execute_activity(
            agent_decide,
            step_input,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=_ACTIVITY_RETRY,
        )

        self._last_reasoning = decision.reasoning
        self._log(LOG_AGENT_ACTION, {"reasoning": decision.reasoning})

        for action in decision.actions:
            # Each simulated action (message_*, create_internal_note) is recorded to
            # the timeline and flushed to activity_log via persist_step — that IS the
            # action, per the brief (nothing external is called).
            self._log(LOG_AGENT_ACTION, {"action_type": action.type, "params": action.params})

        if decision.memory_update:
            self._memory_summary = decision.memory_update

        # Completion is workflow-gated: record the recommendation but never let it
        # end the run on its own.
        if decision.complete_recommendation:
            self._log(
                LOG_SLEEP_DECISION,
                {"complete_recommendation": True, "note": "recorded; completion is workflow-gated"},
            )

        # Workflow-owned terminal rule: a terminal event type was seen this step.
        terminal_event = next((e for e in events if e.type in TERMINAL_EVENT_TYPES), None)
        if terminal_event:
            self._mark_terminal(f"terminal_event:{terminal_event.type}")

        # Schedule the next wake-up.
        self._sleep_seconds = self._clamp_sleep(decision.sleep_for_seconds)
        self._next_wake_at = workflow.now() + timedelta(seconds=self._sleep_seconds)
        self._log(
            LOG_SLEEP_DECISION,
            {"sleep_for_seconds": self._sleep_seconds, "next_wake_at": self._next_wake_at.isoformat()},
        )

    def _clamp_sleep(self, requested: int | None) -> int:
        seconds = int(requested) if requested else self._sleep_seconds
        seconds = max(MIN_SLEEP_SECONDS, seconds)
        # Never sleep past the max workflow age.
        if self._start_time is not None:
            age = (workflow.now() - self._start_time).total_seconds()
            seconds = min(seconds, max(MIN_SLEEP_SECONDS, int(self._max_age_seconds - age)))
        return seconds

    def _mark_terminal(self, reason: str) -> None:
        self._terminal = True
        if self._completion_reason is None:
            self._completion_reason = reason

    async def _finalize(self) -> None:
        self._sleeping = False
        step_input = AgentStepInput(
            trigger="finalize",
            order_id=self._input.order_id,
            base_instruction=self._input.supervisor.base_instruction,
            memory_summary=self._memory_summary,
            recent_timeline=[{"type": t.type, "payload": t.payload} for t in self._timeline[-20:]],
        )
        summary = await workflow.execute_activity(
            build_final_summary,
            step_input,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=_ACTIVITY_RETRY,
        )
        self._final_summary = summary
        self._log(LOG_FINAL_OUTPUT, summary)
        self._status = "terminated" if self._terminate_requested else "completed"

    # ── persistence ─────────────────────────────────────────────────────────────
    async def _flush(self) -> None:
        """Persist newly-added timeline entries and the current run patch.

        Batches everything since the last flush into one activity call. A no-op
        without a run_id (e.g. direct/script starts) — persistence is opt-in on
        the run row created by the API/CLI.
        """
        new_entries = [t for t in self._timeline if t.seq > self._persisted_seq]
        self._persisted_seq = self._seq
        if not self._input or not self._input.run_id:
            return
        await workflow.execute_activity(
            persist_step,
            PersistStepInput(
                run_id=self._input.run_id,
                entries=new_entries,
                memory_summary=self._memory_summary,
                status=self._status,
                next_wake_at=self._next_wake_at.isoformat() if self._next_wake_at else None,
                final_summary=self._final_summary,
            ),
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=_ACTIVITY_RETRY,
        )

    # ── timeline ────────────────────────────────────────────────────────────────
    def _log(self, entry_type: str, payload: dict) -> None:
        self._seq += 1
        self._timeline.append(
            TimelineEntry(seq=self._seq, ts=workflow.now().isoformat(), type=entry_type, payload=payload)
        )
