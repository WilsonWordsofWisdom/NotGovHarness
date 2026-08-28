from __future__ import annotations

from platform_core.config import PlatformSettings

WIDGET_TOPIC = "platform.example.v1"


class ExampleSettings(PlatformSettings):
    service_name: str = "example-service"
    database_url: str = "postgresql+asyncpg://platform:platform@localhost:5432/example_service"
    upstream_url: str = "http://localhost:8001"
    # Verified via mTLS peer cert when SPIRE is available (see UpstreamClient); otherwise unused.
    upstream_spiffe_id: str = "spiffe://notgovharness/upstream-stub"

    # identity-service — for the delegated-token demo on /proxy. Dev-only fixed secrets, matching
    # the seed migration (0002_seed_demo_clients.py) and the platform/platform Postgres posture
    # elsewhere in this repo. "alice" is the simulated principal — see that migration's docstring.
    identity_service_url: str = "http://identity-service:8000"
    identity_client_id: str = "example-service"
    identity_client_secret: str = "example-service-dev-secret"
    principal_client_id: str = "alice"
    principal_client_secret: str = "alice-dev-secret"
