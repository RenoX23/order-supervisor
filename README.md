# Order Supervisor

A long-running AI supervisor that watches a single e-commerce order from creation
to completion, built on **Temporal**. One durable workflow per order reacts to
events, decides when to act, executes business actions, sleeps, and wakes on a
schedule or on important signals — producing a final summary with learnings when
the order reaches a terminal state.

Built as a POC for a Sagepilot AI take-home. See [CLAUDE.md](CLAUDE.md) for the
full spec and the non-negotiable architecture rules.

## Stack

| Layer          | Choice                                  |
| -------------- | --------------------------------------- |
| Orchestration  | Temporal (Python SDK, `temporalio`)     |
| Backend / API  | FastAPI                                  |
| Database       | PostgreSQL                              |
| Frontend       | Next.js (App Router) + Tailwind *(later stage)* |
| LLM            | Claude (structured / tool output) *(later stage)* |

## Repository layout

```
order-supervisor/
├── docker-compose.yml     # Postgres + Temporal dev server (Web UI on :8233)
├── .env.example           # copy to .env
├── backend/
│   ├── requirements.txt
│   ├── app/
│   │   ├── config.py      # settings (single source of truth)
│   │   └── db.py          # asyncpg connection pool
│   └── db/
│       ├── migrate.py     # forward-only migration runner
│       └── migrations/
│           └── 0001_init.sql
└── frontend/              # Next.js UI (added in a later stage)
```

## Prerequisites

- Docker + Docker Compose
- Python 3.11+
- Node.js 18+ (for the frontend, later)

## Setup

### 1. Configure environment

```bash
cp .env.example .env
```

The defaults work out of the box. Postgres is published on host port **5442**
(to avoid clashing with a local Postgres on 5432).

### 2. Start infrastructure

```bash
docker compose up -d
```

This starts:
- **Postgres** on `localhost:5442`
- **Temporal dev server** — gRPC on `localhost:7233`, Web UI on
  <http://localhost:8233>

### 3. Install backend dependencies

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate     |     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Run database migrations

```bash
# from backend/, with the venv active
python db/migrate.py
```

You should see `applied  0001_init.sql`. Re-running is safe (idempotent).

---

*Running the API, worker, event generator, and UI is documented as those parts
are built.*
