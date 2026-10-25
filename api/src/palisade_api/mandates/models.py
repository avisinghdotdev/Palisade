from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import DateTime, ForeignKey, Integer, Text, Uuid, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from palisade_api.agents.models import AgentRecord
from palisade_api.database import Base
from palisade_api.wallets.models import WalletRecord


class MandateRecord(Base):
    """What a buyer allowed an agent to buy, as a signed token and the buyer's proof over it."""

    __tablename__: str = "mandates"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    # "intent": a cap and categories, signed up front; "cart": one exact cart, signed at checkout.
    kind: Mapped[str] = mapped_column(Text)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    wallet_id: Mapped[UUID] = mapped_column(ForeignKey("wallets.id"))
    cart_id: Mapped[UUID | None] = mapped_column(ForeignKey("carts.id"))
    cap_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(Text)
    # Categories an intent mandate allows; empty means any.
    categories: Mapped[list[str]] = mapped_column(ARRAY(Text))
    policy_hash: Mapped[str] = mapped_column(Text)
    # The SD-JWT Palisade issued, and its SHA-256, which the buyer's proof signs and the PayPal
    # order carries in `custom_id`.
    token: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(Text)
    # "pending" until the buyer answers, then "signed" or "declined".
    status: Mapped[str] = mapped_column(Text)
    # "passkey" or "demo_signer" once signed.
    signed_by: Mapped[str | None] = mapped_column(Text)
    proof: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    agent: Mapped[AgentRecord] = relationship()
    wallet: Mapped[WalletRecord] = relationship()


class PasskeyRecord(Base):
    """A passkey a buyer's browser registered to sign mandates; no account stands behind it."""

    __tablename__: str = "passkeys"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    # WebAuthn's credential id and COSE public key, both base64url.
    credential_id: Mapped[str] = mapped_column(Text, unique=True)
    public_key: Mapped[str] = mapped_column(Text)
    sign_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PasskeyChallengeRecord(Base):
    """A registration challenge handed to a browser; consumed when the registration returns."""

    __tablename__: str = "passkey_challenges"

    challenge: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
