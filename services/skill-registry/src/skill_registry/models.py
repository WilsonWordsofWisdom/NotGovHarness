from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.db import Base


class Skill(Base):
    """One published skill version. ``(name, version)`` is unique — a re-publish under a new
    version adds a row rather than overwriting history, same shape as Agent Registry's cards.

    ``skill_md`` holds the exact uploaded `SKILL.md` content (frontmatter + body) — the direct
    source an agent loads at the standard's "Activation" stage. The full bundle archive (any
    scripts/references/assets alongside it) lives in MinIO at ``bundle_object_key``.
    """

    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_skills_name_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(String(1024), nullable=False)
    license: Mapped[str | None] = mapped_column(String(200), nullable=True)
    compatibility: Mapped[str | None] = mapped_column(String(500), nullable=True)
    metadata_: Mapped[dict[str, str]] = mapped_column("metadata", JSONB, nullable=False)
    allowed_tools: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    skill_md: Mapped[str] = mapped_column(Text, nullable=False)
    bundle_object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    bundle_size_bytes: Mapped[int] = mapped_column(nullable=False)
    published_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
