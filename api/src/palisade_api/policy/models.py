from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Integer, Text, Uuid, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from palisade_api.database import Base


class PolicyRecord(Base):
    """What the merchant lets agents buy; the newest row is the policy in force."""

    __tablename__: str = "policies"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    allowed_agents: Mapped[list[str]] = mapped_column(ARRAY(Text))
    per_order_cap_cents: Mapped[int] = mapped_column(Integer)
    per_agent_daily_cap_cents: Mapped[int] = mapped_column(Integer)
    blocked_categories: Mapped[list[str]] = mapped_column(ARRAY(Text))
    # SHA-256 of the rules above in their canonical JSON form; the mandate and the order carry it.
    hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
