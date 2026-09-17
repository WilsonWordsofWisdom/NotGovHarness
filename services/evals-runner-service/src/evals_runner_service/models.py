from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.db import Base


class Run(Base):
    """This harness's own audit trail: one row per run and its full per-case outcome — same
    shape as Agent Builder's `builds`."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    requester: Mapped[str] = mapped_column(String(200), nullable=False)
    suite_name: Mapped[str] = mapped_column(String(128), nullable=False)
    suite_version: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    judge_model: Mapped[str] = mapped_column(String(200), nullable=False)
    # Each entry: {input, expected_output, actual_output,
    #              metrics: [{metric_id, engine, score, reason, passed}]}
    case_results: Mapped[list] = mapped_column(JSON, nullable=False)
    pass_count: Mapped[int] = mapped_column(Integer, nullable=False)
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
