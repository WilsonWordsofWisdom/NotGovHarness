"""fetch_agent_config is stubbed against a fake Agent Registry HTTP server (a pure client, same
ThreadingHTTPServer pattern as evals-runner-service's own test_eval_registry_client.py). The
Docker-orchestration activities run against the real Docker Engine instead of a mock, skip-if-not-
reachable — same posture as sandbox-service's own test_executor.py: a mock would assert this
code's own intent about what `containers.run(...)` does, not that it actually does it on this
host. These only exercise the orchestration mechanics (labels, host-port extraction, stop/remove);
health/invoke behavior against a real agent-runtime image is covered live in test_live_stack.py.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import docker
import pytest
from deployment_pipeline_service.activities import (
    _LABEL_KEY,
    _LABEL_VALUE,
    Activities,
    AgentCardError,
)
from deployment_pipeline_service.config import Settings
from deployment_pipeline_service.temporal_types import (
    AgentConfig,
    DeploymentRequest,
    RunningContainer,
)
from docker.errors import DockerException, NotFound

from platform_core.db import Database
from platform_core.events import Producer
from platform_core.llm_gateway_auth import TokenClient

CARD_WITH_EXTENSION = {
    "name": "deploy-pipeline-test-agent",
    "extensions": [
        {
            "uri": "urn:notgovharness:crewai-agent-config",
            "params": {
                "role": "Geography assistant",
                "goal": "Answer geography questions accurately",
                "backstory": "An assistant that knows world capitals.",
                "task": "Answer the given question in one sentence.",
            },
        }
    ],
}

CARD_WITHOUT_EXTENSION = {"name": "legacy-agent", "extensions": []}


@pytest.fixture
def stub_agent_registry():
    state = {"card": CARD_WITH_EXTENSION}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"card": state["card"]}).encode())

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", state
    server.shutdown()


def _activities(agent_registry_url: str) -> Activities:
    settings = Settings(agent_registry_url=agent_registry_url)
    return Activities(
        settings,
        Database(settings.database_url),
        TokenClient(
            identity_url=settings.identity_url,
            client_id=settings.deployment_client_id,
            client_secret=settings.deployment_client_secret,
        ),
        Producer(settings.kafka_brokers),
    )


async def test_fetch_agent_config_parses_the_crewai_extension(stub_agent_registry):
    base_url, _state = stub_agent_registry
    config = await _activities(base_url).fetch_agent_config(
        DeploymentRequest(
            deployment_id="d-1",
            agent_name="deploy-pipeline-test-agent",
            agent_version="0.1.0",
            eval_suite_name="suite-x",
            eval_suite_version=None,
        )
    )
    assert config == AgentConfig(
        role="Geography assistant",
        goal="Answer geography questions accurately",
        backstory="An assistant that knows world capitals.",
        task="Answer the given question in one sentence.",
    )


async def test_fetch_agent_config_raises_without_the_extension(stub_agent_registry):
    base_url, state = stub_agent_registry
    state["card"] = CARD_WITHOUT_EXTENSION

    with pytest.raises(AgentCardError, match="crewai-agent-config"):
        await _activities(base_url).fetch_agent_config(
            DeploymentRequest(
                deployment_id="d-1",
                agent_name="legacy-agent",
                agent_version="0.1.0",
                eval_suite_name="suite-x",
                eval_suite_version=None,
            )
        )


def _docker_available() -> bool:
    try:
        docker.from_env().ping()
        return True
    except DockerException:
        return False


requires_docker = pytest.mark.skipif(not _docker_available(), reason="Docker Engine not reachable")


@pytest.fixture
def docker_activities() -> Activities:
    settings = Settings(agent_runtime_image="python:3.12-slim")
    return Activities(
        settings,
        Database(settings.database_url),
        TokenClient(
            identity_url=settings.identity_url,
            client_id=settings.deployment_client_id,
            client_secret=settings.deployment_client_secret,
        ),
        Producer(settings.kafka_brokers),
    )


@pytest.fixture
def _cleanup_labeled_containers():
    yield
    client = docker.from_env()
    for c in client.containers.list(all=True, filters={"label": f"{_LABEL_KEY}={_LABEL_VALUE}"}):
        c.remove(force=True)


@requires_docker
async def test_run_container_labels_and_returns_a_real_host_port(
    docker_activities: Activities, _cleanup_labeled_containers
):
    config = AgentConfig(role="r", goal="g", backstory="b", task="t")
    # python:3.12-slim has no server on 8000, but Docker still allocates the host-port mapping
    # and reports it back via container.attrs regardless of whether anything's listening —
    # that's the mechanic this test exercises, not agent-runtime's own /healthz behavior.
    running = await docker_activities.run_container(config)
    try:
        assert isinstance(running, RunningContainer)
        assert running.container_id
        assert running.host_port > 0

        client = docker.from_env()
        container = client.containers.get(running.container_id)
        assert container.labels[_LABEL_KEY] == _LABEL_VALUE
    finally:
        await docker_activities.stop_container(running)


@requires_docker
async def test_stop_container_removes_it(
    docker_activities: Activities, _cleanup_labeled_containers
):
    config = AgentConfig(role="r", goal="g", backstory="b", task="t")
    running = await docker_activities.run_container(config)

    await docker_activities.stop_container(running)

    client = docker.from_env()
    with pytest.raises(NotFound):
        client.containers.get(running.container_id)


@requires_docker
async def test_stop_container_is_a_noop_for_an_already_gone_container():
    settings = Settings(agent_runtime_image="python:3.12-slim")
    activities = Activities(
        settings,
        Database(settings.database_url),
        TokenClient(
            identity_url=settings.identity_url,
            client_id=settings.deployment_client_id,
            client_secret=settings.deployment_client_secret,
        ),
        Producer(settings.kafka_brokers),
    )
    # Never started — matches the D-092 finding that a fully-orphaned deployment can be stopped
    # twice (rollback after a wait_healthy timeout, then never touched again) without raising.
    await activities.stop_container(RunningContainer(container_id="does-not-exist", host_port=1))
