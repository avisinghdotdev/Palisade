"""The activity log: every agent-side and dispute event, written where it happens and read by the console."""

from datetime import datetime
from typing import Literal, final
from uuid import UUID, uuid7

import structlog
from pydantic import BaseModel, JsonValue, TypeAdapter
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload

from palisade_api.activity.models import ActivityRecord
from palisade_api.agents.registry import Agent
from palisade_api.orders.models import OrderRecord

ActivityKind = Literal[
    "conversation_started",
    "shopper_message",
    "assistant_tool_call",
    "signature_accepted",
    "signature_refused",
    "mandate_requested",
    "mandate_signed",
    "mandate_declined",
    "mandate_expired",
    "policy_refused",
    "mandate_refused",
    "checkout_accepted",
    "dispute_opened",
    "dispute_updated",
    "dispute_resolved",
    "sandbox_dispute_filed",
    "sandbox_dispute_adjudicated",
    "evidence_prepared",
    "dispute_escalated",
    "evidence_filed",
    "evidence_refused",
    "dispute_flagged",
    "alert_refund_scheduled",
    "alert_refund_cancelled",
    "alert_refund_skipped",
    "alert_refunded",
    "alert_refund_refused",
]
ACTIVITY_KIND = TypeAdapter(ActivityKind)
ACTIVITY_LISTED = 200
# Details carry what callers typed, such as a search query; the public feed keeps only this much
# of any one text or list, so nobody can fill it with megabytes.
TEXT_KEPT = 300
ITEMS_KEPT = 20

logger = structlog.stdlib.get_logger(__name__)


def bounded(value: JsonValue) -> JsonValue:
    """The value with every text cut to TEXT_KEPT characters and every list to ITEMS_KEPT items."""
    if isinstance(value, str):
        return value if len(value) <= TEXT_KEPT else f"{value[:TEXT_KEPT]}…"
    if isinstance(value, list):
        return [bounded(item) for item in value[:ITEMS_KEPT]]
    if isinstance(value, dict):
        return {key: bounded(item) for key, item in value.items()}
    return value


class Activity(BaseModel):
    """An activity row as the console reads it.

    The conversation it belongs to stays server-side: a conversation id plus the shared chat
    code would open another shopper's chat. Mandate and order ids are safe to show, because
    neither can be acted on without the mandate's key or a signed mandate.
    """

    id: UUID
    kind: ActivityKind
    agent: Agent | None
    mandate_id: UUID | None
    order_id: UUID | None
    # The number of the linked order, so the console can link to it without a second request.
    order_number: str | None
    dispute_id: str | None
    details: dict[str, JsonValue]
    at: datetime


def activity(record: ActivityRecord, order_number: str | None) -> Activity:
    return Activity(
        id=record.id,
        kind=ACTIVITY_KIND.validate_python(record.kind),
        agent=None
        if record.agent is None
        else Agent(id=record.agent.id, name=record.agent.name),
        mandate_id=record.mandate_id,
        order_id=record.order_id,
        order_number=order_number,
        dispute_id=record.dispute_id,
        details=record.details,
        at=record.created_at,
    )


@final
class Activities:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    def add(
        self,
        session: AsyncSession,
        kind: ActivityKind,
        *,
        agent_id: str | None = None,
        conversation_id: UUID | None = None,
        mandate_id: UUID | None = None,
        order_id: UUID | None = None,
        dispute_id: str | None = None,
        **details: JsonValue,
    ) -> None:
        """Write the row inside the caller's transaction, so it lands with what it describes."""
        kept = {name: bounded(value) for name, value in details.items()}
        session.add(
            ActivityRecord(
                id=uuid7(),
                kind=kind,
                agent_id=agent_id,
                conversation_id=conversation_id,
                mandate_id=mandate_id,
                order_id=order_id,
                dispute_id=dispute_id,
                details=kept,
            )
        )
        logger.info(
            kind,
            agent_id=agent_id,
            conversation_id=None if conversation_id is None else str(conversation_id),
            mandate_id=None if mandate_id is None else str(mandate_id),
            order_id=None if order_id is None else str(order_id),
            dispute_id=dispute_id,
            **kept,
        )

    async def record(
        self,
        kind: ActivityKind,
        *,
        agent_id: str | None = None,
        conversation_id: UUID | None = None,
        mandate_id: UUID | None = None,
        order_id: UUID | None = None,
        dispute_id: str | None = None,
        **details: JsonValue,
    ) -> None:
        """Write the row in its own transaction: for events whose own work was refused or rolled back."""
        async with self._sessions.begin() as session:
            self.add(
                session,
                kind,
                agent_id=agent_id,
                conversation_id=conversation_id,
                mandate_id=mandate_id,
                order_id=order_id,
                dispute_id=dispute_id,
                **details,
            )

    async def list(self, kind: ActivityKind | None) -> list[Activity]:
        """The most recent activity, newest first, of one kind or of every kind."""
        statement = (
            select(ActivityRecord, OrderRecord.number)
            .outerjoin(OrderRecord, OrderRecord.id == ActivityRecord.order_id)
            .options(joinedload(ActivityRecord.agent))
            .order_by(ActivityRecord.id.desc())
            .limit(ACTIVITY_LISTED)
        )
        if kind is not None:
            statement = statement.where(ActivityRecord.kind == kind)
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return [activity(record, number) for record, number in rows]

    async def for_order(self, number: str) -> list[Activity]:
        """Everything that led to the order, oldest first.

        Rows that name the order or its mandate, plus every row of the conversations those rows
        came from, so the timeline starts at the shopper's request.

        Raises:
            LookupError: no order has the number.
        """
        async with self._sessions() as session:
            order = await session.scalar(
                select(OrderRecord).where(OrderRecord.number == number)
            )
            if order is None:
                raise LookupError(f"no order has the number {number}")
            linked = or_(ActivityRecord.order_id == order.id)
            if order.mandate_id is not None:
                linked = or_(linked, ActivityRecord.mandate_id == order.mandate_id)
            conversations = (
                select(ActivityRecord.conversation_id)
                .where(linked, ActivityRecord.conversation_id.is_not(None))
                .scalar_subquery()
            )
            statement = (
                select(ActivityRecord)
                .options(joinedload(ActivityRecord.agent))
                .where(or_(linked, ActivityRecord.conversation_id.in_(conversations)))
                .order_by(ActivityRecord.id)
            )
            records = (await session.scalars(statement)).all()
            return [
                activity(record, number if record.order_id == order.id else None)
                for record in records
            ]

    async def for_dispute(self, dispute_id: str) -> list[Activity]:
        """Everything recorded about the dispute, oldest first."""
        statement = (
            select(ActivityRecord, OrderRecord.number)
            .outerjoin(OrderRecord, OrderRecord.id == ActivityRecord.order_id)
            .options(joinedload(ActivityRecord.agent))
            .where(ActivityRecord.dispute_id == dispute_id)
            .order_by(ActivityRecord.id)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return [activity(record, number) for record, number in rows]
