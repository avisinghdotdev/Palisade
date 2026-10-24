from collections.abc import Iterator
from uuid import UUID

import httpx2
import pytest
from order_fixtures import (
    CAPTURE_PATH,
    CREATE_PATH,
    VOID_PATH,
    cart_with_shoes,
    created_order,
    declined,
)
from paypal_stub import PayPalStub, json_answer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.main import app
from palisade_api.orders.calls import PayPalCalls
from palisade_api.orders.checkout import Checkout
from palisade_api.orders.models import OrderRecord
from palisade_api.orders.router import orders as orders_dependency
from palisade_api.orders.router import shipments as shipments_dependency
from palisade_api.orders.shipping import Shipments
from palisade_api.orders.store import Order, Orders
from palisade_api.paypal.auth import PayPalAuth
from palisade_api.paypal.client import PayPalClient
from palisade_api.shop.cart import Carts
from palisade_api.shop.router import carts as carts_dependency
from palisade_api.shop.router import checkout as checkout_dependency

pytestmark = pytest.mark.anyio

TRACK_PATH = "/v2/checkout/orders/PP-ORDER-1/track"
TRACKING = "9400100000000000000001"


class TrackBody(BaseModel):
    capture_id: str
    tracking_number: str
    carrier: str
    notify_payer: bool


def tracked(tracking_number: str) -> dict[str, object]:
    return {
        "id": "PP-ORDER-1",
        "purchase_units": [
            {
                "shipping": {
                    "trackers": [
                        {"id": f"CAP-1-{tracking_number}", "status": "SHIPPED"}
                    ]
                }
            }
        ],
    }


@pytest.fixture
def paypal() -> PayPalStub:
    stub = PayPalStub()
    stub.on("POST", CREATE_PATH, json_answer(201, created_order()))
    stub.on(
        "POST", CAPTURE_PATH, json_answer(201, {"id": "CAP-1", "status": "COMPLETED"})
    )
    stub.on("POST", VOID_PATH, json_answer(204, {}))
    stub.on("POST", TRACK_PATH, json_answer(201, tracked(TRACKING)))
    return stub


@pytest.fixture
def shop(
    sessions: async_sessionmaker[AsyncSession], paypal: PayPalStub
) -> Iterator[None]:
    http = paypal.client()
    calls = PayPalCalls(sessions, PayPalClient(http, PayPalAuth(http, "id", "secret")))
    orders = Orders(sessions)
    app.dependency_overrides[carts_dependency] = lambda: Carts(sessions)
    app.dependency_overrides[checkout_dependency] = lambda: Checkout(
        sessions, calls, orders, source="api"
    )
    app.dependency_overrides[orders_dependency] = lambda: orders
    app.dependency_overrides[shipments_dependency] = lambda: Shipments(
        sessions, calls, orders
    )
    yield
    app.dependency_overrides.clear()


async def paid_order(client: httpx2.AsyncClient, wallet_id: UUID) -> Order:
    cart = await cart_with_shoes(client, wallet_id)
    response = await client.post(f"/api/shop/carts/{cart.id}/checkout")
    return Order.model_validate_json(response.content)


async def ship(client: httpx2.AsyncClient, number: str) -> httpx2.Response:
    return await client.post(
        f"/api/orders/{number}/shipment",
        json={"carrier": "USPS", "tracking_number": TRACKING},
    )


@pytest.mark.usefixtures("shop")
async def test_a_paid_order_is_shipped_with_its_tracking_on_the_capture(
    paypal: PayPalStub, client: httpx2.AsyncClient, wallet_id: UUID
) -> None:
    order = await paid_order(client, wallet_id)

    response = await ship(client, order.number)

    assert response.status_code == 200
    shipped = Order.model_validate_json(response.content)
    assert shipped.shipment is not None
    assert (
        shipped.shipment.carrier,
        shipped.shipment.tracking_number,
        shipped.shipment.paypal_tracker_id,
    ) == ("USPS", TRACKING, f"CAP-1-{TRACKING}")
    assert shipped.calls[-1].action == "track"
    [sent] = paypal.sent("POST", TRACK_PATH)
    assert TrackBody.model_validate_json(sent.content) == TrackBody(
        capture_id="CAP-1", tracking_number=TRACKING, carrier="USPS", notify_payer=False
    )

    again = await ship(client, order.number)
    assert again.status_code == 409
    assert len(paypal.sent("POST", TRACK_PATH)) == 1


@pytest.mark.usefixtures("shop")
async def test_an_unpaid_order_does_not_ship(
    paypal: PayPalStub,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    paypal.on("POST", CREATE_PATH, json_answer(422, declined()))
    cart = await cart_with_shoes(client, wallet_id)
    assert (await client.post(f"/api/shop/carts/{cart.id}/checkout")).status_code == 402
    async with sessions() as session:
        number = await session.scalar(
            select(OrderRecord.number).where(OrderRecord.cart_id == cart.id)
        )
    assert number is not None

    response = await ship(client, number)

    assert response.status_code == 409
    assert paypal.sent("POST", TRACK_PATH) == []


@pytest.mark.usefixtures("shop")
async def test_tracking_paypal_refuses_is_a_422_and_records_no_shipment(
    paypal: PayPalStub, client: httpx2.AsyncClient, wallet_id: UUID
) -> None:
    paypal.on("POST", TRACK_PATH, json_answer(422, declined()))
    order = await paid_order(client, wallet_id)

    response = await ship(client, order.number)

    assert response.status_code == 422
    fetched = Order.model_validate_json(
        (await client.get(f"/api/orders/{order.number}")).content
    )
    assert fetched.shipment is None
