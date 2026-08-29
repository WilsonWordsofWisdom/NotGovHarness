from __future__ import annotations

from platform_core.config import PlatformSettings

# Only one real event-producing topic exists today (see the harness spec's non-goals) — extending
# to more is adding to this list, not a redesign.
AUDITED_TOPICS = ["platform.example.v1"]


class Settings(PlatformSettings):
    service_name: str = "audit-service"
    database_url: str = "postgresql+asyncpg://platform:platform@localhost:5432/audit"
