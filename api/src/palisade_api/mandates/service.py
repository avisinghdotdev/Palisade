"""Mandates: what a buyer allowed an agent to buy, from the agent's request to the checkout."""

import base64
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Literal, final
from uuid import UUID, uuid7

from pydantic import BaseModel, Field, JsonValue, TypeAdapter, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload, selectinload

from palisade_api.activity.store import Activities
from palisade_api.agents.keys import private_key
from palisade_api.agents.registry import Agent
from palisade_api.bearer import key_hash, key_matches, new_key
from palisade_api.mandates.models import MandateRecord
from palisade_api.mandates.passkeys import Passkeys, RegistrationStart
from palisade_api.mandates.tokens import MandateTokens, token_hash
from palisade_api.orders.models import OrderRecord
from palisade_api.policy.engine import Policies, spending
from palisade_api.shop.catalog import CATEGORIES, Category, Money, cents_of, money
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


class MandateLine(BaseModel):
    product_id: str
    size: str
    quantity: int
    name: str
    unit_price: Money


LINES = TypeAdapter(list[MandateLine])


class MandateRequest(BaseModel):
    """What an agent asks the buyer to allow."""

    kind: MandateKind
    wallet_id: UUID
    # A cart mandate's cap is its cart's total.
    cap: Money
    # Intent mandates only; empty allows every category.
    categories: list[Category] = Field(default_factory=list, max_length=len(CATEGORIES))
    # Cart mandates only: the exact cart the buyer approves.
    cart_id: UUID | None = None

    @field_validator("cap")
    @classmethod
    def cap_in_range(cls, cap: Money) -> Money:
        _ = cents_of(cap)
        return cap


class Mandate(BaseModel):
    """A mandate as the consent page and the agent read it; anyone holding its id may read it.

    The cart it covers stays server-side, as on orders: an open cart's id is all it takes to
    change it or check it out, and a mandate id is public on the activity feed. Answering or
    spending the mandate takes the key only its requesting agent got.
    """

    id: UUID
    kind: MandateKind
    status: MandateStatus
    agent: Agent
    wallet_email: str
    # True when the demo buyer's wallet pays: the only wallet the demo signer signs for.
    demo_wallet: bool
    cap: Money
    categories: list[str]
    # The cart lines as signed, which is what the buyer approves and the checkout must match.
    lines: list[MandateLine]
    policy_hash: str
    token_hash: str
    signed_by: SignedBy | None
    signed_at: datetime | None
    expires_at: datetime
    # Cents the mandate's orders hold or may hold; a cart mandate is used once any exist.
    spent: Money
    created_at: datetime


class IssuedMandate(Mandate):
    """A new mandate as its requesting agent gets it: the one time its key is handed out.

    `consent_path` carries the key after `#`, so browsers send it to no server and no Referer.
    """

    key: str
    consent_path: str


class MandateSummary(BaseModel):
    """The mandate as an order carries it, decoded for the console."""

    id: UUID
    kind: MandateKind
    signed_by: SignedBy | None
    signed_at: datetime | None
    cap: Money
    categories: list[str]
    lines: list[MandateLine]
    expires_at: datetime
    token_hash: str


class Approval(BaseModel):
    signed_by: SignedBy
    # The passkey that signed and the browser's assertion, for a passkey approval.
    passkey_id: UUID | None = None
    credential: dict[str, JsonValue] | None = None


class UnknownMandate(LookupError):
    def __init__(self, mandate_id: UUID) -> None:
        super().__init__(f"no mandate has the id {mandate_id}")


class MandateKeyRefused(Exception):
    def __init__(self, mandate_id: UUID) -> None:
        super().__init__(
            f"the key does not open mandate {mandate_id}; use the full link the agent gave"
        )


class MandateNotPending(Exception):
    def __init__(self, mandate_id: UUID, status: str) -> None:
        super().__init__(f"mandate {mandate_id} is {status}, so it cannot be answered")


class MandateCartChanged(Exception):
    """The cart no longer holds what the token names, so the buyer would sign something else."""

    def __init__(self, mandate_id: UUID) -> None:
        super().__init__(
            f"the cart changed after mandate {mandate_id} was requested; ask the agent to request it again"
        )


class DemoSignerRefused(Exception):
    def __init__(self) -> None:
        super().__init__("the demo signer signs only for the demo buyer's wallet")


class MandateRefused(Exception):
    """The checkout does not fit the mandate; the cart stays open."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__(
            f"the mandate does not cover the purchase: {', '.join(reasons)}"
        )
        self.reasons: list[str] = reasons


def status_of(record: MandateRecord) -> MandateStatus:
    return TypeAdapter(MandateStatus).validate_python(record.status)


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


def same_lines(signed: list[MandateLine], lines: list[CartLineRecord]) -> bool:
    """Whether the cart holds exactly the signed lines."""
    return {(line.product_id, line.size, line.quantity) for line in signed} == {
        (line.product_id, line.size, line.quantity) for line in lines
    }


def consent_path(mandate_id: UUID, key: str) -> str:
    return f"/shop/approve/{mandate_id}#key={key}"


def summary(record: MandateRecord) -> MandateSummary:
    return MandateSummary(
        id=record.id,
        kind=MANDATE_KIND.validate_python(record.kind),
        signed_by=None
        if record.signed_by is None
        else SIGNED_BY.validate_python(record.signed_by),
        signed_at=record.signed_at,
        cap=money(record.cap_cents),
        categories=list(record.categories),
        lines=LINES.validate_python(record.lines),
        expires_at=record.expires_at,
        token_hash=record.token_hash,
    )


# Told the id of a mandate once the buyer's answer is stored, or once it expired unanswered.
Answered = Callable[[UUID], Awaitable[None]]


@final
class Mandates:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        tokens: MandateTokens,
        passkeys: Passkeys,
        policies: Policies,
        activities: Activities,
        demo_signer_seed: str,
        public_origin: str,
    ) -> None:
        self._sessions = sessions
        self._tokens = tokens
        self._passkeys = passkeys
        self._policies = policies
        self._activities = activities
        self._demo_signer = private_key(demo_signer_seed)
        self._public_origin = public_origin
        self._answered: list[Answered] = []

    def on_answer(self, listener: Answered) -> None:
        """Have `listener` told about every mandate answered from now on."""
        self._answered.append(listener)

    async def create(self, agent: Agent, request: MandateRequest) -> IssuedMandate:
        """Issue a pending mandate for the agent; the buyer answers it on the consent page.

        Raises:
            LookupError: the wallet cannot pay, or the cart is not the wallet's, open, and filled.
            ValueError: the request does not fit its kind.
        """
        key = new_key()
        cap_cents = cents_of(request.cap)
        async with self._sessions.begin() as session:
            wallet = await session.get(WalletRecord, request.wallet_id)
            if wallet is None or wallet.status != "active":
                raise LookupError(f"wallet {request.wallet_id} cannot pay")
            lines: list[MandateLine] = []
            if request.kind == "cart":
                lines = await self._cart_lines(session, request, cap_cents)
            elif request.cart_id is not None:
                raise ValueError("an intent mandate names no cart")
            policy = await self._policies.current(session)
            mandate_id = uuid7()
            window = CART_WINDOW if request.kind == "cart" else INTENT_WINDOW
            # The database's clock, like every other timestamp, so the expiry and `created_at` agree.
            now = await session.scalar(select(func.now()))
            if now is None:
                raise RuntimeError("the database returned no clock")
            expires_at = now + window
            signed_lines = [line.model_dump(mode="json") for line in lines]
            token = self._tokens.issue(
                request.kind,
                {
                    "jti": str(mandate_id),
                    "iss": self._public_origin,
                    "merchant": MERCHANT,
                    "agent": agent.id,
                    "wallet": str(request.wallet_id),
                    "cap": money(cap_cents).model_dump(),
                    "categories": list(request.categories),
                    "lines": list(signed_lines),
                    "policy_hash": policy.hash,
                    "iat": int(now.timestamp()),
                    "exp": int(expires_at.timestamp()),
                },
            )
            session.add(
                MandateRecord(
                    id=mandate_id,
                    kind=request.kind,
                    agent_id=agent.id,
                    wallet_id=request.wallet_id,
                    cart_id=request.cart_id,
                    cap_cents=cap_cents,
                    currency=request.cap.currency_code,
                    categories=list(request.categories),
                    lines=signed_lines,
                    policy_hash=policy.hash,
                    token=token,
                    token_hash=token_hash(token),
                    key_hash=key_hash(key),
                    status="pending",
                    expires_at=expires_at,
                )
            )
            await session.flush()
            self._activities.add(
                session,
                "mandate_requested",
                agent_id=agent.id,
                mandate_id=mandate_id,
                mandate_kind=request.kind,
                cap=money(cap_cents).value,
                categories=list(request.categories),
            )
            mandate = await self._load(session, mandate_id)
        return IssuedMandate.model_validate(
            {
                **mandate.model_dump(),
                "key": key,
                "consent_path": consent_path(mandate_id, key),
            }
        )

    async def get(self, mandate_id: UUID) -> Mandate:
        """The mandate as it stands; one whose time ran out is marked expired on this read."""
        async with self._sessions.begin() as session:
            record = await session.get(MandateRecord, mandate_id)
            if record is None:
                raise UnknownMandate(mandate_id)
            unanswered = record.status == "pending"
            await self._settle_expiry(session, record)
            lapsed = unanswered and record.status == "expired"
            mandate = await self._load(session, mandate_id)
        if lapsed:
            await self._announce(mandate)
        return mandate

    async def passkey_registration(
        self, mandate_id: UUID, key: str | None
    ) -> RegistrationStart:
        """Options for a new passkey that signs for the mandate's wallet.

        Raises:
            UnknownMandate, MandateKeyRefused: the key does not open the mandate.
        """
        async with self._sessions() as session:
            record = await self._opened(session, mandate_id, key)
        return await self._passkeys.start_registration(record.wallet_id)

    async def passkey_options(
        self, mandate_id: UUID, key: str | None, passkey_id: UUID
    ) -> dict[str, JsonValue]:
        """The browser options that make the given passkey sign this mandate's hash.

        Raises:
            UnknownMandate, MandateKeyRefused: the key does not open the mandate.
            UnknownPasskey: the passkey does not sign for the mandate's wallet.
        """
        async with self._sessions() as session:
            record = await self._opened(session, mandate_id, key)
        return await self._passkeys.assertion_options(
            passkey_id, record.wallet_id, bytes.fromhex(record.token_hash)
        )

    async def approve(
        self, mandate_id: UUID, key: str | None, approval: Approval
    ) -> Mandate:
        """Record the buyer's proof over the token hash; the mandate is signed.

        Raises:
            UnknownMandate, MandateKeyRefused, MandateNotPending: nothing to answer.
            MandateCartChanged: the cart no longer holds the signed lines.
            DemoSignerRefused: the demo signer was asked to sign for a buyer's own wallet.
            UnknownPasskey, PasskeyRefused: the passkey proof does not verify.
            ValueError: a passkey approval without its passkey or assertion.
        """
        async with self._sessions.begin() as session:
            record = await self._pending(session, mandate_id, key)
            if record.kind == "cart":
                await self._cart_unchanged(session, record)
            digest = bytes.fromhex(record.token_hash)
            if approval.signed_by == "passkey":
                if approval.passkey_id is None or approval.credential is None:
                    raise ValueError(
                        "a passkey approval carries the passkey id and its assertion"
                    )
                await self._passkeys.verify(
                    approval.passkey_id, record.wallet_id, digest, approval.credential
                )
                proof: dict[str, JsonValue] = {
                    "passkey_id": str(approval.passkey_id),
                    "assertion": approval.credential,
                }
            else:
                wallet = await session.get_one(WalletRecord, record.wallet_id)
                if not wallet.demo:
                    raise DemoSignerRefused
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
            self._activities.add(
                session,
                "mandate_signed",
                agent_id=record.agent_id,
                mandate_id=mandate_id,
                signed_by=approval.signed_by,
            )
            mandate = await self._load(session, mandate_id)
        await self._announce(mandate)
        return mandate

    async def decline(self, mandate_id: UUID, key: str | None) -> Mandate:
        async with self._sessions.begin() as session:
            record = await self._pending(session, mandate_id, key)
            record.status = "declined"
            self._activities.add(
                session,
                "mandate_declined",
                agent_id=record.agent_id,
                mandate_id=mandate_id,
            )
            mandate = await self._load(session, mandate_id)
        await self._announce(mandate)
        return mandate

    async def claim(
        self,
        session: AsyncSession,
        mandate_id: UUID,
        key: str,
        agent: Agent,
        cart: CartRecord,
        categories: set[str],
        policy_hash: str,
    ) -> MandateRecord:
        """The mandate that covers checking out `cart`, locked for the caller's transaction.

        Raises:
            MandateRefused: it does not exist, the key is not its, it is not signed, has
                expired, or does not fit.
        """
        record = await session.scalar(
            select(MandateRecord)
            .where(MandateRecord.id == mandate_id)
            .with_for_update()
        )
        if record is None:
            raise MandateRefused(["MANDATE_UNKNOWN"])
        if not key_matches(key, record.key_hash):
            raise MandateRefused(["MANDATE_KEY_INVALID"])
        await self._settle_expiry(session, record)
        status = status_of(record)
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
            signed = LINES.validate_python(self._tokens.verify(record.token)["lines"])
            if record.cart_id != cart.id or not same_lines(signed, cart.lines):
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
        """Cents the mandate's orders hold or may hold."""
        total = await session.scalar(spending(OrderRecord.mandate_id == mandate_id))
        return int(total or 0)

    @staticmethod
    async def _cart_lines(
        session: AsyncSession, request: MandateRequest, cap_cents: int
    ) -> list[MandateLine]:
        """The lines a cart mandate signs: the wallet's open cart as it is, at its total."""
        if request.cart_id is None:
            raise ValueError("a cart mandate names the cart it approves")
        if request.categories:
            raise ValueError("a cart mandate covers its cart, not categories")
        cart = await session.get(
            CartRecord, request.cart_id, options=[selectinload(CartRecord.lines)]
        )
        if (
            cart is None
            or cart.wallet_id != request.wallet_id
            or cart.status != "open"
            or not cart.lines
        ):
            raise LookupError(
                f"cart {request.cart_id} is not an open cart with lines for that wallet"
            )
        total = sum(line.unit_price_cents * line.quantity for line in cart.lines)
        if cap_cents != total:
            raise ValueError(
                f"a cart mandate's cap is the cart total, {money(total).value}"
            )
        return lines_of(cart)

    @staticmethod
    async def _cart_unchanged(session: AsyncSession, record: MandateRecord) -> None:
        cart = await session.get(
            CartRecord, record.cart_id, options=[selectinload(CartRecord.lines)]
        )
        if (
            cart is None
            or cart.status != "open"
            or not same_lines(LINES.validate_python(record.lines), cart.lines)
        ):
            raise MandateCartChanged(record.id)

    @staticmethod
    async def _opened(
        session: AsyncSession, mandate_id: UUID, key: str | None
    ) -> MandateRecord:
        record = await session.get(MandateRecord, mandate_id)
        if record is None:
            raise UnknownMandate(mandate_id)
        if not key_matches(key, record.key_hash):
            raise MandateKeyRefused(mandate_id)
        return record

    async def _pending(
        self, session: AsyncSession, mandate_id: UUID, key: str | None
    ) -> MandateRecord:
        record = await session.scalar(
            select(MandateRecord)
            .where(MandateRecord.id == mandate_id)
            .with_for_update()
        )
        if record is None:
            raise UnknownMandate(mandate_id)
        if not key_matches(key, record.key_hash):
            raise MandateKeyRefused(mandate_id)
        await self._settle_expiry(session, record)
        status = status_of(record)
        if status != "pending":
            raise MandateNotPending(mandate_id, status)
        return record

    async def _announce(self, mandate: Mandate) -> None:
        """Tell the listeners, after the answer is committed, so they read it as stored."""
        for listener in self._answered:
            await listener(mandate.id)

    async def _settle_expiry(
        self, session: AsyncSession, record: MandateRecord
    ) -> None:
        """Mark a pending or signed mandate expired once its time has run out, and log it once."""
        if record.status not in ("pending", "signed"):
            return
        now = await session.scalar(select(func.now()))
        if now is None or record.expires_at > now:
            return
        was = record.status
        record.status = "expired"
        self._activities.add(
            session,
            "mandate_expired",
            agent_id=record.agent_id,
            mandate_id=record.id,
            was=was,
        )

    async def _load(self, session: AsyncSession, mandate_id: UUID) -> Mandate:
        record = await session.scalar(
            select(MandateRecord)
            .where(MandateRecord.id == mandate_id)
            .options(joinedload(MandateRecord.agent), joinedload(MandateRecord.wallet))
        )
        if record is None:
            raise UnknownMandate(mandate_id)
        return Mandate(
            id=record.id,
            kind=MANDATE_KIND.validate_python(record.kind),
            status=status_of(record),
            agent=Agent(id=record.agent.id, name=record.agent.name),
            wallet_email=record.wallet.email,
            demo_wallet=record.wallet.demo,
            cap=money(record.cap_cents),
            categories=list(record.categories),
            lines=LINES.validate_python(record.lines),
            policy_hash=record.policy_hash,
            token_hash=record.token_hash,
            signed_by=None
            if record.signed_by is None
            else SIGNED_BY.validate_python(record.signed_by),
            signed_at=record.signed_at,
            expires_at=record.expires_at,
            spent=money(await self.spent(session, mandate_id)),
            created_at=record.created_at,
        )
