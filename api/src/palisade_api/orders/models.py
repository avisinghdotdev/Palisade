from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import DateTime, ForeignKey, Integer, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from palisade_api.database import Base
from palisade_api.wallets.models import WalletRecord


class OrderRecord(Base):
    """A purchase placed from a cart; the PayPal ids arrive as each call to PayPal answers."""

    __tablename__: str = "orders"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    # The code shown to the buyer and quoted to support; random so order volume stays private.
    number: Mapped[str] = mapped_column(Text, unique=True)
    # A cart that is not open has its latest order here; a declined order leaves the cart open
    # for another attempt, which places a new order.
    cart_id: Mapped[UUID] = mapped_column(ForeignKey("carts.id"), index=True)
    wallet_id: Mapped[UUID] = mapped_column(ForeignKey("wallets.id"))
    # "human" when the buyer checked out, "agent" when a shopping agent did under a mandate.
    channel: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text)
    total_cents: Mapped[int] = mapped_column(Integer)
    # The value sent as PayPal's custom_id: the mandate id and hash on an agent order.
    custom_id: Mapped[str | None] = mapped_column(Text)
    paypal_order_id: Mapped[str | None] = mapped_column(Text)
    # Payment webhooks find their order by one of these two ids.
    authorization_id: Mapped[str | None] = mapped_column(Text, index=True)
    authorization_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    capture_id: Mapped[str | None] = mapped_column(Text, index=True)
    payer_email: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    wallet: Mapped[WalletRecord] = relationship()
    lines: Mapped[list[OrderLineRecord]] = relationship(
        order_by="OrderLineRecord.position"
    )
    # UUIDv7 ids sort in creation order, which the server clock cannot promise inside one transaction.
    events: Mapped[list[OrderEventRecord]] = relationship(
        order_by="OrderEventRecord.id"
    )
    calls: Mapped[list[PayPalCallRecord]] = relationship(order_by="PayPalCallRecord.id")
    shipment: Mapped[ShipmentRecord | None] = relationship()


class OrderLineRecord(Base):
    """A cart line as it was when the order was placed; never changed afterwards."""

    __tablename__: str = "order_lines"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    size: Mapped[str] = mapped_column(Text)
    quantity: Mapped[int] = mapped_column(Integer)
    brand: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    unit_price_cents: Mapped[int] = mapped_column(Integer)


class PayPalCallRecord(Base):
    """One request to PayPal for an order: what was sent, under which request id, and the answer."""

    __tablename__: str = "paypal_calls"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    # "create", "capture", "void", or "track".
    action: Mapped[str] = mapped_column(Text)
    # The PayPal-Request-Id sent; a repeat with the same id is answered with the same result, and
    # the row keeps the latest answer.
    request_id: Mapped[str] = mapped_column(Text, unique=True)
    request_body: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    status_code: Mapped[int | None] = mapped_column(Integer)
    debug_id: Mapped[str | None] = mapped_column(Text)
    response_body: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrderEventRecord(Base):
    """One status change of an order and what caused it; rows are only ever added."""

    __tablename__: str = "order_events"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(Text)
    to_status: Mapped[str] = mapped_column(Text)
    # "api" for the checkout itself, "webhook" for a PayPal event, "repair" for the job that
    # finishes a checkout left halfway.
    source: Mapped[str] = mapped_column(Text)
    # Unique, so a replayed or retried webhook can never add a second row.
    webhook_event_id: Mapped[str | None] = mapped_column(
        ForeignKey("webhook_events.id"), unique=True
    )
    detail: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ShipmentRecord(Base):
    """The tracking the merchant added to an order's PayPal capture; one per order."""

    __tablename__: str = "shipments"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), unique=True)
    carrier: Mapped[str] = mapped_column(Text)
    tracking_number: Mapped[str] = mapped_column(Text)
    # PayPal names a tracker by the capture id and the tracking number joined with a hyphen.
    paypal_tracker_id: Mapped[str] = mapped_column(Text)
    shipped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
