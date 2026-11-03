"""The facts record behind a dispute response: what Palisade knows, and nothing it does not.

The narrative is written from this record alone, and the PDF prints it, so both say the same
thing and neither can say more than the store recorded.
"""

from datetime import datetime
from uuid import UUID
from xml.sax.saxutils import escape

from pydantic import BaseModel, JsonValue

from palisade_api.mandates.service import MandateLine
from palisade_api.orders.store import OrderLine
from palisade_api.shop.catalog import Money

MERCHANT_NAME = "Ridgeline Running"
# What the record says where the store holds nothing.
NOT_SPECIFIED = "Not specified"


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
    # Requests on this purchase whose RFC 9421 signature the store verified.
    verified_requests: int


class MandateFacts(BaseModel):
    id: UUID
    # "cart": one exact cart, signed at checkout; "intent": a cap and categories, signed up front.
    kind: str
    # "passkey" for the buyer's own passkey, "demo_signer" for the simulated signer.
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


def as_xml(facts: Facts) -> str:
    """The record as nested XML tags, the shape the narrative prompt delimits it with."""
    return "\n".join(_tags("facts", facts.model_dump(mode="json"), 0))


def _tags(name: str, value: JsonValue, depth: int) -> list[str]:
    pad = "  " * depth
    if isinstance(value, dict):
        inner = [
            line for key, item in value.items() for line in _tags(key, item, depth + 1)
        ]
        return [f"{pad}<{name}>", *inner, f"{pad}</{name}>"]
    if isinstance(value, list):
        if not value:
            return [f"{pad}<{name}>none</{name}>"]
        inner = [line for item in value for line in _tags("item", item, depth + 1)]
        return [f"{pad}<{name}>", *inner, f"{pad}</{name}>"]
    text = NOT_SPECIFIED if value is None else escape(str(value))
    return [f"{pad}<{name}>{text}</{name}>"]
