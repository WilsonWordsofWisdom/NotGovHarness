"""Workflow-level tests for DeploymentWorkflow, using Temporal's time-skipping test environment —
no live Temporal server, no live Docker, no live Postgres. Every activity is stubbed here so these
tests exercise the workflow's own branching (fetch/run/health/gate failures, gate pass, gate fail)
deterministically; the real activities (Docker orchestration, Agent Registry, Evals Runner) are
tested for real in test_live_stack.py, same split approvals-service's own test_workflow.py uses.
"""

from __future__ import annotations

import uuid

import pytest
from deployment_pipeline_service.temporal_types import (
    AgentConfig,
    DeploymentOutcome,
    DeploymentRequest,
    EvalGateInput,
    EvalGateResult,
    RunningContainer,
)
from deployment_pipeline_service.temporal_workflow import (
    EMIT_EVENT_ACTIVITY,
    FETCH_AGENT_CONFIG_ACTIVITY,
    PERSIST_OUTCOME_ACTIVITY,
    RUN_CONTAINER_ACTIVITY,
    RUN_EVAL_GATE_ACTIVITY,
    STOP_CONTAINER_ACTIVITY,
    WAIT_HEALTHY_ACTIVITY,
    DeploymentWorkflow,
)
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

_CONFIG = AgentConfig(role="r", goal="g", backstory="b", task="t")
_CONTAINER = RunningContainer(container_id="c-1", host_port=12345)

persisted: list[DeploymentOutcome] = []
emitted: list[DeploymentOutcome] = []
stopped: list[RunningContainer] = []


@activity.defn(name=PERSIST_OUTCOME_ACTIVITY)
async def _stub_persist_outcome(outcome: DeploymentOutcome) -> None:
    persisted.append(outcome)


@activity.defn(name=EMIT_EVENT_ACTIVITY)
async def _stub_emit_event(outcome: DeploymentOutcome) -> None:
    emitted.append(outcome)


@activity.defn(name=STOP_CONTAINER_ACTIVITY)
async def _stub_stop_container(container: RunningContainer) -> None:
    stopped.append(container)


def _fetch_agent_config_ok():
    @activity.defn(name=FETCH_AGENT_CONFIG_ACTIVITY)
    async def _fetch(_request: DeploymentRequest) -> AgentConfig:
        return _CONFIG

    return _fetch


def _fetch_agent_config_fails():
    @activity.defn(name=FETCH_AGENT_CONFIG_ACTIVITY)
    async def _fetch(_request: DeploymentRequest) -> AgentConfig:
        raise RuntimeError("no card found")

    return _fetch


def _run_container_ok():
    @activity.defn(name=RUN_CONTAINER_ACTIVITY)
    async def _run(_config: AgentConfig) -> RunningContainer:
        return _CONTAINER

    return _run


def _run_container_fails():
    @activity.defn(name=RUN_CONTAINER_ACTIVITY)
    async def _run(_config: AgentConfig) -> RunningContainer:
        raise RuntimeError("failed to start agent-runtime container")

    return _run


def _wait_healthy_ok():
    @activity.defn(name=WAIT_HEALTHY_ACTIVITY)
    async def _wait(_container: RunningContainer) -> None:
        return None

    return _wait


def _wait_healthy_times_out():
    @activity.defn(name=WAIT_HEALTHY_ACTIVITY)
    async def _wait(_container: RunningContainer) -> None:
        raise TimeoutError("agent-runtime container did not become healthy within timeout")

    return _wait


def _run_eval_gate_result(*, passed: bool):
    @activity.defn(name=RUN_EVAL_GATE_ACTIVITY)
    async def _gate(_gate_input: EvalGateInput) -> EvalGateResult:
        return EvalGateResult(run_id="run-1", passed=passed)

    return _gate


def _run_eval_gate_fails():
    @activity.defn(name=RUN_EVAL_GATE_ACTIVITY)
    async def _gate(_gate_input: EvalGateInput) -> EvalGateResult:
        raise RuntimeError("evals-runner-service unreachable")

    return _gate


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        yield environment


@pytest.fixture(autouse=True)
def _reset_recorders():
    persisted.clear()
    emitted.clear()
    stopped.clear()


async def _run_workflow(env: WorkflowEnvironment, *activities) -> DeploymentOutcome:
    task_queue = f"test-{uuid.uuid4().hex}"
    async with Worker(
        env.client,
        task_queue=task_queue,
        workflows=[DeploymentWorkflow],
        activities=[_stub_persist_outcome, _stub_emit_event, _stub_stop_container, *activities],
    ):
        return await env.client.execute_workflow(
            DeploymentWorkflow.run,
            DeploymentRequest(
                deployment_id="d-1",
                agent_name="agent-x",
                agent_version="0.1.0",
                eval_suite_name="suite-x",
                eval_suite_version="0.1.0",
            ),
            id=f"wf-{uuid.uuid4().hex}",
            task_queue=task_queue,
        )


async def test_passing_gate_leaves_the_deployment_running(env):
    result = await _run_workflow(
        env,
        _fetch_agent_config_ok(),
        _run_container_ok(),
        _wait_healthy_ok(),
        _run_eval_gate_result(passed=True),
    )
    assert result.status == "running"
    assert result.container_id == _CONTAINER.container_id
    assert result.host_port == _CONTAINER.host_port
    assert result.eval_run_id == "run-1"
    assert stopped == []  # a passing gate must never roll back
    assert persisted == [result]
    assert emitted == [result]


async def test_failing_gate_rolls_back_the_container(env):
    result = await _run_workflow(
        env,
        _fetch_agent_config_ok(),
        _run_container_ok(),
        _wait_healthy_ok(),
        _run_eval_gate_result(passed=False),
    )
    assert result.status == "eval_failed"
    assert result.container_id is None
    assert result.host_port is None
    assert result.eval_run_id == "run-1"
    assert stopped == [_CONTAINER]  # a failing gate must roll back, never leave it live


async def test_missing_card_extension_fails_before_any_container_starts(env):
    result = await _run_workflow(env, _fetch_agent_config_fails())
    assert result.status == "failed"
    # Temporal wraps the activity's own exception (str(exc) is just "Activity task failed" at
    # this layer) — the workflow's own f-string prefix is what's actually asserted here.
    assert (result.error or "").startswith("fetch_agent_config failed:")
    assert stopped == []  # nothing was ever started, nothing to roll back


async def test_container_start_failure_never_reaches_the_gate(env):
    result = await _run_workflow(
        env, _fetch_agent_config_ok(), _run_container_fails()
    )
    assert result.status == "failed"
    assert (result.error or "").startswith("run_container failed:")
    assert stopped == []  # never started, nothing to roll back


async def test_unhealthy_container_is_stopped_and_deployment_fails(env):
    result = await _run_workflow(
        env,
        _fetch_agent_config_ok(),
        _run_container_ok(),
        _wait_healthy_times_out(),
    )
    assert result.status == "failed"
    assert "never became healthy" in (result.error or "")
    assert stopped == [_CONTAINER]  # rollback-safety: never leave an unhealthy container running


async def test_eval_gate_infra_failure_is_treated_like_a_failing_gate(env):
    result = await _run_workflow(
        env,
        _fetch_agent_config_ok(),
        _run_container_ok(),
        _wait_healthy_ok(),
        _run_eval_gate_fails(),
    )
    assert result.status == "failed"
    assert result.eval_run_id is None
    assert "eval gate failed to run" in (result.error or "")
    assert stopped == [_CONTAINER]  # can't confirm the agent behaves correctly — roll back
