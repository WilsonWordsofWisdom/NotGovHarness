"""agent-registry: catalog of signed A2A Agent Cards (Wave 2 Agent Registry harness).

Greenfield, façade-free (like identity-service and audit-service) — see
docs/superpowers/specs/2026-08-30-agent-registry-harness-design.md for the full design. No Kafka
consumer: this is a catalog CRUD service, not an event listener.
"""

from __future__ import annotations

from typing import Any

import jwt
from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.app import create_app
from platform_core.auth import CallerIdentity, make_require_identity, require_scope
from platform_core.db import Database, lifespan_hook, session_dependency
from platform_core.errors import PlatformError

from .config import Settings
from .models import AgentCard
from .verify import CardVerificationError, verify_card_signature

REQUIRED_CARD_FIELDS = ("name", "url", "version", "capabilities")


class SignatureIn(BaseModel):
    signing_algorithm: str
    signing_key_id: str
    signature_value: str


class PublishIn(BaseModel):
    card: dict[str, Any]
    signature: SignatureIn


def build_app() -> FastAPI:
    settings = Settings()
    db = Database(settings.database_url)
    get_session = session_dependency(db)
    require_identity = make_require_identity(settings)
    require_publish_scope = require_scope(require_identity, "registry:publish")
    # One PyJWKClient, reused across requests (it caches keys itself) — same tool
    # platform_core.auth uses to verify bearer tokens, applied here to stored card signatures.
    jwks_client = jwt.PyJWKClient(settings.oauth2_jwks_url, cache_keys=True)

    app = create_app(
        settings,
        readiness_checks=[db.check],
        lifespan_hooks=[lifespan_hook(db)],
    )

    @app.post("/agents", tags=["agent-registry"], status_code=201)
    async def publish_agent(
        body: PublishIn,
        identity: CallerIdentity = Depends(require_publish_scope),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        missing = [f for f in REQUIRED_CARD_FIELDS if f not in body.card]
        if missing:
            raise PlatformError(
                "invalid_card", f"card missing required fields: {missing}", status_code=422
            )

        try:
            verify_card_signature(
                jwks_client,
                body.card,
                body.signature.signing_algorithm,
                body.signature.signing_key_id,
                body.signature.signature_value,
            )
        except CardVerificationError as exc:
            raise PlatformError("invalid_signature", exc.reason, status_code=401) from exc

        card = body.card
        row = AgentCard(
            name=card["name"],
            version=card["version"],
            url=card["url"],
            description=card.get("description"),
            provider=card.get("provider"),
            capabilities=card["capabilities"],
            default_input_modes=card.get("defaultInputModes"),
            default_output_modes=card.get("defaultOutputModes"),
            skills=card.get("skills"),
            security_schemes=card.get("securitySchemes"),
            security=card.get("security"),
            interfaces=card.get("interfaces"),
            extensions=card.get("extensions"),
            card=card,
            signing_algorithm=body.signature.signing_algorithm,
            signing_key_id=body.signature.signing_key_id,
            signature_value=body.signature.signature_value,
            published_by=identity.id,
        )
        session.add(row)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise PlatformError(
                "already_published",
                f"{card['name']!r} version {card['version']!r} is already published",
                status_code=409,
            ) from exc
        await session.refresh(row)
        return {
            "id": row.id,
            "name": row.name,
            "version": row.version,
            "url": row.url,
            "published_by": row.published_by,
            "created_at": row.created_at.isoformat(),
        }

    return app


app = build_app()
