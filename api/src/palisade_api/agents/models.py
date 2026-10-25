from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from palisade_api.database import Base


class AgentRecord(Base):
    """A shopping agent the merchant knows; its id is the `keyid` on every request it signs."""

    __tablename__: str = "agents"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    # The raw 32-byte Ed25519 public key, base64.
    public_key: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentNonceRecord(Base):
    """A nonce an agent has used; a second request with the same one is a replay."""

    __tablename__: str = "agent_nonces"

    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), primary_key=True)
    nonce: Mapped[str] = mapped_column(Text, primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
