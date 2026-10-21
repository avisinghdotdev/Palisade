"""The shoe catalog, read from PostgreSQL."""

from typing import Literal, final

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.shop.models import ProductRecord


class Money(BaseModel):
    """An amount in PayPal's shape, so an order line can carry it unchanged."""

    value: str
    currency_code: Literal["USD"]


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
    dollars, cents = divmod(record.price_cents, 100)
    return Product(
        id=record.id,
        brand=record.brand,
        name=record.name,
        category=record.category,
        description=record.description,
        price=Money(value=f"{dollars}.{cents:02d}", currency_code="USD"),
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
