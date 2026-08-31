"""Pure dataclasses shared by the workflow, the activity, and the API layer.

Kept import-free (no SQLAlchemy, no platform_core.db) so the workflow module — which Temporal's
SDK re-imports inside a deterministic sandbox to validate replay-safety — never has to import
anything non-deterministic. See D-049's `asyncio.run()`-at-import-time bug for why this
separation matters here specifically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ApprovalDecision:
    """The signal payload a reviewer sends."""

    decision: str  # "approve" | "reject" | "edit"
    decided_by: str
    edited_payload: dict[str, Any] | None = None


@dataclass
class ApprovalOutcome:
    """The workflow's final result — also the activity's input for persisting to Postgres."""

    workflow_id: str
    status: str  # "approved" | "rejected" | "edited" | "expired"
    decision_payload: dict[str, Any] | None
    decided_by: str | None
