import base64
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx2
import pytest
from agent_fixtures import AGENT, ORIGIN, allow, seed, signed
from order_fixtures import CAPTURE_PATH, CREATE_PATH, cart_with_shoes, created_order
from paypal_stub import PayPalStub, json_answer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.agents.keys import encode_public_key, private_key
from palisade_api.agents.registry import Agents, BuiltinAgent
from palisade_api.agents.signatures import AgentSignatures, agent_signatures
from palisade_api.main import app
from palisade_api.mandates.passkeys import Passkeys
from palisade_api.mandates.service import Mandates
from palisade_api.mandates.tokens import MandateTokens
from palisade_api.orders.calls import PayPalCalls
from palisade_api.orders.checkout import Checkout
from palisade_api.orders.store import Orders
from palisade_api.paypal.auth import PayPalAuth
from palisade_api.paypal.client import PayPalClient
from palisade_api.policy.engine import Policies, Rules
from palisade_api.shop.cart import Carts
from palisade_api.shop.router import carts as carts_dependency
from palisade_api.shop.router import checkout as checkout_dependency

pytestmark = pytest.mark.anyio


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
def shop(
    sessions: async_sessionmaker[AsyncSession], paypal: PayPalStub
) -> Iterator[None]:
    http = paypal.client()
    calls = PayPalCalls(sessions, PayPalClient(http, PayPalAuth(http, "id", "secret")))
    agents = Agents(sessions)
    policies = Policies(sessions)
    passkeys = Passkeys(sessions, ORIGIN)
    mandates = Mandates(
        sessions, MandateTokens(seed()), passkeys, policies, seed(), ORIGIN
    )
    app.dependency_overrides[carts_dependency] = lambda: Carts(sessions)
    app.dependency_overrides[checkout_dependency] = lambda: Checkout(
        sessions, calls, Orders(sessions), policies, mandates, source="api"
    )
    app.dependency_overrides[agent_signatures] = lambda: AgentSignatures(
        sessions, agents, ORIGIN
    )
    yield
    app.dependency_overrides.clear()


async def checkout_path(client: httpx2.AsyncClient, wallet_id: UUID) -> str:
    cart = await cart_with_shoes(client, wallet_id)
    return f"/api/shop/carts/{cart.id}/checkout"


@pytest.mark.usefixtures("shop")
async def test_a_signed_checkout_without_a_mandate_is_refused(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await allow(sessions)
    path = await checkout_path(client, wallet_id)

    response = await client.send(signed(client, path, agent_seed))

    assert response.status_code == 403
    assert "MANDATE_REQUIRED" in response.text


@pytest.mark.usefixtures("shop")
async def test_an_unsigned_request_naming_an_agent_is_refused(
    client: httpx2.AsyncClient, wallet_id: UUID
) -> None:
    path = await checkout_path(client, wallet_id)

    response = await client.post(path, headers={"Signature-Agent": AGENT})

    assert response.status_code == 401
    assert "must carry an RFC 9421 signature" in response.json()["detail"]


@pytest.mark.usefixtures("shop", "agent_seed")
async def test_a_signature_by_an_unknown_key_is_refused(
    client: httpx2.AsyncClient, wallet_id: UUID
) -> None:
    path = await checkout_path(client, wallet_id)

    response = await client.send(signed(client, path, seed(), agent_id="stranger"))

    assert response.status_code == 401
    assert "stranger" in response.json()["detail"]


@pytest.mark.usefixtures("shop")
async def test_a_signature_for_another_target_is_refused(
    client: httpx2.AsyncClient, wallet_id: UUID, agent_seed: str
) -> None:
    request = signed(client, await checkout_path(client, wallet_id), agent_seed)
    other_cart = client.build_request("POST", await checkout_path(client, wallet_id))
    other_cart.headers.update(request.headers)

    response = await client.send(other_cart)

    assert response.status_code == 401


@pytest.mark.usefixtures("shop")
async def test_a_stale_signature_is_refused(
    client: httpx2.AsyncClient, wallet_id: UUID, agent_seed: str
) -> None:
    path = await checkout_path(client, wallet_id)
    stale = datetime.now(UTC) - timedelta(minutes=10)

    response = await client.send(signed(client, path, agent_seed, created=stale))

    assert response.status_code == 401


@pytest.mark.usefixtures("shop")
async def test_a_replayed_signature_is_refused(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await allow(sessions)
    path = await checkout_path(client, wallet_id)
    request = signed(client, path, agent_seed)
    first = await client.send(request)
    assert first.status_code == 403

    again = client.build_request("POST", path)
    again.headers.update(request.headers)
    response = await client.send(again)

    assert response.status_code == 401
    assert "nonce was already used" in response.json()["detail"]


@pytest.mark.usefixtures("shop")
async def test_policy_refusals_name_the_rules_broken(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await allow(
        sessions,
        allowed_agents=["someone-else"],
        per_order_cap_cents=100,
        blocked_categories=["Road", "Tempo"],
    )
    path = await checkout_path(client, wallet_id)

    response = await client.send(signed(client, path, agent_seed))

    assert response.status_code == 403
    for reason in (
        "AGENT_NOT_ALLOWED",
        "ORDER_CAP_EXCEEDED",
        "CATEGORY_BLOCKED:Road",
        "CATEGORY_BLOCKED:Tempo",
    ):
        assert reason in response.text
    cart_id = path.split("/")[4]
    cart = await client.get(f"/api/shop/carts/{cart_id}")
    assert cart.json()["status"] == "open"


def test_a_public_key_follows_its_seed() -> None:
    agent_seed = seed()

    first = encode_public_key(private_key(agent_seed).public_key())
    second = encode_public_key(private_key(agent_seed).public_key())

    assert first == second
    assert len(base64.b64decode(first)) == 32
    with pytest.raises(ValueError, match="32 bytes"):
        private_key(base64.b64encode(b"short").decode())


def test_the_policy_hash_ignores_field_order() -> None:
    rules = Rules(
        allowed_agents=["a"],
        per_order_cap_cents=1,
        per_agent_daily_cap_cents=2,
        blocked_categories=["Trail"],
    )
    reordered = Rules.model_validate(
        {
            "blocked_categories": ["Trail"],
            "per_agent_daily_cap_cents": 2,
            "per_order_cap_cents": 1,
            "allowed_agents": ["a"],
        }
    )

    assert rules.hash() == reordered.hash()
    assert len(rules.hash()) == 64
