"""A minimal upstream service. It is instrumented via ``create_app``, so a façade call into it
shows up as its own spans under the *same* trace — proving cross-hop trace propagation."""

from __future__ import annotations

from fastapi import FastAPI, Request

from platform_core.app import create_app
from platform_core.config import PlatformSettings


class StubSettings(PlatformSettings):
    service_name: str = "upstream-stub"


def build_app() -> FastAPI:
    app = create_app(StubSettings())

    @app.get("/echo", tags=["stub"])
    async def echo(request: Request) -> dict[str, str | None]:
        return {
            "upstream": "ok",
            "seen_identity": request.headers.get("x-service-identity"),
        }

    return app


app = build_app()
