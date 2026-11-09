"""The mandate token: an SD-JWT Palisade issues and signs, shaped after AP2's Checkout Mandate.

AP2 names the closed cart mandate `mandate.checkout.1` and the open one, which allows purchases
up to a cap, `mandate.checkout.open.1`. The cart lines and the policy hash are selectively
disclosable, so a buyer could hand the token on without them. The token's header names the
issuer key by its JWK thumbprint, which the JWKS endpoint publishes, so anyone can verify a
token with public keys alone.
"""

# The sd-jwt and jwcrypto packages ship no type information.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false
# pyright: reportUnknownLambdaType=false

import base64
import hashlib
from typing import Final, final

from jwcrypto.jwk import JWK
from pydantic import JsonValue, TypeAdapter
from sd_jwt.common import SDObj
from sd_jwt.issuer import SDJWTIssuer
from sd_jwt.verifier import SDJWTVerifier

from palisade_api.agents.keys import private_key

TYPES: Final = {"cart": "mandate.checkout.1", "intent": "mandate.checkout.open.1"}
# Claims a holder may leave undisclosed.
DISCLOSABLE: Final = frozenset({"lines", "policy_hash"})
CLAIMS = TypeAdapter(dict[str, JsonValue])
JWKS_PATH = "/.well-known/jwks.json"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def token_hash(token: str) -> str:
    """SHA-256 of the token as issued, hex: what the buyer signs and `custom_id` carries."""
    return hashlib.sha256(token.encode()).hexdigest()


def header(token: str) -> dict[str, JsonValue]:
    """The token's protected header, read without checking anything.

    Raises:
        ValueError: the token does not start with a JWS header.
    """
    first = token.split("~", 1)[0].split(".", 1)[0]
    try:
        return CLAIMS.validate_json(unb64url(first))
    except ValueError as error:
        raise ValueError("the token does not start with a JWS header") from error


@final
class MandateTokens:
    """Issues and verifies mandate tokens with Palisade's Ed25519 issuer key."""

    def __init__(self, seed: str) -> None:
        key = private_key(seed)
        parts = {
            "kty": "OKP",
            "crv": "Ed25519",
            "x": b64url(key.public_key().public_bytes_raw()),
        }
        # RFC 7638: the key's id is the thumbprint of its public parts.
        self.kid: str = JWK(**parts).thumbprint()
        self._jwk = JWK(**parts, d=b64url(key.private_bytes_raw()), kid=self.kid)
        self._public = JWK.from_json(self._jwk.export_public())

    def issue(self, kind: str, claims: dict[str, JsonValue]) -> str:
        """The signed token for `claims`, with the disclosable ones wrapped as such."""
        payload: dict[object, JsonValue] = {"vct": TYPES[kind]}
        for name, value in claims.items():
            payload[SDObj(name) if name in DISCLOSABLE else name] = value
        issuer = SDJWTIssuer(
            payload,
            self._jwk,
            sign_alg="EdDSA",
            extra_header_parameters={"kid": self.kid},
        )
        return str(issuer.sd_jwt_issuance)

    def verify(self, token: str) -> dict[str, JsonValue]:
        """The token's claims, every disclosure included; raises when the signature fails."""
        # The library annotates the key callback as returning a string; it takes a JWK.
        verifier = SDJWTVerifier(
            token,
            cb_get_issuer_key=lambda _issuer, _header: self._public,  # pyright: ignore[reportArgumentType]
        )
        return CLAIMS.validate_python(verifier.get_verified_payload())

    def jwks(self) -> dict[str, JsonValue]:
        """The issuer's public key as a JWK Set, for anyone verifying a token on their own."""
        public = CLAIMS.validate_json(self._public.export_public())
        return {"keys": [{**public, "alg": "EdDSA", "use": "sig"}]}
