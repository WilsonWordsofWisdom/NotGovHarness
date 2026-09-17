from __future__ import annotations

from platform_core.config import PlatformSettings


class Settings(PlatformSettings):
    service_name: str = "evals-runner-service"
    database_url: str = "postgresql+asyncpg://platform:platform@localhost:5432/evals"

    auth_mode: str = "oauth2"
    oauth2_issuer: str = "https://identity-service.notgovharness.local"
    oauth2_audience: str = "notgovharness"
    oauth2_jwks_url: str = "http://identity-service:8000/.well-known/jwks.json"

    llm_gateway_url: str = "http://localhost:8099"
    llm_gateway_model: str = "qwen3.8"
    eval_registry_url: str = "http://localhost:8094"

    # This service's own OAuth2 client (D-062/D-073 pattern) — a real registered identity, not a
    # borrowed simulated-principal (D-030), since evals-runner-service is a real platform service
    # calling llm-gateway as itself. Reading Eval Registry needs no scope.
    identity_url: str = "http://localhost:8090"
    evals_client_id: str = "evals-runner-service"
    evals_client_secret: str = "evals-runner-service-dev-secret"
