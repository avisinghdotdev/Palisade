"""The rows the console's analytics dashboard aggregates: paid orders and their disputes.

The dashboard computes every rate and average itself, so it can split them any way the
merchant asks; the API sends one row per order and per dispute instead of totals.
"""

from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import defer

from palisade_api.agents.models import AgentRecord
from palisade_api.disputes.models import DisputeRecord, EvidenceRecord
from palisade_api.disputes.responses import EvidenceStatus
from palisade_api.disputes.service import (
    REASON,
    STATUS,
    DisputeReason,
    DisputeStatus,
)
from palisade_api.orders.models import OrderRecord
from palisade_api.orders.store import CHANNEL, ORDER_STATUS, Channel, OrderStatus

# Orders whose payment PayPal took: only these can be disputed, so they are the dispute rate's base.
PAID: tuple[OrderStatus, ...] = ("capture_pending", "captured", "refunded", "reversed")

# How a closed dispute ended for the merchant, by PayPal's outcome code: a buyer who cancels
# leaves the store its money, a seller who accepts the claim gives it up. The other codes
# (PayPal paid the buyer itself, denied, none) are neither a win nor a loss.
WON = frozenset({"RESOLVED_SELLER_FAVOUR", "CANCELED_BY_BUYER"})
LOST = frozenset({"RESOLVED_BUYER_FAVOUR", "ACCEPTED"})

Result = Literal["open", "won", "lost", "closed"]
# The latest answer Palisade prepared, or "none" before the first.
Evidence = EvidenceStatus | Literal["none"]
EVIDENCE = TypeAdapter[Evidence](Evidence)


class OrderRow(BaseModel):
    order_id: UUID
    number: str
    placed_on: date
    channel: Channel
    # The registered name of the agent behind an agent order; None on a human checkout.
    agent_name: str | None
    status: OrderStatus
    amount_cents: int
    disputed: bool


class DisputeRow(BaseModel):
    dispute_id: str
    order_id: UUID
    opened_on: date
    # The disputed order's channel, so disputes split by agent and human without a join.
    channel: Channel
    reason: DisputeReason
    status: DisputeStatus
    result: Result
    evidence: Evidence
    # From PayPal opening the dispute to Palisade's first answer PayPal took.
    minutes_to_evidence: float | None
    amount_cents: int


class AnalyticsRows(BaseModel):
    # AG Studio reads its licence in the browser, so the key is sent to whoever opens the page.
    license_key: str
    orders: list[OrderRow]
    disputes: list[DisputeRow]


def result_of(outcome: str | None) -> Result:
    if outcome is None:
        return "open"
    if outcome in WON:
        return "won"
    if outcome in LOST:
        return "lost"
    return "closed"


def minutes_between(start: datetime, end: datetime) -> float:
    return round((end - start).total_seconds() / 60, 1)


class Analytics:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], license_key: str
    ) -> None:
        self._sessions: async_sessionmaker[AsyncSession] = sessions
        self._license_key: str = license_key

    async def data(self) -> AnalyticsRows:
        """Every paid order and every dispute, oldest first.

        All rows go out at once: the shop's volume is a few hundred rows, which the browser
        aggregates in milliseconds.
        """
        disputed = (
            select(DisputeRecord.id)
            .where(DisputeRecord.order_id == OrderRecord.id)
            .exists()
        )
        orders = (
            select(OrderRecord, AgentRecord.name, disputed)
            .outerjoin(AgentRecord, AgentRecord.id == OrderRecord.agent_id)
            .where(OrderRecord.status.in_(PAID))
            .order_by(OrderRecord.created_at)
        )
        # The evidence table is read for its status and times, never for the record PDFs.
        latest_evidence = (
            select(EvidenceRecord.status)
            .where(EvidenceRecord.dispute_id == DisputeRecord.id)
            .order_by(EvidenceRecord.prepared_at.desc())
            .limit(1)
            .scalar_subquery()
        )
        disputes = (
            select(
                DisputeRecord,
                OrderRecord.channel,
                func.coalesce(latest_evidence, "none"),
            )
            .join(OrderRecord, OrderRecord.id == DisputeRecord.order_id)
            # PayPal's stored dispute object is the widest column and none of it is needed.
            .options(defer(DisputeRecord.detail))
            .order_by(DisputeRecord.opened_at)
        )
        # Newest first, so each dispute's entry ends on its first filing; only a filed answer
        # has a filing time.
        filings = select(EvidenceRecord.dispute_id, EvidenceRecord.filed_at).order_by(
            EvidenceRecord.filed_at.desc()
        )
        async with self._sessions() as session:
            order_rows = (await session.execute(orders)).all()
            dispute_rows = (await session.execute(disputes)).all()
            first_filed = {
                dispute_id: filed_at
                for dispute_id, filed_at in (await session.execute(filings)).all()
                if filed_at is not None
            }

        return AnalyticsRows(
            license_key=self._license_key,
            orders=[
                OrderRow(
                    order_id=order.id,
                    number=order.number,
                    placed_on=order.created_at.astimezone(UTC).date(),
                    channel=CHANNEL.validate_python(order.channel),
                    agent_name=agent_name,
                    status=ORDER_STATUS.validate_python(order.status),
                    amount_cents=order.total_cents,
                    disputed=is_disputed,
                )
                for order, agent_name, is_disputed in order_rows
            ],
            disputes=[
                DisputeRow(
                    dispute_id=record.id,
                    order_id=record.order_id,
                    opened_on=record.opened_at.astimezone(UTC).date(),
                    channel=CHANNEL.validate_python(channel),
                    reason=REASON.validate_python(record.reason),
                    status=STATUS.validate_python(record.status),
                    result=result_of(record.outcome),
                    evidence=EVIDENCE.validate_python(evidence),
                    minutes_to_evidence=minutes_between(
                        record.opened_at, first_filed[record.id]
                    )
                    if record.id in first_filed
                    else None,
                    amount_cents=record.amount_cents,
                )
                for record, channel, evidence in dispute_rows
            ],
        )
