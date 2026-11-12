from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from uuid import UUID

import httpx2
import pytest
from dispute_fixtures import PaidOrder, paid_order
from paypal_stub import Answer, PayPalStub, json_answer
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.main import app
from palisade_api.orders.ledger import (
    SEARCH_PATH,
    SEARCH_TIME,
    WINDOW,
    Difference,
    Ledger,
    LedgerCheck,
    LedgerEntry,
    windows,
)
from palisade_api.orders.models import OrderRecord
from palisade_api.orders.router import ledger as ledger_dependency
from palisade_api.paypal.auth import PayPalAuth
from palisade_api.paypal.client import PayPalClient

pytestmark = pytest.mark.anyio

LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Row:
    """One row of PayPal's ledger, in the shape the sandbox returned on 2026-11-11."""

    transaction_id: str
    at: datetime
    code: str = "T0003"
    value: str = "120.00"
    currency: str = "USD"
    status: str = "S"
    custom: str | None = None
    reference: str = "AUTH-OR-ORDER"
    invoice: str | None = None

    def body(self) -> dict[str, JsonValue]:
        info: dict[str, JsonValue] = {
            "transaction_id": self.transaction_id,
            "paypal_reference_id": self.reference,
            "paypal_reference_id_type": "TXN",
            "transaction_event_code": self.code,
            "transaction_initiation_date": self.at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "transaction_amount": {"currency_code": self.currency, "value": self.value},
            "transaction_status": self.status,
        }
        if self.custom is not None:
            info["custom_field"] = self.custom
        if self.invoice is not None:
            info["invoice_id"] = self.invoice
        return {"transaction_info": info}


def ago(**span: float) -> datetime:
    return datetime.now(UTC) - timedelta(**span)


def search_time(value: str) -> datetime:
    return datetime.strptime(value, SEARCH_TIME).replace(tzinfo=UTC)


def paypal_ledger(
    *rows: Row, refreshed: datetime | None = None, per_page: int = 500
) -> Answer:
    """PayPal's search over `rows`: a span's rows one page at a time, both ends included.

    A span that starts after `refreshed` (now, unless given) is answered as PayPal answers it.
    """

    def answer(request: httpx2.Request) -> httpx2.Response:
        params = request.url.params
        start = search_time(params["start_date"])
        reaches = datetime.now(UTC) if refreshed is None else refreshed
        if start > reaches:
            return httpx2.Response(
                404,
                json={
                    "name": "INVALID_REQUEST",
                    "message": "Data for the given start date is not available.",
                    "debug_id": "debug-404",
                    "details": [],
                },
            )
        listed = [
            row for row in rows if start <= row.at <= search_time(params["end_date"])
        ]
        page = int(params["page"])
        return httpx2.Response(
            200,
            json={
                "transaction_details": [
                    row.body()
                    for row in listed[(page - 1) * per_page : page * per_page]
                ],
                "page": page,
                "total_items": len(listed),
                "total_pages": ceil(len(listed) / per_page),
                "last_refreshed_datetime": reaches.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )

    return answer


@pytest.fixture
def paypal() -> PayPalStub:
    return PayPalStub()


@pytest.fixture
def shop(
    sessions: async_sessionmaker[AsyncSession], paypal: PayPalStub
) -> Iterator[None]:
    http = paypal.client()
    app.dependency_overrides[ledger_dependency] = lambda: Ledger(
        sessions, PayPalClient(http, PayPalAuth(http, "id", "secret"))
    )
    yield
    app.dependency_overrides.clear()


async def placed(
    sessions: async_sessionmaker[AsyncSession],
    wallet_id: UUID,
    at: datetime,
    *,
    agent: bool = False,
    **changes: object,
) -> PaidOrder:
    """A paid order placed at `at`, with any other column set as given."""
    order = await paid_order(sessions, wallet_id, agent=agent)
    async with sessions.begin() as session:
        record = await session.get_one(OrderRecord, order.id)
        record.created_at = at
        for column, value in changes.items():
            setattr(record, column, value)
    return order


async def checked(
    client: httpx2.AsyncClient,
) -> tuple[LedgerCheck, dict[str, LedgerEntry]]:
    """The check, and its entries by order number.

    The database is shared, so the check also holds the deployed shop's orders, which the
    stubbed ledger does not list; a test reads its own entries only.
    """
    response = await client.get("/api/ledger")
    assert response.status_code == 200
    check = LedgerCheck.model_validate_json(response.content)
    return check, {entry.number: entry for entry in check.orders}


def test_a_long_stretch_is_cut_into_spans_paypal_takes() -> None:
    end = LONG_AGO + timedelta(days=70)

    spans = windows(LONG_AGO, end)

    assert [stop - start for start, stop in spans] == [
        timedelta(days=31),
        timedelta(days=31),
        timedelta(days=8),
    ]
    assert spans[0][0] == LONG_AGO
    assert all(spans[i][1] == spans[i + 1][0] for i in range(2))
    assert spans[-1][1] == end
    assert windows(end, end) == []


@pytest.mark.usefixtures("shop")
async def test_orders_paypal_lists_as_recorded_match(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
) -> None:
    person = await placed(sessions, wallet_id, ago(hours=1))
    agent = await placed(sessions, wallet_id, ago(hours=1), agent=True)
    paypal.on(
        "GET",
        SEARCH_PATH,
        paypal_ledger(
            Row(person.capture_id, ago(minutes=59)),
            Row(agent.capture_id, ago(minutes=59), custom=agent.custom_id),
            # The hold PayPal places on a disputed payment names the capture and is no refund.
            Row("HOLD-1", ago(minutes=30), code="T1110", reference=person.capture_id),
        ),
    )

    check, entries = await checked(client)

    for order in (person, agent):
        entry = entries[order.number]
        assert (entry.verdict, entry.differences, entry.refunds) == ("matched", [], [])
        assert entry.payment is not None
        assert entry.payment.amount.value == "120.00"
        assert entry.payment.custom_id == order.custom_id
    assert check.ledger_as_of is not None
    counts = check.counts
    assert counts.matched + counts.mismatch + counts.pending + counts.missing == len(
        check.orders
    )
    assert counts.matched == sum(1 for e in check.orders if e.verdict == "matched")
    # Every payment listed is a paid order's, and a hold is no payment.
    assert check.unclaimed == []


@pytest.mark.usefixtures("shop")
async def test_a_payment_no_paid_order_names_is_reported(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
) -> None:
    """Money PayPal took for the merchant outside the store's records, such as a test run on another database."""
    order = await placed(sessions, wallet_id, ago(hours=2))
    paypal.on(
        "GET",
        SEARCH_PATH,
        paypal_ledger(
            Row(order.capture_id, ago(minutes=119)),
            Row("STRAY-OLD", ago(minutes=90), value="80.00", invoice="RR-GONE01"),
            Row("STRAY-NEW", ago(minutes=30), custom="a-mandate:a-hash"),
            # Money that left the account is no payment received.
            Row("PAID-OUT", ago(minutes=20), value="-15.00"),
        ),
    )

    check, entries = await checked(client)

    assert entries[order.number].verdict == "matched"
    assert [
        (stray.id, stray.amount.value, stray.invoice_id, stray.custom_id)
        for stray in check.unclaimed
    ] == [
        ("STRAY-NEW", "120.00", None, "a-mandate:a-hash"),
        ("STRAY-OLD", "80.00", "RR-GONE01", None),
    ]


@pytest.mark.parametrize(
    ("listed", "difference"),
    [
        ({"value": "119.00"}, "amount"),
        ({"currency": "EUR"}, "currency"),
        ({"status": "P"}, "status"),
        ({"custom": "another-mandate:another-hash"}, "custom_id"),
        ({"custom": None}, "custom_id"),
    ],
)
@pytest.mark.usefixtures("shop")
async def test_a_field_paypal_lists_differently_is_named(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    listed: dict[str, str | None],
    difference: Difference,
) -> None:
    order = await placed(sessions, wallet_id, ago(hours=1), agent=True)
    row = {"custom": order.custom_id, **listed}
    paypal.on(
        "GET",
        SEARCH_PATH,
        paypal_ledger(
            Row(
                order.capture_id,
                ago(minutes=59),
                value=row.get("value") or "120.00",
                currency=row.get("currency") or "USD",
                status=row.get("status") or "S",
                custom=row["custom"],
            )
        ),
    )

    _, entries = await checked(client)

    entry = entries[order.number]
    assert (entry.verdict, entry.differences) == ("mismatch", [difference])


@pytest.mark.usefixtures("shop")
async def test_an_unlisted_payment_is_pending_while_recent_and_missing_after(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
) -> None:
    recent = await placed(sessions, wallet_id, ago(hours=1))
    old = await placed(sessions, wallet_id, ago(hours=5))
    # PayPal's ledger stops short of a payment older than three hours: still not missing.
    behind = await placed(sessions, wallet_id, ago(hours=4))
    paypal.on("GET", SEARCH_PATH, paypal_ledger(refreshed=ago(hours=4.5)))

    _, entries = await checked(client)

    assert [
        (entries[order.number].verdict, entries[order.number].payment)
        for order in (recent, old, behind)
    ] == [("pending", None), ("missing", None), ("pending", None)]


@pytest.mark.usefixtures("shop")
async def test_a_refund_only_one_side_holds_is_a_mismatch(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
) -> None:
    paid = await placed(sessions, wallet_id, ago(hours=6))
    refunded = await placed(
        sessions, wallet_id, ago(hours=6), status="refunded", updated_at=ago(hours=5)
    )
    just_refunded = await placed(sessions, wallet_id, ago(hours=6), status="refunded")
    agreed = await placed(
        sessions, wallet_id, ago(hours=6), status="refunded", updated_at=ago(hours=5)
    )
    paypal.on(
        "GET",
        SEARCH_PATH,
        paypal_ledger(
            *(
                Row(order.capture_id, ago(hours=5.9))
                for order in (paid, refunded, just_refunded, agreed)
            ),
            Row(
                "REFUND-1",
                ago(hours=5),
                code="T1107",
                value="-120.00",
                reference=paid.capture_id,
            ),
            Row(
                "REFUND-2",
                ago(hours=5),
                code="T1107",
                value="-120.00",
                reference=agreed.capture_id,
            ),
        ),
    )

    _, entries = await checked(client)

    assert [
        (
            entries[order.number].verdict,
            entries[order.number].differences,
            [refund.id for refund in entries[order.number].refunds],
        )
        for order in (paid, refunded, just_refunded, agreed)
    ] == [
        ("mismatch", ["refund"], ["REFUND-1"]),
        ("mismatch", ["refund"], []),
        ("pending", [], []),
        ("matched", [], ["REFUND-2"]),
    ]
    assert entries[agreed.number].refunds[0].amount.value == "-120.00"


@pytest.mark.usefixtures("shop")
async def test_every_page_of_a_span_is_read(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
) -> None:
    orders = [await placed(sessions, wallet_id, ago(hours=1)) for _ in range(3)]
    paypal.on(
        "GET",
        SEARCH_PATH,
        paypal_ledger(
            *(Row(order.capture_id, ago(minutes=59)) for order in orders), per_page=1
        ),
    )

    _, entries = await checked(client)

    assert [entries[order.number].verdict for order in orders] == ["matched"] * 3
    last_span = paypal.sent("GET", SEARCH_PATH)[-3:]
    assert [request.url.params["page"] for request in last_span] == ["1", "2", "3"]
    assert len({request.url.params["start_date"] for request in last_span}) == 1


@pytest.mark.usefixtures("shop")
async def test_a_long_history_is_read_span_by_span(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
) -> None:
    first = await placed(
        sessions,
        wallet_id,
        LONG_AGO,
        status="refunded",
        updated_at=LONG_AGO + timedelta(days=40),
    )
    latest = await placed(sessions, wallet_id, ago(hours=1))
    paypal.on(
        "GET",
        SEARCH_PATH,
        paypal_ledger(
            Row(first.capture_id, LONG_AGO + timedelta(minutes=1)),
            # On the edge of the first two spans, so PayPal returns it in both.
            Row(
                "REFUND-1",
                LONG_AGO + WINDOW,
                code="T1107",
                value="-120.00",
                reference=first.capture_id,
            ),
            Row(latest.capture_id, ago(minutes=59)),
        ),
    )

    _, entries = await checked(client)

    assert entries[first.number].verdict == "matched"
    assert [refund.id for refund in entries[first.number].refunds] == ["REFUND-1"]
    assert entries[latest.number].verdict == "matched"
    spans = [
        (
            search_time(request.url.params["start_date"]),
            search_time(request.url.params["end_date"]),
        )
        for request in paypal.sent("GET", SEARCH_PATH)
    ]
    assert len(spans) > 1
    assert spans[0][0] == LONG_AGO
    assert all(stop - start <= WINDOW for start, stop in spans)
    assert all(spans[i][1] == spans[i + 1][0] for i in range(len(spans) - 1))
    assert ago(minutes=1) < spans[-1][1] <= datetime.now(UTC)


@pytest.mark.usefixtures("shop")
async def test_the_read_stops_where_paypals_ledger_ends(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
) -> None:
    first = await placed(sessions, wallet_id, LONG_AGO)
    latest = await placed(sessions, wallet_id, ago(hours=1))
    refreshed = LONG_AGO + timedelta(days=14)
    paypal.on(
        "GET",
        SEARCH_PATH,
        paypal_ledger(
            Row(first.capture_id, LONG_AGO + timedelta(minutes=1)), refreshed=refreshed
        ),
    )

    check, entries = await checked(client)

    assert check.ledger_as_of == refreshed
    assert entries[first.number].verdict == "matched"
    assert entries[latest.number].verdict == "pending"
    assert len(paypal.sent("GET", SEARCH_PATH)) == 2


@pytest.mark.usefixtures("shop")
async def test_a_ledger_that_reaches_no_order_yet_leaves_them_pending(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
) -> None:
    order = await placed(sessions, wallet_id, ago(hours=1))
    paypal.on(
        "GET", SEARCH_PATH, paypal_ledger(refreshed=datetime(2020, 1, 1, tzinfo=UTC))
    )

    check, entries = await checked(client)

    assert check.ledger_as_of is None
    assert entries[order.number].verdict == "pending"
    assert len(paypal.sent("GET", SEARCH_PATH)) == 1


@pytest.mark.usefixtures("shop")
async def test_a_failed_read_is_a_502(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
) -> None:
    _ = await placed(sessions, wallet_id, ago(hours=1))
    paypal.on(
        "GET",
        SEARCH_PATH,
        json_answer(500, {"name": "INTERNAL_SERVICE_ERROR", "debug_id": "debug-500"}),
    )

    response = await client.get("/api/ledger")

    assert response.status_code == 502
    assert "INTERNAL_SERVICE_ERROR" in response.text
