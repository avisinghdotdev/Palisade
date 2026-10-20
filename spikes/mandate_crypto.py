"""Spike S8: the three signatures Palisade relies on, each issued and verified in one process.

- An AP2-style checkout mandate as an SD-JWT: the buyer's key signs it, cart lines are
    selectively disclosable, the verifier checks the signature and reads the disclosed claims.
- An RFC 9421 HTTP message signature over a checkout request, signed with the agent's Ed25519
    key and verified by key id.
- A passkey: WebAuthn registration and assertion from a software authenticator, verified with
    py_webauthn, standing in for the buyer's browser.

Run from the repo root, no credentials needed:

    uv run --no-project --python 3.14 --with sd-jwt --with http-message-signatures --with webauthn \
        --with soft-webauthn --with requests python spikes/mandate_crypto.py
"""

import base64
import hashlib
import json
import secrets
import sys
from datetime import UTC, datetime
from typing import Any

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from http_message_signatures import (
    HTTPMessageSigner,
    HTTPMessageVerifier,
    HTTPSignatureKeyResolver,
    algorithms,
)
from jwcrypto.jwk import JWK
from sd_jwt.common import SDObj
from sd_jwt.holder import SDJWTHolder
from sd_jwt.issuer import SDJWTIssuer
from sd_jwt.verifier import SDJWTVerifier
from soft_webauthn import SoftWebauthnDevice
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import PublicKeyCredentialDescriptor


def check(condition: bool, message: str) -> None:
    if not condition:
        sys.exit(f"FAILED: {message}")
    print(f"  ok: {message}")


def mandate_sd_jwt() -> None:
    print("\n[1] checkout mandate as an SD-JWT")
    buyer_key = JWK.generate(kty="EC", crv="P-256")
    claims: dict[str, Any] = {
        "iss": "https://palisade.avisingh.dev/buyers/maya",
        "sub": "mandate:01JAX3K9V8Q2M7R4T6W8Y0Z1AB",
        "merchant": "ridgeline-running",
        "agent": "palisade-shopper",
        "amount_cap": {"currency_code": "USD", "value": "120.00"},
        "expires_at": "2026-10-21T00:00:00Z",
        SDObj("cart"): [
            {"sku": "RR-SHOE-10", "name": "Trail runner, size 10", "quantity": 1}
        ],
        SDObj("policy_hash"): hashlib.sha256(b"policy v1").hexdigest(),
    }
    issuer = SDJWTIssuer(claims, buyer_key, sign_alg="ES256")
    issuance = issuer.sd_jwt_issuance
    print(
        f"  issuance {len(issuance)} characters, {issuance.count('~') - 1} disclosures"
    )

    holder = SDJWTHolder(issuance)
    holder.create_presentation({"cart": True})
    presentation = holder.sd_jwt_presentation
    check(presentation.count("~") - 1 == 1, "the presentation discloses only the cart")

    public_key = JWK.from_json(buyer_key.export_public())
    verifier = SDJWTVerifier(
        presentation, cb_get_issuer_key=lambda issuer, header: public_key
    )
    verified = verifier.get_verified_payload()
    check(verified["amount_cap"]["value"] == "120.00", "amount cap verified")
    check(verified["cart"][0]["sku"] == "RR-SHOE-10", "disclosed cart line verified")
    check("policy_hash" not in verified, "undisclosed claim stays hidden")
    check(
        len(hashlib.sha256(issuance.encode()).hexdigest()) == 64,
        "mandate hash for custom_id is 64 hex characters",
    )

    jws, *rest = presentation.split("~")
    header, payload, signature = jws.split(".")
    padded = payload + "=" * (-len(payload) % 4)
    claims_json = base64.urlsafe_b64decode(padded).decode().replace("120.00", "999.00")
    forged = base64.urlsafe_b64encode(claims_json.encode()).decode().rstrip("=")
    tampered = "~".join([f"{header}.{forged}.{signature}", *rest])
    try:
        SDJWTVerifier(
            tampered, cb_get_issuer_key=lambda issuer, header: public_key
        ).get_verified_payload()
    except Exception as error:  # noqa: BLE001 - any failure is the right answer here
        check(True, f"tampered mandate rejected ({type(error).__name__})")
    else:
        sys.exit("FAILED: tampered mandate accepted")


class AgentKeys(HTTPSignatureKeyResolver):
    def __init__(self) -> None:
        self.private = ed25519.Ed25519PrivateKey.generate()
        self.public = self.private.public_key()

    def resolve_public_key(self, key_id: str) -> ed25519.Ed25519PublicKey:
        if key_id != "palisade-shopper-1":
            raise KeyError(key_id)
        return self.public

    def resolve_private_key(self, key_id: str) -> ed25519.Ed25519PrivateKey:
        return self.private


def agent_signature() -> None:
    print("\n[2] RFC 9421 signature on a checkout request")
    keys = AgentKeys()
    body = json.dumps(
        {"cart_id": "cart_123", "mandate_id": "01JAX3K9V8Q2M7R4T6W8Y0Z1AB"}
    )
    request = requests.Request(
        "POST",
        "https://palisade.avisingh.dev/api/shop/checkout",
        data=body,
        headers={"Content-Type": "application/json"},
    ).prepare()
    digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
    request.headers["Content-Digest"] = f"sha-256=:{digest}:"

    signer = HTTPMessageSigner(
        signature_algorithm=algorithms.ED25519, key_resolver=keys
    )
    signer.sign(
        request,
        key_id="palisade-shopper-1",
        covered_component_ids=(
            "@method",
            "@authority",
            "@path",
            "content-digest",
            "content-type",
        ),
        created=datetime.now(UTC),
        nonce=secrets.token_urlsafe(16),
    )
    print(f"  Signature-Input: {request.headers['Signature-Input'][:90]}...")

    verifier = HTTPMessageVerifier(
        signature_algorithm=algorithms.ED25519, key_resolver=keys
    )
    results = verifier.verify(request)
    check(bool(results), "signature verified by key id")

    request.headers["Content-Digest"] = "sha-256=:AAAA:"
    try:
        verifier.verify(request)
    except Exception as error:  # noqa: BLE001 - any failure is the right answer here
        check(True, f"altered digest rejected ({type(error).__name__})")
    else:
        sys.exit("FAILED: altered request accepted")

    public_pem = keys.public.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    check(
        b"BEGIN PUBLIC KEY" in public_pem,
        "agent public key exports as PEM for registration",
    )


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def browser_json(credential: dict[str, Any]) -> dict[str, Any]:
    """soft-webauthn hands back raw bytes; a browser hands back base64url strings."""
    response = {
        key: b64url(value) if isinstance(value, bytes) else value
        for key, value in credential["response"].items()
    }
    return {
        "id": b64url(credential["rawId"]),
        "rawId": b64url(credential["rawId"]),
        "type": credential["type"],
        "response": response,
    }


def passkey() -> None:
    print("\n[3] passkey registration and assertion")
    rp_id = "palisade.avisingh.dev"
    origin = f"https://{rp_id}"
    device = SoftWebauthnDevice()

    registration = generate_registration_options(
        rp_id=rp_id, rp_name="Palisade", user_name="maya", user_id=b"buyer-maya"
    )
    # soft-webauthn wants the browser-side dict with bytes, not the JSON py_webauthn emits.
    attestation = device.create(
        {
            "publicKey": {
                "rp": {"id": rp_id, "name": "Palisade"},
                "user": {"id": b"buyer-maya", "name": "maya", "displayName": "Maya"},
                "challenge": registration.challenge,
                "pubKeyCredParams": [{"type": "public-key", "alg": -7}],
                "attestation": "none",
            }
        },
        origin,
    )
    verified = verify_registration_response(
        credential=browser_json(attestation),
        expected_challenge=registration.challenge,
        expected_rp_id=rp_id,
        expected_origin=origin,
    )
    print(f"  credential id {b64url(verified.credential_id)[:24]}...")
    check(verified.sign_count == 0, "registration verified, sign count starts at 0")

    mandate_hash = hashlib.sha256(b"mandate token").digest()
    authentication = generate_authentication_options(
        rp_id=rp_id,
        challenge=mandate_hash,
        allow_credentials=[PublicKeyCredentialDescriptor(id=verified.credential_id)],
    )
    assertion = device.get(
        {
            "publicKey": {
                "rpId": rp_id,
                "challenge": authentication.challenge,
                "allowCredentials": [
                    {"type": "public-key", "id": verified.credential_id}
                ],
            }
        },
        origin,
    )
    result = verify_authentication_response(
        credential=browser_json(assertion),
        expected_challenge=mandate_hash,
        expected_rp_id=rp_id,
        expected_origin=origin,
        credential_public_key=verified.credential_public_key,
        credential_current_sign_count=verified.sign_count,
    )
    check(result.new_sign_count > 0, "assertion over the mandate hash verified")


if __name__ == "__main__":
    mandate_sd_jwt()
    agent_signature()
    passkey()
    print("\nAll three signatures issue and verify.")
