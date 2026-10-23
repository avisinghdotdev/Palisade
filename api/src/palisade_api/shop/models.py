from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from palisade_api.database import Base


class ProductRecord(Base):
    """One shoe in the catalog; the id is the slug the shop, the carts, and the agent tools use."""

    __tablename__: str = "products"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    brand: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    # Whole US cents; every price in the catalog is in USD.
    price_cents: Mapped[int] = mapped_column(Integer)
    # US sizes offered, as the shop prints them ("9", "9.5"); chosen per cart line, no stock.
    sizes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    # Path of the product photo on the web origin.
    image_path: Mapped[str] = mapped_column(Text)
    # The photo's credit: the photographer, their Unsplash profile, and the photo's page.
    photo_by: Mapped[str] = mapped_column(Text)
    photo_by_url: Mapped[str] = mapped_column(Text)
    photo_url: Mapped[str] = mapped_column(Text)


class CartRecord(Base):
    """What a buyer intends to buy; its id is the only proof of ownership, so it stays opaque."""

    __tablename__: str = "carts"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    wallet_id: Mapped[UUID] = mapped_column(ForeignKey("wallets.id"))
    # "open" while lines can change, "checking_out" once an order is being placed from it,
    # "completed" when that order exists.
    status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    lines: Mapped[list[CartLineRecord]] = relationship(
        cascade="all, delete-orphan", order_by="CartLineRecord.created_at"
    )


class CartLineRecord(Base):
    """One product in one size; the name and price are copied in when the line is added."""

    __tablename__: str = "cart_lines"
    __table_args__: tuple[UniqueConstraint] = (
        UniqueConstraint("cart_id", "product_id", "size"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    cart_id: Mapped[UUID] = mapped_column(ForeignKey("carts.id", ondelete="CASCADE"))
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    size: Mapped[str] = mapped_column(Text)
    quantity: Mapped[int] = mapped_column(Integer)
    brand: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    unit_price_cents: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
