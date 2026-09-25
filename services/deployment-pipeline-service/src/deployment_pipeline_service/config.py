from __future__ import annotations

from platform_core.config import PlatformSettings


class Settings(PlatformSettings):
    service_name: str = "deployment-pipeline-service"
    database_url: str = "postgresql+asyncpg://platform:platform@localhost:5432/deploy"

    # hybrid: POST /deployments and /stop require a verified scoped token; GET reads require any
    # authenticated caller. See
    # docs/superpowers/specs/2026-09-25-deployment-pipeline-harness-design.md.
    auth_mode: str = "hybrid"
    oauth2_issuer: str = "https://identity-service.notgovharness.local"
    oauth2_audience: str = "notgovharness"
    oauth2_jwks_url: str = "http://identity-service:8000/.well-known/jwks.json"

    temporal_address: str = "localhost:7233"
    temporal_task_queue: str = "deployment-pipeline"

    # Docker Engine the activities launch agent-runtime containers on (D-087). None uses the
    # local environment's default (DOCKER_HOST or the platform socket) — set explicitly in
    # compose for the worker container specifically (the API process never touches Docker).
    docker_base_url: str | None = None
    # Found live: a container run without an explicit network joins Docker's own default
    # bridge, not the compose project's network — the deployed agent-runtime container could
    # come up healthy (no outbound calls needed for /healthz) but then fail /invoke, unable to
    # resolve identity-service/llm-gateway by their Docker Compose hostnames at all. None skips
    # the network argument (Docker's own default); compose sets this explicitly to the actual
    # project network name (which is directory/COMPOSE_PROJECT_NAME-derived, not a fixed
    # constant this code should hardcode).
    docker_network: str | None = None
    agent_runtime_image: str = "notgovharness-agent-runtime:latest"
    agent_runtime_healthz_timeout_seconds: int = 30
    # Judge-shaped calls are genuinely slow (D-082/D-091) — a real eval-gate run through a
    # deployed agent-runtime container has been observed taking several minutes.
    eval_gate_timeout_seconds: int = 600

    agent_registry_url: str = "http://localhost:8092"
    evals_runner_url: str = "http://localhost:8103"

    # This service's own OAuth2 client (D-062/D-073 pattern) — reading Agent Registry needs no
    # scope; calling the eval-gate needs evals:run.
    identity_url: str = "http://localhost:8090"
    deployment_client_id: str = "deployment-pipeline-service"
    deployment_client_secret: str = "deployment-pipeline-service-dev-secret"
