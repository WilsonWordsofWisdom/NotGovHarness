"""eval-registry app, pre-wired to the platform-core kit."""

from __future__ import annotations

from fastapi import FastAPI

from platform_core.app import create_app

from .config import Settings


def build_app() -> FastAPI:
    settings = Settings()
    app = create_app(settings)

    @app.get("/hello", tags=["eval_registry"])
    async def hello() -> dict[str, str]:
        return {"service": settings.service_name, "message": "hello"}

    return app


app = build_app()
