"""Temporal worker: hosts the OrderSupervisorWorkflow and its activities.

Run it (from ``backend/``, venv active, ``docker compose up -d`` first):

    python -m app.temporal.worker
"""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from app.config import settings
from app.db import close_pool
from app.temporal.activities import agent_decide, build_final_summary, persist_step
from app.temporal.workflows import OrderSupervisorWorkflow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("order-supervisor.worker")


async def main() -> None:
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[OrderSupervisorWorkflow],
        activities=[agent_decide, build_final_summary, persist_step],
    )
    log.info(
        "Worker connected to %s (ns=%s); polling task queue '%s'. Ctrl+C to stop.",
        settings.temporal_address,
        settings.temporal_namespace,
        settings.temporal_task_queue,
    )
    try:
        await worker.run()
    finally:
        await close_pool()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Worker stopped.")
