from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.db import Base


class Deployment(Base):
    """This harness's own audit trail: one row per deployment and its outcome — same shape as
    Agent Builder's `builds`/Evals Runner's `runs`. Postgres is the queryable projection;
    `workflow_id` (== `id`) is the durable source of truth, same relationship Approvals/HITL's
    `Approval` has to its own workflow."""

    __tablename__ = "deployments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    requester: Mapped[str] = mapped_column(String(200), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_version: Mapped[str] = mapped_column(String(50), nullable=False)
    container_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    host_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # "deploying" | "running" | "eval_failed" | "failed" | "stopped"
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="deploying")
    eval_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
