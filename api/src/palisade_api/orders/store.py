"""Orders as the shop and the console read them."""

from datetime import datetime
from typing import Literal, final
from uuid import UUID

from pydantic import BaseModel, TypeAdapter
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from palisade_api.orders.models import (
    OrderEventRecord,
    OrderLineRecord,
    OrderRecord,
    PayPalCallRecord,
)
from palisade_api.shop.catalog import Money, money

# "creating" until PayPal answers the create call; "declined" when PayPal refused the payment;
# "failed" when the outcome is unknown (the call timed out); then the payment's own states.
OrderStatus = Literal[
    "creating",
    "declined",
    "failed",
    "authorized",
    "capture_pending",
    "captured",
    "voided",
    "refunded",
    "reversed",
]
ORDER_STATUS = TypeAdapter(OrderStatus)
Channel = Literal["human", "agent"]
CHANNEL = TypeAdapter(Channel)
ORDERS_LISTED = 100


class OrderLine(BaseModel):
    product_id: str
    size: str
    quantity: int
    brand: str
    name: str
    unit_price: Money
    line_total: Money


class OrderEvent(BaseModel):
    from_status: OrderStatus | None
    to_status: OrderStatus
    source: Literal["api", "webhook"]
    webhook_event_id: str | None
    detail: str | None
    at: datetime


class PayPalCall(BaseModel):
    action: Literal["create", "capture", "void"]
    request_id: str
    status_code: int | None
    debug_id: str | None
    started_at: datetime
    finished_at: datetime | None


class Order(BaseModel):
    id: UUID
    number: str
    cart_id: UUID
    channel: Channel
    status: OrderStatus
    total: Money
    custom_id: str | None
    paypal_order_id: str | None
    authorization_id: str | None
    authorization_expires_at: datetime | None
    capture_id: str | None
    payer_email: str | None
    wallet_email: str
    wallet_demo: bool
    lines: list[OrderLine]
    events: list[OrderEvent]
    calls: list[PayPalCall]
    created_at: datetime


class UnknownOrder(LookupError):
    def __init__(self, number: str) -> None:
        super().__init__(f"no order has the number {number}")


def order_line(line: OrderLineRecord) -> OrderLine:
    return OrderLine(
        product_id=line.product_id,
        size=line.size,
        quantity=line.quantity,
        brand=line.brand,
        name=line.name,
        unit_price=money(line.unit_price_cents),
        line_total=money(line.unit_price_cents * line.quantity),
    )


def order_event(event: OrderEventRecord) -> OrderEvent:
    return OrderEvent.model_validate(event, from_attributes=True)


def paypal_call(call: PayPalCallRecord) -> PayPalCall:
    return PayPalCall.model_validate(call, from_attributes=True)


def order(record: OrderRecord) -> Order:
    return Order(
        id=record.id,
        number=record.number,
        cart_id=record.cart_id,
        channel=CHANNEL.validate_python(record.channel),
        status=ORDER_STATUS.validate_python(record.status),
        total=money(record.total_cents),
        custom_id=record.custom_id,
        paypal_order_id=record.paypal_order_id,
        authorization_id=record.authorization_id,
        authorization_expires_at=record.authorization_expires_at,
        capture_id=record.capture_id,
        payer_email=record.payer_email,
        wallet_email=record.wallet.email,
        wallet_demo=record.wallet.demo,
        lines=[order_line(line) for line in record.lines],
        events=[order_event(event) for event in record.events],
        calls=[paypal_call(call) for call in record.calls],
        created_at=record.created_at,
    )


def with_details(statement: Select[OrderRecord]) -> Select[OrderRecord]:
    return statement.options(
        selectinload(OrderRecord.wallet),
        selectinload(OrderRecord.lines),
        selectinload(OrderRecord.events),
        selectinload(OrderRecord.calls),
    )


async def load_order(session: AsyncSession, order_id: UUID) -> OrderRecord:
    record = await session.scalar(
        with_details(select(OrderRecord).where(OrderRecord.id == order_id))
    )
    if record is None:
        raise UnknownOrder(str(order_id))
    return record


@final
class Orders:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list(self) -> list[Order]:
        """The most recent orders, newest first."""
        statement = with_details(
            select(OrderRecord)
            .order_by(OrderRecord.created_at.desc())
            .limit(ORDERS_LISTED)
        )
        async with self._sessions() as session:
            records = (await session.scalars(statement)).all()
            return [order(record) for record in records]

    async def get(self, number: str) -> Order:
        statement = with_details(
            select(OrderRecord).where(OrderRecord.number == number)
        )
        async with self._sessions() as session:
            record = await session.scalar(statement)
            if record is None:
                raise UnknownOrder(number)
            return order(record)

    async def get_by_id(self, order_id: UUID) -> Order:
        async with self._sessions() as session:
            return order(await load_order(session, order_id))
