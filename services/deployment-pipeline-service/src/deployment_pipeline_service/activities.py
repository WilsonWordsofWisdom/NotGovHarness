"""Activities: the actual side-effecting steps `DeploymentWorkflow` orchestrates. Bound to one
set of shared clients for the worker process's lifetime (one Docker client, one DB engine, one
token client — not one per call), same shape as Approvals/HITL's own `Activities`.

The `docker` SDK is synchronous, same as Sandbox's `executor.py` — every call here runs in a
worker thread (`run_in_threadpool`).
"""

from __future__ import annotations

import time
from typing import Any

import docker
import httpx
from docker.errors import APIError, DockerException, ImageNotFound
from sqlalchemy import update
from starlette.concurrency import run_in_threadpool
from temporalio import activity

from platform_core.db import Database
from platform_core.events import EventEnvelope, Producer
from platform_core.llm_gateway_auth import TokenClient, call_with_auth_retry

from .config import Settings
from .models import Deployment
from .temporal_types import (
    AgentConfig,
    DeploymentOutcome,
    DeploymentRequest,
    EvalGateInput,
    EvalGateResult,
    RunningContainer,
)

_LABEL_KEY = "com.notgovharness.deployment"
_LABEL_VALUE = "true"


class AgentCardError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class Activities:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        token_client: TokenClient,
        producer: Producer,
    ) -> None:
        self._settings = settings
        self._db = db
        self._token_client = token_client
        self._producer = producer
        self._docker = (
            docker.DockerClient(base_url=settings.docker_base_url)
            if settings.docker_base_url
            else docker.from_env()
        )

    async def _authed_call(self, method_call: Any) -> httpx.Response:
        headers: dict[str, str] = {}

        def apply_token(token: str) -> None:
            headers["Authorization"] = f"Bearer {token}"

        async def call() -> httpx.Response:
            resp = await method_call(headers)
            resp.raise_for_status()
            return resp

        return await call_with_auth_retry(self._token_client, apply_token, call)

    @activity.defn(name="fetch_agent_config")
    async def fetch_agent_config(self, request: DeploymentRequest) -> AgentConfig:
        async with httpx.AsyncClient(
            base_url=self._settings.agent_registry_url, timeout=15.0
        ) as client:
            resp = await client.get(f"/agents/{request.agent_name}/{request.agent_version}")
            resp.raise_for_status()
        card = resp.json()["card"]

        # D-088: role/goal/backstory/task live in the card's extensions, not top-level.
        extensions = card.get("extensions") or []
        config_ext = next(
            (e for e in extensions if e.get("uri") == "urn:notgovharness:crewai-agent-config"),
            None,
        )
        if config_ext is None:
            raise AgentCardError(
                f"card {request.agent_name}/{request.agent_version} has no "
                "urn:notgovharness:crewai-agent-config extension — was it published by "
                "agent-builder-service after D-088?"
            )
        params = config_ext["params"]
        return AgentConfig(
            role=params["role"],
            goal=params["goal"],
            backstory=params["backstory"],
            task=params["task"],
        )

    @activity.defn(name="run_container")
    async def run_container(self, config: AgentConfig) -> RunningContainer:
        return await run_in_threadpool(self._run_container_sync, config)

    def _run_container_sync(self, config: AgentConfig) -> RunningContainer:
        try:
            container = self._docker.containers.run(
                self._settings.agent_runtime_image,
                detach=True,
                environment={
                    "SERVICE_NAME": "agent-runtime",
                    "AGENT_ROLE": config.role,
                    "AGENT_GOAL": config.goal,
                    "AGENT_BACKSTORY": config.backstory,
                    # Real Docker-network hostnames — this container joins the same compose
                    # network deployment-pipeline-worker itself runs in (D-090's lesson: this is
                    # the one place the docker-network default *is* correct, unlike a host-side
                    # pytest process).
                    "OAUTH2_JWKS_URL": "http://identity-service:8000/.well-known/jwks.json",
                    "IDENTITY_URL": "http://identity-service:8000",
                    "LLM_GATEWAY_URL": "http://llm-gateway:8000",
                    "AGENT_RUNTIME_CLIENT_SECRET": "agent-runtime-dev-secret",
                },
                ports={"8000/tcp": None},
                labels={_LABEL_KEY: _LABEL_VALUE},
                network=self._settings.docker_network,
            )
        except (ImageNotFound, APIError, DockerException) as exc:
            raise RuntimeError(f"failed to start agent-runtime container: {exc}") from exc

        container.reload()
        port_bindings = container.attrs["NetworkSettings"]["Ports"]["8000/tcp"]
        host_port = int(port_bindings[0]["HostPort"])
        # docker-py types Container.id as str | None (the value is never actually absent for a
        # container the SDK itself just created and reloaded).
        assert container.id is not None
        return RunningContainer(container_id=container.id, host_port=host_port)

    @activity.defn(name="wait_healthy")
    async def wait_healthy(self, container: RunningContainer) -> None:
        deadline = time.monotonic() + self._settings.agent_runtime_healthz_timeout_seconds
        # host.docker.internal, not localhost (found live — this activity runs inside
        # deployment-pipeline-worker's own container, which has nothing on its own loopback at
        # this port; the same D-090/D-091 lesson, hit a third time in a new spot).
        url = f"http://host.docker.internal:{container.host_port}/healthz"
        async with httpx.AsyncClient(timeout=3.0) as client:
            while time.monotonic() < deadline:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await run_in_threadpool(time.sleep, 1.0)
        raise TimeoutError(f"agent-runtime container did not become healthy within timeout: {url}")

    @activity.defn(name="run_eval_gate")
    async def run_eval_gate(self, gate_input: EvalGateInput) -> EvalGateResult:
        # host.docker.internal, not localhost: this activity runs inside
        # deployment-pipeline-worker's own container, but evals-runner-service is a *different*
        # container — it reaches the deployed agent-runtime the same way llm-gateway already
        # reaches the host's Ollama (D-056's precedent), not via this worker's own localhost.
        runtime_url = f"http://host.docker.internal:{gate_input.agent_runtime_host_port}"

        async def _post(headers: dict[str, str]) -> httpx.Response:
            async with httpx.AsyncClient(
                base_url=self._settings.evals_runner_url, timeout=700.0
            ) as client:
                return await client.post(
                    "/runs",
                    json={
                        "suite_name": gate_input.suite_name,
                        "suite_version": gate_input.suite_version,
                        "target": {"type": "agent_runtime", "url": runtime_url},
                    },
                    headers=headers,
                )

        resp = await self._authed_call(_post)
        body = resp.json()
        return EvalGateResult(run_id=body["run_id"], passed=body["fail_count"] == 0)

    @activity.defn(name="stop_container")
    async def stop_container(self, container: RunningContainer) -> None:
        await run_in_threadpool(self._stop_container_sync, container)

    def _stop_container_sync(self, container: RunningContainer) -> None:
        try:
            c = self._docker.containers.get(container.container_id)
            c.remove(force=True)
        except APIError:
            pass  # already gone — not a failure here, matching Sandbox's own posture

    @activity.defn(name="persist_outcome")
    async def persist_outcome(self, outcome: DeploymentOutcome) -> None:
        async with self._db.session() as session:
            await session.execute(
                update(Deployment)
                .where(Deployment.id == outcome.deployment_id)
                .values(
                    status=outcome.status,
                    container_id=outcome.container_id,
                    host_port=outcome.host_port,
                    eval_run_id=outcome.eval_run_id,
                    error=outcome.error,
                )
            )
            await session.commit()

    @activity.defn(name="emit_deployment_event")
    async def emit_deployment_event(self, outcome: DeploymentOutcome) -> None:
        await self._producer.publish(
            "platform.deployment.v1",
            EventEnvelope(
                type="platform.deployment.v1",
                source="deployment-pipeline-service",
                data={
                    "deployment_id": outcome.deployment_id,
                    "status": outcome.status,
                    "eval_run_id": outcome.eval_run_id,
                },
            ),
        )
