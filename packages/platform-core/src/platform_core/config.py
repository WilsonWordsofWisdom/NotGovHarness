"""12-factor settings base. Services subclass ``PlatformSettings`` to add their own keys."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PlatformSettings(BaseSettings):
    """Base settings every service shares. Env-driven; ``.env`` for local dev.

    Field names map to case-insensitive env vars (e.g. ``service_name`` <- ``SERVICE_NAME``).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    service_name: str = Field(description="Logical service name, used in logs/traces/OpenAPI.")
    env: str = "dev"
    log_level: str = "info"
    auth_mode: str = "dev"

    database_url: str | None = None
    kafka_brokers: str = "localhost:9092"
    otel_exporter_otlp_endpoint: str | None = None
