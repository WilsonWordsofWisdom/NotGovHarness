from __future__ import annotations

from platform_core.config import PlatformSettings


class Settings(PlatformSettings):
    service_name: str = "approvals-service"
    database_url: str = "postgresql+asyncpg://platform:platform@localhost:5432/approvals"

    # hybrid: POST /approvals and /decide require a verified scoped token; GET reads require any
    # authenticated caller. See docs/superpowers/specs/2026-08-31-approvals-hitl-harness-design.md.
    auth_mode: str = "hybrid"
    oauth2_issuer: str = "https://identity-service.notgovharness.local"
    oauth2_audience: str = "notgovharness"
    oauth2_jwks_url: str = "http://identity-service:8000/.well-known/jwks.json"

    temporal_address: str = "localhost:7233"
    temporal_task_queue: str = "approvals"
    # Default durable timeout for an approval nobody ever decides on. Short in tests via
    # per-request override, not a settings override, so production default stays realistic.
    default_timeout_hours: int = 24
