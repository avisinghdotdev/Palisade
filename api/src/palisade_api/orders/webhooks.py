"""Payment events from PayPal, matched to orders by their authorization or capture id."""

from dataclasses import dataclass
from functools import partial
from typing import Literal
from uuid import uuid7

import structlog
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.orders.models import OrderEventRecord, OrderRecord
from palisade_api.orders.store import ORDER_STATUS, OrderStatus, PaymentEvent
from palisade_api.webhooks.store import WebhookEvent
from palisade_api.webhooks.tasks import Handler

Kind = Literal["authorization", "capture"]


@dataclass(frozen=True)
class Effect:
    """What an event says about its order: the status it sets, and from which statuses only.

    PayPal does not promise delivery order, so a late "capture pending" after the capture
    completed, or a void after a refund, is recorded on the timeline without moving the status.
    """

    kind: Kind
    to_status: OrderStatus | None = None
    from_statuses: frozenset[OrderStatus] = frozenset()

    def moves(self, status: OrderStatus) -> bool:
        return self.to_status is not None and status in self.from_statuses


EVENTS: dict[PaymentEvent, Effect] = {
    "PAYMENT.AUTHORIZATION.CREATED": Effect("authorization"),
    "PAYMENT.AUTHORIZATION.VOIDED": Effect(
        "authorization", "voided", frozenset({"authorized"})
    ),
    "PAYMENT.CAPTURE.PENDING": Effect(
        "capture", "capture_pending", frozenset({"authorized"})
    ),
    "PAYMENT.CAPTURE.COMPLETED": Effect(
        "capture", "captured", frozenset({"authorized", "capture_pending"})
    ),
    "PAYMENT.CAPTURE.DECLINED": Effect(
        "capture", "declined", frozenset({"authorized", "capture_pending"})
    ),
    "PAYMENT.CAPTURE.REFUNDED": Effect(
        "capture", "refunded", frozenset({"captured", "capture_pending"})
    ),
    "PAYMENT.CAPTURE.REVERSED": Effect("capture", "reversed", frozenset({"captured"})),
}

logger = structlog.stdlib.get_logger(__name__)


class Link(BaseModel):
    rel: str
    href: str


class Resource(BaseModel):
    """The parts of a payment resource that name it and its parent."""

    id: str
    links: list[Link] = []


def resource_id(event: WebhookEvent, kind: Kind) -> str:
    """The authorization or capture id the event is about.

    A refund event carries the refund as its resource; the capture it belongs to is the `up` link.
    """
    resource = Resource.model_validate(event.resource)
    if event.resource_type == kind:
        return resource.id
    parent = next(link.href for link in resource.links if link.rel == "up")
    return parent.rsplit("/", 1)[-1]


async def apply(
    sessions: async_sessionmaker[AsyncSession], effect: Effect, event: WebhookEvent
) -> None:
    paypal_id = resource_id(event, effect.kind)
    column = (
        OrderRecord.authorization_id
        if effect.kind == "authorization"
        else OrderRecord.capture_id
    )
    async with sessions.begin() as session:
        # The row lock runs one order's events one at a time, across worker replicas too.
        record = await session.scalar(
            select(OrderRecord).where(column == paypal_id).with_for_update()
        )
        if record is None:
            logger.info(
                "webhook_unmatched", event_id=event.id, event_type=event.event_type
            )
            return
        recorded = await session.scalar(
            select(OrderEventRecord.id).where(
                OrderEventRecord.webhook_event_id == event.id
            )
        )
        if recorded is not None:
            logger.info(
                "webhook_already_applied",
                event_id=event.id,
                order_number=record.number,
            )
            return
        from_status = ORDER_STATUS.validate_python(record.status)
        if effect.moves(from_status) and effect.to_status is not None:
            record.status = effect.to_status
        session.add(
            OrderEventRecord(
                id=uuid7(),
                order_id=record.id,
                from_status=from_status,
                to_status=record.status,
                source="webhook",
                webhook_event_id=event.id,
                detail=event.event_type,
            )
        )
        logger.info(
            "order_webhook_applied",
            event_id=event.id,
            order_number=record.number,
            from_status=from_status,
            to_status=record.status,
        )


def handlers(sessions: async_sessionmaker[AsyncSession]) -> dict[str, Handler]:
    return {
        event_type: partial(apply, sessions, effect)
        for event_type, effect in EVENTS.items()
    }
