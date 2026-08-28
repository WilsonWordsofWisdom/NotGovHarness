from __future__ import annotations

from platform_core.config import PlatformSettings

WIDGET_TOPIC = "platform.example.v1"


class ExampleSettings(PlatformSettings):
    service_name: str = "example-service"
    database_url: str = "postgresql+asyncpg://platform:platform@localhost:5432/example_service"
    upstream_url: str = "http://localhost:8001"
    # Verified via mTLS peer cert when SPIRE is available (see UpstreamClient); otherwise unused.
    upstream_spiffe_id: str = "spiffe://notgovharness/upstream-stub"
