from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.db import Base


class Suite(Base):
    """One published eval suite version. ``(name, version)`` is unique — same shape as
    Agent/Skill Registry.

    A ``cases`` suite has ``metrics`` + a dataset in MinIO (``dataset_object_key``); a
    ``redteam`` suite has ``redteam_config`` and no fixed dataset (generation, not assertion —
    see D-040). Unlike Skill Registry's `skill_md`, the dataset is *not* duplicated into
    Postgres (D-042) — only the metadata needed to list/filter/describe a suite lives here.
    """

    __tablename__ = "suites"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_suites_name_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(String(1024), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    applies_to: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    metrics: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    redteam_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    dataset_object_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    case_count: Mapped[int | None] = mapped_column(nullable=True)
    # Advisory (non-blocking) findings from the judge-rubric scan at publish time — a suite
    # that failed a "block"-severity check never reaches storage at all. See scan.py.
    scan_findings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    published_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
