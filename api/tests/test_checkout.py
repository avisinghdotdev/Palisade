import re
from collections.abc import Iterator
from uuid import UUID

import httpx2
import pytest
from agent_fixtures import mandates_for
from order_fixtures import (
    CAPTURE_PATH,
    CREATE_PATH,
    VOID_PATH,
    cart_status,
    cart_with_shoes,
    created_order,
    declined,
)
from paypal_stub import PayPalStub, json_answer, timeout, unreachable
from pydantic import BaseModel, JsonValue, TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.agents.signatures import caller
from palisade_api.main import app
from palisade_api.orders.calls import PayPalCalls
from palisade_api.orders.checkout import Checkout
from palisade_api.orders.models import OrderRecord
from palisade_api.orders.router import orders as orders_dependency
from palisade_api.orders.store import Order, Orders, OrderSummary
from palisade_api.paypal.auth import PayPalAuth
from palisade_api.paypal.client import PayPalClient
from palisade_api.policy.engine import Policies
from palisade_api.shop.cart import Cart, Carts
from palisade_api.shop.models import ProductRecord
from palisade_api.shop.router import carts as carts_dependency
from palisade_api.shop.router import checkout as checkout_dependency

pytestmark = pytest.mark.anyio

NUMBER = re.compile(r"^RR-[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{6}$")
ORDERS = TypeAdapter(list[OrderSummary])


class Item(BaseModel):
    name: str
    quantity: str


class Unit(BaseModel):
    invoice_id: str
    amount: dict[str, JsonValue]
    items: list[Item]


class WalletSource(BaseModel):
    vault_id: str


class Source(BaseModel):
    paypal: WalletSource


class CreateBody(BaseModel):
    """The parts of the create-order request the tests check."""

    intent: str
    purchase_units: list[Unit]
    payment_source: Source


async def orders_for(
    client: httpx2.AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    cart_id: UUID,
) -> list[Order]:
    """Every order placed from the cart, oldest first, as the API returns it."""
    statement = (
        select(OrderRecord.number)
        .where(OrderRecord.cart_id == cart_id)
        .order_by(OrderRecord.created_at)
    )
    async with sessions() as session:
        numbers = (await session.scalars(statement)).all()
    return [
        Order.model_validate_json((await client.get(f"/api/orders/{number}")).content)
        for number in numbers
    ]


@pytest.fixture
def paypal() -> PayPalStub:
    stub = PayPalStub()
    stub.on("POST", CREATE_PATH, json_answer(201, created_order()))
    stub.on(
        "POST", CAPTURE_PATH, json_answer(201, {"id": "CAP-1", "status": "COMPLETED"})
    )
    stub.on("POST", VOID_PATH, json_answer(204, {}))
    return stub


@pytest.fixture
def shop(
    sessions: async_sessionmaker[AsyncSession], paypal: PayPalStub
) -> Iterator[Checkout]:
    http = paypal.client()
    orders = Orders(sessions)
    calls = PayPalCalls(sessions, PayPalClient(http, PayPalAuth(http, "id", "secret")))
    checkout = Checkout(
        sessions,
        calls,
        orders,
        Policies(sessions),
        mandates_for(sessions),
        source="api",
    )
    carts = Carts(sessions)
    app.dependency_overrides[carts_dependency] = lambda: carts
    app.dependency_overrides[checkout_dependency] = lambda: checkout
    app.dependency_overrides[orders_dependency] = lambda: orders
    app.dependency_overrides[caller] = lambda: None
    yield checkout
    app.dependency_overrides.clear()


async def checkout(client: httpx2.AsyncClient, cart_id: UUID) -> httpx2.Response:
    return await client.post(f"/api/shop/carts/{cart_id}/checkout")


@pytest.mark.usefixtures("shop")
async def test_a_cart_is_authorized_captured_and_recorded(
    paypal: PayPalStub, client: httpx2.AsyncClient, wallet_id: UUID
) -> None:
    cart = await cart_with_shoes(client, wallet_id)

    response = await checkout(client, cart.id)

    assert response.status_code == 200
    order = Order.model_validate_json(response.content)
    assert NUMBER.match(order.number)
    assert (order.status, order.channel, order.total.value) == (
        "captured",
        "human",
        "500.00",
    )
    assert (order.agent, order.policy_hash) == (None, None)
    assert (order.paypal_order_id, order.authorization_id, order.capture_id) == (
        "PP-ORDER-1",
        "AUTH-1",
        "CAP-1",
    )
    assert order.payer_email == "buyer@palisade.test"
    assert [(line.name, line.size, line.quantity) for line in order.lines] == [
        ("Free RN Flyknit", "10", 1),
        ("Mach X 2", "9.5", 2),
    ]
    assert [event.to_status for event in order.events] == [
        "creating",
        "authorized",
        "captured",
    ]
    assert [(call.action, call.status_code, call.debug_id) for call in order.calls] == [
        ("create", 201, "debug-201"),
        ("capture", 201, "debug-201"),
    ]
    assert await cart_status(client, cart.id) == "completed"

    [create] = paypal.sent("POST", CREATE_PATH)
    assert create.headers["PayPal-Request-Id"] == order.calls[0].request_id
    assert create.headers["Prefer"] == "return=representation"
    body = CreateBody.model_validate_json(create.content)
    assert body.intent == "AUTHORIZE"
    assert body.payment_source.paypal.vault_id.startswith("PT-TEST-")
    [unit] = body.purchase_units
    assert unit.amount == {
        "value": "500.00",
        "currency_code": "USD",
        "breakdown": {"item_total": {"value": "500.00", "currency_code": "USD"}},
    }
    assert [(item.name, item.quantity) for item in unit.items] == [
        ("Nike Free RN Flyknit", "1"),
        ("Hoka Mach X 2", "2"),
    ]
    assert unit.invoice_id == order.number
    [capture] = paypal.sent("POST", CAPTURE_PATH)
    assert capture.content == b'{"final_capture":true}'

    listed = await client.get("/api/orders")
    assert order.number in [
        item.number for item in ORDERS.validate_json(listed.content)
    ]
    fetched = await client.get(f"/api/orders/{order.number}")
    assert Order.model_validate_json(fetched.content) == order


@pytest.mark.usefixtures("shop")
async def test_checking_out_again_returns_the_same_order(
    paypal: PayPalStub, client: httpx2.AsyncClient, wallet_id: UUID
) -> None:
    cart = await cart_with_shoes(client, wallet_id)
    first = Order.model_validate_json((await checkout(client, cart.id)).content)

    again = await checkout(client, cart.id)

    assert again.status_code == 200
    assert Order.model_validate_json(again.content).number == first.number
    assert len(paypal.sent("POST", CREATE_PATH)) == 1


@pytest.mark.usefixtures("shop")
async def test_a_declined_payment_reopens_the_cart(
    paypal: PayPalStub,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    paypal.on("POST", CREATE_PATH, json_answer(422, declined()))
    cart = await cart_with_shoes(client, wallet_id)

    response = await checkout(client, cart.id)

    assert response.status_code == 402
    assert "INSTRUMENT_DECLINED" in response.json()["detail"]
    assert await cart_status(client, cart.id) == "open"
    [order] = await orders_for(client, sessions, cart.id)
    assert order.status == "declined"
    assert order.events[-1].detail == "INSTRUMENT_DECLINED"
    assert order.calls[0].debug_id == "debug-422"

    paypal.on("POST", CREATE_PATH, json_answer(201, created_order()))
    retried = await checkout(client, cart.id)
    assert retried.status_code == 200
    assert Order.model_validate_json(retried.content).number != order.number


@pytest.mark.usefixtures("shop")
async def test_a_timed_out_create_is_repeated_with_the_same_request_id(
    paypal: PayPalStub, client: httpx2.AsyncClient, wallet_id: UUID
) -> None:
    paypal.on("POST", CREATE_PATH, timeout, json_answer(201, created_order()))
    cart = await cart_with_shoes(client, wallet_id)

    response = await checkout(client, cart.id)

    assert response.status_code == 200
    first, second = paypal.sent("POST", CREATE_PATH)
    assert first.headers["PayPal-Request-Id"] == second.headers["PayPal-Request-Id"]


@pytest.mark.usefixtures("shop")
async def test_a_create_that_never_answers_leaves_the_order_failed(
    paypal: PayPalStub, client: httpx2.AsyncClient, wallet_id: UUID
) -> None:
    paypal.on("POST", CREATE_PATH, timeout)
    cart = await cart_with_shoes(client, wallet_id)

    response = await checkout(client, cart.id)

    assert response.status_code == 502
    assert await cart_status(client, cart.id) == "checking_out"
    again = await checkout(client, cart.id)
    assert again.status_code == 200
    assert Order.model_validate_json(again.content).status == "failed"


@pytest.mark.usefixtures("shop")
async def test_paypal_out_of_reach_leaves_the_order_failed(
    paypal: PayPalStub, client: httpx2.AsyncClient, wallet_id: UUID
) -> None:
    paypal.on("POST", CREATE_PATH, unreachable)
    cart = await cart_with_shoes(client, wallet_id)

    response = await checkout(client, cart.id)

    assert response.status_code == 502
    assert len(paypal.sent("POST", CREATE_PATH)) == 2
    assert await cart_status(client, cart.id) == "checking_out"
    again = Order.model_validate_json((await checkout(client, cart.id)).content)
    assert (again.status, again.events[-1].detail) == ("failed", "NO_ANSWER")


@pytest.mark.usefixtures("shop")
async def test_an_order_waiting_on_the_payer_is_declined(
    paypal: PayPalStub,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    waiting = {
        "id": "PP-ORDER-2",
        "status": "PAYER_ACTION_REQUIRED",
        "purchase_units": [{"reference_id": "default"}],
    }
    paypal.on("POST", CREATE_PATH, json_answer(201, waiting))
    cart = await cart_with_shoes(client, wallet_id)

    response = await checkout(client, cart.id)

    assert response.status_code == 402
    assert "ORDER_PAYER_ACTION_REQUIRED" in response.json()["detail"]
    assert await cart_status(client, cart.id) == "open"
    [order] = await orders_for(client, sessions, cart.id)
    assert (order.status, order.paypal_order_id, order.authorization_id) == (
        "declined",
        "PP-ORDER-2",
        None,
    )


@pytest.mark.usefixtures("shop")
async def test_a_hold_paypal_will_neither_capture_nor_release_is_left_for_repair(
    paypal: PayPalStub,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    paypal.on("POST", CAPTURE_PATH, json_answer(422, declined()))
    paypal.on("POST", VOID_PATH, json_answer(422, declined()))
    cart = await cart_with_shoes(client, wallet_id)

    response = await checkout(client, cart.id)

    assert response.status_code == 502
    assert "outcome is unknown" in response.json()["detail"]
    [order] = await orders_for(client, sessions, cart.id)
    assert order.status == "authorized"
    assert await cart_status(client, cart.id) == "checking_out"


@pytest.mark.usefixtures("shop")
async def test_a_refused_capture_voids_the_hold(
    paypal: PayPalStub,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    paypal.on("POST", CAPTURE_PATH, json_answer(422, declined()))
    cart = await cart_with_shoes(client, wallet_id)

    response = await checkout(client, cart.id)

    assert response.status_code == 402
    assert len(paypal.sent("POST", VOID_PATH)) == 1
    assert await cart_status(client, cart.id) == "open"
    [order] = await orders_for(client, sessions, cart.id)
    assert order.status == "voided"
    assert [call.action for call in order.calls] == ["create", "capture", "void"]


@pytest.mark.usefixtures("shop")
async def test_an_empty_cart_cannot_be_checked_out(
    client: httpx2.AsyncClient, wallet_id: UUID
) -> None:
    response = await client.post("/api/shop/carts", json={"wallet_id": str(wallet_id)})
    cart = Cart.model_validate_json(response.content)

    assert (await checkout(client, cart.id)).status_code == 409


@pytest.mark.usefixtures("shop")
async def test_a_price_change_stops_the_checkout(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    cart = await cart_with_shoes(client, wallet_id)
    async with sessions.begin() as session:
        product = await session.get_one(ProductRecord, "hoka-mach-x-2")
        product.price_cents += 500

    response = await checkout(client, cart.id)

    assert response.status_code == 409
    assert "hoka-mach-x-2" in response.json()["detail"]
    assert await cart_status(client, cart.id) == "open"
