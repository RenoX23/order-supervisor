# Order Supervisor — Project Spec

A 1-2 day POC: a long-running AI supervisor that watches a single e-commerce
order from creation to completion. Built for a Sagepilot AI hiring take-home.
Graded primarily on Temporal usage, long-running workflow modeling, signal
handling, and agent orchestration quality — NOT on frontend polish. Keep that
priority order in every decision.

## Non-negotiable architecture rules

1. **Workflows must stay deterministic.** Never call an LLM API, hit
   Postgres, or do any real I/O directly inside a `@workflow.run` method or
   any workflow-defined method. All I/O — LLM calls, DB writes, "action"
   execution — goes through `@activity.defn` functions, invoked via
   `workflow.execute_activity(...)`. If you catch yourself writing
   `await anthropic_client...` or `await db.execute(...)` inside the workflow
   class, stop and move it into an activity.

2. **One workflow per order.** Workflow ID = `f"order-{order_id}"`. Use
   signal-with-start so the very first `order_created` event both creates the
   workflow and is delivered as its first signal:
   ```python
   await client.start_workflow(
       OrderSupervisorWorkflow.run,
       args=[order_context],
       id=f"order-{order_id}",
       task_queue="order-supervisor",
       start_signal="submit_event",
       start_signal_args=[order_created_event],
   )
   ```
   Do not special-case "create" vs "signal" in the API layer — every
   `/events` call for an order ID hits the same signal-with-start path.

3. **Sleep/wake via `workflow.wait_condition`, not polling.** Never write a
   `while True` + short `asyncio.sleep()` loop — that defeats the point of
   the assignment. The main loop looks like:
   ```python
   try:
       await workflow.wait_condition(
           lambda: self.pending_events or self.terminal,
           timeout=self.sleep_duration,
       )
   except asyncio.TimeoutError:
       pass  # woke on schedule, not on a signal — this is expected, not an error
   ```

4. **Completion is gated by code, not by the LLM.** The agent may return
   `complete_recommendation: true`, but the loop only exits when one of these
   is independently true, checked in workflow code:
   - a configured terminal event type has arrived (e.g. `delivered`, a fully
     processed `refund_requested`)
   - a `terminate` signal was received
   - `workflow.now() - start_time` exceeds a configured max age
   Never let the raw LLM output end the workflow by itself.

5. **Classifier before full agent.** Not every signal should wake the full
   reasoning step. A rule-based allow/deny list is enough for this POC —
   always wake on `payment_failed`, `shipment_delayed`, `refund_requested`,
   `customer_message_received`; log-only for benign updates like
   `shipment_created`. Only upgrade to an LLM classifier if Day 1 items are
   already done and time remains.

6. **Agent decisions are structured output, not free text.** The "agent
   step" activity calls an LLM and gets back structured JSON/tool-output:
   `{reasoning, actions: [{type, params}], memory_update, sleep_for_seconds,
   complete_recommendation}`. Never regex-parse free text out of the model.

## Tech stack

- Frontend: Next.js (App Router) + Tailwind
- Backend: FastAPI
- Orchestration: Temporal Python SDK (`temporalio`)
- DB: Postgres via Supabase
- LLM: Claude or OpenAI, structured/tool output only

## Data model (Postgres)

```sql
supervisors(id, name, base_instruction, tools_enabled jsonb,
            wake_policy jsonb, model_config jsonb, created_at)

runs(id, supervisor_id, order_id, workflow_id, status,
     memory_summary, next_wake_at, final_summary jsonb,
     created_at, updated_at)

activity_log(id, run_id, type, payload jsonb, created_at)
-- type is one of: event | agent_action | wake_decision |
--                  sleep_decision | instruction | final_output
```

One activity_log table for everything — do not build a separate messages
table, per the brief.

## Required actions (all simulated — write to activity_log, nothing external)

`message_fulfillment_team`, `message_payments_team`,
`message_logistics_team`, `message_customer`, `create_internal_note`

## Event types the generator must be able to fire

`order_created`, `payment_confirmed`, `payment_failed`, `shipment_created`,
`shipment_delayed`, `delivered`, `refund_requested`,
`customer_message_received`, `no_update_for_n_hours`

## API surface (FastAPI)

```
POST /api/supervisors
GET  /api/supervisors/{id}
POST /api/runs                        # starts workflow via signal-with-start
GET  /api/runs
GET  /api/runs/{run_id}               # status + timeline + memory, from Postgres
POST /api/runs/{run_id}/events        # signal
POST /api/runs/{run_id}/instructions  # signal
POST /api/runs/{run_id}/interrupt     # signal
POST /api/runs/{run_id}/resume        # signal
POST /api/runs/{run_id}/terminate     # signal (or real Temporal terminate call)
```

## Explicit scope cuts — do not build these

Real commerce/messaging integrations, auth, multi-tenant hardening,
retrieval/RAG memory, multiple cooperating agents, a polished UI. These cost
time and are worth zero evaluation points. If a prompt drifts toward one of
these, push back instead of building it.

## Build order — do not start the UI before Day 1 is proven working

**Day 1 — backend + workflow, no UI:**
1. Repo scaffold, Postgres schema + migrations
2. Workflow class: signals (`submit_event`, `add_instruction`, `interrupt`,
   `resume`, `terminate`), query (`get_state`), main loop with
   `wait_condition`, wired to a **stubbed** decision function
3. Prove the sleep → signal → wake → sleep cycle visibly in the Temporal Web
   UI (localhost:8233) before touching a real LLM call
4. Swap the stub for a real LLM call with structured output; wire the 5
   actions and memory/timeline persistence through activities
5. FastAPI endpoints wired to the Temporal client and Postgres reads

**Day 2 — UI + packaging:**
1. Next.js pages: supervisor config, start run, runs list, run detail
   (timeline, memory, inject event, add instruction,
   interrupt/resume/terminate)
2. Event generator panel
3. README + architecture note
4. Walkthrough video following the brief's own checklist, in that order

## Acceptance criteria (from the brief — this is the actual spec, not a summary)

- One Temporal workflow started per order
- Order events delivered as signals
- Agent wakes on start, signal, and scheduled wake-up
- Agent sleeps and wakes later
- Agent executes the 5 required actions, each stored as an activity record
- UI shows event history, action history, timeline, and compact memory
- UI can inject events and additional instructions into a live run
- Workflow produces a final summary with learnings and feedback on completion
