from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import DateTime, ForeignKey, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from palisade_api.agents.models import AgentRecord
from palisade_api.database import Base


class ActivityRecord(Base):
    """One thing that happened around an agent purchase or a dispute; rows are only ever added.

    Each row points at whatever it concerns: the agent, the conversation the in-page assistant
    ran, the mandate, the order, the dispute. An order's timeline follows these links back to
    the shopper's first request.
    """

    __tablename__: str = "activity"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, index=True)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    mandate_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("mandates.id"), index=True
    )
    order_id: Mapped[UUID | None] = mapped_column(ForeignKey("orders.id"), index=True)
    dispute_id: Mapped[str | None] = mapped_column(
        ForeignKey("disputes.id"), index=True
    )
    details: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    agent: Mapped[AgentRecord | None] = relationship()
