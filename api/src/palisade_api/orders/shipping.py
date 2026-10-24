"""Shipping a paid order: the tracking goes onto its PayPal capture, so a dispute has proof to file."""

from typing import Annotated, final
from uuid import uuid7

from pydantic import BaseModel, StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.orders.calls import PayPalCalls
from palisade_api.orders.models import OrderRecord, ShipmentRecord
from palisade_api.orders.store import Carrier, Order, Orders, UnknownOrder


class ShipmentRequest(BaseModel):
    carrier: Carrier
    tracking_number: Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]{8,34}$")]


class NotShippable(Exception):
    def __init__(self, number: str, status: str) -> None:
        super().__init__(f"order {number} is {status}; only a captured order ships")


class AlreadyShipped(Exception):
    def __init__(self, number: str) -> None:
        super().__init__(f"order {number} is already marked shipped")


class Tracker(BaseModel):
    id: str


class TrackedShipping(BaseModel):
    trackers: list[Tracker]


class TrackedUnit(BaseModel):
    shipping: TrackedShipping


class TrackedOrder(BaseModel):
    """The parts of PayPal's answer to an add-tracking call that list the order's trackers."""

    purchase_units: list[TrackedUnit]


@final
class Shipments:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        calls: PayPalCalls,
        orders: Orders,
    ) -> None:
        self._sessions = sessions
        self._calls = calls
        self._orders = orders

    async def ship(self, number: str, request: ShipmentRequest) -> Order:
        """Add the tracking to the order's PayPal capture, then record the shipment.

        Raises:
            UnknownOrder: no order has this number.
            NotShippable: the payment is not captured.
            AlreadyShipped: the order has its shipment.
            PayPalError: PayPal refused the tracking or did not answer; nothing is recorded.
        """
        async with self._sessions.begin() as session:
            # FOR NO KEY UPDATE holds a second request here until this one is recorded, while the
            # PayPal call's own rows can still reference the order.
            record = await session.scalar(
                select(OrderRecord)
                .where(OrderRecord.number == number)
                .with_for_update(key_share=True)
            )
            if record is None:
                raise UnknownOrder(number)
            if (
                record.status != "captured"
                or record.paypal_order_id is None
                or record.capture_id is None
            ):
                raise NotShippable(number, record.status)
            shipped = await session.scalar(
                select(ShipmentRecord.id).where(ShipmentRecord.order_id == record.id)
            )
            if shipped is not None:
                raise AlreadyShipped(number)
            response = await self._calls.send(
                record.id,
                "track",
                f"/v2/checkout/orders/{record.paypal_order_id}/track",
                {
                    "capture_id": record.capture_id,
                    "tracking_number": request.tracking_number,
                    "carrier": request.carrier,
                    # The shop tells its own buyers; PayPal's email would be a second one.
                    "notify_payer": False,
                },
            )
            tracker_id = f"{record.capture_id}-{request.tracking_number}"
            trackers = TrackedOrder.model_validate(response.body).purchase_units[0]
            if tracker_id not in {tracker.id for tracker in trackers.shipping.trackers}:
                raise ValueError(f"PayPal's answer lists no tracker {tracker_id}")
            session.add(
                ShipmentRecord(
                    id=uuid7(),
                    order_id=record.id,
                    carrier=request.carrier,
                    tracking_number=request.tracking_number,
                    paypal_tracker_id=tracker_id,
                )
            )
        return await self._orders.get(number)
