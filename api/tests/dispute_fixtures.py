"""Paid orders and PayPal dispute objects the dispute and sandbox tests share."""

from dataclasses import dataclass
from uuid import UUID, uuid4, uuid7

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.orders.models import OrderRecord
from palisade_api.shop.models import CartRecord

DISPUTES_PATH = "/v1/customer/disputes"
OPENED = "2026-11-01T07:59:39.261Z"


@dataclass(frozen=True)
class PaidOrder:
    id: UUID
    number: str
    capture_id: str
    custom_id: str | None


def new_dispute_id() -> str:
    return f"PP-R-TST-{uuid4().int % 10**8:08d}"


async def paid_order(
    sessions: async_sessionmaker[AsyncSession],
    wallet_id: UUID,
    *,
    custom_id: str | None = None,
) -> PaidOrder:
    """A captured order of 120.00 USD with its own capture id, as the checkout leaves one."""
    suffix = uuid4().hex[:6].upper()
    paid = PaidOrder(uuid7(), f"RR-{suffix}", f"CAP-{suffix}", custom_id)
    async with sessions.begin() as session:
        cart = CartRecord(id=uuid7(), wallet_id=wallet_id, status="completed", lines=[])
        session.add(cart)
        await session.flush()
        session.add(
            OrderRecord(
                id=paid.id,
                number=paid.number,
                cart_id=cart.id,
                wallet_id=wallet_id,
                channel="agent" if custom_id is not None else "human",
                status="captured",
                currency="USD",
                total_cents=12000,
                custom_id=custom_id,
                capture_id=paid.capture_id,
                lines=[],
                events=[],
            )
        )
    return paid


def paypal_dispute(
    dispute_id: str,
    capture_id: str,
    *,
    updated: str = "2026-11-01T08:03:00.000Z",
    stage: str = "INQUIRY",
    status: str = "UNDER_REVIEW",
    outcome: str | None = None,
    custom: str | None = None,
    actions: tuple[str, ...] = (),
) -> dict[str, JsonValue]:
    """PayPal's dispute object in the shape the sandbox returns it, trimmed to what Palisade reads."""
    href = f"https://api-m.sandbox.paypal.com{DISPUTES_PATH}/{dispute_id}"
    body: dict[str, JsonValue] = {
        "dispute_id": dispute_id,
        "create_time": OPENED,
        "update_time": updated,
        "disputed_transactions": [
            {
                "buyer_transaction_id": "BUYER-TXN-1",
                "seller_transaction_id": capture_id,
                "custom": custom,
                "seller_protection_eligible": True,
            }
        ],
        "reason": "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
        "status": status,
        "dispute_amount": {"currency_code": "USD", "value": "120.00"},
        "dispute_life_cycle_stage": stage,
        "dispute_channel": "INTERNAL",
        "seller_response_due_date": "2026-11-21T07:59:39.261Z",
        "messages": [
            {
                "posted_by": "BUYER",
                "time_posted": OPENED,
                "content": "The shoes are not the model I asked for.",
            }
        ],
        "evidences": [
            {
                "evidence_type": "CREATE",
                "source": "SUBMITTED_BY_BUYER",
                "date": OPENED,
                "dispute_life_cycle_stage": "INQUIRY",
            },
            # What PayPal asks the seller for carries no stage.
            {
                "evidence_type": "PROOF_OF_FULFILLMENT",
                "source": "REQUESTED_FROM_SELLER",
                "date": OPENED,
            },
        ],
        "links": [
            {"rel": "self", "href": href, "method": "GET"},
            *(
                {"rel": rel, "href": f"{href}/{rel}", "method": "POST"}
                for rel in actions
            ),
        ],
    }
    if outcome is not None:
        body["dispute_outcome"] = {"outcome_code": outcome}
    return body
