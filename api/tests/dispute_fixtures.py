"""Paid orders, PayPal dispute objects, and the scripted triage the dispute tests share."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import final
from uuid import UUID, uuid4, uuid7

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.agents.models import AgentRecord
from palisade_api.disputes.facts import Facts
from palisade_api.disputes.triage import Pick, TriageAction
from palisade_api.mandates.models import MandateRecord
from palisade_api.orders.models import OrderRecord, ShipmentRecord
from palisade_api.shop.models import CartRecord

DISPUTES_PATH = "/v1/customer/disputes"
OPENED = "2026-11-01T07:59:39.261Z"
TRACKING = "9400100000000000000001"
FIGHT = Pick(
    complaint="The buyer says the shoes are not the model they asked for.",
    action="fight",
    reasons=["The record holds the mandate the buyer signed for these items."],
    message=None,
    offer_amount=None,
)


@final
class ScriptedReader:
    """Answers every triage with its `pick` and keeps the facts and the answers it was given."""

    def __init__(self) -> None:
        self.pick: Pick = FIGHT
        self.asked: list[tuple[Facts, list[TriageAction]]] = []

    async def read(self, facts: Facts, allowed: Sequence[TriageAction]) -> Pick:
        self.asked.append((facts, list(allowed)))
        return self.pick


@dataclass(frozen=True)
class PaidOrder:
    id: UUID
    number: str
    capture_id: str
    # The mandate's id and token hash on an agent order, as PayPal holds it; None on a human one.
    custom_id: str | None


def new_dispute_id() -> str:
    return f"PP-R-TST-{uuid4().int % 10**8:08d}"


async def paid_order(
    sessions: async_sessionmaker[AsyncSession],
    wallet_id: UUID,
    *,
    agent: bool = False,
    shipped: bool = False,
) -> PaidOrder:
    """A captured order of 120.00 USD with its own capture id, as the checkout leaves one.

    An agent order comes with its signed cart mandate and the agent that placed it; a shipped
    one with the tracking the merchant added.
    """
    suffix = uuid4().hex[:6].upper()
    capture_id = f"CAP-{suffix}"
    now = datetime.now(UTC)
    async with sessions.begin() as session:
        cart = CartRecord(id=uuid7(), wallet_id=wallet_id, status="completed", lines=[])
        session.add(cart)
        await session.flush()
        order = OrderRecord(
            id=uuid7(),
            number=f"RR-{suffix}",
            cart_id=cart.id,
            wallet_id=wallet_id,
            channel="agent" if agent else "human",
            status="captured",
            currency="USD",
            total_cents=12000,
            paypal_order_id=f"PP-ORDER-{suffix}",
            capture_id=capture_id,
            lines=[],
            events=[],
        )
        if agent:
            agent_id = f"agent-{suffix}"
            token_hash = uuid4().hex * 2
            session.add(
                AgentRecord(id=agent_id, name="Test Agent", public_key="PUBLIC-KEY")
            )
            mandate = MandateRecord(
                id=uuid7(),
                kind="cart",
                agent_id=agent_id,
                wallet_id=wallet_id,
                cart_id=cart.id,
                cap_cents=12000,
                currency="USD",
                categories=[],
                lines=[
                    {
                        "product_id": "nike-free-rn-flyknit",
                        "size": "10",
                        "quantity": 1,
                        "name": "Nike Free RN Flyknit",
                        "unit_price": {"value": "120.00", "currency_code": "USD"},
                    }
                ],
                policy_hash="POLICY-HASH",
                token="TOKEN",
                token_hash=token_hash,
                key_hash="KEY-HASH",
                status="signed",
                signed_by="demo_signer",
                signed_at=now,
                expires_at=now + timedelta(minutes=15),
            )
            session.add(mandate)
            await session.flush()
            order.agent_id = agent_id
            order.policy_hash = "POLICY-HASH"
            order.mandate_id = mandate.id
            order.custom_id = f"{mandate.id}:{token_hash}"
        session.add(order)
        await session.flush()
        if shipped:
            session.add(
                ShipmentRecord(
                    id=uuid7(),
                    order_id=order.id,
                    carrier="USPS",
                    tracking_number=TRACKING,
                    paypal_tracker_id=f"{capture_id}-{TRACKING}",
                )
            )
        paid = PaidOrder(order.id, order.number, capture_id, order.custom_id)
    return paid


def paypal_dispute(
    dispute_id: str,
    capture_id: str,
    *,
    updated: str = "2026-11-01T08:03:00.000Z",
    reason: str = "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
    stage: str = "INQUIRY",
    status: str = "UNDER_REVIEW",
    outcome: str | None = None,
    custom: str | None = None,
    actions: tuple[str, ...] = (),
    seller_filed: str | None = None,
    escalated: bool = False,
    channel: str = "INTERNAL",
    reply: tuple[str, str] | None = None,
) -> dict[str, JsonValue]:
    """PayPal's dispute object in the shape the sandbox returns it, trimmed to what Palisade reads.

    `seller_filed` adds the seller's own evidence of that type to the list, in the given stage,
    with the record attached; `escalated` adds the escalation note the way PayPal lists it, as
    the seller's OTHER evidence in the inquiry with nothing attached. `reply` adds a second
    message from the buyer, as its time and its text. An offered `make_offer` or
    `accept_claim` comes with the plain refund among the kinds PayPal takes.
    """
    href = f"https://api-m.sandbox.paypal.com{DISPUTES_PATH}/{dispute_id}"
    evidences: list[JsonValue] = [
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
    ]
    if escalated:
        evidences.append(
            {
                "evidence_type": "OTHER",
                "source": "SUBMITTED_BY_SELLER",
                "date": updated,
                "notes": "The order was placed by a shopping agent under a mandate.",
                "dispute_life_cycle_stage": "INQUIRY",
            }
        )
    if seller_filed is not None:
        evidences.append(
            {
                "evidence_type": seller_filed,
                "source": "SUBMITTED_BY_SELLER",
                "date": updated,
                "dispute_life_cycle_stage": stage,
                "documents": [{"name": "palisade-record.pdf"}],
            }
        )
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
        "reason": reason,
        "status": status,
        "dispute_amount": {"currency_code": "USD", "value": "120.00"},
        "dispute_life_cycle_stage": stage,
        "dispute_channel": channel,
        "seller_response_due_date": "2026-11-21T07:59:39.261Z",
        "messages": [
            {
                "posted_by": "BUYER",
                "time_posted": OPENED,
                "content": "The shoes are not the model I asked for.",
            },
            *(
                []
                if reply is None
                else [
                    {"posted_by": "BUYER", "time_posted": reply[0], "content": reply[1]}
                ]
            ),
        ],
        "allowed_response_options": {
            "make_offer": {
                "offer_types": ["REFUND"] if "make_offer" in actions else []
            },
            "accept_claim": {
                "accept_claim_types": ["REFUND"] if "accept_claim" in actions else []
            },
        },
        "evidences": evidences,
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
