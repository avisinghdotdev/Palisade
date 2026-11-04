"""Pre-chargeback alerts: the buyer asked their card issuer for a refund, and a refund in time ends it.

PayPal marks such a dispute with the channel ALERT and says a refund within 20 hours avoids the
chargeback and its fees. In automatic mode Palisade schedules a full refund 19 hours after the
alert and cancels it if the alert closes first; in review mode nothing runs on its own, and the
merchant refunds from the dispute page or answers the dispute.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Literal, final
from uuid import UUID

import psycopg
import structlog
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload

from palisade_api.activity.store import Activities
from palisade_api.disputes.models import DisputeRecord
from palisade_api.disputes.service import Change, Disputes, UnknownDispute
from palisade_api.jobs import driver_connection
from palisade_api.orders.calls import PayPalCalls
from palisade_api.orders.models import PayPalCallRecord
from palisade_api.orders.store import PayPalCall, paypal_call
from palisade_api.paypal.client import PayPalError
from palisade_api.policy.engine import FilingMode, Policies

# PayPal's window, from the alert's creation, and how much of it Palisade leaves unused.
REFUND_WINDOW = timedelta(hours=20)
REFUND_AFTER = timedelta(hours=19)
REFUNDED = frozenset({200, 201})

# Who refunded: the merchant from the dispute page, or the job when the time came.
RefundSource = Literal["console", "timer"]
# Queues the refund job for the dispute at the given time on the connection, inside its
# transaction; the job id.
ScheduleRefund = Callable[
    [psycopg.AsyncConnection[object], str, datetime], Awaitable[int]
]
# Cancels the waiting job with the given id on the connection; False when it no longer waits.
CancelRefund = Callable[[psycopg.AsyncConnection[object], int], Awaitable[bool]]

logger = structlog.stdlib.get_logger(__name__)


class RefundResult(BaseModel):
    id: str
    status: str


class AlertRefund(BaseModel):
    """Where a pre-chargeback alert's refund stands, as the dispute page shows it."""

    filing_mode: FilingMode
    # When PayPal's 20-hour window closes.
    deadline: datetime
    # When Palisade refunds by itself; None in review mode, and once refunded or resolved.
    scheduled_for: datetime | None
    # The order's refund call to PayPal, once made.
    refund: PayPalCall | None
    refunded: bool
    can_refund: bool


class NotAnAlert(LookupError):
    def __init__(self, dispute_id: str) -> None:
        super().__init__(f"dispute {dispute_id} is not a pre-chargeback alert")


class NotRefundable(Exception):
    def __init__(self, dispute_id: str, why: str) -> None:
        super().__init__(f"dispute {dispute_id} cannot be refunded: {why}")


def refunded(call: PayPalCallRecord | None) -> bool:
    return call is not None and call.status_code in REFUNDED


async def refund_call(session: AsyncSession, order_id: UUID) -> PayPalCallRecord | None:
    """The order's refund call, if one was made; an order is refunded once."""
    return await session.scalar(
        select(PayPalCallRecord).where(
            PayPalCallRecord.order_id == order_id, PayPalCallRecord.action == "refund"
        )
    )


@final
class Alerts:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        calls: PayPalCalls,
        disputes: Disputes,
        policies: Policies,
        activities: Activities,
        schedule_refund: ScheduleRefund,
        cancel_refund: CancelRefund,
    ) -> None:
        self._sessions = sessions
        self._calls = calls
        self._disputes = disputes
        self._policies = policies
        self._activities = activities
        self._schedule_refund = schedule_refund
        self._cancel_refund = cancel_refund

    async def schedule(self, session: AsyncSession, change: Change) -> None:
        """Start or stop an alert's refund timer as its state changes, in the same transaction.

        A new or changed open alert in automatic mode gets one refund job, at the alert's time
        plus REFUND_AFTER; a dispute that has one keeps it. A resolved alert's waiting job is
        cancelled.
        """
        record = change.record
        if record.channel != "ALERT":
            return
        connection = await driver_connection(session)
        if record.status == "RESOLVED":
            if record.refund_job_id is None:
                return
            if await self._cancel_refund(connection, record.refund_job_id):
                self._activities.add(
                    session,
                    "alert_refund_cancelled",
                    order_id=record.order_id,
                    dispute_id=record.id,
                    outcome=record.outcome,
                )
            record.refund_job_id = None
            record.refund_due_at = None
            return
        if record.refund_job_id is not None:
            return
        policy = await self._policies.current(session)
        if policy.dispute_filing != "automatic":
            return
        if refunded(await refund_call(session, record.order_id)):
            return
        due = record.opened_at + REFUND_AFTER
        record.refund_job_id = await self._schedule_refund(connection, record.id, due)
        record.refund_due_at = due
        self._activities.add(
            session,
            "alert_refund_scheduled",
            order_id=record.order_id,
            dispute_id=record.id,
            due=due.isoformat(),
        )

    async def view(self, dispute_id: str) -> AlertRefund:
        """The alert's refund as it stands.

        Raises:
            UnknownDispute, NotAnAlert: no stored alert has the id.
        """
        async with self._sessions() as session:
            record = await self._alert(session, dispute_id)
            call = await refund_call(session, record.order_id)
            policy = await self._policies.current(session)
        done = refunded(call)
        open_alert = record.status != "RESOLVED"
        return AlertRefund(
            filing_mode=policy.dispute_filing,
            deadline=record.opened_at + REFUND_WINDOW,
            scheduled_for=record.refund_due_at if open_alert and not done else None,
            refund=None if call is None else paypal_call(call),
            refunded=done,
            can_refund=open_alert and not done and record.order.status == "captured",
        )

    async def refund(self, dispute_id: str, source: RefundSource) -> AlertRefund:
        """Refund the alert's order in full, once; a repeat answers with the refund already made.

        The call is the order's one refund call, so a retry goes under the same
        `PayPal-Request-Id`. A refund job still waiting is cancelled.

        Raises:
            UnknownDispute, NotAnAlert: no stored alert has the id.
            NotRefundable: the alert is resolved or the order is not a captured payment.
            PayPalError: PayPal refused the refund or did not answer.
        """
        state = await self.view(dispute_id)
        if state.refunded:
            return state
        async with self._sessions() as session:
            record = await self._alert(session, dispute_id)
            order_id, capture_id = record.order_id, record.capture_id
            if record.status == "RESOLVED":
                raise NotRefundable(dispute_id, "PayPal has resolved the alert")
            if record.order.status != "captured":
                raise NotRefundable(dispute_id, f"the order is {record.order.status}")
        try:
            response = await self._calls.once(
                order_id,
                "refund",
                f"/v2/payments/captures/{capture_id}/refund",
                {},
                dispute_id=dispute_id,
            )
        except PayPalError as error:
            await self._activities.record(
                "alert_refund_refused",
                order_id=order_id,
                dispute_id=dispute_id,
                source=source,
                issues=", ".join(error.issues),
                debug_id=error.debug_id,
            )
            raise
        result = RefundResult.model_validate(response.body)
        async with self._sessions.begin() as session:
            record = await session.get_one(
                DisputeRecord, dispute_id, with_for_update=True
            )
            if record.refund_job_id is not None:
                _ = await self._cancel_refund(
                    await driver_connection(session), record.refund_job_id
                )
                record.refund_job_id = None
                record.refund_due_at = None
            self._activities.add(
                session,
                "alert_refunded",
                order_id=order_id,
                dispute_id=dispute_id,
                source=source,
                refund_id=result.id,
                refund_status=result.status,
            )
        return await self.view(dispute_id)

    async def refund_on_time(self, dispute_id: str) -> None:
        """What the refund job does when its time comes.

        Reads the alert from PayPal first: a resolved alert needs nothing, and a merchant who
        switched to review mode since decides themselves. A refund PayPal refused is on the
        dispute's timeline already and is not tried again; asking again gets the same answer.

        Raises:
            PayPalError: PayPal did not return the alert, or did not answer the refund, so the
                job tries again under the same request id.
        """
        live = await self._disputes.sync(dispute_id, "alert")
        if live.status == "RESOLVED":
            logger.info("alert_resolved_before_refund", dispute_id=dispute_id)
            return
        if (await self._policies.get()).dispute_filing != "automatic":
            async with self._sessions.begin() as session:
                record = await session.get_one(
                    DisputeRecord, dispute_id, with_for_update=True
                )
                record.refund_job_id = None
                record.refund_due_at = None
                self._activities.add(
                    session,
                    "alert_refund_skipped",
                    order_id=record.order_id,
                    dispute_id=dispute_id,
                    why="The filing mode is review, so the merchant decides.",
                )
            return
        try:
            _ = await self.refund(dispute_id, "timer")
        except NotRefundable as error:
            logger.info("alert_not_refundable", dispute_id=dispute_id, why=str(error))
        except PayPalError as error:
            if error.outcome_unknown:
                raise
            logger.warning(
                "alert_refund_refused", dispute_id=dispute_id, issues=error.issues
            )

    @staticmethod
    async def _alert(session: AsyncSession, dispute_id: str) -> DisputeRecord:
        record = await session.scalar(
            select(DisputeRecord)
            .options(joinedload(DisputeRecord.order))
            .where(DisputeRecord.id == dispute_id)
        )
        if record is None:
            raise UnknownDispute(dispute_id)
        if record.channel != "ALERT":
            raise NotAnAlert(dispute_id)
        return record
