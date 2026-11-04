"""Pre-chargeback alerts: refunded in time on their own in automatic mode, by the merchant in review.

PayPal's sandbox cannot produce an alert, so these tests are the only proof of this path. They
queue on the real job table, inside the test's rolled-back transaction.
"""

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import NamedTuple
from uuid import UUID

import httpx2
import pytest
from dispute_fixtures import (
    DISPUTES_PATH,
    OPENED,
    new_dispute_id,
    paid_order,
    paypal_dispute,
)
from paypal_stub import PayPalStub, json_answer
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.activity.store import Activities
from palisade_api.disputes.alerts import (
    REFUND_AFTER,
    REFUND_WINDOW,
    AlertRefund,
    Alerts,
)
from palisade_api.disputes.router import alerts as alerts_dependency
from palisade_api.disputes.router import disputes as disputes_dependency
from palisade_api.disputes.service import Disputes
from palisade_api.disputes.tasks import cancel_refund, schedule_refund
from palisade_api.main import app
from palisade_api.orders.calls import PayPalCalls
from palisade_api.orders.router import orders as orders_dependency
from palisade_api.orders.store import Order, Orders
from palisade_api.paypal.auth import PayPalAuth
from palisade_api.paypal.client import PayPalClient, PayPalError
from palisade_api.policy.engine import FilingMode, Policies

pytestmark = pytest.mark.anyio

ALERT_OPENED = datetime.fromisoformat(OPENED)


class Refund(BaseModel):
    """What Palisade sends PayPal to refund a capture: nothing, which refunds it in full."""


class QueuedRefund(NamedTuple):
    status: str
    queue_name: str
    lock: str | None
    queueing_lock: str | None
    scheduled_at: datetime


@pytest.fixture
def paypal() -> PayPalStub:
    return PayPalStub()


@pytest.fixture
def disputes(
    sessions: async_sessionmaker[AsyncSession], paypal: PayPalStub
) -> Disputes:
    http = paypal.client()
    return Disputes(
        sessions,
        PayPalClient(http, PayPalAuth(http, "id", "secret")),
        Activities(sessions),
    )


@pytest.fixture
def alerts(
    sessions: async_sessionmaker[AsyncSession], paypal: PayPalStub, disputes: Disputes
) -> Alerts:
    http = paypal.client()
    service = Alerts(
        sessions,
        PayPalCalls(sessions, PayPalClient(http, PayPalAuth(http, "id", "secret"))),
        disputes,
        Policies(sessions),
        Activities(sessions),
        schedule_refund,
        cancel_refund,
    )
    disputes.on_change(service.schedule)
    return service


@pytest.fixture
def console(
    sessions: async_sessionmaker[AsyncSession], disputes: Disputes, alerts: Alerts
) -> Iterator[None]:
    app.dependency_overrides[disputes_dependency] = lambda: disputes
    app.dependency_overrides[alerts_dependency] = lambda: alerts
    app.dependency_overrides[orders_dependency] = lambda: Orders(sessions)
    yield
    app.dependency_overrides.clear()


async def filing(sessions: async_sessionmaker[AsyncSession], mode: FilingMode) -> None:
    _ = await Policies(sessions).set_dispute_filing(mode)


def alert(
    paypal: PayPalStub,
    dispute_id: str,
    capture_id: str,
    *,
    updated: str = "2026-11-01T08:03:00.000Z",
    status: str = "WAITING_FOR_SELLER_RESPONSE",
    outcome: str | None = None,
) -> None:
    """PayPal answers with the dispute as a pre-chargeback alert."""
    paypal.on(
        "GET",
        f"{DISPUTES_PATH}/{dispute_id}",
        json_answer(
            200,
            paypal_dispute(
                dispute_id,
                capture_id,
                updated=updated,
                status=status,
                outcome=outcome,
                channel="ALERT",
            ),
        ),
    )


def refund_path(capture_id: str) -> str:
    return f"/v2/payments/captures/{capture_id}/refund"


def refunds(paypal: PayPalStub, capture_id: str) -> None:
    paypal.on(
        "POST",
        refund_path(capture_id),
        json_answer(201, {"id": "REFUND-1", "status": "COMPLETED"}),
    )


async def queued(
    sessions: async_sessionmaker[AsyncSession], dispute_id: str
) -> list[QueuedRefund]:
    async with sessions() as session:
        rows = await session.execute(
            text(
                """
                SELECT status, queue_name, lock, queueing_lock, scheduled_at
                FROM procrastinate_jobs
                WHERE task_name = 'palisade_api.disputes.tasks.refund_alert'
                    AND args->>'dispute_id' = :dispute_id
                ORDER BY id
                """
            ),
            {"dispute_id": dispute_id},
        )
        return [QueuedRefund(*row) for row in rows.all()]


async def kinds(
    sessions: async_sessionmaker[AsyncSession], dispute_id: str
) -> list[str]:
    return [row.kind for row in await Activities(sessions).for_dispute(dispute_id)]


async def test_automatic_mode_schedules_one_refund_and_cancels_it_when_the_alert_closes(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    disputes: Disputes,
    alerts: Alerts,
    wallet_id: UUID,
) -> None:
    await filing(sessions, "automatic")
    order = await paid_order(sessions, wallet_id, agent=True)
    dispute_id = new_dispute_id()
    alert(paypal, dispute_id, order.capture_id, status="OPEN")
    _ = await disputes.sync(dispute_id, "webhook")
    # A second change while the refund waits schedules nothing more.
    alert(paypal, dispute_id, order.capture_id, updated="2026-11-01T08:04:00.000Z")
    _ = await disputes.sync(dispute_id, "webhook")

    [job] = await queued(sessions, dispute_id)
    assert job == QueuedRefund(
        "todo",
        "alerts",
        f"alert-refund:{dispute_id}",
        None,
        ALERT_OPENED + REFUND_AFTER,
    )
    waiting = await alerts.view(dispute_id)
    assert (waiting.scheduled_for, waiting.deadline, waiting.can_refund) == (
        ALERT_OPENED + REFUND_AFTER,
        ALERT_OPENED + REFUND_WINDOW,
        True,
    )

    alert(
        paypal,
        dispute_id,
        order.capture_id,
        updated="2026-11-01T09:00:00.000Z",
        status="RESOLVED",
        outcome="CANCELED_BY_BUYER",
    )
    _ = await disputes.sync(dispute_id, "webhook")

    assert [row.status for row in await queued(sessions, dispute_id)] == ["cancelled"]
    closed = await alerts.view(dispute_id)
    assert (closed.scheduled_for, closed.can_refund, closed.refunded) == (
        None,
        False,
        False,
    )
    assert paypal.sent("POST", refund_path(order.capture_id)) == []
    assert await kinds(sessions, dispute_id) == [
        "dispute_opened",
        "alert_refund_scheduled",
        "dispute_updated",
        "dispute_resolved",
        "alert_refund_cancelled",
    ]


@pytest.mark.usefixtures("console")
async def test_review_mode_waits_for_the_merchant_who_refunds_once(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    disputes: Disputes,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
) -> None:
    await filing(sessions, "review")
    order = await paid_order(sessions, wallet_id, agent=True)
    dispute_id = new_dispute_id()
    alert(paypal, dispute_id, order.capture_id)
    refunds(paypal, order.capture_id)
    _ = await disputes.sync(dispute_id, "webhook")

    before = AlertRefund.model_validate_json(
        (await client.get(f"/api/disputes/{dispute_id}/alert")).content
    )
    assert await queued(sessions, dispute_id) == []
    assert (before.filing_mode, before.scheduled_for, before.can_refund) == (
        "review",
        None,
        True,
    )
    assert before.deadline == ALERT_OPENED + REFUND_WINDOW

    first = await client.post(f"/api/disputes/{dispute_id}/alert/refund")
    second = await client.post(f"/api/disputes/{dispute_id}/alert/refund")

    done = AlertRefund.model_validate_json(first.content)
    assert (first.status_code, second.status_code) == (200, 200)
    assert (done.refunded, done.can_refund) == (True, False)
    assert done.refund is not None
    [sent] = paypal.sent("POST", refund_path(order.capture_id))
    assert Refund.model_validate_json(sent.content) == Refund()
    assert sent.headers["PayPal-Request-Id"] == done.refund.request_id
    assert await kinds(sessions, dispute_id) == ["dispute_opened", "alert_refunded"]
    shown = Order.model_validate_json(
        (await client.get(f"/api/orders/{order.number}")).content
    )
    assert [(call.action, call.dispute_id) for call in shown.calls] == [
        ("refund", dispute_id)
    ]


async def test_the_timer_refunds_an_open_alert_and_leaves_a_closed_one(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    disputes: Disputes,
    alerts: Alerts,
    wallet_id: UUID,
) -> None:
    await filing(sessions, "automatic")
    open_order = await paid_order(sessions, wallet_id, agent=True)
    closed_order = await paid_order(sessions, wallet_id)
    still_open, closed = new_dispute_id(), new_dispute_id()
    alert(paypal, still_open, open_order.capture_id)
    alert(paypal, closed, closed_order.capture_id)
    refunds(paypal, open_order.capture_id)
    _ = await disputes.sync(still_open, "webhook")
    _ = await disputes.sync(closed, "webhook")
    # The alert closed without a webhook; the job reads PayPal before it acts.
    alert(
        paypal,
        closed,
        closed_order.capture_id,
        updated="2026-11-01T09:00:00.000Z",
        status="RESOLVED",
        outcome="CANCELED_BY_BUYER",
    )

    await alerts.refund_on_time(still_open)
    await alerts.refund_on_time(closed)

    refunded = await alerts.view(still_open)
    assert (refunded.refunded, refunded.scheduled_for) == (True, None)
    assert len(paypal.sent("POST", refund_path(open_order.capture_id))) == 1
    assert paypal.sent("POST", refund_path(closed_order.capture_id)) == []
    [timer] = [
        row
        for row in await Activities(sessions).for_dispute(still_open)
        if row.kind == "alert_refunded"
    ]
    assert (timer.details["source"], timer.details["refund_id"]) == (
        "timer",
        "REFUND-1",
    )
    assert "alert_refund_cancelled" in await kinds(sessions, closed)


async def test_a_merchant_who_switched_to_review_since_keeps_the_decision(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    disputes: Disputes,
    alerts: Alerts,
    wallet_id: UUID,
) -> None:
    await filing(sessions, "automatic")
    order = await paid_order(sessions, wallet_id, agent=True)
    dispute_id = new_dispute_id()
    alert(paypal, dispute_id, order.capture_id)
    _ = await disputes.sync(dispute_id, "webhook")
    await filing(sessions, "review")

    await alerts.refund_on_time(dispute_id)

    after = await alerts.view(dispute_id)
    assert (after.refunded, after.scheduled_for, after.can_refund) == (
        False,
        None,
        True,
    )
    assert paypal.requests[-1].method == "GET"
    assert await kinds(sessions, dispute_id) == [
        "dispute_opened",
        "alert_refund_scheduled",
        "alert_refund_skipped",
    ]


@pytest.mark.usefixtures("console")
async def test_a_refund_paypal_refuses_is_recorded_and_can_be_tried_again(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    disputes: Disputes,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
) -> None:
    await filing(sessions, "review")
    order = await paid_order(sessions, wallet_id)
    dispute_id = new_dispute_id()
    alert(paypal, dispute_id, order.capture_id)
    paypal.on(
        "POST",
        refund_path(order.capture_id),
        json_answer(
            422,
            {
                "name": "UNPROCESSABLE_ENTITY",
                "details": [{"issue": "TRANSACTION_REFUSED"}],
            },
        ),
    )
    _ = await disputes.sync(dispute_id, "webhook")

    refused = await client.post(f"/api/disputes/{dispute_id}/alert/refund")

    assert refused.status_code == 422
    assert "TRANSACTION_REFUSED" in refused.text
    after = AlertRefund.model_validate_json(
        (await client.get(f"/api/disputes/{dispute_id}/alert")).content
    )
    assert after.refund is not None
    assert (after.refunded, after.can_refund, after.refund.status_code) == (
        False,
        True,
        422,
    )
    assert await kinds(sessions, dispute_id) == [
        "dispute_opened",
        "alert_refund_refused",
    ]


async def test_the_timer_tries_again_only_when_paypal_did_not_answer(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    disputes: Disputes,
    alerts: Alerts,
    wallet_id: UUID,
) -> None:
    await filing(sessions, "automatic")
    refused_order = await paid_order(sessions, wallet_id)
    silent_order = await paid_order(sessions, wallet_id)
    refused, silent = new_dispute_id(), new_dispute_id()
    alert(paypal, refused, refused_order.capture_id)
    alert(paypal, silent, silent_order.capture_id)
    paypal.on(
        "POST",
        refund_path(refused_order.capture_id),
        json_answer(
            422,
            {
                "name": "UNPROCESSABLE_ENTITY",
                "details": [{"issue": "TRANSACTION_REFUSED"}],
            },
        ),
    )
    paypal.on(
        "POST",
        refund_path(silent_order.capture_id),
        json_answer(503, {"name": "SERVICE_UNAVAILABLE"}),
    )
    _ = await disputes.sync(refused, "webhook")
    _ = await disputes.sync(silent, "webhook")

    await alerts.refund_on_time(refused)
    with pytest.raises(PayPalError) as unanswered:
        await alerts.refund_on_time(silent)

    assert unanswered.value.outcome_unknown
    assert (await kinds(sessions, refused)).count("alert_refund_refused") == 1
    # PayPal's client repeats a 5xx once itself, under the same request id.
    [first, repeat] = paypal.sent("POST", refund_path(silent_order.capture_id))
    assert first.headers["PayPal-Request-Id"] == repeat.headers["PayPal-Request-Id"]


@pytest.mark.usefixtures("console")
async def test_only_an_alert_has_a_refund(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    disputes: Disputes,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
) -> None:
    order = await paid_order(sessions, wallet_id)
    dispute_id = new_dispute_id()
    paypal.on(
        "GET",
        f"{DISPUTES_PATH}/{dispute_id}",
        json_answer(200, paypal_dispute(dispute_id, order.capture_id)),
    )
    _ = await disputes.sync(dispute_id, "webhook")

    shown = await client.get(f"/api/disputes/{dispute_id}/alert")
    refund = await client.post(f"/api/disputes/{dispute_id}/alert/refund")

    assert (shown.status_code, refund.status_code) == (404, 404)
    assert paypal.sent("POST", refund_path(order.capture_id)) == []
    assert await queued(sessions, dispute_id) == []


def test_the_refund_runs_an_hour_inside_paypals_window() -> None:
    assert timedelta(hours=1) == REFUND_WINDOW - REFUND_AFTER
