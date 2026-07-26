"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Request
from temporalio.client import Client


def get_temporal(request: Request) -> Client:
    """The Temporal client created once at app startup (see main.lifespan)."""
    return request.app.state.temporal
