"""Thin client for the demo delegated-token flow against identity-service.

Graceful like platform_core.svid: every entry point returns None on failure rather than raising —
example-service also runs under `core` alone (no identity-service), and /proxy must keep working
plain when there's nothing to delegate through. See the harness spec's reference flow.
"""

from __future__ import annotations

import httpx

from platform_core.logging import get_logger

log = get_logger("example_service.identity_client")

TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"


async def try_get_delegated_token(
    identity_service_url: str,
    *,
    actor_client_id: str,
    actor_client_secret: str,
    principal_client_id: str,
    principal_client_secret: str,
    timeout: float = 3.0,  # noqa: ASYNC109 - request budget, not a cancellation deadline
) -> str | None:
    """A bearer token for ``actor_client_id`` acting on behalf of ``principal_client_id``."""
    try:
        async with httpx.AsyncClient(base_url=identity_service_url, timeout=timeout) as client:
            actor = await _client_credentials(client, actor_client_id, actor_client_secret)
            subject = await _client_credentials(
                client, principal_client_id, principal_client_secret
            )
            resp = await client.post(
                "/oauth/token",
                data={
                    "grant_type": TOKEN_EXCHANGE_GRANT,
                    "subject_token": subject,
                    "actor_token": actor,
                },
            )
            resp.raise_for_status()
            return resp.json()["access_token"]
    except Exception:  # noqa: BLE001 - identity-service being unreachable isn't fatal here
        log.info(
            "delegated_token_unavailable", detail="identity-service unreachable — falling back"
        )
        return None


async def _client_credentials(client: httpx.AsyncClient, client_id: str, secret: str) -> str:
    resp = await client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": secret,
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]
