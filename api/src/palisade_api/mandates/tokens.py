"""The mandate token: an SD-JWT Palisade issues and signs, shaped after AP2's Checkout Mandate.

AP2 names the closed cart mandate `mandate.checkout.1` and the open one, which allows purchases
up to a cap, `mandate.checkout.open.1`. The cart lines and the policy hash are selectively
disclosable, so a buyer could hand the token on without them.
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


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def token_hash(token: str) -> str:
    """SHA-256 of the token as issued, hex: what the buyer signs and `custom_id` carries."""
    return hashlib.sha256(token.encode()).hexdigest()


@final
class MandateTokens:
    """Issues and verifies mandate tokens with Palisade's Ed25519 issuer key."""

    def __init__(self, seed: str) -> None:
        key = private_key(seed)
        self._jwk = JWK(
            kty="OKP",
            crv="Ed25519",
            d=b64url(key.private_bytes_raw()),
            x=b64url(key.public_key().public_bytes_raw()),
        )
        self._public = JWK.from_json(self._jwk.export_public())

    def issue(self, kind: str, claims: dict[str, JsonValue]) -> str:
        """The signed token for `claims`, with the disclosable ones wrapped as such."""
        payload: dict[object, JsonValue] = {"vct": TYPES[kind]}
        for name, value in claims.items():
            payload[SDObj(name) if name in DISCLOSABLE else name] = value
        issuer = SDJWTIssuer(payload, self._jwk, sign_alg="EdDSA")
        return str(issuer.sd_jwt_issuance)

    def verify(self, token: str) -> dict[str, JsonValue]:
        """The token's claims, every disclosure included; raises when the signature fails."""
        # The library annotates the key callback as returning a string; it takes a JWK.
        verifier = SDJWTVerifier(
            token,
            cb_get_issuer_key=lambda _issuer, _header: self._public,  # pyright: ignore[reportArgumentType]
        )
        return CLAIMS.validate_python(verifier.get_verified_payload())
