"""The shoe catalog, read from PostgreSQL."""

from decimal import Decimal, InvalidOperation
from typing import Literal, final, get_args

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.shop.models import ProductRecord

Category = Literal["Road", "Tempo", "Trail", "Stability"]
CATEGORIES: tuple[Category, ...] = get_args(Category)
# The most a buyer can allow in one amount: the merchant's own caps sit at the same figure.
MAX_AMOUNT = Decimal("1000000.00")
CENT = Decimal("0.01")


class Money(BaseModel):
    """An amount in PayPal's shape, so an order line can carry it unchanged."""

    value: str
    currency_code: Literal["USD"]


def money(cents: int) -> Money:
    """Whole US cents as PayPal's two-decimal string."""
    dollars, rest = divmod(cents, 100)
    return Money(value=f"{dollars}.{rest:02d}", currency_code="USD")


def cents_of(amount: Money) -> int:
    """The amount in whole cents.

    Raises:
        ValueError: it is not a positive amount of at most two decimals, up to MAX_AMOUNT.
    """
    try:
        value = Decimal(amount.value)
    except InvalidOperation as error:
        raise ValueError(f"{amount.value!r} is not an amount") from error
    if not value.is_finite() or value <= 0 or value > MAX_AMOUNT:
        raise ValueError(f"the amount must be more than 0 and at most {MAX_AMOUNT}")
    if value != value.quantize(CENT):
        raise ValueError("the amount has at most two decimals")
    return int(value * 100)


class PhotoCredit(BaseModel):
    """Who took the product photo and where it lives on Unsplash."""

    by: str
    by_url: str
    url: str


class Product(BaseModel):
    id: str
    brand: str
    name: str
    category: str
    description: str
    price: Money
    sizes: list[str]
    image_path: str
    photo: PhotoCredit


class UnknownProduct(LookupError):
    def __init__(self, product_id: str) -> None:
        super().__init__(f"no product has the id {product_id}")


def product(record: ProductRecord) -> Product:
    return Product(
        id=record.id,
        brand=record.brand,
        name=record.name,
        category=record.category,
        description=record.description,
        price=money(record.price_cents),
        sizes=record.sizes,
        image_path=record.image_path,
        photo=PhotoCredit(
            by=record.photo_by, by_url=record.photo_by_url, url=record.photo_url
        ),
    )


@final
class Catalog:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def products(self) -> list[Product]:
        """Every product, by brand and then name."""
        statement = select(ProductRecord).order_by(
            ProductRecord.brand, ProductRecord.name
        )
        async with self._sessions() as session:
            records = (await session.scalars(statement)).all()
        return [product(record) for record in records]

    async def get(self, product_id: str) -> Product:
        async with self._sessions() as session:
            record = await session.get(ProductRecord, product_id)
        if record is None:
            raise UnknownProduct(product_id)
        return product(record)
