"""deployment-pipeline-service: the last of all 16 harnesses. Packages a published, redeployable
Agent Card (D-088) into a real running container (D-087), gates it with a real Evals Runner run
against the deployed agent itself (D-089), and rolls back on a failing gate. Greenfield service
(Postgres `deploy`) plus a Temporal client — the actual mechanism is `DeploymentWorkflow`
(temporal_workflow.py), executed by a separate worker process (worker.py); this API only starts
workflows and reads the Postgres projection those workflows keep up to date via an activity, same
shape as Approvals/HITL. See
docs/superpowers/specs/2026-09-25-deployment-pipeline-harness-design.md.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import docker
from docker.errors import APIError
from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client

from platform_core.app import create_app
from platform_core.auth import CallerIdentity, make_require_identity, require_scope
from platform_core.db import Database, lifespan_hook, session_dependency
from platform_core.errors import PlatformError

from .config import Settings
from .models import Deployment
from .temporal_types import DeploymentRequest
from .temporal_workflow import DeploymentWorkflow


class CreateDeploymentIn(BaseModel):
    agent_name: str
    agent_version: str
    eval_suite_name: str
    eval_suite_version: str | None = None


class _TemporalClientHolder:
    """`Client.connect` is async, but `build_app()` is synchronous — this holder lets the
    lifespan hook populate the client at startup while routes close over the same instance
    (same pattern Approvals/HITL's own main.py already uses)."""

    client: Client | None = None


def _deployment_out(row: Deployment) -> dict[str, Any]:
    return {
        "id": row.id,
        "requester": row.requester,
        "agent_name": row.agent_name,
        "agent_version": row.agent_version,
        "container_id": row.container_id,
        "host_port": row.host_port,
        "status": row.status,
        "eval_run_id": row.eval_run_id,
        "error": row.error,
        "created_at": row.created_at.isoformat(),
    }


async def _get_deployment(session: AsyncSession, deployment_id: str) -> Deployment:
    row = await session.get(Deployment, deployment_id)
    if row is None:
        raise PlatformError(
            "not_found", f"no deployment with id {deployment_id!r}", status_code=404
        )
    return row


def build_app() -> FastAPI:
    settings = Settings()
    db = Database(settings.database_url)
    get_session = session_dependency(db)
    require_identity = make_require_identity(settings)
    require_create_scope = require_scope(require_identity, "deploy:create")
    temporal = _TemporalClientHolder()
    # /stop is a quick, synchronous, idempotent operation — the API talks to Docker directly
    # here rather than through another Temporal workflow, same posture as sandbox-service's own
    # API process. Needs the same host Docker socket mount as deployment-pipeline-worker.
    docker_client = (
        docker.DockerClient(base_url=settings.docker_base_url)
        if settings.docker_base_url
        else docker.from_env()
    )

    @asynccontextmanager
    async def connect_temporal_hook(_app: FastAPI) -> AsyncIterator[None]:
        temporal.client = await Client.connect(settings.temporal_address)
        yield

    app = create_app(
        settings,
        readiness_checks=[db.check],
        lifespan_hooks=[lifespan_hook(db), connect_temporal_hook],
    )

    @app.post("/deployments", tags=["deployment-pipeline"], status_code=201)
    async def create_deployment(
        body: CreateDeploymentIn,
        identity: CallerIdentity = Depends(require_create_scope),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        assert temporal.client is not None
        deployment_id = str(uuid.uuid4())
        row = Deployment(
            id=deployment_id,
            requester=identity.id,
            agent_name=body.agent_name,
            agent_version=body.agent_version,
            status="deploying",
        )
        session.add(row)
        await session.commit()

        try:
            await temporal.client.start_workflow(
                DeploymentWorkflow.run,
                DeploymentRequest(
                    deployment_id=deployment_id,
                    agent_name=body.agent_name,
                    agent_version=body.agent_version,
                    eval_suite_name=body.eval_suite_name,
                    eval_suite_version=body.eval_suite_version,
                ),
                id=deployment_id,
                task_queue=settings.temporal_task_queue,
            )
        except Exception as exc:
            raise PlatformError(
                "workflow_start_failed",
                f"could not start deployment workflow: {exc}",
                status_code=502,
            ) from exc

        await session.refresh(row)
        return _deployment_out(row)

    @app.get("/deployments", tags=["deployment-pipeline"])
    async def list_deployments(
        _identity: CallerIdentity = Depends(require_identity),
        session: AsyncSession = Depends(get_session),
    ) -> list[dict[str, Any]]:
        rows = (
            await session.execute(select(Deployment).order_by(Deployment.created_at.desc()))
        ).scalars()
        return [_deployment_out(row) for row in rows]

    @app.get("/deployments/{deployment_id}", tags=["deployment-pipeline"])
    async def get_deployment(
        deployment_id: str,
        _identity: CallerIdentity = Depends(require_identity),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        return _deployment_out(await _get_deployment(session, deployment_id))

    @app.post("/deployments/{deployment_id}/stop", tags=["deployment-pipeline"])
    async def stop_deployment(
        deployment_id: str,
        _identity: CallerIdentity = Depends(require_create_scope),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        row = await _get_deployment(session, deployment_id)
        if row.status != "running":
            raise PlatformError(
                "not_running",
                f"deployment {deployment_id} is {row.status!r}, not 'running'",
                status_code=409,
            )
        if row.container_id is not None:
            container_id = row.container_id
            try:
                await asyncio.to_thread(
                    lambda: docker_client.containers.get(container_id).remove(force=True)
                )
            except APIError:
                pass  # already gone — not a failure here, matching Sandbox's own posture
        row.status = "stopped"
        row.container_id = None
        row.host_port = None
        await session.commit()
        await session.refresh(row)
        return _deployment_out(row)

    return app


app = build_app()
