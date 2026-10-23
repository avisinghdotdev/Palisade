"""Payment events from PayPal, matched to orders by their authorization or capture id."""

from functools import partial
from typing import Literal
from uuid import uuid7

import structlog
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.orders.models import OrderEventRecord, OrderRecord
from palisade_api.orders.store import OrderStatus
from palisade_api.webhooks.store import WebhookEvent
from palisade_api.webhooks.tasks import Handler

Kind = Literal["authorization", "capture"]

# What each event says about the order; None records the event without changing the status.
EVENTS: dict[str, tuple[Kind, OrderStatus | None]] = {
    "PAYMENT.AUTHORIZATION.CREATED": ("authorization", None),
    "PAYMENT.AUTHORIZATION.VOIDED": ("authorization", "voided"),
    "PAYMENT.CAPTURE.PENDING": ("capture", "capture_pending"),
    "PAYMENT.CAPTURE.COMPLETED": ("capture", "captured"),
    "PAYMENT.CAPTURE.DECLINED": ("capture", "declined"),
    "PAYMENT.CAPTURE.REFUNDED": ("capture", "refunded"),
    "PAYMENT.CAPTURE.REVERSED": ("capture", "reversed"),
}
# Statuses no later event moves an order out of.
TERMINAL: frozenset[OrderStatus] = frozenset(
    {"declined", "failed", "voided", "refunded", "reversed"}
)

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
    sessions: async_sessionmaker[AsyncSession],
    kind: Kind,
    to_status: OrderStatus | None,
    event: WebhookEvent,
) -> None:
    paypal_id = resource_id(event, kind)
    column = (
        OrderRecord.authorization_id
        if kind == "authorization"
        else OrderRecord.capture_id
    )
    async with sessions.begin() as session:
        record = await session.scalar(select(OrderRecord).where(column == paypal_id))
        if record is None:
            logger.info(
                "webhook_unmatched", event_id=event.id, event_type=event.event_type
            )
            return
        from_status = record.status
        if (
            to_status is not None
            and to_status != record.status
            and record.status not in TERMINAL
        ):
            record.status = to_status
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
        event_type: partial(apply, sessions, kind, to_status)
        for event_type, (kind, to_status) in EVENTS.items()
    }
