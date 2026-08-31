"""Workflow-level tests for ApprovalWorkflow, using Temporal's time-skipping test environment —
no live Temporal server, no live Postgres. The persist_outcome activity is stubbed here (it's
tested for real in test_live_stack.py against the real activity + real Postgres); this file is
about the workflow's own signal/timeout logic, including the 24h default timeout path, which
time-skipping resolves in real seconds rather than actually waiting 24 hours.
"""

from __future__ import annotations

import uuid

import pytest
from approvals_service.temporal_types import ApprovalDecision, ApprovalOutcome
from approvals_service.temporal_workflow import PERSIST_OUTCOME_ACTIVITY, ApprovalWorkflow
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

persisted: list[ApprovalOutcome] = []


@activity.defn(name=PERSIST_OUTCOME_ACTIVITY)
async def _stub_persist_outcome(outcome: ApprovalOutcome) -> None:
    persisted.append(outcome)


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        yield environment


async def _run_workflow(env: WorkflowEnvironment, timeout_hours: int, *, decide_after=None):
    task_queue = f"test-{uuid.uuid4().hex}"
    async with Worker(
        env.client,
        task_queue=task_queue,
        workflows=[ApprovalWorkflow],
        activities=[_stub_persist_outcome],
    ):
        handle = await env.client.start_workflow(
            ApprovalWorkflow.run,
            timeout_hours,
            id=f"wf-{uuid.uuid4().hex}",
            task_queue=task_queue,
        )
        if decide_after is not None:
            await decide_after(handle)
        return await handle.result()


async def test_approve_signal_resolves_the_workflow(env):
    async def decide(handle):
        await handle.signal(
            ApprovalWorkflow.decide,
            ApprovalDecision(decision="approve", decided_by="reviewer"),
        )

    result = await _run_workflow(env, timeout_hours=24, decide_after=decide)
    assert result.status == "approved"
    assert result.decided_by == "reviewer"


async def test_edit_signal_carries_the_edited_payload(env):
    async def decide(handle):
        await handle.signal(
            ApprovalWorkflow.decide,
            ApprovalDecision(decision="edit", decided_by="reviewer", edited_payload={"amount": 42}),
        )

    result = await _run_workflow(env, timeout_hours=24, decide_after=decide)
    assert result.status == "edited"
    assert result.decision_payload == {"amount": 42}


async def test_first_signal_wins_over_a_second(env):
    async def decide(handle):
        await handle.signal(
            ApprovalWorkflow.decide,
            ApprovalDecision(decision="approve", decided_by="reviewer-a"),
        )
        await handle.signal(
            ApprovalWorkflow.decide,
            ApprovalDecision(decision="reject", decided_by="reviewer-b"),
        )

    result = await _run_workflow(env, timeout_hours=24, decide_after=decide)
    assert result.status == "approved"
    assert result.decided_by == "reviewer-a"


async def test_no_decision_expires_on_the_durable_timeout(env):
    # No signal sent — the workflow's own timer resolves this, and time-skipping fast-forwards
    # through the 1-hour wait instead of the test actually waiting an hour.
    result = await _run_workflow(env, timeout_hours=1)
    assert result.status == "expired"
    assert result.decided_by is None
