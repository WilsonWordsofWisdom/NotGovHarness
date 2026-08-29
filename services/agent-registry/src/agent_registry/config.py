from __future__ import annotations

from platform_core.config import PlatformSettings


class Settings(PlatformSettings):
    service_name: str = "agent-registry"
    database_url: str = "postgresql+asyncpg://platform:platform@localhost:5432/agent_registry"

    # hybrid: POST /agents requires a verified registry:publish token; reads stay open. See the
    # Agent Registry harness design (docs/superpowers/specs/2026-08-30-agent-registry-harness-
    # design.md) and D-029 (identity-service is the trust root for both tokens and card
    # signatures).
    auth_mode: str = "hybrid"
    oauth2_issuer: str = "https://identity-service.notgovharness.local"
    oauth2_audience: str = "notgovharness"
    oauth2_jwks_url: str = "http://identity-service:8000/.well-known/jwks.json"
