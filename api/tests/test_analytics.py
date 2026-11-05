from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

import httpx2
import pytest
from dispute_fixtures import new_dispute_id, paid_order
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.analytics.router import analytics as analytics_dependency
from palisade_api.analytics.service import Analytics, AnalyticsRows, result_of
from palisade_api.disputes.models import DisputeRecord, EvidenceRecord
from palisade_api.main import app
from palisade_api.orders.models import OrderRecord

pytestmark = pytest.mark.anyio

OPENED = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
LICENSE_KEY = "AG-STUDIO-TEST-KEY"


async def stored_dispute(
    sessions: async_sessionmaker[AsyncSession],
    order_id: UUID,
    capture_id: str,
    *,
    outcome: str | None = None,
) -> str:
    dispute_id = new_dispute_id()
    async with sessions.begin() as session:
        session.add(
            DisputeRecord(
                id=dispute_id,
                order_id=order_id,
                capture_id=capture_id,
                reason="MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
                stage="CHARGEBACK",
                status="RESOLVED" if outcome is not None else "UNDER_REVIEW",
                channel="INTERNAL",
                outcome=outcome,
                amount_cents=12000,
                currency="USD",
                detail={},
                opened_at=OPENED,
                paypal_updated_at=OPENED,
            )
        )
    return dispute_id


async def stored_evidence(
    sessions: async_sessionmaker[AsyncSession],
    dispute_id: str,
    status: str,
    *,
    prepared_at: datetime,
    filed_at: datetime | None = None,
) -> None:
    async with sessions.begin() as session:
        session.add(
            EvidenceRecord(
                id=uuid7(),
                dispute_id=dispute_id,
                stage="CHARGEBACK",
                evidence_type="PROOF_OF_FULFILLMENT",
                notes="Notes",
                prompt=None,
                document_name="palisade-record.pdf",
                document=b"%PDF",
                document_sha256="SHA",
                status=status,
                prepared_at=prepared_at,
                filed_at=filed_at,
            )
        )


async def analytics_data(sessions: async_sessionmaker[AsyncSession]) -> AnalyticsRows:
    return await Analytics(sessions, LICENSE_KEY).data()


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (None, "open"),
        ("RESOLVED_SELLER_FAVOUR", "won"),
        ("CANCELED_BY_BUYER", "won"),
        ("RESOLVED_BUYER_FAVOUR", "lost"),
        ("ACCEPTED", "lost"),
        ("RESOLVED_WITH_PAYOUT", "closed"),
        ("DENIED", "closed"),
        ("NONE", "closed"),
    ],
)
def test_each_outcome_is_a_win_a_loss_or_neither(
    outcome: str | None, expected: str
) -> None:
    assert result_of(outcome) == expected


async def test_paid_orders_are_rows_with_their_channel_agent_and_dispute(
    sessions: async_sessionmaker[AsyncSession], wallet_id: UUID
) -> None:
    human = await paid_order(sessions, wallet_id)
    agent = await paid_order(sessions, wallet_id, agent=True)
    declined = await paid_order(sessions, wallet_id)
    async with sessions.begin() as session:
        _ = await session.execute(
            update(OrderRecord)
            .where(OrderRecord.id == declined.id)
            .values(status="declined")
        )
    _ = await stored_dispute(sessions, agent.id, agent.capture_id)

    data = await analytics_data(sessions)

    rows = {row.order_id: row for row in data.orders}
    assert declined.id not in rows
    assert rows[human.id].channel == "human"
    assert rows[human.id].agent_name is None
    assert rows[human.id].disputed is False
    assert rows[agent.id].channel == "agent"
    assert rows[agent.id].agent_name == "Test Agent"
    assert rows[agent.id].disputed is True
    assert rows[agent.id].amount_cents == 12000


async def test_a_dispute_row_times_its_first_filing_and_shows_its_latest_answer(
    sessions: async_sessionmaker[AsyncSession], wallet_id: UUID
) -> None:
    order = await paid_order(sessions, wallet_id, agent=True)
    dispute_id = await stored_dispute(
        sessions, order.id, order.capture_id, outcome="RESOLVED_SELLER_FAVOUR"
    )
    await stored_evidence(
        sessions,
        dispute_id,
        "filed",
        prepared_at=OPENED + timedelta(minutes=2),
        filed_at=OPENED + timedelta(minutes=4, seconds=30),
    )
    await stored_evidence(
        sessions,
        dispute_id,
        "refused",
        prepared_at=OPENED + timedelta(hours=1),
    )

    data = await analytics_data(sessions)

    (row,) = [row for row in data.disputes if row.dispute_id == dispute_id]
    assert row.order_id == order.id
    assert row.channel == "agent"
    assert row.opened_on == OPENED.date()
    assert row.reason == "MERCHANDISE_OR_SERVICE_NOT_RECEIVED"
    assert row.result == "won"
    assert row.evidence == "refused"
    assert row.minutes_to_evidence == 4.5


async def test_a_dispute_without_a_filed_answer_has_no_time_to_evidence(
    sessions: async_sessionmaker[AsyncSession], wallet_id: UUID
) -> None:
    unanswered = await paid_order(sessions, wallet_id)
    prepared = await paid_order(sessions, wallet_id)
    unanswered_id = await stored_dispute(sessions, unanswered.id, unanswered.capture_id)
    prepared_id = await stored_dispute(sessions, prepared.id, prepared.capture_id)
    await stored_evidence(
        sessions, prepared_id, "prepared", prepared_at=OPENED + timedelta(minutes=1)
    )

    data = await analytics_data(sessions)

    rows = {row.dispute_id: row for row in data.disputes}
    assert rows[unanswered_id].evidence == "none"
    assert rows[unanswered_id].minutes_to_evidence is None
    assert rows[unanswered_id].result == "open"
    assert rows[prepared_id].evidence == "prepared"
    assert rows[prepared_id].minutes_to_evidence is None


async def test_the_endpoint_sends_the_rows_with_the_dashboard_licence(
    sessions: async_sessionmaker[AsyncSession],
    wallet_id: UUID,
    client: httpx2.AsyncClient,
) -> None:
    order = await paid_order(sessions, wallet_id)
    app.dependency_overrides[analytics_dependency] = lambda: Analytics(
        sessions, LICENSE_KEY
    )
    try:
        response = await client.get("/api/analytics")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = AnalyticsRows.model_validate_json(response.content)
    assert data.license_key == LICENSE_KEY
    assert order.id in {row.order_id for row in data.orders}
