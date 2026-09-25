"""The DeploymentWorkflow: fetch a redeployable agent config from its published Card (D-088),
run a real container from it (D-087), gate it with a real Evals Runner run against the deployed
agent itself (D-089) — and roll back (stop the container) on a failing gate, never leaving a bad
deployment live. Same durable activity-chain shape as Approvals/HITL's own `ApprovalWorkflow`;
referenced by string name (see worker.py) so this module stays side-effect-free and
non-deterministic-import-free for Temporal's own replay-safety sandbox (D-049's lesson).
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from .temporal_types import (
    AgentConfig,
    DeploymentOutcome,
    DeploymentRequest,
    EvalGateInput,
    EvalGateResult,
    RunningContainer,
)

FETCH_AGENT_CONFIG_ACTIVITY = "fetch_agent_config"
RUN_CONTAINER_ACTIVITY = "run_container"
WAIT_HEALTHY_ACTIVITY = "wait_healthy"
RUN_EVAL_GATE_ACTIVITY = "run_eval_gate"
STOP_CONTAINER_ACTIVITY = "stop_container"
PERSIST_OUTCOME_ACTIVITY = "persist_outcome"
EMIT_EVENT_ACTIVITY = "emit_deployment_event"

# run_container is deliberately not auto-retried: a retry after a partial failure could start a
# second container without the workflow ever learning the first one's id, orphaning it the same
# way D-050 found live for Sandbox before its own reap-on-startup fix — safer to fail the
# deployment and let a human retry the whole thing than to silently double-start.
_NO_RETRY = RetryPolicy(maximum_attempts=1)


@workflow.defn
class DeploymentWorkflow:
    @workflow.run
    async def run(self, request: DeploymentRequest) -> DeploymentOutcome:
        outcome = await self._run(request)
        await workflow.execute_activity(
            PERSIST_OUTCOME_ACTIVITY, outcome, schedule_to_close_timeout=timedelta(seconds=30)
        )
        await workflow.execute_activity(
            EMIT_EVENT_ACTIVITY, outcome, schedule_to_close_timeout=timedelta(seconds=15)
        )
        return outcome

    async def _run(self, request: DeploymentRequest) -> DeploymentOutcome:
        try:
            config = await workflow.execute_activity(
                FETCH_AGENT_CONFIG_ACTIVITY,
                request,
                result_type=AgentConfig,
                schedule_to_close_timeout=timedelta(seconds=15),
            )
        except Exception as exc:  # noqa: BLE001
            return DeploymentOutcome(
                deployment_id=request.deployment_id,
                status="failed",
                container_id=None,
                host_port=None,
                eval_run_id=None,
                error=f"fetch_agent_config failed: {exc}",
            )

        try:
            container = await workflow.execute_activity(
                RUN_CONTAINER_ACTIVITY,
                config,
                result_type=RunningContainer,
                schedule_to_close_timeout=timedelta(seconds=30),
                retry_policy=_NO_RETRY,
            )
        except Exception as exc:  # noqa: BLE001
            return DeploymentOutcome(
                deployment_id=request.deployment_id,
                status="failed",
                container_id=None,
                host_port=None,
                eval_run_id=None,
                error=f"run_container failed: {exc}",
            )

        try:
            await workflow.execute_activity(
                WAIT_HEALTHY_ACTIVITY, container, schedule_to_close_timeout=timedelta(seconds=45)
            )
        except Exception as exc:  # noqa: BLE001
            await workflow.execute_activity(
                STOP_CONTAINER_ACTIVITY,
                container,
                schedule_to_close_timeout=timedelta(seconds=15),
            )
            return DeploymentOutcome(
                deployment_id=request.deployment_id,
                status="failed",
                container_id=None,
                host_port=None,
                eval_run_id=None,
                error=f"container never became healthy: {exc}",
            )

        gate_input = EvalGateInput(
            suite_name=request.eval_suite_name,
            suite_version=request.eval_suite_version,
            agent_runtime_host_port=container.host_port,
        )
        try:
            gate = await workflow.execute_activity(
                RUN_EVAL_GATE_ACTIVITY,
                gate_input,
                result_type=EvalGateResult,
                schedule_to_close_timeout=timedelta(seconds=700),
                retry_policy=_NO_RETRY,
            )
        except Exception as exc:  # noqa: BLE001
            await workflow.execute_activity(
                STOP_CONTAINER_ACTIVITY,
                container,
                schedule_to_close_timeout=timedelta(seconds=15),
            )
            return DeploymentOutcome(
                deployment_id=request.deployment_id,
                status="failed",
                container_id=None,
                host_port=None,
                eval_run_id=None,
                error=f"eval gate failed to run: {exc}",
            )

        if not gate.passed:
            await workflow.execute_activity(
                STOP_CONTAINER_ACTIVITY,
                container,
                schedule_to_close_timeout=timedelta(seconds=15),
            )
            return DeploymentOutcome(
                deployment_id=request.deployment_id,
                status="eval_failed",
                container_id=None,
                host_port=None,
                eval_run_id=gate.run_id,
                error=None,
            )

        return DeploymentOutcome(
            deployment_id=request.deployment_id,
            status="running",
            container_id=container.container_id,
            host_port=container.host_port,
            eval_run_id=gate.run_id,
            error=None,
        )
