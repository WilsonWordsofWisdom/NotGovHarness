from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.db import Base


class Approval(Base):
    """One approval request and its lifecycle.

    Postgres is the queryable *projection* of state; the Temporal workflow (``workflow_id``) is
    the durable *source of truth* — this row is updated only after the workflow itself observes a
    signal or its own timeout, never written to directly by a decision. ``status`` starts
    ``pending`` and ends in exactly one of ``approved`` / ``rejected`` / ``edited`` / ``expired``.
    """

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    requester: Mapped[str] = mapped_column(String(200), nullable=False)
    action_type: Mapped[str] = mapped_column(String(200), nullable=False)
    action_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    decision_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
