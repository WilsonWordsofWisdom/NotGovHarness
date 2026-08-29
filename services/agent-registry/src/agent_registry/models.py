from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.db import Base


class AgentCard(Base):
    """One published, signed A2A Agent Card. ``(name, version)`` is unique — publishing the same
    name again with a new version adds a row rather than overwriting history.

    The `AgentCard`-shaped columns mirror the A2A spec's field names (see the harness design doc);
    ``card`` holds the exact payload that was signed, so signature re-verification has something
    byte-for-byte to check against without reassembling it from the individual columns.
    """

    __tablename__ = "agent_cards"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_agent_cards_name_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    provider: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    default_input_modes: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    default_output_modes: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    skills: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    security_schemes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    security: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    interfaces: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    extensions: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    card: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    signing_algorithm: Mapped[str] = mapped_column(String(20), nullable=False)
    signing_key_id: Mapped[str] = mapped_column(String(100), nullable=False)
    signature_value: Mapped[str] = mapped_column(String(4000), nullable=False)
    published_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
