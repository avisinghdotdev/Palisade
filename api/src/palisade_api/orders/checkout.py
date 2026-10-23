"""Placing an order: freeze the cart, authorize from the saved wallet, capture, record every step."""

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import final
from uuid import UUID, uuid4, uuid7

from pydantic import BaseModel, JsonValue
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from palisade_api.orders.models import (
    OrderEventRecord,
    OrderLineRecord,
    OrderRecord,
    PayPalCallRecord,
)
from palisade_api.orders.store import Order, Orders, OrderStatus, load_order, order
from palisade_api.paypal.client import PayPalClient, PayPalError, PayPalResponse
from palisade_api.shop.cart import CartNotOpen, CartStatus, UnknownCart
from palisade_api.shop.catalog import money
from palisade_api.shop.models import CartLineRecord, CartRecord, ProductRecord
from palisade_api.wallets.models import WalletRecord

# Letters and digits that are not mistaken for each other when read aloud or typed.
NUMBER_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
NUMBER_LENGTH = 6


class EmptyCart(Exception):
    def __init__(self, cart_id: UUID) -> None:
        super().__init__(f"cart {cart_id} has no lines")


class CartChanged(Exception):
    """A product in the cart no longer matches the catalog; the buyer must look again."""

    def __init__(self, product_id: str) -> None:
        super().__init__(f"{product_id} changed since it was added to the cart")


class WalletUnavailable(Exception):
    def __init__(self, wallet_id: UUID) -> None:
        super().__init__(f"wallet {wallet_id} cannot pay")


class CheckoutDeclined(Exception):
    """PayPal refused the payment; the cart is open again for another try."""

    def __init__(self, issues: list[str]) -> None:
        super().__init__(f"PayPal declined the payment: {', '.join(issues)}")
        self.issues: list[str] = issues


class CheckoutFailed(Exception):
    """PayPal did not answer; whether the payment went through is unknown."""

    def __init__(self, number: str) -> None:
        super().__init__(f"order {number} did not complete; its outcome is unknown")
        self.number: str = number


class Authorization(BaseModel):
    id: str
    status: str
    expiration_time: datetime


class Payments(BaseModel):
    authorizations: list[Authorization]


class PurchaseUnit(BaseModel):
    payments: Payments


class Payer(BaseModel):
    email_address: str | None = None


class CreatedOrder(BaseModel):
    id: str
    purchase_units: list[PurchaseUnit]
    payer: Payer | None = None


class Capture(BaseModel):
    id: str
    status: str


@dataclass(frozen=True)
class Started:
    order_id: UUID
    number: str
    create_body: dict[str, JsonValue]


def order_number() -> str:
    code = "".join(secrets.choice(NUMBER_ALPHABET) for _ in range(NUMBER_LENGTH))
    return f"RR-{code}"


def create_order_body(
    number: str, lines: list[CartLineRecord], total_cents: int, vault_id: str
) -> dict[str, JsonValue]:
    """The Orders v2 request: authorize the cart's total from the saved wallet, buyer present."""
    items: list[JsonValue] = [
        {
            "name": f"{line.brand} {line.name}",
            "description": f"US size {line.size}",
            "sku": f"{line.product_id}/{line.size}",
            "unit_amount": money(line.unit_price_cents).model_dump(),
            "quantity": str(line.quantity),
            "category": "PHYSICAL_GOODS",
        }
        for line in lines
    ]
    total: dict[str, JsonValue] = dict(money(total_cents).model_dump())
    return {
        "intent": "AUTHORIZE",
        "purchase_units": [
            {
                "reference_id": number,
                "invoice_id": number,
                "amount": {**total, "breakdown": {"item_total": total}},
                "items": items,
            }
        ],
        "payment_source": {
            "paypal": {
                "vault_id": vault_id,
                "stored_credential": {
                    "payment_initiator": "CUSTOMER",
                    "usage": "SUBSEQUENT",
                    "usage_pattern": "IMMEDIATE",
                },
            }
        },
    }


def note_created(
    created: CreatedOrder, authorization: Authorization
) -> Callable[[OrderRecord], None]:
    def apply(record: OrderRecord) -> None:
        record.paypal_order_id = created.id
        record.authorization_id = authorization.id
        record.authorization_expires_at = authorization.expiration_time
        record.payer_email = created.payer.email_address if created.payer else None

    return apply


@final
class Checkout:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        paypal: PayPalClient,
        orders: Orders,
    ) -> None:
        self._sessions = sessions
        self._paypal = paypal
        self._orders = orders

    async def place_order(self, cart_id: UUID) -> Order:
        """Turn the cart into a paid order; a repeat call returns the order already placed.

        Raises:
            UnknownCart, EmptyCart, CartChanged, WalletUnavailable: nothing was sent to PayPal.
            CheckoutDeclined: PayPal refused the payment; the cart is open again.
            CheckoutFailed: PayPal did not answer; the order stays with status `failed`.
        """
        started = await self._begin(cart_id)
        if isinstance(started, Order):
            return started
        try:
            response = await self._call(
                started.order_id, "create", "/v2/checkout/orders", started.create_body
            )
        except PayPalError as error:
            raise await self._refused(started, error) from error
        created = CreatedOrder.model_validate(response.body)
        authorization = created.purchase_units[0].payments.authorizations[0]
        if authorization.status != "CREATED":
            await self._transition(
                started.order_id,
                "declined",
                detail=f"authorization {authorization.status}",
                cart_status="open",
                apply=note_created(created, authorization),
            )
            raise CheckoutDeclined([f"AUTHORIZATION_{authorization.status}"])
        await self._transition(
            started.order_id, "authorized", apply=note_created(created, authorization)
        )
        try:
            response = await self._call(
                started.order_id,
                "capture",
                f"/v2/payments/authorizations/{authorization.id}/capture",
                {"final_capture": True},
            )
        except PayPalError as error:
            raise await self._void(started, authorization.id, error.issues) from error
        capture = Capture.model_validate(response.body)
        if capture.status not in ("COMPLETED", "PENDING"):
            raise await self._void(
                started, authorization.id, [f"CAPTURE_{capture.status}"]
            )

        def note_capture(record: OrderRecord) -> None:
            record.capture_id = capture.id

        await self._transition(
            started.order_id,
            "captured" if capture.status == "COMPLETED" else "capture_pending",
            cart_status="completed",
            apply=note_capture,
        )
        return await self._orders.get_by_id(started.order_id)

    async def _begin(self, cart_id: UUID) -> Order | Started:
        """Freeze the cart and write the order before anything is sent to PayPal."""
        async with self._sessions.begin() as session:
            cart = await session.get(
                CartRecord,
                cart_id,
                options=[selectinload(CartRecord.lines)],
                with_for_update=True,
            )
            if cart is None:
                raise UnknownCart(cart_id)
            if cart.status != "open":
                placed = await session.scalar(
                    select(OrderRecord.id)
                    .where(OrderRecord.cart_id == cart_id)
                    .order_by(OrderRecord.created_at.desc())
                    .limit(1)
                )
                if placed is None:
                    raise CartNotOpen(cart_id, cart.status)
                return order(await load_order(session, placed))
            if not cart.lines:
                raise EmptyCart(cart_id)
            wallet = await session.get(WalletRecord, cart.wallet_id)
            if wallet is None or wallet.status != "active":
                raise WalletUnavailable(cart.wallet_id)
            for line in cart.lines:
                product = await session.get(ProductRecord, line.product_id)
                if (
                    product is None
                    or product.price_cents != line.unit_price_cents
                    or line.size not in product.sizes
                ):
                    raise CartChanged(line.product_id)
            total_cents = sum(
                line.unit_price_cents * line.quantity for line in cart.lines
            )
            record = OrderRecord(
                id=uuid7(),
                number=await self._unused_number(session),
                cart_id=cart.id,
                wallet_id=wallet.id,
                channel="human",
                status="creating",
                currency="USD",
                total_cents=total_cents,
                lines=[
                    OrderLineRecord(
                        id=uuid7(),
                        position=position,
                        product_id=line.product_id,
                        size=line.size,
                        quantity=line.quantity,
                        brand=line.brand,
                        name=line.name,
                        unit_price_cents=line.unit_price_cents,
                    )
                    for position, line in enumerate(cart.lines)
                ],
                events=[
                    OrderEventRecord(
                        id=uuid7(), from_status=None, to_status="creating", source="api"
                    )
                ],
            )
            session.add(record)
            cart.status = "checking_out"
            body = create_order_body(
                record.number, cart.lines, total_cents, wallet.payment_token_id
            )
            return Started(record.id, record.number, body)

    @staticmethod
    async def _unused_number(session: AsyncSession) -> str:
        while True:
            number = order_number()
            taken = await session.scalar(
                select(OrderRecord.id).where(OrderRecord.number == number)
            )
            if taken is None:
                return number

    async def _call(
        self, order_id: UUID, action: str, path: str, body: dict[str, JsonValue]
    ) -> PayPalResponse:
        """Send one request to PayPal, recorded before it leaves and again when it is answered."""
        call_id = uuid7()
        request_id = str(uuid4())
        async with self._sessions.begin() as session:
            session.add(
                PayPalCallRecord(
                    id=call_id,
                    order_id=order_id,
                    action=action,
                    request_id=request_id,
                    request_body=body,
                )
            )
        try:
            response = await self._paypal.post(path, body, request_id=request_id)
        except PayPalError as error:
            await self._finish_call(
                call_id, error.status_code, error.debug_id, error.error.model_dump()
            )
            raise
        await self._finish_call(
            call_id, response.status_code, response.debug_id, response.body
        )
        return response

    async def _finish_call(
        self,
        call_id: UUID,
        status_code: int,
        debug_id: str | None,
        response_body: dict[str, JsonValue],
    ) -> None:
        async with self._sessions.begin() as session:
            call = await session.get_one(PayPalCallRecord, call_id)
            call.status_code = status_code
            call.debug_id = debug_id
            call.response_body = response_body
            # The database's clock, like every other timestamp, so the API host's clock never skews it.
            call.finished_at = func.now()

    async def _refused(self, started: Started, error: PayPalError) -> Exception:
        """Record a create call PayPal refused or never answered; the exception to raise."""
        if error.status_code >= 500:
            await self._transition(
                started.order_id, "failed", detail=", ".join(error.issues)
            )
            return CheckoutFailed(started.number)
        await self._transition(
            started.order_id,
            "declined",
            detail=", ".join(error.issues),
            cart_status="open",
        )
        return CheckoutDeclined(error.issues)

    async def _void(
        self, started: Started, authorization_id: str, issues: list[str]
    ) -> Exception:
        """Release the hold after a capture that did not complete; the exception to raise."""
        await self._call(
            started.order_id,
            "void",
            f"/v2/payments/authorizations/{authorization_id}/void",
            {},
        )
        await self._transition(
            started.order_id, "voided", detail=", ".join(issues), cart_status="open"
        )
        return CheckoutDeclined(issues)

    async def _transition(
        self,
        order_id: UUID,
        to_status: OrderStatus,
        *,
        detail: str | None = None,
        cart_status: CartStatus | None = None,
        apply: Callable[[OrderRecord], None] | None = None,
    ) -> None:
        async with self._sessions.begin() as session:
            record = await session.get_one(OrderRecord, order_id)
            session.add(
                OrderEventRecord(
                    id=uuid7(),
                    order_id=order_id,
                    from_status=record.status,
                    to_status=to_status,
                    source="api",
                    detail=detail,
                )
            )
            if apply is not None:
                apply(record)
            record.status = to_status
            if cart_status is not None:
                cart = await session.get_one(CartRecord, record.cart_id)
                cart.status = cart_status
                if cart_status == "completed":
                    cart.completed_at = func.now()
