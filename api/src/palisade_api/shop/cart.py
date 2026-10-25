"""Carts: what a buyer intends to buy, with the wallet that will pay for it."""

from typing import Literal, final
from uuid import UUID, uuid7

from pydantic import BaseModel, Field, TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from palisade_api.shop.catalog import Money, UnknownProduct, money
from palisade_api.shop.models import CartLineRecord, CartRecord, ProductRecord
from palisade_api.wallets.models import WalletRecord
from palisade_api.wallets.service import UnknownWallet

CartStatus = Literal["open", "checking_out", "completed"]
CART_STATUS = TypeAdapter(CartStatus)


class CartLine(BaseModel):
    product_id: str
    size: str
    quantity: int
    brand: str
    name: str
    unit_price: Money
    line_total: Money


class Cart(BaseModel):
    """A cart as the shop reads it.

    The wallet it pays with stays server-side: a cart id reached through an order's page must
    not hand out a wallet that another cart could charge.
    """

    id: UUID
    status: CartStatus
    lines: list[CartLine]
    total: Money


class LineChange(BaseModel):
    """A product in a size and the quantity wanted; 0 removes the line."""

    product_id: str
    size: str
    quantity: int = Field(ge=0, le=20)


class UnknownCart(LookupError):
    def __init__(self, cart_id: UUID) -> None:
        super().__init__(f"no cart has the id {cart_id}")


class CartNotOpen(Exception):
    def __init__(self, cart_id: UUID, status: str) -> None:
        super().__init__(f"cart {cart_id} is {status}, so it cannot change")


class UnknownSize(ValueError):
    def __init__(self, product_id: str, size: str) -> None:
        super().__init__(f"{product_id} is not offered in size {size}")


def cart_line(line: CartLineRecord) -> CartLine:
    return CartLine(
        product_id=line.product_id,
        size=line.size,
        quantity=line.quantity,
        brand=line.brand,
        name=line.name,
        unit_price=money(line.unit_price_cents),
        line_total=money(line.unit_price_cents * line.quantity),
    )


def cart(record: CartRecord) -> Cart:
    return Cart(
        id=record.id,
        status=CART_STATUS.validate_python(record.status),
        lines=[cart_line(line) for line in record.lines],
        total=money(
            sum(line.unit_price_cents * line.quantity for line in record.lines)
        ),
    )


async def load_cart(session: AsyncSession, cart_id: UUID) -> CartRecord:
    record = await session.get(
        CartRecord, cart_id, options=[selectinload(CartRecord.lines)]
    )
    if record is None:
        raise UnknownCart(cart_id)
    return record


@final
class Carts:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(self, wallet_id: UUID) -> Cart:
        async with self._sessions.begin() as session:
            wallet = await session.get(WalletRecord, wallet_id)
            if wallet is None or wallet.status != "active":
                raise UnknownWallet(wallet_id)
            record = CartRecord(
                id=uuid7(), wallet_id=wallet_id, status="open", lines=[]
            )
            session.add(record)
            return cart(record)

    async def get(self, cart_id: UUID) -> Cart:
        async with self._sessions() as session:
            return cart(await load_cart(session, cart_id))

    async def set_line(self, cart_id: UUID, change: LineChange) -> Cart:
        """Set the quantity of one product in one size, adding or removing the line as needed."""
        async with self._sessions.begin() as session:
            record = await self._open(session, cart_id)
            product = await session.get(ProductRecord, change.product_id)
            if product is None:
                raise UnknownProduct(change.product_id)
            if change.size not in product.sizes:
                raise UnknownSize(change.product_id, change.size)
            line = next(
                (
                    line
                    for line in record.lines
                    if line.product_id == product.id and line.size == change.size
                ),
                None,
            )
            if change.quantity == 0:
                if line is not None:
                    record.lines.remove(line)
            elif line is None:
                record.lines.append(
                    CartLineRecord(
                        id=uuid7(),
                        product_id=product.id,
                        size=change.size,
                        quantity=change.quantity,
                        brand=product.brand,
                        name=product.name,
                        unit_price_cents=product.price_cents,
                    )
                )
            else:
                line.quantity = change.quantity
            return cart(record)

    async def set_wallet(self, cart_id: UUID, wallet_id: UUID) -> Cart:
        async with self._sessions.begin() as session:
            record = await self._open(session, cart_id)
            wallet = await session.get(WalletRecord, wallet_id)
            if wallet is None or wallet.status != "active":
                raise UnknownWallet(wallet_id)
            record.wallet_id = wallet_id
            return cart(record)

    @staticmethod
    async def _open(session: AsyncSession, cart_id: UUID) -> CartRecord:
        record = await load_cart(session, cart_id)
        if record.status != "open":
            raise CartNotOpen(cart_id, record.status)
        return record
