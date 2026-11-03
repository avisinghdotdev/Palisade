"""Filing and settling disputes as PayPal's sandbox lets a test buyer and a test reviewer do."""

from datetime import UTC, datetime, timedelta
from typing import Literal, final
from urllib.parse import urlencode
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload

from palisade_api.activity.store import Activities
from palisade_api.disputes.service import Dispute, Disputes, PayPalLink
from palisade_api.orders.models import OrderRecord
from palisade_api.orders.store import UnknownOrder
from palisade_api.paypal.client import PayPalClient
from palisade_api.shop.catalog import money

# The reasons the demo buyer's app can file by API; UNAUTHORISED answers 500 in the sandbox and
# is filed on the sandbox site instead.
SimulatedReason = Literal[
    "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
    "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
    "OTHER",
]
Adjudication = Literal["SELLER_FAVOR", "BUYER_FAVOR"]

SEARCH_PATH = "/v1/reporting/transactions"
SEARCH_TIME = "%Y-%m-%dT%H:%M:%S-0000"
# PayPal's payment rows; holds (T15) and their releases (T11) sit beside them for one payment.
PAYMENT_EVENT_CODES = "T00"


class DisputeRequest(BaseModel):
    reason: SimulatedReason


class AdjudicationRequest(BaseModel):
    outcome: Adjudication


class NotDisputable(Exception):
    def __init__(self, number: str, why: str) -> None:
        super().__init__(f"order {number} cannot be disputed by the demo buyer: {why}")


class NotSearchableYet(Exception):
    def __init__(self, number: str) -> None:
        super().__init__(
            f"PayPal's transaction search does not list order {number} for the demo buyer"
            + " yet; payments appear there within three hours, so try again later"
        )


class NotAdjudicable(Exception):
    def __init__(self, dispute_id: str, status: str) -> None:
        super().__init__(
            f"PayPal does not offer a decision on {dispute_id} while it is {status}; the"
            + " sandbox offers one once a claim has been under review a while, often hours"
        )


class SearchAmount(BaseModel):
    value: str


class SearchInfo(BaseModel):
    transaction_id: str
    transaction_event_code: str
    transaction_amount: SearchAmount
    invoice_id: str | None = None


class SearchRow(BaseModel):
    transaction_info: SearchInfo


class SearchPage(BaseModel):
    transaction_details: list[SearchRow]


class CreatedDispute(BaseModel):
    links: list[PayPalLink]


def search_window(placed_at: datetime) -> dict[str, str]:
    """A day from just before the order: PayPal's search lists a payment in the buyer's own time."""
    end = min(placed_at + timedelta(days=1), datetime.now(UTC))
    return {
        "start_date": (placed_at - timedelta(minutes=5)).strftime(SEARCH_TIME),
        "end_date": end.strftime(SEARCH_TIME),
    }


@final
class Simulator:
    """The demo buyer filing a dispute through its own REST app, and PayPal's sandbox deciding one.

    Only the demo buyer's app can open a dispute on its payments, so only demo-buyer orders can
    be disputed here; a buyer on their own account files on sandbox.paypal.com.
    """

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        buyer: PayPalClient,
        seller: PayPalClient,
        disputes: Disputes,
        activities: Activities,
    ) -> None:
        self._sessions = sessions
        self._buyer = buyer
        self._seller = seller
        self._disputes = disputes
        self._activities = activities

    async def file_dispute(self, number: str, reason: SimulatedReason) -> Dispute:
        """File a dispute on the order as the demo buyer, then store it as PayPal opened it.

        Raises:
            UnknownOrder: no order has this number.
            NotDisputable: the order is not a paid demo-buyer order.
            NotSearchableYet: PayPal's search does not list the buyer's side of the payment yet.
            PayPalError: PayPal refused a call or did not answer.
        """
        async with self._sessions() as session:
            order = await session.scalar(
                select(OrderRecord)
                .options(joinedload(OrderRecord.wallet))
                .where(OrderRecord.number == number)
            )
        if order is None:
            raise UnknownOrder(number)
        if not order.wallet.demo:
            raise NotDisputable(number, "it was paid from another buyer's wallet")
        if order.status != "captured":
            raise NotDisputable(number, f"it is {order.status}, not paid")
        transaction_id = await self._buyer_transaction(order)
        created = await self._buyer.post(
            "/v1/customer/disputes",
            {
                "disputed_transactions": [{"buyer_transaction_id": transaction_id}],
                "reason": reason,
                "dispute_amount": money(order.total_cents).model_dump(),
                "extensions": {
                    "merchant_contacted": True,
                    "merchant_contacted_outcome": "NO_RESPONSE",
                    "merchandize_dispute_properties": {"issue_type": "PRODUCT"},
                },
            },
            request_id=str(uuid4()),
        )
        links = CreatedDispute.model_validate(created.body).links
        dispute_id = next(link.href for link in links if link.rel == "self").rsplit(
            "/", 1
        )[-1]
        live, raw = await self._disputes.fetch(dispute_id)
        async with self._sessions.begin() as session:
            change = await self._disputes.upsert(session, live, raw)
            self._activities.add(
                session,
                "sandbox_dispute_filed",
                order_id=order.id,
                dispute_id=dispute_id,
                reason=reason,
            )
            await self._disputes.record(session, change, "simulator")
        return await self._disputes.get(dispute_id)

    async def adjudicate(self, dispute_id: str, outcome: Adjudication) -> Dispute:
        """Have PayPal's sandbox decide the dispute, then store it as PayPal holds it after.

        Raises:
            NotAdjudicable: PayPal does not offer a decision on the dispute now.
            PayPalError: PayPal refused a call or did not answer.
        """
        live, _ = await self._disputes.fetch(dispute_id)
        if "adjudicate" not in live.actions:
            raise NotAdjudicable(dispute_id, live.status)
        _ = await self._seller.post(
            f"/v1/customer/disputes/{dispute_id}/adjudicate",
            {"adjudication_outcome": outcome},
            request_id=str(uuid4()),
        )
        live, raw = await self._disputes.fetch(dispute_id)
        async with self._sessions.begin() as session:
            change = await self._disputes.upsert(session, live, raw)
            self._activities.add(
                session,
                "sandbox_dispute_adjudicated",
                order_id=change.record.order_id,
                dispute_id=dispute_id,
                outcome=outcome,
            )
            await self._disputes.record(session, change, "simulator")
        return await self._disputes.get(dispute_id)

    async def _buyer_transaction(self, order: OrderRecord) -> str:
        """The demo buyer's own id for the order's payment, which a dispute must name.

        The buyer and the seller see one payment under different ids; the checkout sends the
        order number as PayPal's invoice id, which both sides keep.
        """
        query = urlencode(
            {
                **search_window(order.created_at),
                "fields": "transaction_info",
                "page_size": "500",
            }
        )
        page = SearchPage.model_validate(
            (await self._buyer.get(f"{SEARCH_PATH}?{query}")).body
        )
        payments = [
            row.transaction_info
            for row in page.transaction_details
            if row.transaction_info.invoice_id == order.number
            and row.transaction_info.transaction_event_code.startswith(
                PAYMENT_EVENT_CODES
            )
            and row.transaction_info.transaction_amount.value.startswith("-")
        ]
        match payments:
            case []:
                raise NotSearchableYet(order.number)
            case [payment]:
                return payment.transaction_id
            case _:
                raise ValueError(
                    f"PayPal lists {len(payments)} buyer payments for order {order.number}"
                )
