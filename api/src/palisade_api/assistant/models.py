from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from palisade_api.database import Base


class ConversationRecord(Base):
    """One chat between a shopper and the in-page agent, with the history Claude is replayed."""

    __tablename__: str = "conversations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    # The wallet that pays for whatever the agent buys in this conversation.
    wallet_id: Mapped[UUID] = mapped_column(ForeignKey("wallets.id"))
    # The cart the agent fills; None until it adds the first pair, and again after an order.
    cart_id: Mapped[UUID | None] = mapped_column(ForeignKey("carts.id"))
    # SHA-256 of the key handed to the browser that started the conversation; every read and
    # every message must carry that key, so the shared chat code alone opens nobody else's chat.
    key_hash: Mapped[str] = mapped_column(Text)
    # The messages as the Messages API takes them, appended after every turn and never edited.
    history: Mapped[list[JsonValue]] = mapped_column(JSONB)
    # Set while a turn runs, so two turns never run at once; a stale value marks a crashed turn.
    busy_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    turns: Mapped[list[TurnRecord]] = relationship(order_by="TurnRecord.position")


class TurnRecord(Base):
    """One exchange: what the shopper sent and everything the agent said and did in answer."""

    __tablename__: str = "conversation_turns"
    __table_args__: tuple[UniqueConstraint] = (
        UniqueConstraint("conversation_id", "position"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    # The shopper's side as the panel shows it: a message, or the outcome of a consent page.
    request: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    # The agent's side as the panel shows it: text, tool calls, and their results, in order.
    events: Mapped[list[JsonValue]] = mapped_column(JSONB)
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cache_read_tokens: Mapped[int] = mapped_column(Integer)
    cache_write_tokens: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
