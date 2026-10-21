from datetime import datetime

from pydantic import JsonValue
from sqlalchemy import DateTime, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from palisade_api.database import Base


class WebhookEventRecord(Base):
    """One verified PayPal delivery, keyed by PayPal's event id so a repeat delivery is a no-op."""

    __tablename__: str = "webhook_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Set by the job that processed the event; null until it has run through.
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
