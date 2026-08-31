"""The approvals-service Temporal worker: a long-running process, not an HTTP server.

First non-HTTP-server container in the compose stack — it owns the same Postgres engine an
activity needs (to persist a workflow's outcome) but has no readiness/health HTTP endpoint of its
own, since the Temporal SDK's own worker loop is the thing keeping it alive.
"""

from __future__ import annotations

import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from platform_core.db import Database
from platform_core.logging import configure_logging, get_logger

from .activities import Activities
from .config import Settings
from .temporal_workflow import ApprovalWorkflow

logger = get_logger(__name__)


async def run_worker() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    db = Database(settings.database_url)
    activities = Activities(db)

    client = await Client.connect(settings.temporal_address)
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[ApprovalWorkflow],
        activities=[activities.persist_outcome],
    )
    logger.info(
        "approvals_worker_starting",
        task_queue=settings.temporal_task_queue,
        temporal_address=settings.temporal_address,
    )
    async with worker:
        await asyncio.Event().wait()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
