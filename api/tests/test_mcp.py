"""The MCP endpoint: the chat's shopping tools over the protocol, for any agent."""

from collections.abc import AsyncIterator
from uuid import UUID, uuid4, uuid7

import httpx2
import pytest
from agent_fixtures import AGENT, ORIGIN, allow, seed
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer
from mcp.types import CallToolResult
from order_fixtures import CAPTURE_PATH, CREATE_PATH, created_order
from paypal_stub import PayPalStub, json_answer
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.activity.models import ActivityRecord
from palisade_api.activity.store import Activities
from palisade_api.agents.client import AgentHttp, AgentSigner
from palisade_api.agents.registry import Agents, BuiltinAgent
from palisade_api.agents.signatures import AgentSignatures, agent_signatures
from palisade_api.assistant.local import LocalApp
from palisade_api.main import app
from palisade_api.mandates.passkeys import Passkeys
from palisade_api.mandates.router import mandates as mandates_dependency
from palisade_api.mandates.service import Mandates
from palisade_api.mandates.tokens import MandateTokens
from palisade_api.mcp.server import ENDPOINT, MOUNT, asgi, build
from palisade_api.orders.calls import PayPalCalls
from palisade_api.orders.checkout import Checkout
from palisade_api.orders.router import orders as orders_dependency
from palisade_api.orders.store import Orders
from palisade_api.paypal.auth import PayPalAuth
from palisade_api.paypal.client import PayPalClient
from palisade_api.policy.engine import Policies
from palisade_api.shop.cart import Carts
from palisade_api.shop.catalog import Catalog
from palisade_api.shop.router import carts as carts_dependency
from palisade_api.shop.router import catalog as catalog_dependency
from palisade_api.shop.router import checkout as checkout_dependency
from palisade_api.wallets.models import WalletRecord
from palisade_api.wallets.service import Wallets

pytestmark = pytest.mark.anyio

TOOLS = [
    "search_products",
    "get_product",
    "get_cart",
    "set_cart_line",
    "request_mandate",
    "check_mandate",
    "checkout",
    "get_order",
]
RESULT = TypeAdapter(dict[str, JsonValue])


@pytest.fixture
def paypal() -> PayPalStub:
    stub = PayPalStub()
    stub.on("POST", CREATE_PATH, json_answer(201, created_order()))
    stub.on(
        "POST", CAPTURE_PATH, json_answer(201, {"id": "CAP-1", "status": "COMPLETED"})
    )
    return stub


@pytest.fixture(autouse=True)
async def demo_wallet(sessions: async_sessionmaker[AsyncSession]) -> UUID:
    """The demo buyer's wallet, which the endpoint pays from."""
    identifier = uuid7()
    async with sessions.begin() as session:
        session.add(
            WalletRecord(
                id=identifier,
                paypal_customer_id="customer_demo",
                payment_token_id=f"PT-DEMO-{uuid4().hex[:8]}",
                payer_id="PAYER-DEMO",
                email="demo@palisade.test",
                first_name="Demo",
                demo=True,
                status="active",
            )
        )
    return identifier


@pytest.fixture
async def server(
    sessions: async_sessionmaker[AsyncSession], paypal: PayPalStub
) -> AsyncIterator[MCPServer]:
    """The endpoint wired to this app, its calls signed as the test agent."""
    agent_seed = seed()
    await Agents(sessions).register(BuiltinAgent(AGENT, "Test Agent", agent_seed))
    await allow(sessions)
    http = paypal.client()
    calls = PayPalCalls(sessions, PayPalClient(http, PayPalAuth(http, "id", "secret")))
    orders = Orders(sessions)
    policies = Policies(sessions)
    activities = Activities(sessions)
    mandates = Mandates(
        sessions,
        MandateTokens(seed()),
        Passkeys(sessions, ORIGIN),
        policies,
        activities,
        seed(),
        ORIGIN,
    )
    wallets = Wallets(
        sessions,
        PayPalClient(http, PayPalAuth(http, "id", "secret")),
        ORIGIN,
        "demo@palisade.test",
    )
    shop_http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app, raise_app_exceptions=False),
        base_url=ORIGIN,
    )
    app.dependency_overrides[catalog_dependency] = lambda: Catalog(sessions)
    app.dependency_overrides[carts_dependency] = lambda: Carts(sessions)
    app.dependency_overrides[checkout_dependency] = lambda: Checkout(
        sessions, calls, orders, policies, mandates, activities, source="api"
    )
    app.dependency_overrides[orders_dependency] = lambda: orders
    app.dependency_overrides[agent_signatures] = lambda: AgentSignatures(
        sessions, Agents(sessions), activities, ORIGIN
    )
    app.dependency_overrides[mandates_dependency] = lambda: mandates
    yield build(
        AgentHttp(shop_http, AgentSigner(AGENT, agent_seed)),
        activities,
        wallets,
        AGENT,
        ORIGIN,
    )
    app.dependency_overrides.clear()
    await shop_http.aclose()


def result_of(call: CallToolResult) -> dict[str, JsonValue]:
    """The tool's structured result, which the SDK types as Any and the tools return as an object."""
    return RESULT.validate_python(call.structured_content)  # pyright: ignore[reportAny]


async def test_any_agent_shops_through_the_endpoint_under_the_same_rules(
    server: MCPServer,
    client: httpx2.AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with Client(server, raise_exceptions=True) as agent:
        assert [tool.name for tool in (await agent.list_tools()).tools] == TOOLS
        listed = {tool.name: tool for tool in (await agent.list_tools()).tools}
        assert listed["checkout"].annotations is not None
        assert listed["checkout"].annotations.destructive_hint is True
        assert listed["search_products"].annotations is not None
        assert listed["search_products"].annotations.read_only_hint is True

        found = await agent.call_tool(
            "search_products", {"query": "hoka mach", "category": ""}
        )
        assert not found.is_error
        products = result_of(found)["products"]
        assert isinstance(products, list)
        assert RESULT.validate_python(products[0])["id"] == "hoka-mach-x-2"

        added = await agent.call_tool(
            "set_cart_line",
            {"product_id": "hoka-mach-x-2", "size": "9.5", "quantity": 1},
        )
        assert not added.is_error
        cart = result_of(added)
        cart_id = cart["cart_id"]
        assert isinstance(cart_id, str)
        assert cart["total_usd"] == "190.00"

        asked = await agent.call_tool(
            "request_mandate",
            {"kind": "cart", "cart_id": cart_id, "cap_usd": "", "categories": []},
        )
        assert not asked.is_error
        mandate = result_of(asked)
        mandate_id = mandate["mandate_id"]
        assert isinstance(mandate_id, str)
        assert mandate["consent_url"] == f"{ORIGIN}/shop/approve/{mandate_id}"
        assert mandate["status"] == "pending"

        early = await agent.call_tool(
            "checkout", {"cart_id": cart_id, "mandate_id": mandate_id}
        )
        assert early.is_error
        assert "MANDATE_PENDING" in early.content[0].model_dump_json()

        approved = await client.post(
            f"/api/mandates/{mandate_id}/approval", json={"signed_by": "demo_signer"}
        )
        assert approved.status_code == 200, approved.text

        paid = await agent.call_tool(
            "checkout", {"cart_id": cart_id, "mandate_id": mandate_id}
        )
        assert not paid.is_error, paid.content
        order = result_of(paid)
        assert order["status"] == "captured"
        number = order["order_number"]
        assert order["receipt_url"] == f"{ORIGIN}/shop/orders/{number}"

        checked = await agent.call_tool("get_order", {"order_number": number})
        assert result_of(checked)["status"] == "captured"

    async with sessions() as session:
        logged = list(
            await session.scalars(
                select(ActivityRecord)
                .where(
                    ActivityRecord.agent_id == AGENT,
                    ActivityRecord.kind == "assistant_tool_call",
                    ActivityRecord.conversation_id.is_(None),
                )
                .order_by(ActivityRecord.id)
            )
        )
    assert [row.details["tool"] for row in logged] == [
        "search_products",
        "set_cart_line",
        "request_mandate",
        "checkout",
        "checkout",
        "get_order",
    ]
    assert [row.details["ok"] for row in logged] == [
        True,
        True,
        True,
        False,
        True,
        True,
    ]


async def test_a_wrong_cart_id_is_refused_in_words(server: MCPServer) -> None:
    async with Client(server, raise_exceptions=True) as agent:
        refused = await agent.call_tool("get_cart", {"cart_id": "not-a-cart"})
    assert refused.is_error
    assert "cart_id must be" in refused.content[0].model_dump_json()


async def test_a_cart_mandate_without_a_cart_id_asks_for_one(server: MCPServer) -> None:
    async with Client(server, raise_exceptions=True) as agent:
        added = await agent.call_tool(
            "set_cart_line",
            {"product_id": "hoka-mach-x-2", "size": "9.5", "quantity": 1},
        )
        assert not added.is_error
        refused = await agent.call_tool("request_mandate", {"kind": "cart"})
    assert refused.is_error
    message = refused.content[0].model_dump_json()
    assert "pass the cart_id that set_cart_line returned" in message
    assert "empty" not in message


async def test_the_endpoint_answers_over_streamable_http(server: MCPServer) -> None:
    """The mounted path serves the protocol over HTTP, the way a remote client reaches it."""
    mcp_app = asgi(server)
    async with (
        server.session_manager.run(),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=LocalApp(app, {"mcp_app": mcp_app})),
            base_url=ORIGIN,
            timeout=httpx2.Timeout(30.0),
        ) as http,
        Client(
            streamable_http_client(f"{ORIGIN}{MOUNT}{ENDPOINT}", http_client=http)
        ) as agent,
    ):
        assert [tool.name for tool in (await agent.list_tools()).tools] == TOOLS
        found = await agent.call_tool(
            "search_products", {"query": "", "category": "Trail"}
        )
    assert not found.is_error
    products = result_of(found)["products"]
    assert isinstance(products, list)
    assert len(products) == 1
