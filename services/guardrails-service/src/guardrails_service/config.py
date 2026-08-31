from __future__ import annotations

from platform_core.config import PlatformSettings


class Settings(PlatformSettings):
    service_name: str = "guardrails-service"
    database_url: str = "postgresql+asyncpg://platform:platform@localhost:5432/guardrails"

    # hybrid: POST /check requires a verified guardrails:check token; GET reads require any
    # authenticated caller. See docs/superpowers/specs/2026-08-31-guardrails-harness-design.md.
    auth_mode: str = "hybrid"
    oauth2_issuer: str = "https://identity-service.notgovharness.local"
    oauth2_audience: str = "notgovharness"
    oauth2_jwks_url: str = "http://identity-service:8000/.well-known/jwks.json"
