# The browser-side passkey simulator and the raw JSON it exchanges carry no types.
# pyright: reportMissingTypeStubs=false, reportAny=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false

import base64
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid7

import httpx2
import pytest
from agent_fixtures import AGENT, ORIGIN, allow, seed, signed
from order_fixtures import CAPTURE_PATH, CREATE_PATH, cart_with_shoes, created_order
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
from palisade_api.mandates.service import Mandate, Mandates
from palisade_api.mandates.tokens import MandateTokens
from palisade_api.orders.calls import PayPalCalls
from palisade_api.orders.checkout import Checkout
from palisade_api.orders.router import orders as orders_dependency
from palisade_api.orders.store import Order, Orders
from palisade_api.paypal.auth import PayPalAuth
from palisade_api.paypal.client import PayPalClient
from palisade_api.policy.engine import Policies
from palisade_api.shop.cart import Carts
from palisade_api.shop.router import carts as carts_dependency
from palisade_api.shop.router import checkout as checkout_dependency

pytestmark = pytest.mark.anyio

OTHER_AGENT = "other-agent"


class Unit(BaseModel):
    custom_id: str | None = None


class CreateBody(BaseModel):
    purchase_units: list[Unit]


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
    calls = PayPalCalls(sessions, PayPalClient(http, PayPalAuth(http, "id", "secret")))
    agents = Agents(sessions)
    orders = Orders(sessions)
    policies = Policies(sessions)
    activities = Activities(sessions)
    passkeys = Passkeys(sessions, ORIGIN)
    mandates = Mandates(
        sessions, tokens, passkeys, policies, activities, seed(), ORIGIN
    )
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


async def cart_mandate(
    client: httpx2.AsyncClient, wallet_id: UUID, agent_seed: str
) -> tuple[UUID, Mandate]:
    cart = await cart_with_shoes(client, wallet_id)
    response = await ask(
        client,
        agent_seed,
        {
            "kind": "cart",
            "wallet_id": str(wallet_id),
            "cap": {"value": "500.00", "currency_code": "USD"},
            "cart_id": str(cart.id),
        },
    )
    assert response.status_code == 201, response.text
    return cart.id, Mandate.model_validate_json(response.content)


async def intent_mandate(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    cap: str,
    categories: list[JsonValue],
) -> Mandate:
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
    return Mandate.model_validate_json(response.content)


async def approve(client: httpx2.AsyncClient, mandate_id: UUID) -> Mandate:
    response = await client.post(
        f"/api/mandates/{mandate_id}/approval", json={"signed_by": "demo_signer"}
    )
    assert response.status_code == 200, response.text
    return Mandate.model_validate_json(response.content)


async def checkout(
    client: httpx2.AsyncClient, agent_seed: str, cart_id: UUID, mandate_id: UUID
) -> httpx2.Response:
    return await client.send(
        signed(
            client,
            f"/api/shop/carts/{cart_id}/checkout",
            agent_seed,
            json={"mandate_id": str(mandate_id)},
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
    assert (asked.status, asked.kind, asked.policy_hash) == (
        "pending",
        "cart",
        policy_hash,
    )
    assert [(line.name, line.quantity) for line in asked.lines] == [
        ("Nike Free RN Flyknit", 1),
        ("Hoka Mach X 2", 2),
    ]
    assert asked.consent_path == f"/shop/approve/{asked.id}"
    assert asked.expires_at - asked.created_at == timedelta(minutes=15)

    signed_mandate = await approve(client, asked.id)
    assert (signed_mandate.status, signed_mandate.signed_by) == (
        "signed",
        "demo_signer",
    )

    response = await checkout(client, agent_seed, cart_id, asked.id)

    assert response.status_code == 200, response.text
    order = Order.model_validate_json(response.content)
    assert (order.channel, order.status) == ("agent", "captured")
    assert order.custom_id == f"{asked.id}:{asked.token_hash}"
    assert order.mandate is not None
    assert (order.mandate.kind, order.mandate.signed_by) == ("cart", "demo_signer")
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
    assert (await client.get(f"/api/mandates/{asked.id}")).json()["spent"][
        "value"
    ] == "500.00"


@pytest.mark.usefixtures("shop")
async def test_a_cart_mandate_covers_only_its_cart_and_only_once(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await allow(sessions)
    cart_id, asked = await cart_mandate(client, wallet_id, agent_seed)
    await approve(client, asked.id)
    other = await cart_with_shoes(client, wallet_id)

    elsewhere = await checkout(client, agent_seed, other.id, asked.id)
    assert elsewhere.status_code == 403
    assert "MANDATE_CART_MISMATCH" in elsewhere.text

    assert (await checkout(client, agent_seed, cart_id, asked.id)).status_code == 200
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

    pending = await checkout(client, agent_seed, cart_id, asked.id)
    assert pending.status_code == 403
    assert "MANDATE_PENDING" in pending.text

    declined = await client.post(f"/api/mandates/{asked.id}/decline")
    assert declined.json()["status"] == "declined"
    assert (await client.post(f"/api/mandates/{asked.id}/decline")).status_code == 409
    refused = await checkout(client, agent_seed, cart_id, asked.id)
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
    await approve(client, intent.id)

    first = await cart_with_shoes(client, wallet_id)
    paid = await checkout(client, agent_seed, first.id, intent.id)
    assert paid.status_code == 200, paid.text
    assert (
        Order.model_validate_json(paid.content).custom_id
        == f"{intent.id}:{intent.token_hash}"
    )

    second = await cart_with_shoes(client, wallet_id)
    over = await checkout(client, agent_seed, second.id, intent.id)

    assert over.status_code == 403
    assert "MANDATE_CAP_EXCEEDED" in over.text
    assert (await client.get(f"/api/mandates/{intent.id}")).json()["spent"][
        "value"
    ] == "500.00"


@pytest.mark.usefixtures("shop")
async def test_an_intent_mandate_keeps_to_its_categories(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await allow(sessions)
    intent = await intent_mandate(client, wallet_id, agent_seed, "900.00", ["Trail"])
    await approve(client, intent.id)
    cart = await cart_with_shoes(client, wallet_id)

    response = await checkout(client, agent_seed, cart.id, intent.id)

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
    await approve(client, asked.id)

    stranger = await client.send(
        signed(
            client,
            f"/api/shop/carts/{cart_id}/checkout",
            other_seed,
            json={"mandate_id": str(asked.id)},
            agent_id=OTHER_AGENT,
        )
    )
    assert stranger.status_code == 403
    assert "MANDATE_AGENT_MISMATCH" in stranger.text

    await allow(
        sessions, allowed_agents=[AGENT, OTHER_AGENT], per_order_cap_cents=99_000
    )
    changed = await checkout(client, agent_seed, cart_id, asked.id)
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
    await approve(client, asked.id)
    async with sessions.begin() as session:
        await session.execute(
            update(MandateRecord)
            .where(MandateRecord.id == asked.id)
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )

    response = await checkout(client, agent_seed, cart_id, asked.id)

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
    await approve(client, intent.id)
    first = await cart_with_shoes(client, wallet_id)
    assert (await checkout(client, agent_seed, first.id, intent.id)).status_code == 200

    second = await cart_with_shoes(client, wallet_id)
    response = await checkout(client, agent_seed, second.id, intent.id)

    assert response.status_code == 403
    assert "AGENT_DAILY_CAP_EXCEEDED" in response.text


@pytest.mark.usefixtures("shop")
async def test_only_a_signed_agent_asks_and_only_for_a_real_cart(
    client: httpx2.AsyncClient, wallet_id: UUID, agent_seed: str
) -> None:
    body: dict[str, JsonValue] = {
        "kind": "cart",
        "wallet_id": str(wallet_id),
        "cap": {"value": "10.00", "currency_code": "USD"},
        "cart_id": str(uuid7()),
    }

    unsigned = await client.post("/api/mandates", json=body)
    assert unsigned.status_code == 401

    missing = await ask(client, agent_seed, body)
    assert missing.status_code == 404

    unknown = await checkout(client, agent_seed, uuid7(), uuid7())
    assert unknown.status_code == 404


@pytest.mark.usefixtures("shop")
async def test_a_checkout_naming_no_mandate_is_refused_and_logged(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await allow(sessions)
    cart = await cart_with_shoes(client, wallet_id)
    named = uuid7()

    response = await checkout(client, agent_seed, cart.id, named)

    assert response.status_code == 403
    assert "MANDATE_UNKNOWN" in response.text
    async with sessions() as session:
        refusal = await session.scalar(
            select(ActivityRecord).where(
                ActivityRecord.kind == "mandate_refused",
                ActivityRecord.details["named_mandate"].as_string() == str(named),
            )
        )
    assert refusal is not None
    assert refusal.mandate_id is None


def browser_json(credential: dict[str, Any]) -> dict[str, JsonValue]:  # pyright: ignore[reportExplicitAny]
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


@pytest.mark.usefixtures("shop")
async def test_a_passkey_registers_without_an_account_and_signs_a_mandate(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await allow(sessions)
    device = SoftWebauthnDevice()
    start = (await client.post("/api/passkeys/registration")).json()
    options = start["options"]
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
    registered = await client.post(
        "/api/passkeys",
        json={"challenge": start["challenge"], "credential": browser_json(attestation)},
    )
    assert registered.status_code == 201, registered.text
    passkey_id = registered.json()["id"]
    assert (
        await client.post(
            "/api/passkeys",
            json={
                "challenge": start["challenge"],
                "credential": browser_json(attestation),
            },
        )
    ).status_code == 422

    cart_id, asked = await cart_mandate(client, wallet_id, agent_seed)
    sign_options = (
        await client.get(
            f"/api/mandates/{asked.id}/passkey-options",
            params={"passkey_id": passkey_id},
        )
    ).json()
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
    approved = await client.post(
        f"/api/mandates/{asked.id}/approval",
        json={
            "signed_by": "passkey",
            "passkey_id": passkey_id,
            "credential": browser_json(assertion),
        },
    )

    assert approved.status_code == 200, approved.text
    assert approved.json()["signed_by"] == "passkey"
    assert unb64url(sign_options["challenge"]) == bytes.fromhex(asked.token_hash)
    paid = await checkout(client, agent_seed, cart_id, asked.id)
    assert paid.status_code == 200, paid.text
