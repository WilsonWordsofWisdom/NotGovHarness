from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.db import Base


class Client(Base):
    """A registered caller. ``secret_hash`` is the dev fallback; SPIRE-issued SVIDs (step 5 of
    the harness build order) will authenticate a client without one.
    """

    __tablename__ = "clients"

    client_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    spiffe_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    allowed_scopes: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    secret_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def scopes(self) -> list[str]:
        return self.allowed_scopes.split()
