from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from palisade_api.database import Base


class WalletRecord(Base):
    """A PayPal wallet a buyer saved once; `payment_token_id` is the vault id later orders pay with."""

    __tablename__: str = "wallets"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    paypal_customer_id: Mapped[str] = mapped_column(Text)
    payment_token_id: Mapped[str] = mapped_column(Text, unique=True)
    payer_id: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text)
    first_name: Mapped[str] = mapped_column(Text)
    # True on the shop's default buyer; the newest active one is used.
    demo: Mapped[bool] = mapped_column(Boolean)
    # "active" until PayPal reports the token deleted.
    status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
