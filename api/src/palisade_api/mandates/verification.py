"""Public verification of a mandate: anyone can check what Palisade claims about it.

The checks run from what is stored and what PayPal holds: the issuer's signature on the token,
the token's claims against the record, the hash the buyer signed, the buyer's own proof over
that hash, the policy the mandate was signed under, and the custom id on every PayPal order the
mandate paid for. Nothing here needs the console or a key.
"""

import base64
from datetime import UTC, datetime
from typing import final
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, JsonValue, TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.agents.keys import private_key
from palisade_api.mandates.models import MandateRecord
from palisade_api.mandates.passkeys import PasskeyRefused, Passkeys, UnknownPasskey
from palisade_api.mandates.service import (
    MANDATE_KIND,
    MERCHANT,
    SIGNED_BY,
    MandateKind,
    MandateStatus,
    SignedBy,
    UnknownMandate,
    status_of,
)
from palisade_api.mandates.tokens import JWKS_PATH, MandateTokens, header, token_hash
from palisade_api.mandates.tokens import TYPES as TOKEN_TYPES
from palisade_api.orders.models import OrderRecord
from palisade_api.paypal.client import PayPalClient
from palisade_api.policy.engine import Policies, Rules
from palisade_api.policy.models import PolicyRecord
from palisade_api.shop.catalog import money

# How each proof is made, in words a verifier outside Palisade can act on.
METHODS = {
    "passkey": "A WebAuthn assertion by the buyer's passkey over the token hash as the challenge, user verification required.",
    "demo_signer": "An Ed25519 signature over the token hash by the demo signer, a server-held key that stands in for a buyer's passkey in PayPal's sandbox.",
}
UNSIGNED = "The buyer has not signed this mandate."
PROOF = TypeAdapter(dict[str, JsonValue])
ASSERTION = TypeAdapter(dict[str, JsonValue])


class TokenCheck(BaseModel):
    """The issuer's signature, and whether the token says what the record says."""

    signature_valid: bool
    # The issuer key's id from the token header; the key is at `jwks_path`.
    kid: str | None
    claims: dict[str, JsonValue] | None
    claims_match_record: bool


class HashCheck(BaseModel):
    """The hash the buyer signed and the payment names, recomputed from the token."""

    recorded: str
    computed: str
    matches: bool


class ProofCheck(BaseModel):
    signed_by: SignedBy | None
    signed_at: datetime | None
    method: str | None
    verified: bool
    detail: str | None


class PolicyCheck(BaseModel):
    """The merchant's rules the mandate was signed under, by their hash."""

    hash: str
    in_force: bool
    rules: Rules | None


class PaymentCheck(BaseModel):
    """One PayPal order the mandate paid for, and the custom id PayPal holds on it now."""

    order_number: str
    paypal_order_id: str
    custom_id_sent: str
    custom_id_at_paypal: str | None
    matches: bool


class MandateVerification(BaseModel):
    mandate_id: UUID
    kind: MandateKind
    status: MandateStatus
    token: TokenCheck
    token_hash: HashCheck
    buyer_proof: ProofCheck
    policy: PolicyCheck
    payments: list[PaymentCheck]
    # Every check above passed: the token is Palisade's, it matches the record, the buyer's
    # proof covers it, and every payment under it names it.
    verified: bool
    jwks_path: str
    checked_at: datetime


class PurchaseUnit(BaseModel):
    custom_id: str | None = None


class PayPalOrder(BaseModel):
    purchase_units: list[PurchaseUnit]


def expected_claims(record: MandateRecord) -> dict[str, JsonValue]:
    """The claims the token must carry for this record, as `Mandates.create` wrote them."""
    return {
        "vct": TOKEN_TYPES[record.kind],
        "jti": str(record.id),
        "merchant": MERCHANT,
        "agent": record.agent_id,
        "wallet": str(record.wallet_id),
        "cap": money(record.cap_cents).model_dump(),
        "categories": list(record.categories),
        "lines": list(record.lines),
        "policy_hash": record.policy_hash,
        "exp": int(record.expires_at.timestamp()),
    }


def claims_match(claims: dict[str, JsonValue], record: MandateRecord) -> bool:
    return all(
        claims.get(name) == value for name, value in expected_claims(record).items()
    )


@final
class Verifier:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        tokens: MandateTokens,
        passkeys: Passkeys,
        policies: Policies,
        paypal: PayPalClient,
        demo_signer_seed: str,
    ) -> None:
        self._sessions = sessions
        self._tokens = tokens
        self._passkeys = passkeys
        self._policies = policies
        self._paypal = paypal
        self._demo_signer_public = private_key(demo_signer_seed).public_key()

    async def verify(self, mandate_id: UUID) -> MandateVerification:
        """Every check on the mandate, from the stored record and PayPal's orders.

        Raises:
            UnknownMandate: no mandate has the id.
            PayPalError: PayPal refused to show an order the mandate paid for.
        """
        async with self._sessions() as session:
            record = await session.get(MandateRecord, mandate_id)
            if record is None:
                raise UnknownMandate(mandate_id)
            policy = await session.scalar(
                select(PolicyRecord).where(PolicyRecord.hash == record.policy_hash)
            )
            current = await self._policies.current(session)
            orders = (
                await session.scalars(
                    select(OrderRecord)
                    .where(
                        OrderRecord.mandate_id == mandate_id,
                        OrderRecord.paypal_order_id.is_not(None),
                        OrderRecord.custom_id.is_not(None),
                    )
                    .order_by(OrderRecord.created_at)
                )
            ).all()
        token = self._token_check(record)
        digest = HashCheck(
            recorded=record.token_hash,
            computed=token_hash(record.token),
            matches=record.token_hash == token_hash(record.token),
        )
        proof = await self._proof_check(record)
        payments = [await self._payment_check(order) for order in orders]
        return MandateVerification(
            mandate_id=record.id,
            kind=MANDATE_KIND.validate_python(record.kind),
            status=status_of(record),
            token=token,
            token_hash=digest,
            buyer_proof=proof,
            policy=PolicyCheck(
                hash=record.policy_hash,
                in_force=record.policy_hash == current.hash,
                rules=None
                if policy is None
                else Rules.model_validate(policy, from_attributes=True),
            ),
            payments=payments,
            verified=token.signature_valid
            and token.claims_match_record
            and digest.matches
            and proof.verified
            and all(payment.matches for payment in payments),
            jwks_path=JWKS_PATH,
            checked_at=datetime.now(UTC),
        )

    def _token_check(self, record: MandateRecord) -> TokenCheck:
        try:
            kid = header(record.token).get("kid")
        except ValueError:
            kid = None
        try:
            claims = self._tokens.verify(record.token)
        except Exception:  # noqa: BLE001
            # The library raises its own mix of errors for a bad signature, a mangled
            # disclosure, or a token that is not an SD-JWT at all; every one means "not valid".
            return TokenCheck(
                signature_valid=False,
                kid=kid if isinstance(kid, str) else None,
                claims=None,
                claims_match_record=False,
            )
        return TokenCheck(
            signature_valid=True,
            kid=kid if isinstance(kid, str) else None,
            claims=claims,
            claims_match_record=claims_match(claims, record),
        )

    async def _proof_check(self, record: MandateRecord) -> ProofCheck:
        signed_by = (
            None
            if record.signed_by is None
            else SIGNED_BY.validate_python(record.signed_by)
        )
        if signed_by is None or record.proof is None:
            return ProofCheck(
                signed_by=signed_by,
                signed_at=record.signed_at,
                method=None,
                verified=False,
                detail=UNSIGNED,
            )
        digest = bytes.fromhex(record.token_hash)
        proof = PROOF.validate_python(record.proof)
        detail = (
            await self._passkey_detail(record.wallet_id, digest, proof)
            if signed_by == "passkey"
            else self._demo_signer_detail(digest, proof)
        )
        return ProofCheck(
            signed_by=signed_by,
            signed_at=record.signed_at,
            method=METHODS[signed_by],
            verified=detail is None,
            detail=detail,
        )

    async def _passkey_detail(
        self, wallet_id: UUID, digest: bytes, proof: dict[str, JsonValue]
    ) -> str | None:
        """Why the stored passkey assertion does not verify; None when it does."""
        passkey_id, assertion = proof.get("passkey_id"), proof.get("assertion")
        if not isinstance(passkey_id, str) or not isinstance(assertion, dict):
            return "The stored proof names no passkey and assertion."
        try:
            await self._passkeys.check(
                UUID(passkey_id),
                wallet_id,
                digest,
                ASSERTION.validate_python(assertion),
            )
        except (UnknownPasskey, PasskeyRefused, ValueError) as error:
            return str(error)
        return None

    def _demo_signer_detail(
        self, digest: bytes, proof: dict[str, JsonValue]
    ) -> str | None:
        """Why the demo signer's signature does not verify; None when it does."""
        signature, public_key = proof.get("signature"), proof.get("public_key")
        if not isinstance(signature, str) or not isinstance(public_key, str):
            return "The stored proof holds no signature and public key."
        try:
            key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key))
            key.verify(base64.b64decode(signature), digest)
        except ValueError, InvalidSignature:
            return "The signature does not verify over the token hash."
        if key.public_bytes_raw() != self._demo_signer_public.public_bytes_raw():
            return "The signing key is not the demo signer's."
        return None

    async def _payment_check(self, order: OrderRecord) -> PaymentCheck:
        """PayPal's order read now, so the custom id is PayPal's word, not the store's copy.

        Raises:
            PayPalError: PayPal refused the read or did not answer.
        """
        if order.paypal_order_id is None or order.custom_id is None:
            raise ValueError("a payment check needs an order PayPal created")
        response = await self._paypal.get(
            f"/v2/checkout/orders/{order.paypal_order_id}"
        )
        units = PayPalOrder.model_validate(response.body).purchase_units
        at_paypal = units[0].custom_id if units else None
        return PaymentCheck(
            order_number=order.number,
            paypal_order_id=order.paypal_order_id,
            custom_id_sent=order.custom_id,
            custom_id_at_paypal=at_paypal,
            matches=at_paypal == order.custom_id,
        )
