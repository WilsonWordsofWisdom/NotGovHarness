from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.db import Base


class Build(Base):
    """This harness's own audit trail: one row per build request and its outcome — same shape
    as Sandbox's `executions`/Guardrails' `checks`."""

    __tablename__ = "builds"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    requester: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    task: Mapped[str] = mapped_column(Text, nullable=False)
    kickoff_raw_output: Mapped[str] = mapped_column(Text, nullable=False)
    tool_was_called: Mapped[bool] = mapped_column(Boolean, nullable=False)
    agent_card_id: Mapped[str] = mapped_column(String(36), nullable=False)
    eval_suite_names: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
