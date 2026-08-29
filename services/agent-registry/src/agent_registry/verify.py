"""Verifies an Agent Card's JWS signature against identity-service's JWKS.

Same mechanism ``platform_core.auth`` already uses to verify bearer tokens (``jwt.PyJWKClient``,
fetch-by-kid) — applied here to a stored card signature instead of a request header. See the
harness design's D-029 (identity-service is the trust root for both) and D-031 (equality is
checked on the *decoded* payload, not byte-exact RFC 8785 canonicalization).
"""

from __future__ import annotations

from typing import Any

import jwt


class CardVerificationError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def verify_card_signature(
    jwks_client: jwt.PyJWKClient,
    card: dict[str, Any],
    signing_algorithm: str,
    signing_key_id: str,
    signature_value: str,
) -> None:
    """Raises ``CardVerificationError`` unless ``signature_value`` is a valid signature over
    exactly ``card``, from a key ``jwks_client``'s JWKS actually publishes under ``signing_key_id``.
    """
    try:
        signing_key = jwks_client.get_signing_key(signing_key_id)
    except jwt.PyJWKClientError as exc:
        raise CardVerificationError(f"unknown signing key: {exc}") from exc

    try:
        decoded = jwt.decode(signature_value, signing_key.key, algorithms=[signing_algorithm])
    except jwt.InvalidTokenError as exc:
        raise CardVerificationError(f"invalid signature: {exc}") from exc

    if decoded != card:
        raise CardVerificationError("signed payload does not match submitted card content")
