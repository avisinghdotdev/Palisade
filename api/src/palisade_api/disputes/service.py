"""Disputes as PayPal reports them, stored and joined to the order whose capture they name."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, final
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import structlog
from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload

from palisade_api.activity.store import Activities
from palisade_api.disputes.models import DisputeRecord
from palisade_api.orders.models import OrderRecord
from palisade_api.orders.store import CHANNEL, Channel
from palisade_api.paypal.client import PayPalClient, PayPalError
from palisade_api.shop.catalog import Money, cents_of, money

DisputeReason = Literal[
    "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
    "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
    "UNAUTHORISED",
    "CREDIT_NOT_PROCESSED",
    "DUPLICATE_TRANSACTION",
    "INCORRECT_AMOUNT",
    "PAYMENT_BY_OTHER_MEANS",
    "CANCELED_RECURRING_BILLING",
    "PROBLEM_WITH_REMITTANCE",
    "OTHER",
]
DisputeStage = Literal["INQUIRY", "CHARGEBACK", "PRE_ARBITRATION", "ARBITRATION"]
DisputeStatus = Literal[
    "OPEN",
    "WAITING_FOR_BUYER_RESPONSE",
    "WAITING_FOR_SELLER_RESPONSE",
    "UNDER_REVIEW",
    "RESOLVED",
    "OTHER",
]
# INTERNAL: the buyer asked PayPal; EXTERNAL: the buyer asked their card issuer; ALERT: the
# issuer's pre-chargeback alert, which a refund within 20 hours settles.
DisputeChannel = Literal["INTERNAL", "EXTERNAL", "ALERT"]
OutcomeCode = Literal[
    "RESOLVED_BUYER_FAVOUR",
    "RESOLVED_SELLER_FAVOUR",
    "RESOLVED_WITH_PAYOUT",
    "CANCELED_BY_BUYER",
    "ACCEPTED",
    "DENIED",
    "NONE",
]
# What made Palisade read the dispute from PayPal.
SyncSource = Literal[
    "webhook", "reconciliation", "console", "simulator", "filing", "alert", "triage"
]

REASON = TypeAdapter(DisputeReason)
STAGE = TypeAdapter(DisputeStage)
STATUS = TypeAdapter(DisputeStatus)
DISPUTE_CHANNEL = TypeAdapter(DisputeChannel)
OUTCOME = TypeAdapter(OutcomeCode)

DISPUTES_LISTED = 100
# PayPal's largest page of disputes.
PAGE_SIZE = 50
# How far back the hourly reconciliation asks PayPal for changed disputes: wide enough to cover
# a worker that slept for weeks, with only disputes new or newer than the stored copy read again.
RECONCILE_WINDOW = timedelta(days=30)

logger = structlog.stdlib.get_logger(__name__)


class PayPalLink(BaseModel):
    rel: str
    href: str


class PayPalDisputedTransaction(BaseModel):
    seller_transaction_id: str | None = None
    buyer_transaction_id: str | None = None
    custom: str | None = None
    seller_protection_eligible: bool | None = None


class PayPalMessage(BaseModel):
    posted_by: str
    time_posted: datetime
    content: str


class PayPalDocument(BaseModel):
    name: str


class PayPalEvidence(BaseModel):
    """An item on PayPal's evidence list: a party's filing, or what PayPal asks a party for."""

    evidence_type: str
    # SUBMITTED_BY_BUYER or _SELLER for a filing, REQUESTED_FROM_SELLER or _BUYER for a request.
    source: str
    date: datetime
    notes: str | None = None
    documents: list[PayPalDocument] = []
    # PayPal names the stage of a filing, not of a request.
    dispute_life_cycle_stage: DisputeStage | None = None


class PayPalOutcome(BaseModel):
    outcome_code: OutcomeCode


class PayPalOfferOptions(BaseModel):
    offer_types: list[str] = []


class PayPalAcceptOptions(BaseModel):
    accept_claim_types: list[str] = []


class PayPalResponseOptions(BaseModel):
    """The kinds of offer and of accepted claim PayPal takes on the dispute now."""

    make_offer: PayPalOfferOptions = PayPalOfferOptions()
    accept_claim: PayPalAcceptOptions = PayPalAcceptOptions()


class PayPalDispute(BaseModel):
    """The parts of PayPal's dispute object Palisade stores and shows."""

    dispute_id: str
    create_time: datetime
    update_time: datetime
    disputed_transactions: list[PayPalDisputedTransaction]
    reason: DisputeReason
    status: DisputeStatus
    dispute_amount: Money
    dispute_life_cycle_stage: DisputeStage
    dispute_channel: DisputeChannel | None = None
    dispute_outcome: PayPalOutcome | None = None
    seller_response_due_date: datetime | None = None
    messages: list[PayPalMessage] = []
    evidences: list[PayPalEvidence] = []
    allowed_response_options: PayPalResponseOptions = PayPalResponseOptions()
    links: list[PayPalLink] = []

    @property
    def actions(self) -> list[str]:
        """The actions PayPal offers on the dispute now, named by their links."""
        return [link.rel for link in self.links if link.rel != "self"]


class PayPalDisputeItem(BaseModel):
    dispute_id: str
    update_time: datetime


class PayPalDisputePage(BaseModel):
    items: list[PayPalDisputeItem] = []
    links: list[PayPalLink] = []


class DisputeMessage(BaseModel):
    posted_by: str
    at: datetime
    content: str


class DisputeEvidence(BaseModel):
    """One item on PayPal's evidence list: a party's filing, or what PayPal asks a party for."""

    evidence_type: str
    source: str
    at: datetime
    notes: str | None
    documents: list[str]
    # None on a request, which PayPal files under no stage.
    stage: DisputeStage | None


class DisputeSummary(BaseModel):
    """A dispute as the console's list shows it."""

    id: str
    # The order whose capture PayPal names.
    order_number: str
    order_channel: Channel
    reason: DisputeReason
    stage: DisputeStage
    status: DisputeStatus
    dispute_channel: DisputeChannel | None
    outcome: OutcomeCode | None
    amount: Money
    seller_response_due_at: datetime | None
    opened_at: datetime
    updated_at: datetime


class Dispute(DisputeSummary):
    """A dispute as its console page shows it.

    The buyer's name, email, and PayPal account stay server-side; the order already names its
    wallet.
    """

    capture_id: str
    buyer_transaction_id: str | None
    mandate_id: UUID | None
    # Whether the custom value PayPal holds on the payment is the order's own: the signed
    # mandate's id and hash. None on an order placed without a mandate.
    custom_matches: bool | None
    seller_protection_eligible: bool | None
    actions: list[str]
    messages: list[DisputeMessage]
    # What the demo buyer typed when filing through the sandbox simulator; PayPal does not
    # hold it.
    sandbox_complaint: str | None
    evidences: list[DisputeEvidence]
    synced_at: datetime


class UnknownDispute(LookupError):
    def __init__(self, dispute_id: str) -> None:
        super().__init__(f"no stored dispute has the id {dispute_id}")


class NotTheShops(LookupError):
    """The dispute names a payment that is none of the shop's orders, so Palisade keeps no copy.

    The merchant's PayPal account also holds payments made outside the shop, such as the
    sandbox experiments of October 2026; their disputes are PayPal's business alone.
    """

    def __init__(self, dispute_id: str, capture_id: str | None) -> None:
        super().__init__(
            f"dispute {dispute_id} names payment {capture_id}, which is none of the shop's orders"
        )


@dataclass(frozen=True)
class Reconciled:
    """What one reconciliation run read, what it left alone, and what it had to skip."""

    read: list[str]
    # Disputes on payments that are none of the shop's orders.
    ignored: list[str]
    failed: list[str]


@dataclass(frozen=True)
class State:
    stage: str
    status: str
    outcome: str | None


@dataclass(frozen=True)
class Change:
    """A dispute as just stored, and how it stood before; `before` is None for a new one."""

    record: DisputeRecord
    before: State | None


def state(record: DisputeRecord) -> State:
    return State(record.stage, record.status, record.outcome)


# Told, inside the storing transaction, of every dispute whose stage, status, or outcome changed.
Changed = Callable[[AsyncSession, Change], Awaitable[None]]


def summary(record: DisputeRecord) -> DisputeSummary:
    return DisputeSummary(
        id=record.id,
        order_number=record.order.number,
        order_channel=CHANNEL.validate_python(record.order.channel),
        reason=REASON.validate_python(record.reason),
        stage=STAGE.validate_python(record.stage),
        status=STATUS.validate_python(record.status),
        dispute_channel=None
        if record.channel is None
        else DISPUTE_CHANNEL.validate_python(record.channel),
        outcome=None
        if record.outcome is None
        else OUTCOME.validate_python(record.outcome),
        amount=money(record.amount_cents),
        seller_response_due_at=record.seller_response_due_at,
        opened_at=record.opened_at,
        updated_at=record.paypal_updated_at,
    )


def dispute(record: DisputeRecord) -> Dispute:
    live = PayPalDispute.model_validate(record.detail)
    transaction = live.disputed_transactions[0]
    # The summary's fields plus the page's own, validated together like any other input.
    return Dispute.model_validate(
        {
            **summary(record).model_dump(),
            "capture_id": record.capture_id,
            "buyer_transaction_id": record.buyer_transaction_id,
            "mandate_id": record.order.mandate_id,
            "custom_matches": None
            if record.order.custom_id is None
            else record.custom == record.order.custom_id,
            "seller_protection_eligible": transaction.seller_protection_eligible,
            "actions": live.actions,
            "messages": [
                DisputeMessage(
                    posted_by=message.posted_by,
                    at=message.time_posted,
                    content=message.content,
                )
                for message in live.messages
            ],
            "sandbox_complaint": record.sandbox_complaint,
            "evidences": [
                DisputeEvidence(
                    evidence_type=evidence.evidence_type,
                    source=evidence.source,
                    at=evidence.date,
                    notes=evidence.notes,
                    documents=[document.name for document in evidence.documents],
                    stage=evidence.dispute_life_cycle_stage,
                )
                for evidence in live.evidences
            ],
            "synced_at": record.synced_at,
        }
    )


def page_path(href: str) -> str:
    """The path and query of a link PayPal hands out as a full URL."""
    parts = urlsplit(href)
    return f"{parts.path}?{parts.query}"


def paypal_time(moment: datetime) -> str:
    """A moment as the dispute list's time filters take it."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


@final
class Disputes:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        paypal: PayPalClient,
        activities: Activities,
    ) -> None:
        self._sessions = sessions
        self._paypal = paypal
        self._activities = activities
        self._changed: list[Changed] = []

    def on_change(self, listener: Changed) -> None:
        """Have `listener` told about every change of state stored from now on."""
        self._changed.append(listener)

    async def fetch(
        self, dispute_id: str
    ) -> tuple[PayPalDispute, dict[str, JsonValue]]:
        """PayPal's dispute now, parsed and as PayPal sent it.

        Raises:
            PayPalError: PayPal refused the read or did not answer.
        """
        response = await self._paypal.get(f"/v1/customer/disputes/{dispute_id}")
        return PayPalDispute.model_validate(response.body), response.body

    async def sync(self, dispute_id: str, source: SyncSource) -> Dispute:
        """Read the dispute from PayPal and store it; a change is written to the activity log.

        Raises:
            NotTheShops: the dispute names a payment that is none of the shop's orders.
            PayPalError: PayPal refused the read or did not answer.
        """
        live, raw = await self.fetch(dispute_id)
        async with self._sessions.begin() as session:
            change = await self.upsert(session, live, raw)
            await self.record(session, change, source)
        return await self.get(dispute_id)

    async def upsert(
        self,
        session: AsyncSession,
        live: PayPalDispute,
        raw: dict[str, JsonValue],
    ) -> Change:
        """Store PayPal's dispute in the caller's transaction, under a row lock.

        A read older than the stored one changes nothing, so deliveries in any order agree. A
        new dispute is flushed at once, so rows that name it can follow in the same transaction.

        Raises:
            NotTheShops: a new dispute names a payment that is none of the shop's orders.
        """
        record = await session.get(DisputeRecord, live.dispute_id, with_for_update=True)
        before = None if record is None else state(record)
        if record is not None and live.update_time < record.paypal_updated_at:
            return Change(record, before)
        if record is not None:
            # The time of the last read, shown in the console, even when nothing else changed.
            record.synced_at = func.now()
        transaction = live.disputed_transactions[0]
        if record is None:
            capture_id = transaction.seller_transaction_id
            order_id = (
                None
                if capture_id is None
                else await session.scalar(
                    select(OrderRecord.id).where(OrderRecord.capture_id == capture_id)
                )
            )
            if capture_id is None or order_id is None:
                raise NotTheShops(live.dispute_id, capture_id)
            record = DisputeRecord(
                id=live.dispute_id,
                order_id=order_id,
                capture_id=capture_id,
                opened_at=live.create_time,
            )
            session.add(record)
        record.buyer_transaction_id = transaction.buyer_transaction_id
        record.custom = transaction.custom
        record.reason = live.reason
        record.stage = live.dispute_life_cycle_stage
        record.status = live.status
        record.channel = live.dispute_channel
        record.outcome = (
            None if live.dispute_outcome is None else live.dispute_outcome.outcome_code
        )
        record.amount_cents = cents_of(live.dispute_amount)
        record.currency = live.dispute_amount.currency_code
        record.seller_response_due_at = live.seller_response_due_date
        record.detail = raw
        record.paypal_updated_at = live.update_time
        await session.flush()
        return Change(record, before)

    async def record(
        self, session: AsyncSession, change: Change, source: SyncSource
    ) -> None:
        """Write what changed to the activity log, in the caller's transaction, and tell the listeners."""
        record = change.record
        after = state(record)
        if change.before == after:
            return
        if change.before is None:
            self._activities.add(
                session,
                "dispute_opened",
                order_id=record.order_id,
                dispute_id=record.id,
                reason=record.reason,
                stage=record.stage,
                status=record.status,
                amount=money(record.amount_cents).value,
                source=source,
            )
        if record.status == "RESOLVED" and (
            change.before is None or change.before.status != "RESOLVED"
        ):
            self._activities.add(
                session,
                "dispute_resolved",
                order_id=record.order_id,
                dispute_id=record.id,
                outcome=record.outcome,
                source=source,
            )
        elif change.before is not None:
            self._activities.add(
                session,
                "dispute_updated",
                order_id=record.order_id,
                dispute_id=record.id,
                from_stage=change.before.stage,
                from_status=change.before.status,
                stage=record.stage,
                status=record.status,
                source=source,
            )
        logger.info(
            "dispute_stored",
            dispute_id=record.id,
            order_id=str(record.order_id),
            stage=record.stage,
            status=record.status,
            outcome=record.outcome,
            source=source,
        )
        for listener in self._changed:
            await listener(session, change)

    async def get(self, dispute_id: str) -> Dispute:
        async with self._sessions() as session:
            record = await session.scalar(
                select(DisputeRecord)
                .options(joinedload(DisputeRecord.order))
                .where(DisputeRecord.id == dispute_id)
            )
            if record is None:
                raise UnknownDispute(dispute_id)
            return dispute(record)

    async def list(self, order_number: str | None) -> list[DisputeSummary]:
        """The most recently opened disputes, newest first; `order_number` narrows them to one order."""
        statement = (
            select(DisputeRecord)
            .options(joinedload(DisputeRecord.order))
            .order_by(DisputeRecord.opened_at.desc())
            .limit(DISPUTES_LISTED)
        )
        if order_number is not None:
            statement = statement.join(DisputeRecord.order).where(
                OrderRecord.number == order_number
            )
        async with self._sessions() as session:
            records = (await session.scalars(statement)).all()
            return [summary(record) for record in records]

    async def reconcile(self) -> Reconciled:
        """Read what no webhook announced: disputes PayPal lists as new or newer, and open ones.

        PayPal's list covers disputes changed in RECONCILE_WINDOW and opened no earlier than
        the shop's first order, since a dispute follows its payment; older ones cannot be the
        shop's. The list also leaves some out: in the sandbox an OTHER-reason claim filed by
        API is neither listed nor announced, yet reads fine by id, so every stored dispute
        still open that the list did not name is read again too.

        A dispute PayPal will not return, or returns in a shape this code does not expect, is
        logged with its traceback and skipped, so it cannot hold up the others every hour.

        Raises:
            PayPalError: PayPal refused the list or did not answer.
        """
        result = Reconciled(read=[], ignored=[], failed=[])
        async with self._sessions() as session:
            first_order = await session.scalar(select(func.min(OrderRecord.created_at)))
        if first_order is None:
            return result
        changed_since = datetime.now(UTC) - RECONCILE_WINDOW
        query = {
            "page_size": str(PAGE_SIZE),
            "start_time": paypal_time(first_order),
            "update_time_after": paypal_time(changed_since),
        }
        path: str | None = f"/v1/customer/disputes?{urlencode(query)}"
        listed: set[str] = set()
        while path is not None:
            page = PayPalDisputePage.model_validate((await self._paypal.get(path)).body)
            listed.update(item.dispute_id for item in page.items)
            async with self._sessions() as session:
                rows = await session.execute(
                    select(DisputeRecord.id, DisputeRecord.paypal_updated_at).where(
                        DisputeRecord.id.in_([item.dispute_id for item in page.items])
                    )
                )
                # A result has keys() of its own, so dict() takes its rows only as a list.
                stored = dict(rows.all())
            for item in page.items:
                known = stored.get(item.dispute_id)
                if known is None or item.update_time > known:
                    await self._read(item.dispute_id, result)
            path = next(
                (page_path(link.href) for link in page.links if link.rel == "next"),
                None,
            )
        async with self._sessions() as session:
            unlisted = (
                await session.scalars(
                    select(DisputeRecord.id).where(
                        DisputeRecord.status != "RESOLVED",
                        DisputeRecord.id.not_in(listed),
                    )
                )
            ).all()
        for dispute_id in unlisted:
            await self._read(dispute_id, result)
        return result

    async def _read(self, dispute_id: str, result: Reconciled) -> None:
        try:
            _ = await self.sync(dispute_id, "reconciliation")
        except NotTheShops:
            result.ignored.append(dispute_id)
        except PayPalError, ValidationError:
            logger.exception("dispute_unreadable", dispute_id=dispute_id)
            result.failed.append(dispute_id)
        else:
            result.read.append(dispute_id)
