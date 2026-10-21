from sqlalchemy import Integer, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

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
