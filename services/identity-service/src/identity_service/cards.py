"""Signs Agent Cards with identity-service's existing OAuth2 RSA key.

See the Agent Registry harness design (D-029): no second trust root — the same ``SigningKey``
that signs OAuth2 tokens signs cards, and ``agent-registry`` verifies against the same
``/.well-known/jwks.json`` a resource server would use to verify a bearer token.
"""

from __future__ import annotations

import jwt

from .keys import SigningKey


def sign_card(signing_key: SigningKey, card: dict) -> dict:
    signature_value = jwt.encode(
        card, signing_key.private_pem, algorithm="RS256", headers={"kid": signing_key.kid}
    )
    return {
        "signing_algorithm": "RS256",
        "signing_key_id": signing_key.kid,
        "signature_value": signature_value,
    }
