from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.db import Base


class Document(Base):
    """Document-level metadata and provenance. `PGVectorStore`'s own chunk/embedding table lives
    in this same database but is managed by LlamaIndex directly — this is the one table this
    service owns, since chunk-level metadata alone can't answer "whose document is this" (D-068's
    minimal permission model needs it at the document level, not per-chunk).
    """

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    ingested_by: Mapped[str] = mapped_column(String(200), nullable=False)
    visibility: Mapped[str] = mapped_column(String(10), nullable=False)  # "shared" | "private"
    minio_object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
