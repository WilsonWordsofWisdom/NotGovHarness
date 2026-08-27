"""JWT issuance: the AIP-shaped claim model from the harness design spec.

Standard claims (iss/aud/exp/iat/jti/scope) plus agent-grade ones: ``sub`` (the principal),
``mode`` (delegated vs autonomous), ``act`` (RFC 8693 actor chain, delegated only), ``prov``
(provenance for Audit, delegated only), ``depth`` (delegation depth, enforced at verify by
platform-core — this module only stamps it).

Verification lives in ``platform-core`` (the ``oauth2`` auth mode), not here — identity-service
only issues.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import jwt

from .config import Settings
from .keys import SigningKey


@dataclass
class IssuedToken:
    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 0
    scope: str = ""


def issue_autonomous_token(
    signing_key: SigningKey,
    settings: Settings,
    *,
    client_id: str,
    scope: str,
) -> IssuedToken:
    """``grant_type=client_credentials`` — the agent acting as itself, not on anyone's behalf."""
    now = int(time.time())
    claims = {
        "iss": settings.oauth2_issuer,
        "aud": settings.oauth2_audience,
        "iat": now,
        "exp": now + settings.access_token_ttl_seconds,
        "jti": uuid.uuid4().hex,
        "sub": client_id,
        "scope": scope,
        "mode": "autonomous",
        "depth": 0,
    }
    encoded = jwt.encode(
        claims, signing_key.private_pem, algorithm="RS256", headers={"kid": signing_key.kid}
    )
    return IssuedToken(
        access_token=encoded, expires_in=settings.access_token_ttl_seconds, scope=scope
    )
