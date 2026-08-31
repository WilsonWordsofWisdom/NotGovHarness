"""The ApprovalWorkflow: the durable mechanism D-015 actually specifies (Temporal signals), not a
plain Postgres status column.

Blocks on a `decide` signal, racing a durable timeout — if nobody ever decides, Temporal's own
timer resolves it to "expired" without any cron or polling loop. Persistence happens via a
named activity (registered by the worker process, see worker.py) referenced by string name here
rather than imported directly — this workflow module must stay side-effect-free and
non-deterministic-import-free, since Temporal's SDK re-imports it inside a sandbox to validate
replay safety (see docs/superpowers/specs/2026-09-01-temporal-harness-design.md's "SDK sandbox
re-import bug" finding).
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from .temporal_types import ApprovalDecision, ApprovalOutcome

PERSIST_OUTCOME_ACTIVITY = "persist_outcome"

_STATUS_FOR_DECISION = {"approve": "approved", "reject": "rejected", "edit": "edited"}


@workflow.defn
class ApprovalWorkflow:
    def __init__(self) -> None:
        self._decision: ApprovalDecision | None = None

    @workflow.signal
    def decide(self, decision: ApprovalDecision) -> None:
        # First signal wins — a reviewer double-clicking "approve" (or a racing second reviewer)
        # must not silently overwrite an already-recorded decision.
        if self._decision is None:
            self._decision = decision

    @workflow.run
    async def run(self, timeout_hours: float) -> ApprovalOutcome:
        workflow_id = workflow.info().workflow_id
        try:
            await workflow.wait_condition(
                lambda: self._decision is not None,
                timeout=timedelta(hours=timeout_hours),
            )
        except TimeoutError:
            outcome = ApprovalOutcome(
                workflow_id=workflow_id,
                status="expired",
                decision_payload=None,
                decided_by=None,
            )
        else:
            decision = self._decision
            assert decision is not None
            outcome = ApprovalOutcome(
                workflow_id=workflow_id,
                status=_STATUS_FOR_DECISION[decision.decision],
                decision_payload=decision.edited_payload,
                decided_by=decision.decided_by,
            )

        # The workflow itself finalizes storage — durable even if the API process (or this
        # worker) restarts between the signal/timeout landing and the activity running; Temporal
        # retries the activity from history until it succeeds.
        await workflow.execute_activity(
            PERSIST_OUTCOME_ACTIVITY, outcome, schedule_to_close_timeout=timedelta(seconds=30)
        )
        return outcome
