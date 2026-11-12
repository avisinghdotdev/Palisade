"""The facts record behind a dispute response: what Palisade knows, and nothing it does not.

The narrative is written from this record alone and cites it line by line, and the PDF prints
it, so both say the same thing and neither can say more than the store recorded.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, JsonValue

from palisade_api.mandates.service import MandateLine
from palisade_api.orders.store import OrderLine
from palisade_api.shop.catalog import Money

MERCHANT_NAME = "Ridgeline Running"
# What the record says where the store holds nothing.
NOT_SPECIFIED = "Not specified"
# Marks the complaint the demo buyer typed in the sandbox simulator, which PayPal does not hold.
SIMULATED = "[Sandbox simulator]"
# How the agent and the mandate are checked, as facts the narrative can cite by name.
SIGNATURE_SCHEME = (
    "RFC 9421 HTTP message signatures with an Ed25519 key in the store's registry"
)
MANDATE_FORMAT = "SD-JWT in the shape of an AP2 Checkout Mandate, issued by Palisade; the token hash is SHA-256"
# How each way of signing reads in the record, so the narrative and the PDF never print a code.
SIGNERS = {
    "passkey": "The buyer's own passkey (WebAuthn)",
    "demo_signer": "The demo signer, a simulated stand-in for the buyer's passkey in PayPal's"
    + " sandbox",
}


class DisputeFacts(BaseModel):
    id: str
    reason: str
    stage: str
    status: str
    channel: str | None
    amount: Money
    opened_at: datetime
    buyer_messages: list[str]


class OrderFacts(BaseModel):
    number: str
    placed_at: datetime
    # "agent" when a shopping agent placed it under a mandate, "human" when the buyer did.
    channel: str
    status: str
    total: Money
    lines: list[OrderLine]
    paypal_order_id: str | None
    capture_id: str | None
    # The value the store sent PayPal as the payment's custom id: the mandate's id and hash.
    custom_id: str | None


class AgentFacts(BaseModel):
    id: str
    name: str
    # The Ed25519 public key in the store's registry, which signed the agent's requests.
    public_key: str
    signature_scheme: str
    # Requests on this purchase whose RFC 9421 signature the store verified.
    verified_requests: int


class MandateFacts(BaseModel):
    id: UUID
    # "cart": one exact cart, signed at checkout; "intent": a cap and categories, signed up front.
    kind: str
    token_format: str
    # How the buyer signed, in SIGNERS' words; None while unsigned.
    signed_by: str | None
    signed_at: datetime | None
    cap: Money
    categories: list[str]
    lines: list[MandateLine]
    # SHA-256 of the signed token: what the buyer's proof covers and the payment's custom id names.
    token_hash: str
    policy_hash: str
    expires_at: datetime
    # Whether PayPal's custom value on the disputed payment is this mandate's id and hash.
    named_on_payment: bool | None


class PolicyFacts(BaseModel):
    hash: str
    allowed_agents: list[str]
    per_order_cap: Money
    per_agent_daily_cap: Money
    blocked_categories: list[str]


class ShipmentFacts(BaseModel):
    carrier: str
    tracking_number: str
    paypal_tracker_id: str
    shipped_at: datetime


class CallFacts(BaseModel):
    action: str
    at: datetime
    status_code: int | None
    debug_id: str | None


class Facts(BaseModel):
    generated_at: datetime
    merchant: str
    dispute: DisputeFacts
    order: OrderFacts
    agent: AgentFacts | None
    mandate: MandateFacts | None
    policy: PolicyFacts | None
    shipment: ShipmentFacts | None
    paypal_calls: list[CallFacts]


@dataclass(frozen=True)
class Leaf:
    """One fact in the record: its path, such as `order.lines[0].name`, and its value as text."""

    path: str
    text: str

    def line(self) -> str:
        return f"{self.path}: {self.text}"


def with_complaint(facts: Facts, complaint: str | None) -> Facts:
    """The record with the simulator's complaint as the buyer's first message, marked as such.

    Only the triage reads it. PayPal does not hold the text, so the notes and the PDF filed
    with PayPal are written from the record without it.
    """
    if complaint is None:
        return facts
    dispute = facts.dispute.model_copy(
        update={
            "buyer_messages": [
                f"{SIMULATED} {complaint}",
                *facts.dispute.buyer_messages,
            ]
        }
    )
    return facts.model_copy(update={"dispute": dispute})


def leaves(facts: Facts) -> list[Leaf]:
    """The record as one line per fact, in the record's own order: what the narrative cites."""
    return _leaves("", facts.model_dump(mode="json"))


def record_text(facts: Facts) -> str:
    return "\n".join(leaf.line() for leaf in leaves(facts))


def _leaves(path: str, value: JsonValue) -> list[Leaf]:
    if isinstance(value, dict):
        return [
            leaf
            for key, item in value.items()
            for leaf in _leaves(f"{path}.{key}" if path else key, item)
        ]
    if isinstance(value, list):
        if not value:
            return [Leaf(path, "none")]
        return [
            leaf
            for index, item in enumerate(value)
            for leaf in _leaves(f"{path}[{index}]", item)
        ]
    return [Leaf(path, NOT_SPECIFIED if value is None else str(value))]
