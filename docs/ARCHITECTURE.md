# Architecture Note — Order Supervisor

This note explains *why* the system is shaped the way it is. It maps directly to
the grading priorities in [../CLAUDE.md](../CLAUDE.md): Temporal usage,
long-running workflow modeling, signal handling, and agent-orchestration quality.
For setup and how to run it, see [../README.md](../README.md).

---

## 1. The core idea

Watching an order from creation to completion is a **long-running, mostly-idle,
event-driven** process. It may span days, spend almost all of that time waiting,
and must react promptly when something important happens (payment fails, shipment
delays, customer writes in). That is exactly the shape Temporal is built for, so
the whole design falls out of one decision:

> **One durable Temporal workflow instance per order** — `id = order-{order_id}` —
> is the single source of truth for that order's supervision. It holds the state,
> owns the lifecycle, and decides when to think.

Everything else (API, DB, LLM, UI) is a satellite of that workflow.

## 2. Determinism boundary — the non-negotiable rule

Temporal replays workflow code to recover state, so workflow code must be
**deterministic**: no clocks, no randomness, no network, no DB. We keep a hard
line:

- **Inside the workflow** (`temporal/workflows.py`): only pure logic, in-memory
  state, `workflow.now()`, `workflow.wait_condition`, and
  `workflow.execute_activity(...)`. The wake **classifier** (`policy.py`) is pure
  and dependency-free, so it's safe to call inline without an activity round-trip.
- **Outside the workflow, in activities** (`temporal/activities.py`): *all* real
  I/O — the LLM calls (`agent_decide`, `build_final_summary`) and every Postgres
  write (`persist_step`). Activities are retried with a `RetryPolicy` and bounded
  by a `start_to_close_timeout`.

The workflow imports its activity dependencies under
`workflow.unsafe.imports_passed_through()` and never touches `asyncpg` or the LLM
client itself. If a change ever needs `await db...` or `await llm...` inside the
workflow class, that's the signal it belongs in an activity instead.

## 3. Lifecycle: signal-with-start, one ingestion path

Every order event — **including the very first** — enters through the same door:

```python
await client.start_workflow(
    OrderSupervisorWorkflow.run,
    run_input,
    id=f"order-{order_id}",
    task_queue="order-supervisor",
    start_signal="submit_event",
    start_signal_args=[order_created_event],
)
```

`start_or_signal_event` (`temporal/client.py`) is called identically by
`POST /api/runs` (run creation) and `POST /api/runs/{id}/events` (later events).
There is **no "create vs. signal" branching** in the API layer — signal-with-start
means the first `order_created` both *creates* the workflow and is *delivered* as
its first signal. Idempotent on the workflow id: a second start for a live order
is just another signal.

**Guard:** signalling a workflow that has already closed would start a *new*
execution. The API prevents that — the five signal endpoints load the run and
return **409** if its status is terminal (`completed`/`terminated`/`failed`).

## 4. The main loop — sleep and wake without polling

The heart of the assignment. The workflow runs the agent on **start**, then loops,
sleeping on `workflow.wait_condition` between three trigger types. It never does a
`while True: sleep(short)` poll.

```python
self._enter_sleep()
woke_on_signal = True
try:
    await workflow.wait_condition(self._should_wake, timeout=remaining)
except asyncio.TimeoutError:
    woke_on_signal = False   # the scheduled wake-up timer fired — expected, not an error
self._exit_sleep()
```

`wait_condition` compiles to a **Temporal timer** plus a predicate over signal
state. Two ways out, both first-class:

1. **Scheduled wake** — the timer fires (`TimeoutError`). Time comes only from
   `workflow.now()`; `_seconds_until_wake()` / `_clamp_sleep()` compute the
   duration, clamped to `[1s, remaining time before max age]`.
2. **Signal wake** — `_should_wake()` becomes true because a signal arrived
   (event / instruction / interrupt / terminate). The timer is **cancelled early**.

In the Temporal Web UI this cycle is visible as
`TIMER_STARTED → TIMER_FIRED` (scheduled) or `TIMER_STARTED → TIMER_CANCELED`
(signal), interleaved with `WORKFLOW_EXECUTION_SIGNALED` and
`ACTIVITY_TASK_COMPLETED`. The `demo` CLI subcommand prints exactly these to prove
it end-to-end.

### The three inference triggers

| Trigger | Cause | Handling |
| --- | --- | --- |
| **start** | workflow created | run agent once, establish a plan, then sleep |
| **signal** | important event/instruction wakes it early | classifier decides → run agent or log-only |
| **scheduled** | sleep timer fires | run agent (re-evaluate, chase stale orders) |

## 5. Classifier before the full agent

Not every signal deserves an LLM call. `policy.classify()` is a rule-based
allow/deny list (per CLAUDE.md rule 5 — an LLM classifier would be gold-plating
for a POC):

- **Always wake:** `order_created`, `payment_failed`, `shipment_delayed`,
  `delivered`, `refund_requested`, `customer_message_received`,
  `no_update_for_n_hours`.
- **Log-only** (record, keep sleeping): `payment_confirmed`, `shipment_created`.
- **Unknown types:** wake, to be safe (escalate the unexpected).
- Tunable per supervisor via `wake_policy` (`mode: aggressive`, or override the
  wake/log sets).

When a signal wakes the loop, `_handle_signal_wake()` drains the queued events,
runs the classifier over them, and only calls `agent_decide` if at least one is a
wake event (or a new instruction arrived). Otherwise it logs the classifier's
decision and the existing scheduled wake still stands — cheap signals stay cheap.

## 6. The agent step — structured output only

`agent_decide` (activity → `agent.decide`) calls the LLM through an
OpenAI-compatible client (Groq by default) using **forced tool/function calling**.
The model must return a structured decision — we `json.loads` the tool arguments;
**no regex parsing of free text** (CLAUDE.md rule 6):

```jsonc
{
  "reasoning": "...",
  "actions": [{ "type": "message_payments_team", "params": { ... } }],
  "memory_update": "compact rolling summary of the order so far",
  "sleep_for_seconds": 120,
  "complete_recommendation": false
}
```

The agent is given the base instruction, current compact memory, the new
events/instructions, and the **last ~10 timeline entries** as context — enough to
act coherently without unbounded prompt growth.

**Actions are simulated.** The five actions (`message_fulfillment_team`,
`message_payments_team`, `message_logistics_team`, `message_customer`,
`create_internal_note`) don't call anything external — executing an action *is*
recording it to `activity_log`, per the brief. This keeps the POC self-contained
while still exercising the full decide → act → persist path.

## 7. Completion is gated by workflow code, never the LLM

The agent may *recommend* completion, but only the workflow ends the run
(CLAUDE.md rule 4). `complete_recommendation: true` is recorded to the timeline
and otherwise ignored. The run terminates only when one of these is independently
true in workflow code:

1. a **terminal event type** arrived — `delivered` (see `TERMINAL_EVENT_TYPES`);
2. a **terminate signal** was received; or
3. **max age** exceeded — `workflow.now() - start_time ≥ max_age_seconds`.

On termination the workflow runs one last activity, `build_final_summary`, to
produce the **final summary with learnings and feedback**, persists it, and sets
status to `completed` (natural) or `terminated` (operator-ended). This satisfies
the acceptance criterion "produces a final summary … on completion."

## 8. Memory & timeline

Two complementary records, both owned by the workflow:

- **Compact memory** (`memory_summary`): a single rolling string the agent
  rewrites each step (`memory_update`). Bounded by construction — it's a summary,
  not an append log — so the prompt never grows without limit. This is the
  agent's working state across the (possibly days-long) run.
- **Timeline** (`activity_log`): the full, append-only audit trail. Every event,
  wake/sleep decision, agent action, instruction, and the final output is one row
  with a `type` (`event | agent_action | wake_decision | sleep_decision |
  instruction | final_output`). **One table for everything**, per the brief — no
  separate messages table.

**Persistence pattern:** the workflow holds the timeline in memory and **flushes
incrementally**. `_flush()` sends only entries above a `_persisted_seq`
high-water mark plus the current run patch (status, memory, `next_wake_at`, final
summary) to `persist_step` in one call. So the API can serve status/timeline/
memory straight from Postgres — reads never hit Temporal. One subtlety: the paused
status is flushed *before* the interrupt `wait_condition` blocks, otherwise the DB
would never observe `interrupted` (the wait only returns on resume).

## 9. Reads vs. writes (CQRS-lite)

A clean split keeps the API thin and the workflow authoritative:

- **Writes** → Temporal signals only (start / event / instruction / interrupt /
  resume / terminate). The API never mutates order state in Postgres directly.
- **Reads** → Postgres only (`GET /runs`, `GET /runs/{id}`). Fed by the
  workflow's `persist_step` flushes. A `get_state` **query** on the workflow also
  exists (used by the CLI) for a live, un-persisted view.

## 10. Data model

```sql
supervisors(id, name, base_instruction, tools_enabled jsonb,
            wake_policy jsonb, model_config jsonb, created_at)
runs(id, supervisor_id, order_id, workflow_id UNIQUE, status,
     memory_summary, next_wake_at, final_summary jsonb, created_at, updated_at)
activity_log(id, run_id, type, payload jsonb, created_at)   -- the one log table
```

A supervisor is a **reusable config** (instruction + wake policy + model config);
a run is **one order under one supervisor**, 1:1 with a workflow via the unique
`workflow_id`. (`model_config` is aliased to `configuration` in the Pydantic
schema — `model_config` is a reserved attribute name in Pydantic v2.)

## 11. Testing strategy

Tests are hermetic and fast (`backend/tests/`): no Docker, no network, no real
LLM. They run against Temporal's **time-skipping** `WorkflowEnvironment` and
register **mock activities under the same names** the workflow calls. Because
Temporal dispatches activities by name, the workflow is exercised unchanged while
sleep timers are skipped instantly — so the full start → scheduled-wake →
signal-wake → terminal-completion cycle is verified deterministically in
milliseconds. `policy.py` is unit-tested directly.

## 12. Tradeoffs & deliberate scope cuts

Per CLAUDE.md's "explicit scope cuts," these were **not** built — each is zero
evaluation value and real time cost for a 1–2 day POC:

| Cut | Why it's fine here | What it'd take in production |
| --- | --- | --- |
| Real commerce/messaging integrations | Actions are simulated to `activity_log` — the orchestration is the point | Real activities calling Stripe/Shippo/email, with their own retries/idempotency |
| Auth & multi-tenant hardening | Local POC, permissive CORS | AuthN/Z, per-tenant task queues / namespaces, rate limits |
| RAG / retrieval memory | Compact rolling summary is enough for one order | Vector store + retrieval for long histories |
| Multiple cooperating agents | Single supervisor meets the brief | Child workflows per concern, coordinated via signals |
| LLM-based classifier | Rule list is deterministic, free, and sufficient | LLM/heuristic classifier with confidence + cost budgeting |
| Polished UI | Explicitly de-prioritised for Temporal correctness | Real design system, optimistic updates, websockets over polling |

Other conscious choices:

- **UI polls** `GET /runs/{id}` every ~2s instead of streaming. Simple, robust,
  good enough at POC scale; a websocket/SSE push is the obvious upgrade.
- **Groq free tier** as the default LLM (any OpenAI-compatible endpoint works via
  three env vars) — $0 to run and review, no vendor lock-in in the code.
- **Incremental flush** rather than persisting per-entry — one activity call per
  wake cycle keeps DB writes and history noise down.

## 13. Where each acceptance criterion is satisfied

| Criterion | Where |
| --- | --- |
| One workflow started per order | `client.py` signal-with-start, `id=order-{order_id}` |
| Order events delivered as signals | `submit_event` signal; same path for first + subsequent |
| Agent wakes on start, signal, and schedule | `workflows.py` main loop (§4) |
| Agent sleeps and wakes later | `workflow.wait_condition` + timer (§4) |
| 5 required actions, each an activity record | agent actions → `LOG_AGENT_ACTION` rows via `persist_step` (§6) |
| UI shows events, actions, timeline, memory | run-detail page: timeline + compact memory panels |
| UI can inject events & instructions live | Event generator + Add instruction panels → signal endpoints |
| Final summary with learnings on completion | `_finalize()` → `build_final_summary` → `final_output` row (§7) |
