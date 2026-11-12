"""The ledger check: every paid order beside the row PayPal's own transaction ledger holds for it.

PayPal's Transaction Search lists each movement on the merchant's account. A capture is a row
under its own id, with the amount, the status, and the custom value the order was created with;
a refund is a row of its own that names the capture. The check reads the ledger from the shop's
first paid order to now and says, for every paid order, whether the two records agree, and
which payments PayPal lists that no paid order names.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal, final
from urllib.parse import urlencode

from fastapi import status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.orders.models import OrderRecord
from palisade_api.orders.store import CHANNEL, ORDER_STATUS, Channel, OrderStatus
from palisade_api.paypal.client import PayPalClient, PayPalError
from palisade_api.shop.catalog import Money, money

SEARCH_PATH = "/v1/reporting/transactions"
SEARCH_TIME = "%Y-%m-%dT%H:%M:%S-0000"
# The longest span PayPal's search takes in one request, and its largest page.
WINDOW = timedelta(days=31)
PAGE_SIZE = 500
# PayPal lists a transaction within three hours of it happening.
REPORTING_LAG = timedelta(hours=3)
# PayPal's event codes: T00 and two more digits is a payment received, T1107 a refund the
# merchant made.
PAYMENT_CODES = "T00"
REFUND_CODE = "T1107"
# The states of an order PayPal took money for, each with the status letters its payment row
# may carry: S completed, P pending, V reversed with the money back at the sender. A refunded
# payment's row stayed S in the sandbox (2026-10-20), and PayPal documents V for one whose
# money went back in full, so an order refunded or reversed passes with either.
EXPECTED_STATUS: dict[str, frozenset[str]] = {
    "capture_pending": frozenset({"P"}),
    "captured": frozenset({"S"}),
    "refunded": frozenset({"S", "V"}),
    "reversed": frozenset({"S", "V"}),
}

# matched: PayPal lists the capture and every compared field agrees.
# mismatch: PayPal lists it, and the fields named beside the verdict disagree.
# pending: PayPal does not list the capture, or a refund the store recorded, and it is too
#   recent for the ledger to have to.
# missing: PayPal does not list the capture although its ledger reaches past it.
Verdict = Literal["matched", "mismatch", "pending", "missing"]
Difference = Literal["amount", "currency", "custom_id", "status", "refund"]


class SearchAmount(BaseModel):
    value: Decimal
    currency_code: str


class SearchInfo(BaseModel):
    """The parts of a ledger row the check reads."""

    transaction_id: str
    transaction_event_code: str
    transaction_status: str
    transaction_amount: SearchAmount
    transaction_initiation_date: datetime
    # On a refund's row, the capture it returns.
    paypal_reference_id: str | None = None
    custom_field: str | None = None
    # The order number the checkout sent with the payment.
    invoice_id: str | None = None


class SearchRow(BaseModel):
    transaction_info: SearchInfo


class SearchPage(BaseModel):
    transaction_details: list[SearchRow] = []
    total_pages: int
    # How far PayPal's ledger reaches.
    last_refreshed_datetime: datetime


class LedgerAmount(BaseModel):
    """An amount as PayPal's ledger states it, whatever its currency."""

    value: str
    currency_code: str


class LedgerPayment(BaseModel):
    """PayPal's row for an order's capture."""

    event_code: str
    status: str
    amount: LedgerAmount
    custom_id: str | None
    at: datetime


class LedgerRefund(BaseModel):
    """A refund PayPal lists against a capture; its amount is negative, as the ledger has it."""

    id: str
    status: str
    amount: LedgerAmount
    at: datetime


class LedgerEntry(BaseModel):
    """One paid order beside PayPal's ledger."""

    number: str
    channel: Channel
    status: OrderStatus
    total: Money
    capture_id: str
    custom_id: str | None
    placed_at: datetime
    verdict: Verdict
    # The fields that disagree, on a mismatch.
    differences: list[Difference]
    # None while PayPal lists no row for the capture.
    payment: LedgerPayment | None
    refunds: list[LedgerRefund]


class UnclaimedPayment(BaseModel):
    """A payment PayPal took for the merchant that no paid order names as its capture."""

    id: str
    status: str
    amount: LedgerAmount
    # The order number PayPal holds on the payment, if the payer's checkout sent one.
    invoice_id: str | None
    custom_id: str | None
    at: datetime


class VerdictCounts(BaseModel):
    matched: int
    mismatch: int
    pending: int
    missing: int


class LedgerCheck(BaseModel):
    checked_at: datetime
    # How far PayPal says its ledger reaches; None when the shop has no paid order to look up,
    # or the ledger does not reach its first one yet.
    ledger_as_of: datetime | None
    counts: VerdictCounts
    orders: list[LedgerEntry]
    # Payments in PayPal's ledger since the first paid order that no paid order names, newest
    # first: money the merchant's account received outside the store's records.
    unclaimed: list[UnclaimedPayment]


@dataclass(frozen=True)
class Listed:
    """What PayPal's ledger lists, by the capture each row belongs to."""

    payments: dict[str, SearchInfo]
    refunds: dict[str, list[SearchInfo]]


def windows(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """`start` to `end` cut into consecutive spans of at most WINDOW."""
    spans: list[tuple[datetime, datetime]] = []
    while start < end:
        stop = min(start + WINDOW, end)
        spans.append((start, stop))
        start = stop
    return spans


def listed(rows: list[SearchInfo]) -> Listed:
    """The payments by their own id and the refunds by the capture they return.

    A row on the edge of two spans is read twice and kept once.
    """
    unique = {
        (row.transaction_id, row.transaction_event_code): row for row in rows
    }.values()
    refunds: dict[str, list[SearchInfo]] = {}
    for row in unique:
        if (
            row.transaction_event_code == REFUND_CODE
            and row.paypal_reference_id is not None
        ):
            refunds.setdefault(row.paypal_reference_id, []).append(row)
    return Listed(
        payments={
            row.transaction_id: row
            for row in unique
            if row.transaction_event_code.startswith(PAYMENT_CODES)
        },
        refunds=refunds,
    )


def judge(
    order: OrderRecord,
    payment: SearchInfo | None,
    refunds: list[SearchInfo],
    cutoff: datetime,
) -> tuple[Verdict, list[Difference]]:
    """The verdict on one order, and the fields that disagree.

    `cutoff` is the moment PayPal's ledger must reach: what the store recorded before it is
    listed or wrong, and what the store recorded after it may only not be listed yet.
    """
    if payment is None:
        return ("pending" if order.created_at >= cutoff else "missing"), []
    amount = payment.transaction_amount
    refund_awaited = order.status == "refunded" and not refunds
    checks: dict[Difference, bool] = {
        "amount": amount.value * 100 != order.total_cents,
        "currency": amount.currency_code != order.currency,
        "custom_id": payment.custom_field != order.custom_id,
        "status": payment.transaction_status not in EXPECTED_STATUS[order.status],
        "refund": (bool(refunds) and order.status != "refunded")
        or (refund_awaited and order.updated_at < cutoff),
    }
    differences: list[Difference] = [
        field for field, differs in checks.items() if differs
    ]
    if differences:
        return "mismatch", differences
    return ("pending" if refund_awaited else "matched"), []


def ledger_amount(amount: SearchAmount) -> LedgerAmount:
    return LedgerAmount(value=str(amount.value), currency_code=amount.currency_code)


def unclaimed(ledger: Listed, claimed: set[str]) -> list[UnclaimedPayment]:
    """The payments received that none of the `claimed` captures is, newest first."""
    strays = [
        row
        for capture_id, row in ledger.payments.items()
        if capture_id not in claimed and row.transaction_amount.value > 0
    ]
    return [
        UnclaimedPayment(
            id=row.transaction_id,
            status=row.transaction_status,
            amount=ledger_amount(row.transaction_amount),
            invoice_id=row.invoice_id,
            custom_id=row.custom_field,
            at=row.transaction_initiation_date,
        )
        for row in sorted(
            strays, key=lambda row: row.transaction_initiation_date, reverse=True
        )
    ]


def entry(order: OrderRecord, ledger: Listed, cutoff: datetime) -> LedgerEntry:
    """The order beside its rows in the ledger.

    Raises:
        ValueError: the order is in a paid state without a capture.
    """
    if order.capture_id is None:
        raise ValueError(f"order {order.number} is {order.status} without a capture")
    payment = ledger.payments.get(order.capture_id)
    refunds = ledger.refunds.get(order.capture_id, [])
    verdict, differences = judge(order, payment, refunds, cutoff)
    return LedgerEntry(
        number=order.number,
        channel=CHANNEL.validate_python(order.channel),
        status=ORDER_STATUS.validate_python(order.status),
        total=money(order.total_cents),
        capture_id=order.capture_id,
        custom_id=order.custom_id,
        placed_at=order.created_at,
        verdict=verdict,
        differences=differences,
        payment=None
        if payment is None
        else LedgerPayment(
            event_code=payment.transaction_event_code,
            status=payment.transaction_status,
            amount=ledger_amount(payment.transaction_amount),
            custom_id=payment.custom_field,
            at=payment.transaction_initiation_date,
        ),
        refunds=[
            LedgerRefund(
                id=refund.transaction_id,
                status=refund.transaction_status,
                amount=ledger_amount(refund.transaction_amount),
                at=refund.transaction_initiation_date,
            )
            for refund in refunds
        ],
    )


@final
class Ledger:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], paypal: PayPalClient
    ) -> None:
        self._sessions = sessions
        self._paypal = paypal

    async def check(self) -> LedgerCheck:
        """Read PayPal's ledger since the first paid order and compare every paid order with it.

        Raises:
            PayPalError: PayPal refused the read or did not answer.
        """
        now = datetime.now(UTC)
        async with self._sessions() as session:
            orders = (
                await session.scalars(
                    select(OrderRecord)
                    .where(OrderRecord.status.in_(list(EXPECTED_STATUS)))
                    .order_by(OrderRecord.created_at.desc())
                )
            ).all()
        rows, as_of = (
            ([], None) if not orders else await self._read(orders[-1].created_at, now)
        )
        ledger = listed(rows)
        lag_ends = now - REPORTING_LAG
        cutoff = lag_ends if as_of is None else min(as_of, lag_ends)
        entries = [entry(order, ledger, cutoff) for order in orders]
        counts = Counter(row.verdict for row in entries)
        return LedgerCheck(
            checked_at=now,
            ledger_as_of=as_of,
            counts=VerdictCounts(
                matched=counts["matched"],
                mismatch=counts["mismatch"],
                pending=counts["pending"],
                missing=counts["missing"],
            ),
            orders=entries,
            unclaimed=unclaimed(ledger, {row.capture_id for row in entries}),
        )

    async def _read(
        self, start: datetime, end: datetime
    ) -> tuple[list[SearchInfo], datetime | None]:
        """Every ledger row between the two moments, and how far PayPal says its ledger reaches.

        PayPal answers 404 INVALID_REQUEST for a span that starts after that point ("Data for
        the given start date is not available", seen 2026-11-11), so that span and the ones
        after it hold nothing yet.
        """
        rows: list[SearchInfo] = []
        as_of: datetime | None = None
        for span_start, span_end in windows(start, end):
            try:
                found, as_of = await self._span(span_start, span_end)
            except PayPalError as error:
                if error.status_code == status.HTTP_404_NOT_FOUND and error.issues == [
                    "INVALID_REQUEST"
                ]:
                    break
                raise
            rows.extend(found)
        return rows, as_of

    async def _span(
        self, start: datetime, end: datetime
    ) -> tuple[list[SearchInfo], datetime]:
        """One span's rows, every page of them, and how far PayPal says its ledger reaches."""
        rows: list[SearchInfo] = []
        number = 1
        while True:
            query = urlencode(
                {
                    "start_date": start.astimezone(UTC).strftime(SEARCH_TIME),
                    "end_date": end.astimezone(UTC).strftime(SEARCH_TIME),
                    "fields": "transaction_info",
                    "page_size": str(PAGE_SIZE),
                    "page": str(number),
                }
            )
            page = SearchPage.model_validate(
                (await self._paypal.get(f"{SEARCH_PATH}?{query}")).body
            )
            rows.extend(row.transaction_info for row in page.transaction_details)
            if number >= page.total_pages:
                return rows, page.last_refreshed_datetime
            number += 1
