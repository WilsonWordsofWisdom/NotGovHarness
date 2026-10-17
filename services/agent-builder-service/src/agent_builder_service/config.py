from __future__ import annotations

from platform_core.config import PlatformSettings


class Settings(PlatformSettings):
    service_name: str = "agent-builder-service"
    database_url: str = "postgresql+asyncpg://platform:platform@localhost:5432/builder"

    auth_mode: str = "oauth2"
    oauth2_issuer: str = "https://identity-service.notgovharness.local"
    oauth2_audience: str = "notgovharness"
    oauth2_jwks_url: str = "http://identity-service:8000/.well-known/jwks.json"

    llm_gateway_url: str = "http://localhost:8099"
    llm_gateway_model: str = "qwen3.8"
    mcp_gateway_registry_url: str = "http://localhost:8095"
    agent_registry_url: str = "http://localhost:8092"
    eval_registry_url: str = "http://localhost:8094"

    # This service's own OAuth2 client (D-062/D-073) — a real registered identity, not a
    # borrowed simulated-principal (D-030), since agent-builder-service is a real platform
    # service calling three other platform services.
    identity_url: str = "http://localhost:8090"
    builder_client_id: str = "agent-builder-service"
    builder_client_secret: str = "agent-builder-service-dev-secret"
