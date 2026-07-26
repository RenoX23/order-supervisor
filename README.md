# Order Supervisor

A long-running **AI supervisor** that watches a single e-commerce order from
creation to completion, built on **Temporal**. One durable workflow per order
reacts to events, decides when to act, executes business actions, sleeps, and
wakes on a schedule or on important signals — producing a final summary with
learnings when the order reaches a terminal state.

Built as a POC for a Sagepilot AI take-home. The full spec and the
non-negotiable architecture rules live in [CLAUDE.md](CLAUDE.md); the design
rationale (workflow model, signals, wake/sleep, memory, tradeoffs) is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Stack

| Layer          | Choice                                              |
| -------------- | --------------------------------------------------- |
| Orchestration  | Temporal (Python SDK, `temporalio`)                 |
| Backend / API  | FastAPI + asyncpg                                    |
| Database       | PostgreSQL                                           |
| Frontend       | Next.js (App Router) + Tailwind                      |
| LLM            | OpenAI-compatible client → **Groq** free tier by default (structured/tool output only) |

## Architecture at a glance

```
             signal-with-start                    execute_activity (I/O only here)
Next.js UI ─▶ FastAPI ─────────────▶ Temporal ─▶ OrderSupervisorWorkflow ─┬─▶ agent_decide ───▶ LLM (Groq)
   ▲            │                     (durable,    (deterministic:         ├─▶ build_final_summary ─▶ LLM
   │            │                      one wf        wait_condition         └─▶ persist_step ──▶ Postgres
   │            ▼                      per order)    sleep/wake loop)                                │
   └──── reads ── Postgres ◀──────────────────────────────────────────────────────────────────────┘
        (runs, activity_log, supervisors)
```

- **Writes** (start run, inject event, instruction, interrupt/resume/terminate)
  go to Temporal as **signals**. Every `/events` call — including the first —
  uses the same **signal-with-start** path.
- **Reads** (timeline, memory, status) come from **Postgres**, which the
  workflow keeps up to date through the `persist_step` activity.
- The **workflow itself does no I/O**. Every LLM call and DB write is an
  activity invoked via `workflow.execute_activity(...)`. See
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Repository layout

```
order-supervisor/
├── docker-compose.yml          # Postgres + Temporal dev server (Web UI on :8233)
├── .env.example                # copy to .env
├── CLAUDE.md                   # the spec + non-negotiable rules
├── docs/ARCHITECTURE.md        # design note
├── backend/
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py             # FastAPI app (lifespan wires the Temporal client)
│   │   ├── config.py           # settings (single source of truth)
│   │   ├── db.py               # asyncpg connection pool
│   │   ├── persistence.py      # raw-SQL reads/writes for runs & activity_log
│   │   ├── schemas.py          # API request/response models
│   │   ├── api/                # supervisors + runs routers
│   │   ├── agent/agent.py      # LLM agent (structured/tool output)
│   │   └── temporal/
│   │       ├── workflows.py    # OrderSupervisorWorkflow (deterministic)
│   │       ├── activities.py   # agent_decide, build_final_summary, persist_step
│   │       ├── policy.py       # rule-based wake classifier
│   │       ├── client.py       # signal-with-start helpers
│   │       ├── worker.py       # `python -m app.temporal.worker`
│   │       └── shared.py       # DTOs + constants shared by wf/activities
│   ├── scripts/supervisor_cli.py  # drive a run from the terminal (+ `demo`)
│   ├── db/migrate.py           # forward-only migration runner
│   └── tests/                  # hermetic tests (time-skipping env, mock activities)
└── frontend/                   # Next.js UI (supervisor config, runs, run detail)
```

## Prerequisites

- Docker + Docker Compose
- Python 3.11+
- Node.js 18+
- A free **Groq** API key — <https://console.groq.com> (or any
  OpenAI-compatible endpoint; see [Using a different LLM](#using-a-different-llm))

---

## Setup — from a clean checkout

Follow these in order. Steps 4–7 each run in their **own terminal** (the worker,
API, and UI are long-running).

### 1. Configure environment

```bash
cp .env.example .env
```

Then open `.env` and paste your Groq key into `LLM_API_KEY=`. Everything else
works out of the box. Postgres is published on host port **5442** (to avoid
clashing with a local Postgres on 5432).

```ini
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=gsk_your_key_here          # ← required; the agent step fails without it
LLM_MODEL=llama-3.3-70b-versatile      # ← must be set (a blank model is an error)
```

### 2. Start infrastructure

```bash
docker compose up -d
```

Starts **Postgres** on `localhost:5442` and the **Temporal dev server** — gRPC
on `localhost:7233`, Web UI on <http://localhost:8233>.

### 3. Install backend deps + run migrations

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate     |     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python db/migrate.py                    # first run: "applied  0001_init.sql"; re-runs are a no-op
```

### 4. Start the Temporal worker  *(terminal A)*

Hosts the workflow and its activities. **Nothing runs without this.**

```bash
# from backend/, venv active
python -m app.temporal.worker
# → Worker connected to localhost:7233 … polling task queue 'order-supervisor'.
```

### 5. Start the API  *(terminal B)*

```bash
# from backend/, venv active
uvicorn app.main:app --reload --port 8000
# health check: curl http://localhost:8000/api/health  →  {"status":"ok"}
```

### 6. Start the UI  *(terminal C)*

```bash
cd frontend
npm install
npm run dev            # → http://localhost:3000
```

The UI defaults to the API on `http://localhost:8000`. To point elsewhere, set
`NEXT_PUBLIC_API_URL` in `frontend/.env.local`.

### 7. Drive a run

**In the UI** (<http://localhost:3000>):

1. **Create a supervisor** (`/supervisors/new`) — name, base instruction, default
   sleep seconds.
2. **Start a run** with it → lands on the **run detail** page.
3. Use the **Event generator** panel to inject any of the 9 event types, or click
   **Simulate lifecycle** to fire `payment_confirmed → shipment_created →
   shipment_delayed → delivered` spaced out so the agent processes each step.
4. Add an **instruction** ("VIP customer — prioritise"), or
   **interrupt/resume/terminate** the live run.
5. Watch the **timeline**, **compact memory**, and (on completion) the **final
   summary** update live. Open the same workflow in the Temporal Web UI
   (<http://localhost:8233>) to see the signals, timers, and activities durably
   recorded.

**Or from the terminal** — the CLI drives the exact same signal paths and prints
Temporal history as evidence:

```bash
# from backend/, venv active, worker running
python -m scripts.supervisor_cli demo --order-id demo-001
```

`demo` runs a full scripted cycle: signal-with-start → sleep → **scheduled** wake
(timer fires) → **signal** wake (timer cancelled early by `payment_failed`) →
terminal `delivered` → workflow-gated completion + final summary, ending with the
Temporal event history that proves the sleep/wake cycle
(`TIMER_STARTED`/`TIMER_FIRED`/`TIMER_CANCELED`). Other subcommands: `start`,
`event`, `instruct`, `interrupt`, `resume`, `terminate`, `state`, `history`.

---

## Tests

Hermetic — no Docker, no network, no real LLM. They use Temporal's
time-skipping test environment and register **mock activities under the same
names**, so the workflow's sleep/wake logic is exercised deterministically.

```bash
# from backend/, venv active
pytest -q
```

## Using a different LLM

The agent talks to any **OpenAI-compatible** endpoint. Swap three env vars:

| Provider | `LLM_BASE_URL` | `LLM_MODEL` | `LLM_API_KEY` |
| --- | --- | --- | --- |
| **Groq** (default) | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` | your Groq key |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` | your OpenAI key |
| Ollama (local) | `http://localhost:11434/v1` | `llama3.1` | `ollama` |

The agent always requests **structured tool output** — no free-text parsing —
so any model with tool/function-calling support works.

## API surface

All under `/api`. Writes go through Temporal; reads come from Postgres.

```
POST /api/supervisors            GET /api/supervisors/{id}
POST /api/runs                   # start a run via signal-with-start
GET  /api/runs                   GET /api/runs/{run_id}        # status + timeline + memory
POST /api/runs/{run_id}/events         # inject an event  (signal-with-start)
POST /api/runs/{run_id}/instructions   # add an instruction (signal)
POST /api/runs/{run_id}/interrupt      POST /api/runs/{run_id}/resume
POST /api/runs/{run_id}/terminate      # graceful terminate signal (still summarises)
```

Signals to a run that has already ended return **409** (a stray event can't
resurrect a closed workflow).

## Notes / scope

This is a POC, deliberately scoped per the brief: no auth, no real
commerce/messaging integrations (the 5 actions are simulated — each is recorded
to `activity_log`), no multi-agent orchestration, no RAG memory. Frontend polish
was explicitly de-prioritised in favour of Temporal correctness. See the
**Tradeoffs** section of [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
