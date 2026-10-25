import base64
import secrets
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import final, override
from uuid import UUID, uuid7

import httpx2
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from http_message_signatures import algorithms
from http_message_signatures.resolvers import HTTPSignatureKeyResolver
from http_message_signatures.signatures import HTTPMessageSigner
from order_fixtures import CAPTURE_PATH, CREATE_PATH, cart_with_shoes, created_order
from paypal_stub import PayPalStub, json_answer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.agents.keys import encode_public_key, private_key
from palisade_api.agents.registry import Agents, BuiltinAgent
from palisade_api.agents.signatures import AgentSignatures, agent_signatures
from palisade_api.main import app
from palisade_api.orders.calls import PayPalCalls
from palisade_api.orders.checkout import Checkout
from palisade_api.orders.store import Order, Orders
from palisade_api.paypal.auth import PayPalAuth
from palisade_api.paypal.client import PayPalClient
from palisade_api.policy.engine import Policies, Rules
from palisade_api.policy.models import PolicyRecord
from palisade_api.shop.cart import Carts
from palisade_api.shop.router import carts as carts_dependency
from palisade_api.shop.router import checkout as checkout_dependency

pytestmark = pytest.mark.anyio

ORIGIN = "http://palisade.test"
AGENT = "test-agent"


def seed() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()


@final
class AgentKey(HTTPSignatureKeyResolver):
    def __init__(self, agent_seed: str) -> None:
        self._key = private_key(agent_seed)

    @override
    def resolve_private_key(self, key_id: str) -> Ed25519PrivateKey:
        return self._key


def signed(
    client: httpx2.AsyncClient,
    path: str,
    agent_seed: str,
    *,
    agent_id: str = AGENT,
    created: datetime | None = None,
    nonce: str | None = None,
) -> httpx2.Request:
    """A checkout request signed the way an agent signs it."""
    request = client.build_request("POST", path)
    signer = HTTPMessageSigner(
        signature_algorithm=algorithms.ED25519,  # pyright: ignore[reportPrivateImportUsage]
        key_resolver=AgentKey(agent_seed),
    )
    signer.sign(  # pyright: ignore[reportUnknownMemberType]
        request,
        key_id=agent_id,
        covered_component_ids=("@method", "@target-uri"),
        created=created or datetime.now(UTC),
        nonce=nonce or secrets.token_urlsafe(16),
    )
    return request


async def allow(sessions: async_sessionmaker[AsyncSession], **changes: object) -> str:
    """A policy allowing the test agent, with any rule overridden; its hash."""
    rules = Rules(
        allowed_agents=[AGENT],
        per_order_cap_cents=100_000,
        per_agent_daily_cap_cents=1_000_000,
        blocked_categories=[],
    ).model_copy(update=changes)
    async with sessions.begin() as session:
        session.add(PolicyRecord(id=uuid7(), hash=rules.hash(), **rules.model_dump()))
    return rules.hash()


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
    app.dependency_overrides[carts_dependency] = lambda: Carts(sessions)
    app.dependency_overrides[checkout_dependency] = lambda: Checkout(
        sessions, calls, Orders(sessions), Policies(sessions), source="api"
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
async def test_a_signed_checkout_places_an_agent_order_under_the_policy(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    policy_hash = await allow(sessions)
    path = await checkout_path(client, wallet_id)

    response = await client.send(signed(client, path, agent_seed))

    assert response.status_code == 200
    order = Order.model_validate_json(response.content)
    assert (order.channel, order.status, order.policy_hash) == (
        "agent",
        "captured",
        policy_hash,
    )
    assert order.agent is not None
    assert (order.agent.id, order.agent.name) == (AGENT, "Test Agent")


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
    assert "max_age" in response.json()["detail"] or "age" in response.json()["detail"]


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
    assert first.status_code == 200

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
    detail = response.text
    for reason in (
        "AGENT_NOT_ALLOWED",
        "ORDER_CAP_EXCEEDED",
        "CATEGORY_BLOCKED:Road",
        "CATEGORY_BLOCKED:Tempo",
    ):
        assert reason in detail
    cart_id = path.split("/")[4]
    cart = await client.get(f"/api/shop/carts/{cart_id}")
    assert cart.json()["status"] == "open"


@pytest.mark.usefixtures("shop")
async def test_the_daily_cap_counts_the_agents_earlier_orders(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    agent_seed: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await allow(sessions, per_agent_daily_cap_cents=60_000)
    first = await client.send(
        signed(client, await checkout_path(client, wallet_id), agent_seed)
    )
    assert first.status_code == 200

    second = await client.send(
        signed(client, await checkout_path(client, wallet_id), agent_seed)
    )

    assert second.status_code == 403
    assert "AGENT_DAILY_CAP_EXCEEDED" in second.json()["detail"]


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
