from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import update
from temporalio import activity

from platform_core.db import Database

from .models import Approval
from .temporal_types import ApprovalOutcome


class Activities:
    """Bound to one `Database` for the worker process's lifetime (one engine, not one per call)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    @activity.defn(name="persist_outcome")
    async def persist_outcome(self, outcome: ApprovalOutcome) -> None:
        async with self._db.session() as session:
            await session.execute(
                update(Approval)
                .where(Approval.workflow_id == outcome.workflow_id)
                .values(
                    status=outcome.status,
                    decision_payload=outcome.decision_payload,
                    decided_by=outcome.decided_by,
                    decided_at=datetime.now(UTC),
                )
            )
            await session.commit()
