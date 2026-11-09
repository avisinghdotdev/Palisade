# The browser-side passkey simulator and the raw JSON it exchanges carry no types.
# pyright: reportMissingTypeStubs=false, reportAny=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false
# pyright: reportExplicitAny=false

import base64
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, override
from uuid import UUID, uuid7

import httpx2
import pytest
from agent_fixtures import AGENT, ORIGIN, allow, seed, signed
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fido2 import cbor
from fido2.utils import sha256
from order_fixtures import (
    CAPTURE_PATH,
    CREATE_PATH,
    ORDER_PATH,
    cart_with_shoes,
    created_order,
)
from paypal_stub import PayPalStub, json_answer
from pydantic import BaseModel, JsonValue
from soft_webauthn import SoftWebauthnDevice
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.activity.models import ActivityRecord
from palisade_api.activity.store import Activities
from palisade_api.agents.registry import Agents, BuiltinAgent
from palisade_api.agents.signatures import AgentSignatures, agent_signatures
from palisade_api.main import app
from palisade_api.mandates.models import MandateRecord
from palisade_api.mandates.passkeys import Passkeys
from palisade_api.mandates.router import mandates as mandates_dependency
from palisade_api.mandates.router import passkeys as passkeys_dependency
from palisade_api.mandates.router import tokens as tokens_dependency
from palisade_api.mandates.router import verifier as verifier_dependency
from palisade_api.mandates.service import IssuedMandate, Mandate, Mandates
from palisade_api.mandates.tokens import JWKS_PATH, MandateTokens, header
from palisade_api.mandates.verification import MandateVerification, Verifier
from palisade_api.orders.calls import PayPalCalls
from palisade_api.orders.checkout import Checkout
from palisade_api.orders.models import OrderRecord
from palisade_api.orders.router import orders as orders_dependency
from palisade_api.orders.store import Order, Orders
from palisade_api.paypal.auth import PayPalAuth
from palisade_api.paypal.client import PayPalClient
from palisade_api.policy.engine import Policies
from palisade_api.shop.cart import Carts
from palisade_api.shop.models import CartRecord
from palisade_api.shop.router import carts as carts_dependency
from palisade_api.shop.router import checkout as checkout_dependency

pytestmark = pytest.mark.anyio

OTHER_AGENT = "other-agent"
# The authenticator data's "user verified" flag: the device checked the person, not only a touch.
USER_VERIFIED = 0x04
DEMO_SIGNER_SEED = seed()


class Unit(BaseModel):
    custom_id: str | None = None


class CreateBody(BaseModel):
    purchase_units: list[Unit]


class VerifyingDevice(SoftWebauthnDevice):
    """soft-webauthn as a platform authenticator after a PIN or face check: the flag set."""

    @override
    def create(self, options: dict[str, Any], origin: str) -> dict[str, Any]:
        credential: dict[str, Any] = super().create(options, origin)
        response: dict[str, Any] = credential["response"]
        decoded = cbor.decode(response["attestationObject"])
        assert isinstance(decoded, dict)
        attestation: dict[str, Any] = decoded
        data = bytearray(attestation["authData"])
        data[32] |= USER_VERIFIED
        attestation["authData"] = bytes(data)
        response["attestationObject"] = cbor.encode(attestation)
        return credential

    @override
    def get(self, options: dict[str, Any], origin: str) -> dict[str, Any]:
        credential: dict[str, Any] = super().get(options, origin)
        response: dict[str, Any] = credential["response"]
        data = bytearray(response["authenticatorData"])
        data[32] |= USER_VERIFIED
        response["authenticatorData"] = bytes(data)
        key: ec.EllipticCurvePrivateKey | None = self.private_key
        assert key is not None
        response["signature"] = key.sign(
            bytes(data) + sha256(response["clientDataJSON"]),
            ec.ECDSA(hashes.SHA256()),
        )
        return credential


@pytest.fixture
def paypal() -> PayPalStub:
    stub = PayPalStub()
    stub.on("POST", CREATE_PATH, json_answer(201, created_order()))
    stub.on(
        "POST", CAPTURE_PATH, json_answer(201, {"id": "CAP-1", "status": "COMPLETED"})
    )
    return stub


@pytest.fixture
async def agent_seed(sessions: async_sessionmaker[AsyncSession]) -> str:
    agent_seed = seed()
    await Agents(sessions).register(BuiltinAgent(AGENT, "Test Agent", agent_seed))
    return agent_seed


@pytest.fixture
def tokens() -> MandateTokens:
    return MandateTokens(seed())


@pytest.fixture
def shop(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    tokens: MandateTokens,
) -> Iterator[Mandates]:
    http = paypal.client()
    paypal_client = PayPalClient(http, PayPalAuth(http, "id", "secret"))
    calls = PayPalCalls(sessions, paypal_client)
    agents = Agents(sessions)
    orders = Orders(sessions)
    policies = Policies(sessions)
    activities = Activities(sessions)
    passkeys = Passkeys(sessions, ORIGIN)
    mandates = Mandates(
        sessions, tokens, passkeys, policies, activities, DEMO_SIGNER_SEED, ORIGIN
    )
    app.dependency_overrides[verifier_dependency] = lambda: Verifier(
        sessions, tokens, passkeys, policies, paypal_client, DEMO_SIGNER_SEED
    )
    app.dependency_overrides[tokens_dependency] = lambda: tokens
    app.dependency_overrides[carts_dependency] = lambda: Carts(sessions)
    app.dependency_overrides[checkout_dependency] = lambda: Checkout(
        sessions, calls, orders, policies, mandates, activities, source="api"
    )
    app.dependency_overrides[orders_dependency] = lambda: orders
    app.dependency_overrides[agent_signatures] = lambda: AgentSignatures(
        sessions, agents, activities, ORIGIN
    )
    app.dependency_overrides[mandates_dependency] = lambda: mandates
    app.dependency_overrides[passkeys_dependency] = lambda: passkeys
    yield mandates
    app.dependency_overrides.clear()


async def ask(
    client: httpx2.AsyncClient,
    agent_seed: str,
    body: dict[str, JsonValue],
    *,
    agent_id: str = AGENT,
) -> httpx2.Response:
    return await client.send(
        signed(client, "/api/mandates", agent_seed, json=body, agent_id=agent_id)
    )


def cart_request(wallet_id: UUID, cart_id: UUID, cap: str) -> dict[str, JsonValue]:
    return {
        "kind": "cart",
        "wallet_id": str(wallet_id),
        "cap": {"value": cap, "currency_code": "USD"},
        "cart_id": str(cart_id),
    }


async def cart_mandate(
    client: httpx2.AsyncClient, wallet_id: UUID, agent_seed: str
) -> tuple[UUID, IssuedMandate]:
    cart = await cart_with_shoes(client, wallet_id)
    response = await ask(client, agent_seed, cart_request(wallet_id, cart.id, "500.00"))
    assert response.status_code == 201, response.text
    return cart.id, IssuedMandate.model_validate_json(response.content)


async def intent_mandate(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    cap: str,
    categories: list[JsonValue],
) -> IssuedMandate:
    response = await ask(
        client,
        agent_seed,
        {
            "kind": "intent",
            "wallet_id": str(wallet_id),
            "cap": {"value": cap, "currency_code": "USD"},
            "categories": categories,
        },
    )
    assert response.status_code == 201, response.text
    return IssuedMandate.model_validate_json(response.content)


def opened(mandate: IssuedMandate) -> dict[str, str]:
    return {"X-Mandate-Key": mandate.key}


async def approve(client: httpx2.AsyncClient, mandate: IssuedMandate) -> Mandate:
    response = await client.post(
        f"/api/mandates/{mandate.id}/approval",
        json={"signed_by": "demo_signer"},
        headers=opened(mandate),
    )
    assert response.status_code == 200, response.text
    return Mandate.model_validate_json(response.content)


async def checkout(
    client: httpx2.AsyncClient,
    agent_seed: str,
    cart_id: UUID,
    mandate: IssuedMandate,
    *,
    key: str | None = None,
) -> httpx2.Response:
    return await client.send(
        signed(
            client,
            f"/api/shop/carts/{cart_id}/checkout",
            agent_seed,
            json={
                "mandate_id": str(mandate.id),
                "mandate_key": mandate.key if key is None else key,
            },
        )
    )


@pytest.mark.usefixtures("shop")
async def test_a_cart_mandate_is_asked_signed_and_spent(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    tokens: MandateTokens,
) -> None:
    policy_hash = await allow(sessions)
    cart_id, asked = await cart_mandate(client, wallet_id, agent_seed)
    assert (asked.status, asked.kind, asked.policy_hash, asked.demo_wallet) == (
        "pending",
        "cart",
        policy_hash,
        True,
    )
    assert [(line.name, line.quantity) for line in asked.lines] == [
        ("Nike Free RN Flyknit", 1),
        ("Hoka Mach X 2", 2),
    ]
    assert asked.consent_path == f"/shop/approve/{asked.id}#key={asked.key}"
    assert asked.expires_at - asked.created_at == timedelta(minutes=15)

    signed_mandate = await approve(client, asked)
    assert (signed_mandate.status, signed_mandate.signed_by) == (
        "signed",
        "demo_signer",
    )

    response = await checkout(client, agent_seed, cart_id, asked)

    assert response.status_code == 200, response.text
    order = Order.model_validate_json(response.content)
    assert (order.channel, order.status) == ("agent", "captured")
    assert order.custom_id == f"{asked.id}:{asked.token_hash}"
    assert order.mandate is not None
    assert (order.mandate.kind, order.mandate.signed_by) == ("cart", "demo_signer")
    assert order.mandate.lines == asked.lines
    [create] = paypal.sent("POST", CREATE_PATH)
    [unit] = CreateBody.model_validate_json(create.content).purchase_units
    assert unit.custom_id == order.custom_id
    async with sessions() as session:
        record = await session.get_one(MandateRecord, asked.id)
    claims = tokens.verify(record.token)
    assert (claims["jti"], claims["agent"], claims["vct"]) == (
        str(asked.id),
        AGENT,
        "mandate.checkout.1",
    )
    public = await client.get(f"/api/mandates/{asked.id}")
    assert public.json()["spent"]["value"] == "500.00"
    assert asked.key not in public.text


async def verification(
    client: httpx2.AsyncClient, mandate_id: UUID
) -> MandateVerification:
    response = await client.get(f"/api/mandates/{mandate_id}/verify")
    assert response.status_code == 200, response.text
    return MandateVerification.model_validate_json(response.content)


@pytest.mark.usefixtures("shop")
async def test_a_mandate_is_verifiable_with_the_published_key_and_paypals_order(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    tokens: MandateTokens,
) -> None:
    await allow(sessions)
    cart_id, asked = await cart_mandate(client, wallet_id, agent_seed)

    unsigned = await verification(client, asked.id)
    assert (unsigned.verified, unsigned.buyer_proof.verified) == (False, False)
    assert unsigned.buyer_proof.detail == "The buyer has not signed this mandate."
    assert unsigned.token.signature_valid is True
    assert unsigned.token.claims_match_record is True
    assert unsigned.payments == []

    await approve(client, asked)
    paid = await checkout(client, agent_seed, cart_id, asked)
    assert paid.status_code == 200, paid.text
    order = Order.model_validate_json(paid.content)
    paypal.on(
        "GET",
        ORDER_PATH,
        json_answer(
            200,
            {"id": "PP-ORDER-1", "purchase_units": [{"custom_id": order.custom_id}]},
        ),
    )

    checked = await verification(client, asked.id)

    assert checked.verified is True
    assert (checked.status, checked.token_hash.matches, checked.policy.in_force) == (
        "signed",
        True,
        True,
    )
    assert checked.buyer_proof.signed_by == "demo_signer"
    assert checked.buyer_proof.method is not None
    assert "simulated" in checked.buyer_proof.method.lower() or "stands in" in (
        checked.buyer_proof.method
    )
    [payment] = checked.payments
    assert (payment.order_number, payment.custom_id_at_paypal, payment.matches) == (
        order.number,
        order.custom_id,
        True,
    )
    assert checked.policy.rules is not None
    assert checked.policy.rules.allowed_agents == [AGENT]
    # The token names the key the JWKS publishes.
    published = await client.get(JWKS_PATH)
    assert published.status_code == 200
    [key] = published.json()["keys"]
    assert (key["kty"], key["crv"], key["alg"], key["kid"]) == (
        "OKP",
        "Ed25519",
        "EdDSA",
        tokens.kid,
    )
    assert "d" not in key
    assert checked.token.kid == tokens.kid
    async with sessions() as session:
        record = await session.get_one(MandateRecord, asked.id)
        assert header(record.token)["kid"] == tokens.kid
    assert checked.jwks_path == JWKS_PATH
    assert (await client.get(f"/api/mandates/{uuid7()}/verify")).status_code == 404


@pytest.mark.usefixtures("shop")
async def test_verification_fails_on_a_foreign_token_and_a_payment_that_names_another(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
) -> None:
    await allow(sessions)
    cart_id, asked = await cart_mandate(client, wallet_id, agent_seed)
    await approve(client, asked)
    assert (await checkout(client, agent_seed, cart_id, asked)).status_code == 200
    paypal.on(
        "GET",
        ORDER_PATH,
        json_answer(
            200,
            {
                "id": "PP-ORDER-1",
                "purchase_units": [{"custom_id": "someone-else:0000"}],
            },
        ),
    )

    mismatched = await verification(client, asked.id)

    assert mismatched.verified is False
    [payment] = mismatched.payments
    assert (payment.matches, payment.custom_id_at_paypal) == (
        False,
        "someone-else:0000",
    )
    assert mismatched.token.signature_valid is True
    # A token another issuer signed, swapped into the record: the hash the buyer signed no
    # longer matches it, and the published key does not verify it.
    stranger = MandateTokens(seed())
    async with sessions.begin() as session:
        record = await session.get_one(MandateRecord, asked.id)
        record.token = stranger.issue("cart", {"jti": str(asked.id)})

    foreign = await verification(client, asked.id)

    assert (foreign.verified, foreign.token.signature_valid) == (False, False)
    assert foreign.token.claims is None
    assert foreign.token_hash.matches is False
    assert foreign.token.kid == stranger.kid


@pytest.mark.usefixtures("shop")
async def test_a_mandate_answers_only_to_its_key(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await allow(sessions)
    cart_id, asked = await cart_mandate(client, wallet_id, agent_seed)
    stranger = {"X-Mandate-Key": "not-the-key"}

    for path, body in (
        ("approval", {"signed_by": "demo_signer"}),
        ("decline", None),
        ("passkey-registration", None),
    ):
        keyless = await client.post(f"/api/mandates/{asked.id}/{path}", json=body)
        assert keyless.status_code == 403, path
        wrong = await client.post(
            f"/api/mandates/{asked.id}/{path}", json=body, headers=stranger
        )
        assert wrong.status_code == 403, path
    await approve(client, asked)

    refused = await checkout(client, agent_seed, cart_id, asked, key="not-the-key")

    assert refused.status_code == 403
    assert "MANDATE_KEY_INVALID" in refused.text
    async with sessions() as session:
        refusal = await session.scalar(
            select(ActivityRecord)
            .where(ActivityRecord.kind == "mandate_refused")
            .order_by(ActivityRecord.id.desc())
        )
    assert refusal is not None
    assert refusal.mandate_id is None
    assert str(asked.id) not in str(refusal.details)


@pytest.mark.usefixtures("shop")
async def test_the_demo_signer_signs_only_for_the_demo_buyer(
    client: httpx2.AsyncClient,
    own_wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await allow(sessions)
    _, asked = await cart_mandate(client, own_wallet_id, agent_seed)
    assert asked.demo_wallet is False

    response = await client.post(
        f"/api/mandates/{asked.id}/approval",
        json={"signed_by": "demo_signer"},
        headers=opened(asked),
    )

    assert response.status_code == 422
    assert "demo buyer" in response.text
    assert (await client.get(f"/api/mandates/{asked.id}")).json()["status"] == "pending"


@pytest.mark.usefixtures("shop")
async def test_the_buyer_approves_the_signed_lines_and_a_changed_cart_is_refused(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await allow(sessions)
    cart_id, asked = await cart_mandate(client, wallet_id, agent_seed)
    removed = await client.put(
        f"/api/shop/carts/{cart_id}/lines",
        json={"product_id": "hoka-mach-x-2", "size": "9.5", "quantity": 0},
    )
    assert removed.status_code == 200

    shown = Mandate.model_validate_json(
        (await client.get(f"/api/mandates/{asked.id}")).content
    )
    assert [line.name for line in shown.lines] == [
        "Nike Free RN Flyknit",
        "Hoka Mach X 2",
    ]
    response = await client.post(
        f"/api/mandates/{asked.id}/approval",
        json={"signed_by": "demo_signer"},
        headers=opened(asked),
    )
    assert response.status_code == 409
    assert "cart changed" in response.text


@pytest.mark.usefixtures("shop")
async def test_a_mandate_request_is_checked_before_it_is_issued(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    own_wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await allow(sessions)
    cart = await cart_with_shoes(client, wallet_id)
    for cap in ("-5", "0", "abc", "Infinity", "1e30", "1000000.01", "10.005"):
        bad = await ask(
            client,
            agent_seed,
            {
                "kind": "intent",
                "wallet_id": str(wallet_id),
                "cap": {"value": cap, "currency_code": "USD"},
            },
        )
        assert bad.status_code == 422, cap
    unknown_category = await ask(
        client,
        agent_seed,
        {
            "kind": "intent",
            "wallet_id": str(wallet_id),
            "cap": {"value": "100.00", "currency_code": "USD"},
            "categories": ["road"],
        },
    )
    assert unknown_category.status_code == 422
    not_the_total = await ask(
        client, agent_seed, cart_request(wallet_id, cart.id, "900.00")
    )
    assert not_the_total.status_code == 422
    assert "500.00" in not_the_total.text
    other_wallets_cart = await ask(
        client, agent_seed, cart_request(own_wallet_id, cart.id, "500.00")
    )
    assert other_wallets_cart.status_code == 404


@pytest.mark.usefixtures("shop")
async def test_a_cart_mandate_covers_only_its_cart_and_only_once(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await allow(sessions)
    cart_id, asked = await cart_mandate(client, wallet_id, agent_seed)
    await approve(client, asked)
    other = await cart_with_shoes(client, wallet_id)

    elsewhere = await checkout(client, agent_seed, other.id, asked)
    assert elsewhere.status_code == 403
    assert "MANDATE_CART_MISMATCH" in elsewhere.text

    assert (await checkout(client, agent_seed, cart_id, asked)).status_code == 200
    again = await client.put(
        f"/api/shop/carts/{cart_id}/lines",
        json={"product_id": "nike-free-rn-flyknit", "size": "10", "quantity": 1},
    )
    assert again.status_code == 409


@pytest.mark.usefixtures("shop")
async def test_an_unanswered_or_declined_mandate_cannot_pay(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await allow(sessions)
    cart_id, asked = await cart_mandate(client, wallet_id, agent_seed)

    pending = await checkout(client, agent_seed, cart_id, asked)
    assert pending.status_code == 403
    assert "MANDATE_PENDING" in pending.text

    declined = await client.post(
        f"/api/mandates/{asked.id}/decline", headers=opened(asked)
    )
    assert declined.json()["status"] == "declined"
    again = await client.post(
        f"/api/mandates/{asked.id}/decline", headers=opened(asked)
    )
    assert again.status_code == 409
    refused = await checkout(client, agent_seed, cart_id, asked)
    assert "MANDATE_DECLINED" in refused.text


@pytest.mark.usefixtures("shop")
async def test_an_intent_mandate_covers_purchases_up_to_its_cap(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await allow(sessions)
    intent = await intent_mandate(client, wallet_id, agent_seed, "600.00", [])
    assert intent.expires_at - intent.created_at == timedelta(hours=24)
    await approve(client, intent)

    first = await cart_with_shoes(client, wallet_id)
    paid = await checkout(client, agent_seed, first.id, intent)
    assert paid.status_code == 200, paid.text
    assert (
        Order.model_validate_json(paid.content).custom_id
        == f"{intent.id}:{intent.token_hash}"
    )

    second = await cart_with_shoes(client, wallet_id)
    over = await checkout(client, agent_seed, second.id, intent)

    assert over.status_code == 403
    assert "MANDATE_CAP_EXCEEDED" in over.text
    assert (await client.get(f"/api/mandates/{intent.id}")).json()["spent"][
        "value"
    ] == "500.00"


@pytest.mark.usefixtures("shop")
async def test_an_order_whose_payment_is_unsettled_counts_against_the_cap(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A checkout still waiting on PayPal, or one PayPal never answered, may yet take money."""
    await allow(sessions)
    intent = await intent_mandate(client, wallet_id, agent_seed, "600.00", [])
    await approve(client, intent)
    for status in ("creating", "failed"):
        unsettled = await cart_with_shoes(client, wallet_id)
        async with sessions.begin() as session:
            session.add(
                OrderRecord(
                    id=uuid7(),
                    number=f"RR-T{status[:5].upper()}",
                    cart_id=unsettled.id,
                    wallet_id=wallet_id,
                    channel="agent",
                    agent_id=AGENT,
                    mandate_id=intent.id,
                    status=status,
                    currency="USD",
                    total_cents=50_000,
                )
            )
            _ = await session.execute(
                update(CartRecord)
                .where(CartRecord.id == unsettled.id)
                .values(status="checking_out")
            )
        assert (await client.get(f"/api/mandates/{intent.id}")).json()["spent"][
            "value"
        ] == "500.00"
        cart = await cart_with_shoes(client, wallet_id)

        over = await checkout(client, agent_seed, cart.id, intent)

        assert over.status_code == 403, status
        assert "MANDATE_CAP_EXCEEDED" in over.text
        async with sessions.begin() as session:
            _ = await session.execute(
                update(CartRecord)
                .where(CartRecord.id == unsettled.id)
                .values(status="open")
            )
            _ = await session.execute(
                update(OrderRecord)
                .where(OrderRecord.cart_id == unsettled.id)
                .values(status="declined")
            )


@pytest.mark.usefixtures("shop")
async def test_an_intent_mandate_keeps_to_its_categories(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await allow(sessions)
    intent = await intent_mandate(client, wallet_id, agent_seed, "900.00", ["Trail"])
    await approve(client, intent)
    cart = await cart_with_shoes(client, wallet_id)

    response = await checkout(client, agent_seed, cart.id, intent)

    assert response.status_code == 403
    assert "MANDATE_CATEGORY_NOT_ALLOWED:Road" in response.text
    assert "MANDATE_CATEGORY_NOT_ALLOWED:Tempo" in response.text


@pytest.mark.usefixtures("shop")
async def test_a_mandate_binds_its_agent_and_its_policy(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    other_seed = seed()
    await Agents(sessions).register(BuiltinAgent(OTHER_AGENT, "Other", other_seed))
    await allow(sessions, allowed_agents=[AGENT, OTHER_AGENT])
    cart_id, asked = await cart_mandate(client, wallet_id, agent_seed)
    await approve(client, asked)

    stranger = await client.send(
        signed(
            client,
            f"/api/shop/carts/{cart_id}/checkout",
            other_seed,
            json={"mandate_id": str(asked.id), "mandate_key": asked.key},
            agent_id=OTHER_AGENT,
        )
    )
    assert stranger.status_code == 403
    assert "MANDATE_AGENT_MISMATCH" in stranger.text

    await allow(
        sessions, allowed_agents=[AGENT, OTHER_AGENT], per_order_cap_cents=99_000
    )
    changed = await checkout(client, agent_seed, cart_id, asked)
    assert changed.status_code == 403
    assert "POLICY_CHANGED" in changed.text


@pytest.mark.usefixtures("shop")
async def test_an_expired_mandate_is_refused_and_cannot_be_answered(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await allow(sessions)
    cart_id, asked = await cart_mandate(client, wallet_id, agent_seed)
    await approve(client, asked)
    async with sessions.begin() as session:
        await session.execute(
            update(MandateRecord)
            .where(MandateRecord.id == asked.id)
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )

    response = await checkout(client, agent_seed, cart_id, asked)

    assert response.status_code == 403
    assert "MANDATE_EXPIRED" in response.text
    assert (await client.get(f"/api/mandates/{asked.id}")).json()["status"] == "expired"


@pytest.mark.usefixtures("shop")
async def test_the_agents_daily_cap_counts_its_mandated_orders(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await allow(sessions, per_agent_daily_cap_cents=60_000)
    intent = await intent_mandate(client, wallet_id, agent_seed, "5000.00", [])
    await approve(client, intent)
    first = await cart_with_shoes(client, wallet_id)
    assert (await checkout(client, agent_seed, first.id, intent)).status_code == 200

    second = await cart_with_shoes(client, wallet_id)
    response = await checkout(client, agent_seed, second.id, intent)

    assert response.status_code == 403
    assert "AGENT_DAILY_CAP_EXCEEDED" in response.text


@pytest.mark.usefixtures("shop")
async def test_only_a_signed_agent_asks_and_only_for_a_real_cart(
    client: httpx2.AsyncClient, wallet_id: UUID, agent_seed: str
) -> None:
    body = cart_request(wallet_id, uuid7(), "10.00")

    unsigned = await client.post("/api/mandates", json=body)
    assert unsigned.status_code == 401

    missing = await ask(client, agent_seed, body)
    assert missing.status_code == 404

    unknown = await client.send(
        signed(
            client,
            f"/api/shop/carts/{uuid7()}/checkout",
            agent_seed,
            json={"mandate_id": str(uuid7()), "mandate_key": "k"},
        )
    )
    assert unknown.status_code == 404


@pytest.mark.usefixtures("shop")
async def test_a_checkout_naming_no_mandate_is_refused_without_publishing_the_id(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """An agent that passes its cart id as a mandate id must not publish the cart id."""
    await allow(sessions)
    cart = await cart_with_shoes(client, wallet_id)

    response = await client.send(
        signed(
            client,
            f"/api/shop/carts/{cart.id}/checkout",
            agent_seed,
            json={"mandate_id": str(cart.id), "mandate_key": "k"},
        )
    )

    assert response.status_code == 403
    assert "MANDATE_UNKNOWN" in response.text
    async with sessions() as session:
        refusal = await session.scalar(
            select(ActivityRecord)
            .where(ActivityRecord.kind == "mandate_refused")
            .order_by(ActivityRecord.id.desc())
        )
    assert refusal is not None
    assert refusal.mandate_id is None
    assert refusal.details == {"reasons": ["MANDATE_UNKNOWN"]}


def browser_json(credential: dict[str, Any]) -> dict[str, JsonValue]:
    """soft-webauthn answers with raw bytes where a browser sends base64url strings."""

    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode().rstrip("=")

    response: dict[str, JsonValue] = {
        key: b64url(value) if isinstance(value, bytes) else value
        for key, value in credential["response"].items()
    }
    return {
        "id": b64url(credential["rawId"]),
        "rawId": b64url(credential["rawId"]),
        "type": credential["type"],
        "response": response,
        "clientExtensionResults": {},
    }


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


async def register_passkey(
    client: httpx2.AsyncClient, device: SoftWebauthnDevice, mandate: IssuedMandate
) -> httpx2.Response:
    """A passkey registered from the mandate's consent page, so it signs for its wallet."""
    started = await client.post(
        f"/api/mandates/{mandate.id}/passkey-registration", headers=opened(mandate)
    )
    assert started.status_code == 201, started.text
    start = started.json()
    options = start["options"]
    assert options["authenticatorSelection"]["userVerification"] == "required"
    attestation = device.create(
        {
            "publicKey": {
                "rp": options["rp"],
                "user": {
                    "id": unb64url(options["user"]["id"]),
                    "name": options["user"]["name"],
                    "displayName": options["user"]["displayName"],
                },
                "challenge": unb64url(options["challenge"]),
                "pubKeyCredParams": options["pubKeyCredParams"],
                "attestation": "none",
            }
        },
        ORIGIN,
    )
    return await client.post(
        "/api/passkeys",
        json={"challenge": start["challenge"], "credential": browser_json(attestation)},
    )


async def sign_with_passkey(
    client: httpx2.AsyncClient,
    device: SoftWebauthnDevice,
    mandate: IssuedMandate,
    passkey_id: str,
) -> httpx2.Response:
    sign_options = (
        await client.get(
            f"/api/mandates/{mandate.id}/passkey-options",
            params={"passkey_id": passkey_id},
            headers=opened(mandate),
        )
    ).json()
    assert sign_options["userVerification"] == "required"
    assert unb64url(sign_options["challenge"]) == bytes.fromhex(mandate.token_hash)
    assertion = device.get(
        {
            "publicKey": {
                "rpId": sign_options["rpId"],
                "challenge": unb64url(sign_options["challenge"]),
                "allowCredentials": [
                    {
                        "type": "public-key",
                        "id": unb64url(sign_options["allowCredentials"][0]["id"]),
                    }
                ],
            }
        },
        ORIGIN,
    )
    return await client.post(
        f"/api/mandates/{mandate.id}/approval",
        json={
            "signed_by": "passkey",
            "passkey_id": passkey_id,
            "credential": browser_json(assertion),
        },
        headers=opened(mandate),
    )


@pytest.mark.usefixtures("shop")
async def test_a_passkey_registers_for_its_wallet_and_signs_a_mandate(
    client: httpx2.AsyncClient,
    own_wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
) -> None:
    await allow(sessions)
    device = VerifyingDevice()
    cart_id, asked = await cart_mandate(client, own_wallet_id, agent_seed)
    registered = await register_passkey(client, device, asked)
    assert registered.status_code == 201, registered.text

    approved = await sign_with_passkey(client, device, asked, registered.json()["id"])

    assert approved.status_code == 200, approved.text
    assert approved.json()["signed_by"] == "passkey"
    paid = await checkout(client, agent_seed, cart_id, asked)
    assert paid.status_code == 200, paid.text
    paypal.on(
        "GET",
        ORDER_PATH,
        json_answer(
            200,
            {
                "id": "PP-ORDER-1",
                "purchase_units": [
                    {"custom_id": Order.model_validate_json(paid.content).custom_id}
                ],
            },
        ),
    )
    # The stored assertion verifies again, read-only, against the passkey on record.
    checked = await verification(client, asked.id)
    assert (checked.buyer_proof.signed_by, checked.buyer_proof.verified) == (
        "passkey",
        True,
    )
    assert checked.buyer_proof.method is not None
    assert "WebAuthn" in checked.buyer_proof.method


@pytest.mark.usefixtures("shop")
async def test_a_passkey_signs_only_for_its_wallet_and_only_with_the_person_verified(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    own_wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await allow(sessions)
    _, demo_request = await cart_mandate(client, wallet_id, agent_seed)
    device = VerifyingDevice()
    passkey_id = (await register_passkey(client, device, demo_request)).json()["id"]
    _, own_request = await cart_mandate(client, own_wallet_id, agent_seed)

    elsewhere = await client.get(
        f"/api/mandates/{own_request.id}/passkey-options",
        params={"passkey_id": passkey_id},
        headers=opened(own_request),
    )
    assert elsewhere.status_code == 404

    touched_only = await register_passkey(client, SoftWebauthnDevice(), own_request)
    assert touched_only.status_code == 422
    assert "verif" in touched_only.text.lower()
