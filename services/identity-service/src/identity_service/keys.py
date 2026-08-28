"""RSA signing key for RS256 token issuance, and its JWKS representation.

Dev posture: generate an ephemeral keypair at startup unless one is provided via
``OAUTH2_SIGNING_KEY_PEM`` (see Settings). Tokens are short-lived (minutes), so a restart
invalidating previously-issued tokens is expected, not a bug.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm


@dataclass
class SigningKey:
    kid: str
    private_pem: str
    public_key: rsa.RSAPublicKey

    def jwk(self) -> dict:
        jwk = json.loads(RSAAlgorithm.to_jwk(self.public_key))
        jwk.update(kid=self.kid, use="sig", alg="RS256")
        return jwk


def generate_signing_key(*, pem: str | None = None) -> SigningKey:
    """Build a ``SigningKey`` from a provided PEM, or generate a fresh RSA-2048 keypair."""
    if pem:
        private_key = serialization.load_pem_private_key(pem.encode(), password=None)
        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise ValueError("OAUTH2_SIGNING_KEY_PEM must be an RSA private key")
        private_pem = pem
    else:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()

    return SigningKey(
        kid=secrets.token_hex(8),
        private_pem=private_pem,
        public_key=private_key.public_key(),
    )
