"""The in-page agent's conversations, with a scripted model standing in for Claude."""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import final
from uuid import UUID, uuid7

import httpx2
import pytest
from agent_fixtures import AGENT, ORIGIN, allow, seed
from anthropic.lib.tools import ToolError
from order_fixtures import CAPTURE_PATH, CREATE_PATH, created_order
from paypal_stub import PayPalStub, json_answer
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.activity.models import ActivityRecord
from palisade_api.activity.router import activities as activities_dependency
from palisade_api.activity.store import Activities, Activity
from palisade_api.agents.client import AgentHttp, AgentSigner
from palisade_api.agents.registry import Agents, BuiltinAgent
from palisade_api.agents.signatures import AgentSignatures, agent_signatures
from palisade_api.assistant.access import ChatAccess, chat_access
from palisade_api.assistant.events import (
    AssistantEvent,
    Conversation,
    CreatedConversation,
    DoneEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from palisade_api.assistant.model import OnText, Reply, Tool, Usage
from palisade_api.assistant.router import assistant as assistant_dependency
from palisade_api.assistant.service import Assistant
from palisade_api.main import app
from palisade_api.mandates.passkeys import Passkeys
from palisade_api.mandates.router import mandates as mandates_dependency
from palisade_api.mandates.service import Mandates
from palisade_api.mandates.tokens import MandateTokens
from palisade_api.orders.calls import PayPalCalls
from palisade_api.orders.checkout import Checkout
from palisade_api.orders.router import orders as orders_dependency
from palisade_api.orders.store import Orders
from palisade_api.paypal.auth import PayPalAuth
from palisade_api.paypal.client import PayPalClient
from palisade_api.policy.engine import Policies
from palisade_api.shop.cart import Cart, Carts
from palisade_api.shop.catalog import Catalog
from palisade_api.shop.router import carts as carts_dependency
from palisade_api.shop.router import catalog as catalog_dependency
from palisade_api.shop.router import checkout as checkout_dependency

pytestmark = pytest.mark.anyio

CHAT_KEY = "judges-only"
HEADERS = {"X-Chat-Key": CHAT_KEY}
EVENT = TypeAdapter(AssistantEvent)
ACTIVITY = TypeAdapter(list[Activity])


@dataclass(frozen=True)
class Call:
    tool: str
    arguments: dict[str, JsonValue]


# One step of a scripted reply: words to stream, or a tool to call.
Step = str | Call


@final
@dataclass
class ScriptedModel:
    """Plays the steps of each turn in order, running tools the way the SDK's runner would."""

    turns: list[list[Step]]
    histories: list[Sequence[JsonValue]] = field(default_factory=list)

    async def reply(
        self, history: Sequence[JsonValue], tools: Sequence[Tool], on_text: OnText
    ) -> Reply:
        self.histories.append(history)
        by_name = {tool.name: tool for tool in tools}
        content: list[JsonValue] = []
        for step in self.turns.pop(0):
            if isinstance(step, str):
                on_text(step)
                content.append({"type": "text", "text": step})
                continue
            try:
                result: JsonValue = {
                    "tool": step.tool,
                    "result": str(await by_name[step.tool].call(step.arguments)),
                }
            except ToolError as error:
                result = {"tool": step.tool, "error": str(error)}
            content.append(result)
        return Reply(
            messages=[{"role": "assistant", "content": content}],
            usage=Usage(input_tokens=10, output_tokens=5),
        )


@pytest.fixture
def paypal() -> PayPalStub:
    stub = PayPalStub()
    stub.on("POST", CREATE_PATH, json_answer(201, created_order()))
    stub.on(
        "POST", CAPTURE_PATH, json_answer(201, {"id": "CAP-1", "status": "COMPLETED"})
    )
    return stub


@pytest.fixture
def model() -> ScriptedModel:
    return ScriptedModel(turns=[])


@pytest.fixture
async def shop(
    sessions: async_sessionmaker[AsyncSession], paypal: PayPalStub, model: ScriptedModel
) -> AsyncIterator[Mandates]:
    """The app wired for a conversation whose tools call this same app, signed as the test agent."""
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
    app.dependency_overrides[activities_dependency] = lambda: activities
    app.dependency_overrides[chat_access] = lambda: ChatAccess(CHAT_KEY)
    app.dependency_overrides[assistant_dependency] = lambda: Assistant(
        sessions,
        model,
        AgentHttp(shop_http, AgentSigner(AGENT, agent_seed)),
        mandates,
        activities,
        AGENT,
    )
    yield mandates
    app.dependency_overrides.clear()
    await shop_http.aclose()


def opened(conversation: CreatedConversation) -> dict[str, str]:
    """The headers that open a conversation: the chat code and the conversation's own key."""
    return {**HEADERS, "X-Conversation-Key": conversation.key}


async def start(client: httpx2.AsyncClient, wallet_id: UUID) -> CreatedConversation:
    response = await client.post(
        "/api/assistant/conversations",
        json={"wallet_id": str(wallet_id)},
        headers=HEADERS,
    )
    assert response.status_code == 201, response.text
    return CreatedConversation.model_validate_json(response.content)


async def send(
    client: httpx2.AsyncClient,
    conversation: CreatedConversation,
    body: dict[str, JsonValue],
) -> list[AssistantEvent]:
    """Post a message and read the whole event stream once the turn is over."""
    response = await client.post(
        f"/api/assistant/conversations/{conversation.id}/messages",
        json=body,
        headers=opened(conversation),
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = [frame for frame in response.text.split("\n\n") if frame.strip()]
    events: list[AssistantEvent] = []
    for frame in frames:
        [data] = [line for line in frame.split("\n") if line.startswith("data: ")]
        events.append(EVENT.validate_json(data.removeprefix("data: ")))
    return events


async def activity_kinds(
    sessions: async_sessionmaker[AsyncSession], conversation_id: UUID
) -> list[str]:
    async with sessions() as session:
        return list(
            await session.scalars(
                select(ActivityRecord.kind)
                .where(ActivityRecord.conversation_id == conversation_id)
                .order_by(ActivityRecord.id)
            )
        )


def tool_results(events: list[AssistantEvent]) -> list[ToolResultEvent]:
    return [event for event in events if isinstance(event, ToolResultEvent)]


@pytest.mark.usefixtures("shop")
async def test_the_chat_opens_only_with_the_access_code(
    client: httpx2.AsyncClient, wallet_id: UUID
) -> None:
    assert (await client.get("/api/assistant/access")).status_code == 401
    assert (
        await client.get("/api/assistant/access", headers={"X-Chat-Key": "wrong"})
    ).status_code == 401
    assert (
        await client.get("/api/assistant/access", headers=HEADERS)
    ).status_code == 204
    assert (
        await client.post(
            "/api/assistant/conversations", json={"wallet_id": str(wallet_id)}
        )
    ).status_code == 401


@pytest.mark.usefixtures("shop")
async def test_a_turn_streams_the_agents_words_tools_and_cards(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    model: ScriptedModel,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    conversation = await start(client, wallet_id)
    assert (conversation.cart_id, conversation.turns) == (None, [])
    model.turns.append(
        [
            "Let me look.",
            Call("search_products", {"query": "hoka mach", "category": ""}),
            Call(
                "set_cart_line",
                {"product_id": "hoka-mach-x-2", "size": "9.5", "quantity": 1},
            ),
            Call("request_mandate", {"kind": "cart", "cap_usd": "", "categories": []}),
            " Approve it below.",
        ]
    )

    events = await send(
        client, conversation, {"type": "message", "text": "Hoka Mach, size 9.5"}
    )

    assert [event.type for event in events] == [
        "text",
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "text",
        "done",
    ]
    searched, added, asked = tool_results(events)
    assert searched.ok
    assert searched.attachment is not None
    assert searched.attachment.type == "products"
    assert searched.attachment.products[0].id == "hoka-mach-x-2"
    assert added.attachment is not None
    assert added.attachment.type == "cart"
    assert (added.attachment.cart.items, added.attachment.cart.total.value) == (
        1,
        added.attachment.cart.total.value,
    )
    assert asked.attachment is not None
    assert asked.attachment.type == "mandate"
    assert asked.attachment.mandate.status == "pending"
    assert asked.attachment.mandate.cap == added.attachment.cart.total
    done = events[-1]
    assert isinstance(done, DoneEvent)
    assert done.cart_id == added.attachment.cart.id
    assert [event.type for event in done.turn.events] == [
        "text",
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "text",
    ]
    assert done.turn.events[0].type == "text"
    assert done.turn.request.text == "Hoka Mach, size 9.5"

    stored = Conversation.model_validate_json(
        (
            await client.get(
                f"/api/assistant/conversations/{conversation.id}",
                headers=opened(conversation),
            )
        ).content
    )
    assert (stored.busy, stored.cart_id, len(stored.turns)) == (False, done.cart_id, 1)
    assert stored.turns[0] == done.turn
    [history] = model.histories
    assert history == [{"role": "user", "content": "Hoka Mach, size 9.5"}]
    assert await activity_kinds(sessions, conversation.id) == [
        "conversation_started",
        "shopper_message",
        "assistant_tool_call",
        "assistant_tool_call",
        "assistant_tool_call",
    ]
    asked_rows = [
        row
        for row in ACTIVITY.validate_json(
            (
                await client.get(
                    "/api/activity", params={"kind": "assistant_tool_call"}
                )
            ).content
        )
        if row.mandate_id == asked.attachment.mandate.id
    ]
    assert [row.details["tool"] for row in asked_rows] == ["request_mandate"]
    # The feed is public, so it names nothing that would open the cart or the conversation.
    public = (await client.get("/api/activity")).text
    assert str(done.cart_id) not in public
    assert str(conversation.id) not in public


@pytest.mark.usefixtures("shop")
async def test_a_conversation_opens_only_with_its_own_key(
    client: httpx2.AsyncClient, wallet_id: UUID
) -> None:
    conversation = await start(client, wallet_id)
    other = await start(client, wallet_id)
    path = f"/api/assistant/conversations/{conversation.id}"

    assert (await client.get(path, headers=HEADERS)).status_code == 401
    assert (await client.get(path, headers=opened(other))).status_code == 401
    assert (await client.get(path, headers=opened(conversation))).status_code == 200
    stranger = await client.post(
        f"{path}/messages",
        json={"type": "message", "text": "Buy everything"},
        headers=opened(other),
    )
    assert stranger.status_code == 401


@pytest.mark.usefixtures("shop")
async def test_the_shoppers_approval_lets_the_agent_pay(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    model: ScriptedModel,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    conversation = await start(client, wallet_id)
    model.turns.append(
        [
            Call(
                "set_cart_line",
                {"product_id": "nike-free-rn-flyknit", "size": "10", "quantity": 1},
            ),
            Call("request_mandate", {"kind": "cart", "cap_usd": "", "categories": []}),
        ]
    )
    first = await send(client, conversation, {"type": "message", "text": "Buy it"})
    [_, asked] = tool_results(first)
    assert asked.attachment is not None
    assert asked.attachment.type == "mandate"
    mandate_id = asked.attachment.mandate.id

    unanswered = await client.post(
        f"/api/assistant/conversations/{conversation.id}/messages",
        json={"type": "mandate", "mandate_id": str(mandate_id)},
        headers=opened(conversation),
    )
    assert unanswered.status_code == 409

    model.turns.append(
        [
            Call("checkout", {"mandate_id": str(mandate_id)}),
            "Done.",
        ]
    )
    approved = await client.post(
        f"/api/mandates/{mandate_id}/approval", json={"signed_by": "demo_signer"}
    )
    assert approved.status_code == 200
    events = await send(
        client, conversation, {"type": "mandate", "mandate_id": str(mandate_id)}
    )

    [paid] = tool_results(events)
    assert paid.ok, paid.summary
    assert paid.attachment is not None
    assert paid.attachment.type == "order"
    assert paid.attachment.order.status == "captured"
    done = events[-1]
    assert isinstance(done, DoneEvent)
    assert done.cart_id is None
    assert done.turn.request.kind == "mandate_approved"
    history = model.histories[-1]
    assert isinstance(history[-1], dict)
    assert str(history[-1]["content"]).startswith(
        f"[The shopper answered mandate {mandate_id}"
    )
    number = paid.attachment.order.number
    chain = ACTIVITY.validate_json(
        (await client.get(f"/api/orders/{number}/activity")).content
    )
    # Signature rows carry only the agent, so they are not part of an order's chain.
    assert [row.kind for row in chain] == [
        "conversation_started",
        "shopper_message",
        "assistant_tool_call",
        "mandate_requested",
        "assistant_tool_call",
        "mandate_signed",
        "shopper_message",
        "checkout_accepted",
        "assistant_tool_call",
    ]
    assert chain[-2].order_number == number
    assert chain[-1].details["tool"] == "checkout"
    assert await activity_kinds(sessions, conversation.id) == [
        "conversation_started",
        "shopper_message",
        "assistant_tool_call",
        "assistant_tool_call",
        "shopper_message",
        "assistant_tool_call",
    ]


@pytest.mark.usefixtures("shop")
async def test_the_agent_reads_the_cart_the_shopper_filled(
    client: httpx2.AsyncClient, wallet_id: UUID, model: ScriptedModel
) -> None:
    fresh = await start(client, wallet_id)
    model.turns.append([Call("get_cart", {})])
    [nothing] = tool_results(
        await send(client, fresh, {"type": "message", "text": "What is in my bag?"})
    )
    assert (nothing.ok, nothing.summary, nothing.attachment) == (
        True,
        "No cart yet",
        None,
    )
    created = await client.post("/api/shop/carts", json={"wallet_id": str(wallet_id)})
    cart_id = Cart.model_validate_json(created.content).id
    filled = await client.put(
        f"/api/shop/carts/{cart_id}/lines",
        json={"product_id": "hoka-mach-x-2", "size": "9.5", "quantity": 2},
    )
    assert filled.status_code == 200, filled.text
    response = await client.post(
        "/api/assistant/conversations",
        json={"wallet_id": str(wallet_id), "cart_id": str(cart_id)},
        headers=HEADERS,
    )
    conversation = CreatedConversation.model_validate_json(response.content)
    model.turns.append([Call("get_cart", {})])

    [read] = tool_results(
        await send(
            client, conversation, {"type": "message", "text": "Check out my cart"}
        )
    )

    assert read.ok
    assert read.attachment is not None
    assert read.attachment.type == "cart"
    assert (read.attachment.cart.id, read.attachment.cart.items) == (cart_id, 2)
    assert read.summary == f"Cart: 2 pairs, ${read.attachment.cart.total.value}"


@pytest.mark.usefixtures("shop")
async def test_a_refusal_reaches_the_shopper_and_the_log(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    model: ScriptedModel,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    conversation = await start(client, wallet_id)
    model.turns.append(
        [
            Call(
                "set_cart_line",
                {"product_id": "nike-free-rn-flyknit", "size": "10", "quantity": 1},
            ),
            Call("request_mandate", {"kind": "cart", "cap_usd": "", "categories": []}),
        ]
    )
    [_, asked] = tool_results(
        await send(client, conversation, {"type": "message", "text": "Buy it"})
    )
    assert asked.attachment is not None
    assert asked.attachment.type == "mandate"
    model.turns.append(
        [Call("checkout", {"mandate_id": str(asked.attachment.mandate.id)})]
    )

    events = await send(client, conversation, {"type": "message", "text": "Pay now"})

    [refused] = tool_results(events)
    assert not refused.ok
    # The shopper reads plain words; the code and the store's detail go to the log and to Claude.
    assert refused.summary == "The store is still waiting for your approval."
    assert refused.attachment is None
    calls = [event for event in events if isinstance(event, ToolCallEvent)]
    assert calls[0].id == refused.id
    refusals = ACTIVITY.validate_json(
        (await client.get("/api/activity", params={"kind": "mandate_refused"})).content
    )
    assert refusals[0].mandate_id == asked.attachment.mandate.id
    assert refusals[0].details["reasons"] == ["MANDATE_PENDING"]
    async with sessions() as session:
        failed_call = await session.scalar(
            select(ActivityRecord)
            .where(
                ActivityRecord.conversation_id == conversation.id,
                ActivityRecord.kind == "assistant_tool_call",
            )
            .order_by(ActivityRecord.id.desc())
            .limit(1)
        )
    assert failed_call is not None
    assert failed_call.details["summary"] == refused.summary
    assert "MANDATE_PENDING" in str(failed_call.details["told"])


@pytest.mark.usefixtures("shop")
async def test_a_conversation_needs_a_wallet_that_can_pay(
    client: httpx2.AsyncClient,
) -> None:
    response = await client.post(
        "/api/assistant/conversations",
        json={"wallet_id": str(uuid7())},
        headers=HEADERS,
    )
    assert response.status_code == 404
    assert (
        await client.get(f"/api/assistant/conversations/{uuid7()}", headers=HEADERS)
    ).status_code == 404
