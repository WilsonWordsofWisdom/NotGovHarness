"""The deployment-pipeline-service Temporal worker: a long-running process, not an HTTP server.
Owns the Docker socket (D-087) and the same Postgres engine an activity needs to persist a
deployment's outcome — same shape as Approvals/HITL's own worker.py.
"""

from __future__ import annotations

import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from platform_core.db import Database
from platform_core.events import Producer
from platform_core.llm_gateway_auth import TokenClient
from platform_core.logging import configure_logging, get_logger

from .activities import Activities
from .config import Settings
from .temporal_workflow import DeploymentWorkflow

logger = get_logger(__name__)


async def run_worker() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    db = Database(settings.database_url)
    token_client = TokenClient(
        identity_url=settings.identity_url,
        client_id=settings.deployment_client_id,
        client_secret=settings.deployment_client_secret,
        scope="evals:run",
    )
    producer = Producer(settings.kafka_brokers)
    await producer.start()
    activities = Activities(settings, db, token_client, producer)

    client = await Client.connect(settings.temporal_address)
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[DeploymentWorkflow],
        activities=[
            activities.fetch_agent_config,
            activities.run_container,
            activities.wait_healthy,
            activities.run_eval_gate,
            activities.stop_container,
            activities.persist_outcome,
            activities.emit_deployment_event,
        ],
    )
    logger.info(
        "deployment_worker_starting",
        task_queue=settings.temporal_task_queue,
        temporal_address=settings.temporal_address,
    )
    try:
        async with worker:
            await asyncio.Event().wait()
    finally:
        await producer.stop()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
