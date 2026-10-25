"""Mandates: what a buyer allowed an agent to buy, from the agent's request to the checkout."""

import base64
from datetime import UTC, datetime, timedelta
from typing import Literal, final
from uuid import UUID, uuid7

import structlog
from pydantic import BaseModel, Field, JsonValue, TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload, selectinload

from palisade_api.agents.keys import private_key
from palisade_api.agents.registry import Agent
from palisade_api.mandates.models import MandateRecord
from palisade_api.mandates.passkeys import Passkeys
from palisade_api.mandates.tokens import MandateTokens, token_hash
from palisade_api.orders.models import OrderRecord
from palisade_api.policy.engine import SPENDING_STATUSES, Policies
from palisade_api.shop.catalog import Money, money
from palisade_api.shop.models import CartLineRecord, CartRecord
from palisade_api.wallets.models import WalletRecord

MandateKind = Literal["intent", "cart"]
MandateStatus = Literal["pending", "signed", "declined", "expired"]
SignedBy = Literal["passkey", "demo_signer"]
MANDATE_KIND = TypeAdapter(MandateKind)
SIGNED_BY = TypeAdapter(SignedBy)
MERCHANT = "ridgeline-running"
# How long the buyer has to answer a cart mandate, and how long an intent mandate stays usable.
CART_WINDOW = timedelta(minutes=15)
INTENT_WINDOW = timedelta(hours=24)

logger = structlog.stdlib.get_logger(__name__)


class MandateLine(BaseModel):
    product_id: str
    size: str
    quantity: int
    name: str
    unit_price: Money


class MandateRequest(BaseModel):
    """What an agent asks the buyer to allow."""

    kind: MandateKind
    wallet_id: UUID
    cap: Money
    # Intent mandates only; empty allows every category.
    categories: list[str] = Field(default_factory=list)
    # Cart mandates only: the exact cart the buyer approves.
    cart_id: UUID | None = None


class Mandate(BaseModel):
    id: UUID
    kind: MandateKind
    status: MandateStatus
    agent: Agent
    wallet_email: str
    cap: Money
    categories: list[str]
    cart_id: UUID | None
    lines: list[MandateLine]
    policy_hash: str
    token_hash: str
    signed_by: SignedBy | None
    signed_at: datetime | None
    expires_at: datetime
    # Cents the mandate's orders hold or took; a cart mandate is used once any exist.
    spent: Money
    consent_path: str
    created_at: datetime


class MandateSummary(BaseModel):
    """The mandate as an order carries it."""

    id: UUID
    kind: MandateKind
    signed_by: SignedBy | None
    signed_at: datetime | None
    cap: Money
    token_hash: str


class Approval(BaseModel):
    signed_by: SignedBy
    # The passkey that signed and the browser's assertion, for a passkey approval.
    passkey_id: UUID | None = None
    credential: dict[str, JsonValue] | None = None


class UnknownMandate(LookupError):
    def __init__(self, mandate_id: UUID) -> None:
        super().__init__(f"no mandate has the id {mandate_id}")


class MandateNotPending(Exception):
    def __init__(self, mandate_id: UUID, status: str) -> None:
        super().__init__(f"mandate {mandate_id} is {status}, so it cannot be answered")


class MandateRefused(Exception):
    """The checkout does not fit the mandate; the cart stays open."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__(
            f"the mandate does not cover the purchase: {', '.join(reasons)}"
        )
        self.reasons: list[str] = reasons


def status_of(record: MandateRecord, now: datetime) -> MandateStatus:
    stored = record.status
    if stored in ("pending", "signed") and record.expires_at <= now:
        return "expired"
    return TypeAdapter(MandateStatus).validate_python(stored)


def lines_of(cart: CartRecord) -> list[MandateLine]:
    return [
        MandateLine(
            product_id=line.product_id,
            size=line.size,
            quantity=line.quantity,
            name=f"{line.brand} {line.name}",
            unit_price=money(line.unit_price_cents),
        )
        for line in cart.lines
    ]


def summary(record: MandateRecord) -> MandateSummary:
    return MandateSummary(
        id=record.id,
        kind=MANDATE_KIND.validate_python(record.kind),
        signed_by=None
        if record.signed_by is None
        else SIGNED_BY.validate_python(record.signed_by),
        signed_at=record.signed_at,
        cap=money(record.cap_cents),
        token_hash=record.token_hash,
    )


@final
class Mandates:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        tokens: MandateTokens,
        passkeys: Passkeys,
        policies: Policies,
        demo_signer_seed: str,
        public_origin: str,
    ) -> None:
        self._sessions = sessions
        self._tokens = tokens
        self._passkeys = passkeys
        self._policies = policies
        self._demo_signer = private_key(demo_signer_seed)
        self._public_origin = public_origin

    async def create(self, agent: Agent, request: MandateRequest) -> Mandate:
        """Issue a pending mandate for the agent; the buyer answers it on the consent page.

        Raises:
            LookupError: the wallet or the cart does not exist, or the cart is not open.
        """
        async with self._sessions.begin() as session:
            wallet = await session.get(WalletRecord, request.wallet_id)
            if wallet is None or wallet.status != "active":
                raise LookupError(f"wallet {request.wallet_id} cannot pay")
            lines: list[MandateLine] = []
            if request.kind == "cart":
                if request.cart_id is None:
                    raise LookupError("a cart mandate names the cart it approves")
                cart = await session.get(
                    CartRecord,
                    request.cart_id,
                    options=[selectinload(CartRecord.lines)],
                )
                if cart is None or cart.status != "open" or not cart.lines:
                    raise LookupError(f"cart {request.cart_id} is not open with lines")
                lines = lines_of(cart)
            policy = await self._policies.current(session)
            mandate_id = uuid7()
            window = CART_WINDOW if request.kind == "cart" else INTENT_WINDOW
            # The database's clock, like every other timestamp, so the expiry and `created_at` agree.
            now = await session.scalar(select(func.now()))
            if now is None:
                raise RuntimeError("the database returned no clock")
            expires_at = now + window
            cap_cents = round(float(request.cap.value) * 100)
            token = self._tokens.issue(
                request.kind,
                {
                    "jti": str(mandate_id),
                    "iss": self._public_origin,
                    "merchant": MERCHANT,
                    "agent": agent.id,
                    "wallet": str(request.wallet_id),
                    "cap": request.cap.model_dump(),
                    "categories": list(request.categories),
                    "lines": [line.model_dump() for line in lines],
                    "policy_hash": policy.hash,
                    "iat": int(now.timestamp()),
                    "exp": int(expires_at.timestamp()),
                },
            )
            record = MandateRecord(
                id=mandate_id,
                kind=request.kind,
                agent_id=agent.id,
                wallet_id=request.wallet_id,
                cart_id=request.cart_id if request.kind == "cart" else None,
                cap_cents=cap_cents,
                currency=request.cap.currency_code,
                categories=list(request.categories),
                policy_hash=policy.hash,
                token=token,
                token_hash=token_hash(token),
                status="pending",
                expires_at=expires_at,
            )
            session.add(record)
            await session.flush()
            logger.info(
                "mandate_created",
                mandate_id=str(mandate_id),
                kind=request.kind,
                agent_id=agent.id,
            )
            return await self._load(session, mandate_id)

    async def get(self, mandate_id: UUID) -> Mandate:
        async with self._sessions() as session:
            return await self._load(session, mandate_id)

    async def passkey_options(
        self, mandate_id: UUID, passkey_id: UUID
    ) -> dict[str, JsonValue]:
        """The browser options that make the passkey sign this mandate's hash."""
        async with self._sessions() as session:
            record = await session.get(MandateRecord, mandate_id)
        if record is None:
            raise UnknownMandate(mandate_id)
        return await self._passkeys.assertion_options(
            passkey_id, bytes.fromhex(record.token_hash)
        )

    async def approve(self, mandate_id: UUID, approval: Approval) -> Mandate:
        """Record the buyer's proof over the token hash; the mandate is signed.

        Raises:
            UnknownMandate, MandateNotPending: nothing to answer.
            UnknownPasskey, PasskeyRefused: the passkey proof does not verify.
            ValueError: a passkey approval without its passkey or assertion.
        """
        async with self._sessions.begin() as session:
            record = await self._pending(session, mandate_id)
            digest = bytes.fromhex(record.token_hash)
            if approval.signed_by == "passkey":
                if approval.passkey_id is None or approval.credential is None:
                    raise ValueError(
                        "a passkey approval carries the passkey id and its assertion"
                    )
                await self._passkeys.verify(
                    approval.passkey_id, digest, approval.credential
                )
                proof: dict[str, JsonValue] = {
                    "passkey_id": str(approval.passkey_id),
                    "assertion": approval.credential,
                }
            else:
                proof = {
                    "signature": base64.b64encode(
                        self._demo_signer.sign(digest)
                    ).decode(),
                    "public_key": base64.b64encode(
                        self._demo_signer.public_key().public_bytes_raw()
                    ).decode(),
                    "simulated": True,
                }
            record.status = "signed"
            record.signed_by = approval.signed_by
            record.signed_at = await session.scalar(select(func.now()))
            record.proof = proof
            logger.info(
                "mandate_signed",
                mandate_id=str(mandate_id),
                signed_by=approval.signed_by,
            )
            return await self._load(session, mandate_id)

    async def decline(self, mandate_id: UUID) -> Mandate:
        async with self._sessions.begin() as session:
            record = await self._pending(session, mandate_id)
            record.status = "declined"
            logger.info("mandate_declined", mandate_id=str(mandate_id))
            return await self._load(session, mandate_id)

    async def claim(
        self,
        session: AsyncSession,
        mandate_id: UUID,
        agent: Agent,
        cart: CartRecord,
        categories: set[str],
        policy_hash: str,
    ) -> MandateRecord:
        """The mandate that covers checking out `cart`, locked for the caller's transaction.

        Raises:
            MandateRefused: it does not exist, is not signed, has expired, or does not fit.
        """
        record = await session.scalar(
            select(MandateRecord)
            .where(MandateRecord.id == mandate_id)
            .with_for_update()
        )
        if record is None:
            raise MandateRefused(["MANDATE_UNKNOWN"])
        now = datetime.now(UTC)
        status = status_of(record, now)
        reasons: list[str] = []
        if status != "signed":
            reasons.append(f"MANDATE_{status.upper()}")
        if record.agent_id != agent.id:
            reasons.append("MANDATE_AGENT_MISMATCH")
        if record.wallet_id != cart.wallet_id:
            reasons.append("MANDATE_WALLET_MISMATCH")
        if record.policy_hash != policy_hash:
            reasons.append("POLICY_CHANGED")
        total = sum(line.unit_price_cents * line.quantity for line in cart.lines)
        spent = await self.spent(session, mandate_id)
        if record.kind == "cart":
            if record.cart_id != cart.id or not self._same_lines(record, cart.lines):
                reasons.append("MANDATE_CART_MISMATCH")
            if spent > 0:
                reasons.append("MANDATE_USED")
            if total > record.cap_cents:
                reasons.append("MANDATE_CAP_EXCEEDED")
        else:
            reasons.extend(
                f"MANDATE_CATEGORY_NOT_ALLOWED:{category}"
                for category in sorted(categories - set(record.categories))
                if record.categories
            )
            if spent + total > record.cap_cents:
                reasons.append("MANDATE_CAP_EXCEEDED")
        if reasons:
            raise MandateRefused(reasons)
        return record

    @staticmethod
    async def spent(session: AsyncSession, mandate_id: UUID) -> int:
        """Cents the mandate's orders hold or took."""
        total = await session.scalar(
            select(func.coalesce(func.sum(OrderRecord.total_cents), 0)).where(
                OrderRecord.mandate_id == mandate_id,
                OrderRecord.status.in_(SPENDING_STATUSES),
            )
        )
        return int(total or 0)

    def _same_lines(self, record: MandateRecord, lines: list[CartLineRecord]) -> bool:
        """Whether the cart still holds exactly the lines the signed token names."""
        claims = self._tokens.verify(record.token)
        signed_lines = TypeAdapter(list[MandateLine]).validate_python(claims["lines"])
        signed = {(line.product_id, line.size, line.quantity) for line in signed_lines}
        return signed == {(line.product_id, line.size, line.quantity) for line in lines}

    async def _pending(self, session: AsyncSession, mandate_id: UUID) -> MandateRecord:
        record = await session.scalar(
            select(MandateRecord)
            .where(MandateRecord.id == mandate_id)
            .with_for_update()
        )
        if record is None:
            raise UnknownMandate(mandate_id)
        status = status_of(record, datetime.now(UTC))
        if status != "pending":
            raise MandateNotPending(mandate_id, status)
        return record

    async def _load(self, session: AsyncSession, mandate_id: UUID) -> Mandate:
        record = await session.scalar(
            select(MandateRecord)
            .where(MandateRecord.id == mandate_id)
            .options(joinedload(MandateRecord.agent), joinedload(MandateRecord.wallet))
        )
        if record is None:
            raise UnknownMandate(mandate_id)
        lines: list[MandateLine] = []
        if record.cart_id is not None:
            cart = await session.get(
                CartRecord, record.cart_id, options=[selectinload(CartRecord.lines)]
            )
            if cart is not None:
                lines = lines_of(cart)
        return Mandate(
            id=record.id,
            kind=MANDATE_KIND.validate_python(record.kind),
            status=status_of(record, datetime.now(UTC)),
            agent=Agent(id=record.agent.id, name=record.agent.name),
            wallet_email=record.wallet.email,
            cap=money(record.cap_cents),
            categories=list(record.categories),
            cart_id=record.cart_id,
            lines=lines,
            policy_hash=record.policy_hash,
            token_hash=record.token_hash,
            signed_by=None
            if record.signed_by is None
            else SIGNED_BY.validate_python(record.signed_by),
            signed_at=record.signed_at,
            expires_at=record.expires_at,
            spent=money(await self.spent(session, mandate_id)),
            consent_path=f"/shop/approve/{record.id}",
            created_at=record.created_at,
        )
