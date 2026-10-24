from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import final
from uuid import UUID

import httpx2
import pytest
from order_fixtures import (
    CAPTURE_PATH,
    CREATE_PATH,
    ORDER_PATH,
    VOID_PATH,
    cart_with_shoes,
    created_order,
    declined,
)
from paypal_stub import PayPalStub, json_answer, timeout
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.main import app
from palisade_api.orders.calls import PayPalCalls
from palisade_api.orders.checkout import Checkout, CheckoutFailed
from palisade_api.orders.models import OrderRecord, PayPalCallRecord
from palisade_api.orders.repair import LEASE, Repairs
from palisade_api.orders.store import Order, Orders
from palisade_api.paypal.auth import PayPalAuth
from palisade_api.paypal.client import PayPalClient, PayPalError
from palisade_api.shop.cart import Carts
from palisade_api.shop.models import CartRecord
from palisade_api.shop.router import carts as carts_dependency

pytestmark = pytest.mark.anyio


@final
class Shop:
    """The checkout that leaves an order halfway, and the repairs that finish it."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], paypal: PayPalStub
    ) -> None:
        http = paypal.client()
        client = PayPalClient(http, PayPalAuth(http, "id", "secret"))
        calls = PayPalCalls(sessions, client)
        orders = Orders(sessions)
        self.sessions = sessions
        self.orders = orders
        self.checkout = Checkout(sessions, calls, orders, source="api")
        self.repairs = Repairs(
            sessions, Checkout(sessions, calls, orders, source="repair"), client
        )

    async def order_of(self, cart_id: UUID) -> Order:
        async with self.sessions() as session:
            number = await session.scalar(
                select(OrderRecord.number).where(OrderRecord.cart_id == cart_id)
            )
        if number is None:
            raise LookupError(f"cart {cart_id} has no order")
        return await self.orders.get(number)

    async def cart_status(self, cart_id: UUID) -> str:
        async with self.sessions() as session:
            return (await session.get_one(CartRecord, cart_id)).status

    async def age(self, order_id: UUID) -> None:
        """Make the order look untouched for longer than the lease."""
        async with self.sessions.begin() as session:
            await session.execute(
                update(OrderRecord)
                .where(OrderRecord.id == order_id)
                .values(updated_at=func.now() - LEASE - timedelta(minutes=1))
            )

    async def repair(self, number: str) -> bool:
        """Repair this order if the job would pick it; whether it did."""
        stuck = [
            order for order in await self.repairs.stuck() if order.number == number
        ]
        for order in stuck:
            await self.repairs.repair(order)
        return bool(stuck)


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
) -> Iterator[Shop]:
    app.dependency_overrides[carts_dependency] = lambda: Carts(sessions)
    yield Shop(sessions, paypal)
    app.dependency_overrides.clear()


def held(
    authorization_status: str, captures: list[dict[str, str]]
) -> dict[str, object]:
    """PayPal's order as GET returns it once the create authorized it."""
    return {
        "id": "PP-ORDER-1",
        "purchase_units": [
            {
                "payments": {
                    "authorizations": [
                        {"id": "AUTH-1", "status": authorization_status}
                    ],
                    "captures": captures,
                }
            }
        ],
    }


async def unanswered_create(
    shop: Shop, paypal: PayPalStub, client: httpx2.AsyncClient, wallet_id: UUID
) -> tuple[UUID, Order]:
    paypal.on("POST", CREATE_PATH, timeout)
    cart = await cart_with_shoes(client, wallet_id)
    with pytest.raises(CheckoutFailed):
        await shop.checkout.place_order(cart.id)
    return cart.id, await shop.order_of(cart.id)


async def uncaptured_hold(
    shop: Shop, paypal: PayPalStub, client: httpx2.AsyncClient, wallet_id: UUID
) -> tuple[UUID, Order]:
    """An order PayPal authorized whose capture never answered and whose void was refused."""
    paypal.on("POST", CAPTURE_PATH, timeout)
    paypal.on("POST", VOID_PATH, json_answer(422, declined()))
    cart = await cart_with_shoes(client, wallet_id)
    with pytest.raises(PayPalError):
        await shop.checkout.place_order(cart.id)
    order = await shop.order_of(cart.id)
    assert order.status == "authorized"
    return cart.id, order


async def test_a_create_paypal_never_answered_is_repeated_and_finished(
    shop: Shop, paypal: PayPalStub, client: httpx2.AsyncClient, wallet_id: UUID
) -> None:
    cart_id, order = await unanswered_create(shop, paypal, client, wallet_id)
    assert order.status == "failed"
    await shop.age(order.id)
    paypal.on("POST", CREATE_PATH, json_answer(201, created_order()))

    assert await shop.repair(order.number)

    repaired = await shop.orders.get(order.number)
    assert (repaired.status, repaired.capture_id) == ("captured", "CAP-1")
    assert await shop.cart_status(cart_id) == "completed"
    creates = paypal.sent("POST", CREATE_PATH)
    assert len(creates) == 3
    assert len({create.headers["PayPal-Request-Id"] for create in creates}) == 1
    assert [(event.to_status, event.source) for event in repaired.events[-2:]] == [
        ("authorized", "repair"),
        ("captured", "repair"),
    ]


async def test_a_create_past_paypals_repeat_window_is_given_up(
    shop: Shop, paypal: PayPalStub, client: httpx2.AsyncClient, wallet_id: UUID
) -> None:
    cart_id, order = await unanswered_create(shop, paypal, client, wallet_id)
    await shop.age(order.id)
    async with shop.sessions.begin() as session:
        await session.execute(
            update(PayPalCallRecord)
            .where(PayPalCallRecord.order_id == order.id)
            .values(started_at=datetime.now(UTC) - timedelta(hours=7))
        )

    assert await shop.repair(order.number)

    repaired = await shop.orders.get(order.number)
    assert repaired.status == "failed"
    assert (repaired.events[-1].source, repaired.events[-1].detail) == (
        "repair",
        "CREATE_REPEAT_WINDOW_PASSED",
    )
    assert await shop.cart_status(cart_id) == "open"
    assert len(paypal.sent("POST", CREATE_PATH)) == 2


async def test_a_checkout_still_running_is_left_alone(
    shop: Shop, paypal: PayPalStub, client: httpx2.AsyncClient, wallet_id: UUID
) -> None:
    _, order = await unanswered_create(shop, paypal, client, wallet_id)

    assert not await shop.repair(order.number)


async def test_a_capture_paypal_made_is_recorded_without_capturing_again(
    shop: Shop, paypal: PayPalStub, client: httpx2.AsyncClient, wallet_id: UUID
) -> None:
    cart_id, order = await uncaptured_hold(shop, paypal, client, wallet_id)
    await shop.age(order.id)
    paypal.on(
        "GET",
        ORDER_PATH,
        json_answer(200, held("CAPTURED", [{"id": "CAP-9", "status": "COMPLETED"}])),
    )

    assert await shop.repair(order.number)

    repaired = await shop.orders.get(order.number)
    assert (repaired.status, repaired.capture_id) == ("captured", "CAP-9")
    assert await shop.cart_status(cart_id) == "completed"
    assert len(paypal.sent("POST", CAPTURE_PATH)) == 2


async def test_a_hold_never_captured_is_captured_under_the_first_request_id(
    shop: Shop, paypal: PayPalStub, client: httpx2.AsyncClient, wallet_id: UUID
) -> None:
    cart_id, order = await uncaptured_hold(shop, paypal, client, wallet_id)
    await shop.age(order.id)
    paypal.on("GET", ORDER_PATH, json_answer(200, held("CREATED", [])))
    paypal.on(
        "POST", CAPTURE_PATH, json_answer(201, {"id": "CAP-1", "status": "COMPLETED"})
    )

    assert await shop.repair(order.number)

    repaired = await shop.orders.get(order.number)
    assert repaired.status == "captured"
    assert await shop.cart_status(cart_id) == "completed"
    captures = paypal.sent("POST", CAPTURE_PATH)
    assert len({capture.headers["PayPal-Request-Id"] for capture in captures}) == 1


async def test_a_hold_near_its_expiry_is_voided(
    shop: Shop, paypal: PayPalStub, client: httpx2.AsyncClient, wallet_id: UUID
) -> None:
    soon = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    paypal.on("POST", CREATE_PATH, json_answer(201, created_order(soon)))
    cart_id, order = await uncaptured_hold(shop, paypal, client, wallet_id)
    await shop.age(order.id)
    paypal.on("GET", ORDER_PATH, json_answer(200, held("CREATED", [])))
    paypal.on("POST", VOID_PATH, json_answer(204, {}))

    assert await shop.repair(order.number)

    repaired = await shop.orders.get(order.number)
    assert (repaired.status, repaired.events[-1].detail) == (
        "voided",
        "AUTHORIZATION_NEAR_EXPIRY",
    )
    assert await shop.cart_status(cart_id) == "open"
