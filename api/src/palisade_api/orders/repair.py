"""Finishing orders a checkout left halfway: PayPal's answer was lost, or the capture never landed."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import final
from uuid import UUID

import structlog
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
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

logger = structlog.stdlib.get_logger(__name__)


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
        """Take the order one step further; one PayPal refuses or does not answer waits a run."""
        try:
            if order.status == "authorized":
                await self._settle(order)
            else:
                await self._recreate(order)
        except (CheckoutDeclined, CheckoutFailed, PayPalError) as error:
            # A refusal is already on the order's timeline; silence leaves it for the next run.
            logger.info(
                "order_repair_outcome", order_number=order.number, outcome=str(error)
            )
        except Exception:
            # An order PayPal describes in a shape this code does not expect stays stuck, with
            # its traceback here for a person; it must not stop the orders behind it.
            logger.exception("order_repair_failed", order_number=order.number)

    async def stuck(self) -> list[Stuck]:
        """Orders untouched for a lease: a create with no known outcome, or a hold not captured."""
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
                    OrderRecord.status == "authorized",
                ),
            )
            .order_by(OrderRecord.created_at)
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
        """Ask PayPal what became of the hold, then record it, capture it, or void it."""
        if (
            order.paypal_order_id is None
            or order.authorization_id is None
            or order.authorization_expires_at is None
        ):
            raise ValueError(f"authorized order {order.number} lacks its PayPal ids")
        response = await self._paypal.get(
            f"/v2/checkout/orders/{order.paypal_order_id}"
        )
        payments = PayPalOrder.model_validate(response.body).purchase_units[0].payments
        made = [c for c in payments.captures if c.status in ("COMPLETED", "PENDING")]
        authorization = next(
            (a for a in payments.authorizations if a.id == order.authorization_id),
            None,
        )
        if authorization is None:
            raise ValueError(
                f"PayPal lists no authorization {order.authorization_id} on {order.paypal_order_id}"
            )
        if made:
            await self._checkout.captured(order.order_id, made[0])
        elif authorization.status in ("VOIDED", "EXPIRED"):
            await self._checkout.released(
                order.order_id, [f"AUTHORIZATION_{authorization.status}"]
            )
        elif order.authorization_expires_at - datetime.now(UTC) < EXPIRY_MARGIN:
            await self._checkout.void(
                order.order_id, order.authorization_id, ["AUTHORIZATION_NEAR_EXPIRY"]
            )
        else:
            await self._checkout.capture(order.order_id, order.authorization_id)
        logger.info("order_repaired", order_number=order.number, step="settle")
