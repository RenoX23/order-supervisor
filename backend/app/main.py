"""FastAPI application.

Thin layer over the Temporal client (writes) and Postgres (reads). Run with:

    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import runs, supervisors
from app.db import close_pool
from app.temporal import client as tclient


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One Temporal client for the process; the asyncpg pool is created lazily.
    app.state.temporal = await tclient.get_client()
    try:
        yield
    finally:
        await close_pool()


app = FastAPI(title="Order Supervisor API", version="0.1.0", lifespan=lifespan)

# Permissive CORS for the local Next.js UI (POC only — no auth by design).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(supervisors.router, prefix="/api")
app.include_router(runs.router, prefix="/api")


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}
