from __future__ import annotations

from pydantic import Field

from platform_core.config import PlatformSettings


class Settings(PlatformSettings):
    service_name: str = "agent-runtime"

    auth_mode: str = "oauth2"
    oauth2_issuer: str = "https://identity-service.notgovharness.local"
    oauth2_audience: str = "notgovharness"
    # Unlike every prior service, this default matters for real: agent-runtime is tested via an
    # in-process TestClient (there's no always-up compose instance to point a fixture at, since
    # it's a build-only image instantiated per deployment), so Settings() actually gets
    # constructed with these dev defaults during `pytest`, not just overridden by a separately
    # -started process's own env vars. `identity_url`/`llm_gateway_url` below already default to
    # localhost for the same reason; this one has to match, not the Docker-network hostname every
    # other harness's config.py defaults to (found live — the jwks fetch failed to resolve
    # `identity-service` from the host).
    oauth2_jwks_url: str = "http://localhost:8090/.well-known/jwks.json"

    # Baked in per deployment via env vars at container start (D-087) — deliberately required,
    # no default: a container started without a real role/goal/backstory should fail fast at
    # startup, not silently run an empty agent.
    agent_role: str = Field(...)
    agent_goal: str = Field(...)
    agent_backstory: str = Field(...)

    llm_gateway_url: str = "http://localhost:8099"
    llm_gateway_model: str = "qwen3.8"

    # This shared instance's own OAuth2 client (D-062/D-087) — every deployed agent-runtime
    # container uses this same identity; not provisioned per instance.
    identity_url: str = "http://localhost:8090"
    agent_runtime_client_id: str = "agent-runtime"
    agent_runtime_client_secret: str = "agent-runtime-dev-secret"
