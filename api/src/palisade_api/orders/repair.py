"""Finishing orders a checkout left halfway: PayPal's answer was lost, or the capture never landed."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import final
from uuid import UUID

import structlog
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.orders.checkout import (
    Capture,
    Checkout,
    CheckoutDeclined,
    CheckoutFailed,
)
from palisade_api.orders.models import OrderRecord, PayPalCallRecord
from palisade_api.paypal.client import PayPalClient, PayPalError
from palisade_api.shop.models import CartRecord

# A checkout still running answers well within this, both PayPal repeats included.
LEASE = timedelta(minutes=5)
# PayPal answers a repeated create with the first result for six hours.
CREATE_REPEAT_WINDOW = timedelta(hours=6)
# A hold this close to its expiry is voided rather than captured.
EXPIRY_MARGIN = timedelta(days=1)
BATCH = 20
# Authorization statuses that no longer hold the buyer's money.
RELEASED = ("VOIDED", "EXPIRED", "DENIED")
# Capture statuses that took no money.
FAILED_CAPTURES = ("DECLINED", "FAILED")

logger = structlog.stdlib.get_logger(__name__)


class Waiting(Exception):
    """PayPal is still deciding; the order is looked at again on a later run."""

    def __init__(self, what: str) -> None:
        super().__init__(f"PayPal still lists the {what} as pending")


class PayPalAuthorization(BaseModel):
    id: str
    status: str


class PayPalPayments(BaseModel):
    authorizations: list[PayPalAuthorization] = []
    captures: list[Capture] = []


class PayPalUnit(BaseModel):
    payments: PayPalPayments


class PayPalOrder(BaseModel):
    """The parts of a PayPal order that tell what became of its authorization."""

    purchase_units: list[PayPalUnit]


@dataclass(frozen=True)
class Stuck:
    order_id: UUID
    number: str
    status: str
    paypal_order_id: str | None
    authorization_id: str | None
    authorization_expires_at: datetime | None


@final
class Repairs:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        checkout: Checkout,
        paypal: PayPalClient,
    ) -> None:
        self._sessions = sessions
        self._checkout = checkout
        self._paypal = paypal

    async def run(self) -> list[str]:
        """Repair every stuck order once; the numbers of the orders looked at."""
        stuck = await self.stuck()
        for order in stuck:
            await self.repair(order)
        return [order.number for order in stuck]

    async def repair(self, order: Stuck) -> None:
        """Take the order one step further; one PayPal refuses or does not answer waits a run.

        An order left where it was goes to the back of the queue, so a few that fail every
        time never keep the ones behind them from being looked at.
        """
        try:
            if order.status in ("authorized", "capture_pending"):
                await self._settle(order)
            else:
                await self._recreate(order)
        except (CheckoutDeclined, CheckoutFailed, PayPalError, Waiting) as error:
            # A refusal is already on the order's timeline; silence leaves it for the next run.
            logger.info(
                "order_repair_outcome", order_number=order.number, outcome=str(error)
            )
            await self._touch(order.order_id)
        except Exception:
            # An order PayPal describes in a shape this code does not expect stays stuck, with
            # its traceback here for a person; it must not stop the orders behind it.
            logger.exception("order_repair_failed", order_number=order.number)
            await self._touch(order.order_id)

    async def stuck(self) -> list[Stuck]:
        """Orders untouched for a lease, the longest untouched first.

        That is a create with no known outcome, a hold not captured, or a capture PayPal has
        not settled.
        """
        statement = (
            select(
                OrderRecord.id,
                OrderRecord.number,
                OrderRecord.status,
                OrderRecord.paypal_order_id,
                OrderRecord.authorization_id,
                OrderRecord.authorization_expires_at,
            )
            .join(CartRecord, CartRecord.id == OrderRecord.cart_id)
            .where(
                OrderRecord.updated_at < func.now() - LEASE,
                or_(
                    and_(
                        OrderRecord.status.in_(("creating", "failed")),
                        CartRecord.status == "checking_out",
                    ),
                    OrderRecord.status.in_(("authorized", "capture_pending")),
                ),
            )
            .order_by(OrderRecord.updated_at)
            .limit(BATCH)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return [
            Stuck(order_id, number, status, paypal_order, authorization, expires_at)
            for order_id, number, status, paypal_order, authorization, expires_at in rows
        ]

    async def _recreate(self, order: Stuck) -> None:
        """Repeat the create under its request id, or give up once PayPal no longer can answer."""
        async with self._sessions() as session:
            create = await session.scalar(
                select(PayPalCallRecord).where(
                    PayPalCallRecord.order_id == order.order_id,
                    PayPalCallRecord.action == "create",
                )
            )
        if create is None:
            await self._checkout.give_up(order.order_id, "CREATE_NEVER_SENT")
        elif create.started_at < datetime.now(UTC) - CREATE_REPEAT_WINDOW:
            await self._checkout.give_up(order.order_id, "CREATE_REPEAT_WINDOW_PASSED")
        else:
            await self._checkout.create(
                order.order_id, order.number, create.request_body
            )
        logger.info("order_repaired", order_number=order.number, step="create")

    async def _settle(self, order: Stuck) -> None:
        """Ask PayPal what became of the hold or the capture, then record it, capture, or void.

        Raises:
            Waiting: PayPal is still reviewing the hold or the capture.
        """
        if (
            order.paypal_order_id is None
            or order.authorization_id is None
            or order.authorization_expires_at is None
        ):
            raise ValueError(
                f"{order.status} order {order.number} lacks its PayPal ids"
            )
        response = await self._paypal.get(
            f"/v2/checkout/orders/{order.paypal_order_id}"
        )
        payments = PayPalOrder.model_validate(response.body).purchase_units[0].payments
        completed = [c for c in payments.captures if c.status == "COMPLETED"]
        pending = [c for c in payments.captures if c.status == "PENDING"]
        authorization = next(
            (a for a in payments.authorizations if a.id == order.authorization_id),
            None,
        )
        if authorization is None:
            raise ValueError(
                f"PayPal lists no authorization {order.authorization_id} on {order.paypal_order_id}"
            )
        if completed:
            await self._checkout.captured(order.order_id, completed[0])
        elif order.status == "capture_pending":
            if pending:
                raise Waiting("capture")
            failed = [c for c in payments.captures if c.status in FAILED_CAPTURES]
            if not failed:
                raise ValueError(
                    f"PayPal lists no capture for capture_pending order {order.number}"
                )
            await self._checkout.capture_declined(
                order.order_id, f"CAPTURE_{failed[0].status}"
            )
        elif pending:
            await self._checkout.captured(order.order_id, pending[0])
        elif authorization.status in RELEASED:
            await self._checkout.released(
                order.order_id, [f"AUTHORIZATION_{authorization.status}"]
            )
        elif authorization.status == "PENDING":
            raise Waiting("authorization")
        elif order.authorization_expires_at - datetime.now(UTC) < EXPIRY_MARGIN:
            await self._checkout.void(
                order.order_id, order.authorization_id, ["AUTHORIZATION_NEAR_EXPIRY"]
            )
        else:
            await self._checkout.capture(order.order_id, order.authorization_id)
        logger.info("order_repaired", order_number=order.number, step="settle")

    async def _touch(self, order_id: UUID) -> None:
        async with self._sessions.begin() as session:
            _ = await session.execute(
                update(OrderRecord)
                .where(OrderRecord.id == order_id)
                .values(updated_at=func.now())
            )
