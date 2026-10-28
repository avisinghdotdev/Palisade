from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid7

import pytest
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.orders import webhooks
from palisade_api.orders.models import OrderRecord
from palisade_api.orders.store import Orders, OrderStatus
from palisade_api.shop.models import CartRecord
from palisade_api.webhooks.models import WebhookEventRecord
from palisade_api.webhooks.store import WebhookEvent

pytestmark = pytest.mark.anyio


async def event(
    sessions: async_sessionmaker[AsyncSession],
    event_type: str,
    resource_type: str,
    resource: dict[str, JsonValue],
) -> WebhookEvent:
    """A delivery as the receiver would have stored it before queueing its job."""
    delivered = WebhookEvent(
        id=f"WH-TEST-{uuid7().hex}",
        event_type=event_type,
        create_time=datetime.now(UTC),
        resource_type=resource_type,
        summary=event_type,
        resource=resource,
    )
    async with sessions.begin() as session:
        session.add(
            WebhookEventRecord(
                id=delivered.id,
                event_type=event_type,
                payload=delivered.model_dump(mode="json"),
            )
        )
    return delivered


async def placed_order(
    sessions: async_sessionmaker[AsyncSession], wallet_id: UUID, status: OrderStatus
) -> UUID:
    suffix = uuid4().hex[:6].upper()
    async with sessions.begin() as session:
        cart = CartRecord(
            id=uuid7(),
            wallet_id=wallet_id,
            status="completed",
            completed_at=datetime.now(UTC),
            lines=[],
        )
        session.add(cart)
        await session.flush()
        order = OrderRecord(
            id=uuid7(),
            number=f"RR-{suffix}",
            cart_id=cart.id,
            wallet_id=wallet_id,
            channel="human",
            status=status,
            currency="USD",
            total_cents=12000,
            authorization_id=f"AUTH-{suffix}",
            capture_id=f"CAP-{suffix}",
            lines=[],
            events=[],
        )
        session.add(order)
        return order.id


async def cart_of(
    sessions: async_sessionmaker[AsyncSession], order_id: UUID
) -> CartRecord:
    async with sessions() as session:
        order = await session.get_one(OrderRecord, order_id)
        return await session.get_one(CartRecord, order.cart_id)


async def test_a_completed_capture_settles_a_pending_order(
    sessions: async_sessionmaker[AsyncSession], wallet_id: UUID
) -> None:
    order_id = await placed_order(sessions, wallet_id, "capture_pending")
    order = await Orders(sessions).get_by_id(order_id)
    completed = await event(
        sessions, "PAYMENT.CAPTURE.COMPLETED", "capture", {"id": order.capture_id}
    )

    await webhooks.handlers(sessions)["PAYMENT.CAPTURE.COMPLETED"](completed)

    settled = await Orders(sessions).get_by_id(order_id)
    assert settled.status == "captured"
    [recorded] = settled.events
    assert (
        recorded.source,
        recorded.from_status,
        recorded.webhook_event_id,
        recorded.paypal_event,
    ) == ("webhook", "capture_pending", completed.id, "PAYMENT.CAPTURE.COMPLETED")


async def test_a_replayed_event_is_recorded_once(
    sessions: async_sessionmaker[AsyncSession], wallet_id: UUID
) -> None:
    order_id = await placed_order(sessions, wallet_id, "captured")
    order = await Orders(sessions).get_by_id(order_id)
    created = await event(
        sessions,
        "PAYMENT.AUTHORIZATION.CREATED",
        "authorization",
        {"id": order.authorization_id},
    )
    handler = webhooks.handlers(sessions)["PAYMENT.AUTHORIZATION.CREATED"]

    await handler(created)
    await handler(created)

    [recorded] = (await Orders(sessions).get_by_id(order_id)).events
    assert (recorded.from_status, recorded.to_status, recorded.paypal_event) == (
        "captured",
        "captured",
        "PAYMENT.AUTHORIZATION.CREATED",
    )


async def test_a_refund_names_the_capture_through_its_up_link(
    sessions: async_sessionmaker[AsyncSession], wallet_id: UUID
) -> None:
    order_id = await placed_order(sessions, wallet_id, "captured")
    order = await Orders(sessions).get_by_id(order_id)
    refunded = await event(
        sessions,
        "PAYMENT.CAPTURE.REFUNDED",
        "refund",
        {
            "id": "REF-1",
            "links": [
                {
                    "rel": "up",
                    "href": f"https://api-m.sandbox.paypal.com/v2/payments/captures/{order.capture_id}",
                }
            ],
        },
    )

    await webhooks.handlers(sessions)["PAYMENT.CAPTURE.REFUNDED"](refunded)

    assert (await Orders(sessions).get_by_id(order_id)).status == "refunded"


async def test_a_voided_authorization_does_not_undo_a_refund(
    sessions: async_sessionmaker[AsyncSession], wallet_id: UUID
) -> None:
    order_id = await placed_order(sessions, wallet_id, "refunded")
    order = await Orders(sessions).get_by_id(order_id)
    voided = await event(
        sessions,
        "PAYMENT.AUTHORIZATION.VOIDED",
        "authorization",
        {"id": order.authorization_id},
    )

    await webhooks.handlers(sessions)["PAYMENT.AUTHORIZATION.VOIDED"](voided)

    after = await Orders(sessions).get_by_id(order_id)
    assert after.status == "refunded"
    assert [item.to_status for item in after.events] == ["refunded"]


async def test_a_voided_authorization_reopens_the_cart(
    sessions: async_sessionmaker[AsyncSession], wallet_id: UUID
) -> None:
    order_id = await placed_order(sessions, wallet_id, "authorized")
    order = await Orders(sessions).get_by_id(order_id)
    voided = await event(
        sessions,
        "PAYMENT.AUTHORIZATION.VOIDED",
        "authorization",
        {"id": order.authorization_id},
    )

    await webhooks.handlers(sessions)["PAYMENT.AUTHORIZATION.VOIDED"](voided)

    assert (await Orders(sessions).get_by_id(order_id)).status == "voided"
    cart = await cart_of(sessions, order_id)
    assert (cart.status, cart.completed_at) == ("open", None)


async def test_a_declined_capture_reopens_the_cart(
    sessions: async_sessionmaker[AsyncSession], wallet_id: UUID
) -> None:
    order_id = await placed_order(sessions, wallet_id, "capture_pending")
    order = await Orders(sessions).get_by_id(order_id)
    declined = await event(
        sessions, "PAYMENT.CAPTURE.DECLINED", "capture", {"id": order.capture_id}
    )

    await webhooks.handlers(sessions)["PAYMENT.CAPTURE.DECLINED"](declined)

    assert (await Orders(sessions).get_by_id(order_id)).status == "declined"
    cart = await cart_of(sessions, order_id)
    assert (cart.status, cart.completed_at) == ("open", None)


async def test_a_late_pending_event_does_not_undo_a_capture(
    sessions: async_sessionmaker[AsyncSession], wallet_id: UUID
) -> None:
    order_id = await placed_order(sessions, wallet_id, "captured")
    order = await Orders(sessions).get_by_id(order_id)
    pending = await event(
        sessions, "PAYMENT.CAPTURE.PENDING", "capture", {"id": order.capture_id}
    )

    await webhooks.handlers(sessions)["PAYMENT.CAPTURE.PENDING"](pending)

    after = await Orders(sessions).get_by_id(order_id)
    assert after.status == "captured"
    [recorded] = after.events
    assert (recorded.to_status, recorded.paypal_event) == (
        "captured",
        "PAYMENT.CAPTURE.PENDING",
    )


async def test_an_event_for_an_unknown_capture_is_ignored(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    stray = await event(
        sessions, "PAYMENT.CAPTURE.COMPLETED", "capture", {"id": "CAP-UNKNOWN"}
    )

    await webhooks.handlers(sessions)["PAYMENT.CAPTURE.COMPLETED"](stray)
