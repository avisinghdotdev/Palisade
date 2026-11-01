from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from palisade_api.database import Base
from palisade_api.orders.models import OrderRecord


class DisputeRecord(Base):
    """A PayPal dispute on one of the shop's orders, as last read from PayPal.

    Disputes on payments made outside the shop are not kept.
    """

    __tablename__: str = "disputes"

    # PayPal's case id, such as PP-R-RXG-10195753.
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    capture_id: Mapped[str] = mapped_column(Text)
    buyer_transaction_id: Mapped[str | None] = mapped_column(Text)
    # The custom value PayPal holds on the disputed payment: the mandate's id and hash on an
    # agent order, which the console checks against the order's own.
    custom: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    channel: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text)
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(Text)
    seller_response_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # PayPal's dispute object as last fetched; the public view picks fields from it.
    detail: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # PayPal's update_time as last stored: an older read never overwrites a newer one.
    paypal_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    order: Mapped[OrderRecord] = relationship()
