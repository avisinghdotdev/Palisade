from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid7

import httpx2
import pytest
from dispute_fixtures import (
    DISPUTES_PATH,
    new_dispute_id,
    paid_order,
    paypal_dispute,
)
from paypal_stub import PayPalStub, json_answer
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.activity.router import activities as activities_dependency
from palisade_api.activity.store import Activities, Activity
from palisade_api.disputes import webhooks
from palisade_api.disputes.models import DisputeRecord
from palisade_api.disputes.router import disputes as disputes_dependency
from palisade_api.disputes.service import (
    Dispute,
    Disputes,
    DisputeSummary,
    NotTheShops,
    UnknownDispute,
)
from palisade_api.main import app
from palisade_api.paypal.auth import PayPalAuth
from palisade_api.paypal.client import PayPalClient
from palisade_api.webhooks.store import WebhookEvent

pytestmark = pytest.mark.anyio


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
def console(
    sessions: async_sessionmaker[AsyncSession], disputes: Disputes
) -> Iterator[None]:
    app.dependency_overrides[disputes_dependency] = lambda: disputes
    app.dependency_overrides[activities_dependency] = lambda: Activities(sessions)
    yield
    app.dependency_overrides.clear()


def delivery(event_type: str, dispute_id: str) -> WebhookEvent:
    """A dispute event as PayPal delivers it; the handler reads only the dispute id."""
    return WebhookEvent(
        id=f"WH-TEST-{uuid7().hex}",
        event_type=event_type,
        create_time=datetime.now(UTC),
        resource_type="dispute",
        summary=f"A dispute was updated with case # {dispute_id}",
        resource={"dispute_id": dispute_id, "status": "OPEN"},
    )


def answer_with(
    paypal: PayPalStub, dispute_id: str, body: dict[str, JsonValue]
) -> None:
    paypal.on("GET", f"{DISPUTES_PATH}/{dispute_id}", json_answer(200, body))


async def kinds(
    sessions: async_sessionmaker[AsyncSession], dispute_id: str
) -> list[str]:
    return [row.kind for row in await Activities(sessions).for_dispute(dispute_id)]


async def test_a_dispute_event_stores_the_dispute_joined_to_its_order(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    disputes: Disputes,
    wallet_id: UUID,
) -> None:
    order = await paid_order(sessions, wallet_id, agent=True)
    dispute_id = new_dispute_id()
    answer_with(
        paypal,
        dispute_id,
        paypal_dispute(dispute_id, order.capture_id, custom=order.custom_id),
    )
    handle = webhooks.handlers(disputes)["CUSTOMER.DISPUTE.CREATED"]

    await handle(delivery("CUSTOMER.DISPUTE.CREATED", dispute_id))
    await handle(delivery("CUSTOMER.DISPUTE.CREATED", dispute_id))

    stored = await disputes.get(dispute_id)
    assert (stored.order_number, stored.order_channel, stored.custom_matches) == (
        order.number,
        "agent",
        True,
    )
    assert (stored.stage, stored.status, stored.amount.value) == (
        "INQUIRY",
        "UNDER_REVIEW",
        "120.00",
    )
    assert [message.content for message in stored.messages] == [
        "The shoes are not the model I asked for."
    ]
    [opened] = await Activities(sessions).for_dispute(dispute_id)
    assert (opened.kind, opened.order_number, opened.details["source"]) == (
        "dispute_opened",
        order.number,
        "webhook",
    )


async def test_a_later_read_is_recorded_and_an_older_one_changes_nothing(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    disputes: Disputes,
    wallet_id: UUID,
) -> None:
    order = await paid_order(sessions, wallet_id)
    dispute_id = new_dispute_id()
    answer_with(paypal, dispute_id, paypal_dispute(dispute_id, order.capture_id))
    _ = await disputes.sync(dispute_id, "webhook")
    answer_with(
        paypal,
        dispute_id,
        paypal_dispute(
            dispute_id,
            order.capture_id,
            updated="2026-11-01T18:58:37.392Z",
            stage="CHARGEBACK",
            status="RESOLVED",
            outcome="RESOLVED_SELLER_FAVOUR",
        ),
    )
    resolved = await disputes.sync(dispute_id, "webhook")
    answer_with(
        paypal,
        dispute_id,
        paypal_dispute(
            dispute_id,
            order.capture_id,
            updated="2026-11-01T08:07:35.000Z",
            stage="CHARGEBACK",
            status="WAITING_FOR_SELLER_RESPONSE",
        ),
    )

    late = await disputes.sync(dispute_id, "webhook")

    assert (resolved.status, resolved.outcome) == ("RESOLVED", "RESOLVED_SELLER_FAVOUR")
    assert (late.status, late.outcome) == ("RESOLVED", "RESOLVED_SELLER_FAVOUR")
    assert await kinds(sessions, dispute_id) == ["dispute_opened", "dispute_resolved"]


async def test_a_dispute_on_a_payment_outside_the_shop_is_not_kept(
    sessions: async_sessionmaker[AsyncSession], paypal: PayPalStub, disputes: Disputes
) -> None:
    dispute_id = new_dispute_id()
    answer_with(paypal, dispute_id, paypal_dispute(dispute_id, "CAP-NOT-OURS"))

    # The webhook takes it calmly, so PayPal's delivery counts as handled.
    await webhooks.handlers(disputes)["CUSTOMER.DISPUTE.CREATED"](
        delivery("CUSTOMER.DISPUTE.CREATED", dispute_id)
    )

    with pytest.raises(NotTheShops):
        _ = await disputes.sync(dispute_id, "console")
    with pytest.raises(UnknownDispute):
        _ = await disputes.get(dispute_id)
    assert await kinds(sessions, dispute_id) == []


async def test_the_reconciliation_reads_new_changed_and_unlisted_open_disputes(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    disputes: Disputes,
    wallet_id: UUID,
) -> None:
    # The shared database may hold open disputes already; PayPal's stand-in knows none of them.
    async with sessions() as session:
        others = (
            await session.scalars(
                select(DisputeRecord.id).where(DisputeRecord.status != "RESOLVED")
            )
        ).all()
    for other in others:
        paypal.on(
            "GET",
            f"{DISPUTES_PATH}/{other}",
            json_answer(404, {"name": "RESOURCE_NOT_FOUND"}),
        )
    known, unlisted, broken, outside, new, later = (new_dispute_id() for _ in range(6))
    for dispute_id in (known, unlisted, new, later):
        order = await paid_order(sessions, wallet_id)
        answer_with(paypal, dispute_id, paypal_dispute(dispute_id, order.capture_id))
    _ = await disputes.sync(known, "webhook")
    _ = await disputes.sync(unlisted, "simulator")
    answer_with(paypal, broken, {"dispute_id": broken})
    answer_with(paypal, outside, paypal_dispute(outside, "CAP-NOT-OURS"))
    next_query = "page_size=50&next_page_token=PAGE-2"
    next_page = f"https://api-m.sandbox.paypal.com{DISPUTES_PATH}?{next_query}"
    paypal.on(
        "GET",
        DISPUTES_PATH,
        json_answer(
            200,
            {
                "items": [
                    # PayPal's list rounds the update time down to the second.
                    {"dispute_id": known, "update_time": "2026-11-01T08:03:00.000Z"},
                    {"dispute_id": broken, "update_time": "2026-11-01T08:30:00.000Z"},
                    {"dispute_id": outside, "update_time": "2026-11-01T08:40:00.000Z"},
                    {"dispute_id": new, "update_time": "2026-11-01T09:00:00.000Z"},
                ],
                "links": [{"rel": "next", "href": next_page}],
            },
        ),
        json_answer(
            200,
            {
                "items": [
                    {"dispute_id": later, "update_time": "2026-11-01T10:00:00.000Z"}
                ]
            },
        ),
    )

    result = await disputes.reconcile()

    assert result.read == [new, later, unlisted]
    assert result.ignored == [outside]
    assert set(result.failed) == {broken, *others}
    assert len(paypal.sent("GET", f"{DISPUTES_PATH}/{known}")) == 1
    [first_page, second_page] = paypal.sent("GET", DISPUTES_PATH)
    # Disputes opened before the shop's first order cannot be the shop's.
    assert {"start_time", "update_time_after"} <= set(first_page.url.params)
    assert second_page.url.query.decode() == next_query
    assert await kinds(sessions, new) == ["dispute_opened"]


@pytest.mark.usefixtures("console")
async def test_the_console_lists_and_shows_a_dispute_with_its_timeline(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    disputes: Disputes,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
) -> None:
    order = await paid_order(sessions, wallet_id)
    dispute_id = new_dispute_id()
    answer_with(
        paypal,
        dispute_id,
        paypal_dispute(
            dispute_id, order.capture_id, actions=("accept_claim", "escalate")
        ),
    )
    _ = await disputes.sync(dispute_id, "webhook")

    listed = await client.get("/api/disputes", params={"order_number": order.number})
    shown = await client.get(f"/api/disputes/{dispute_id}")
    timeline = await client.get(f"/api/disputes/{dispute_id}/activity")

    [summary] = TypeAdapter(list[DisputeSummary]).validate_json(listed.content)
    assert summary.id == dispute_id
    page = Dispute.model_validate_json(shown.content)
    # A human order carries no mandate, so there is nothing for PayPal's custom value to match.
    assert (page.actions, page.custom_matches) == (["accept_claim", "escalate"], None)
    rows = TypeAdapter(list[Activity]).validate_json(timeline.content)
    assert [row.kind for row in rows] == ["dispute_opened"]
    assert (await client.get("/api/disputes/PP-R-NONE-00000000")).status_code == 404
    assert (await client.get("/api/disputes/not-a-dispute")).status_code == 422
