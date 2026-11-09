"""Labelled disputes: what the store holds, what PayPal says, and what Palisade must do.

Each case is one dispute as the response job meets it. The label is the action the console
must offer and the evidence type Palisade files, which `route` checks against the real
routing code with nothing faked but the records. The agent cases also carry what the
narrative must say, for the eval runner that has Claude write it.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid5

from palisade_api.disputes.facts import (
    MANDATE_FORMAT,
    MERCHANT_NAME,
    SIGNATURE_SCHEME,
    SIGNERS,
    AgentFacts,
    CallFacts,
    DisputeFacts,
    Facts,
    MandateFacts,
    OrderFacts,
    PolicyFacts,
    ShipmentFacts,
)
from palisade_api.disputes.models import DisputeRecord, EvidenceRecord
from palisade_api.disputes.responses import (
    EvidenceStatus,
    EvidenceType,
    Filing,
    FilingAction,
    Loaded,
    decide,
    plan,
)
from palisade_api.disputes.service import (
    DisputeChannel,
    DisputeReason,
    DisputeStage,
    DisputeStatus,
    PayPalDispute,
)
from palisade_api.mandates.models import MandateRecord
from palisade_api.mandates.service import MandateLine
from palisade_api.orders.models import OrderRecord, ShipmentRecord
from palisade_api.orders.store import Channel, OrderLine
from palisade_api.policy.engine import FilingMode
from palisade_api.shop.catalog import money

NAMESPACE = UUID("6f1c5d2a-0000-4000-8000-00000000e7a1")
OPENED = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
PLACED = OPENED - timedelta(days=3)
AGENT_ID = "palisade-shopper"
AGENT_NAME = "Ridgeline Assistant"
AGENT_KEY = "k1v5mD1XoY2kq1a0h0t4t1c6f1c5d2a0b8e3a9c7d6f5e4b3a2c1d0e9f8a7b6c5="
POLICY_HASH = "3f8a" * 16
TRACKING = "9400100000000000000001"
TOTAL_CENTS = 12_000
LINE = MandateLine(
    product_id="nike-free-rn-flyknit",
    size="10",
    quantity=1,
    name="Nike Free RN Flyknit",
    unit_price=money(TOTAL_CENTS),
)
SELLER_ASKED = "WAITING_FOR_SELLER_RESPONSE"
MandateKind = Literal["cart", "intent"]
Signer = Literal["passkey", "demo_signer"]


@dataclass(frozen=True)
class Case:
    name: str
    reason: DisputeReason
    channel: Channel
    stage: DisputeStage
    status: DisputeStatus
    # What the console must offer, and what Palisade files when it prepares an answer.
    action: FilingAction
    evidence_type: EvidenceType | None
    actions: tuple[str, ...] = ()
    mandate: MandateKind | None = None
    signer: Signer | None = None
    shipped: bool = False
    mode: FilingMode = "review"
    dispute_channel: DisputeChannel = "INTERNAL"
    outcome: str | None = None
    buyer_message: str = "The shoes are not the model I asked for."
    # An answer already on the dispute, by status, in the dispute's stage.
    existing: EvidenceStatus | None = None
    escalated: bool = False
    # What the narrative must say, beyond the ids the record carries.
    must_mention: tuple[str, ...] = field(default_factory=tuple)

    @property
    def narrated(self) -> bool:
        """Whether Claude writes this case's notes: an agent order Palisade prepares an answer for."""
        return self.action == "prepare" and self.mandate is not None

    def ids(self) -> dict[str, str]:
        """Stable ids per case, so a result can be read back against the case by name."""
        root = uuid5(NAMESPACE, self.name)
        return {
            "dispute": f"PP-R-EVL-{root.int % 10**8:08d}",
            "order": f"RR-{root.hex[:6].upper()}",
            "paypal_order": f"PP-ORDER-{root.hex[6:12].upper()}",
            "capture": f"CAP-{root.hex[12:18].upper()}",
            "mandate": str(uuid5(root, "mandate")),
            "token_hash": uuid5(root, "token").hex * 2,
        }


CASES: tuple[Case, ...] = (
    Case(
        "cart mandate by passkey, not as described, inquiry asks the seller",
        "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
        "agent",
        "INQUIRY",
        SELLER_ASKED,
        "prepare",
        "OTHER",
        actions=("escalate", "send_message", "accept_claim"),
        mandate="cart",
        signer="passkey",
        must_mention=("passkey", "mandate"),
    ),
    Case(
        "cart mandate by the demo signer, not as described, inquiry asks the seller",
        "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
        "agent",
        "INQUIRY",
        SELLER_ASKED,
        "prepare",
        "OTHER",
        actions=("escalate", "send_message", "accept_claim"),
        mandate="cart",
        signer="demo_signer",
        must_mention=("demo signer", "mandate"),
    ),
    Case(
        "intent mandate by passkey, not as described, inquiry asks the seller",
        "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
        "agent",
        "INQUIRY",
        SELLER_ASKED,
        "prepare",
        "OTHER",
        actions=("escalate", "send_message"),
        mandate="intent",
        signer="passkey",
        buyer_message="I never agreed to these exact shoes.",
        must_mention=("passkey", "cap", "mandate"),
    ),
    Case(
        "cart mandate, not received, shipped: the tracking goes in the inquiry",
        "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        "agent",
        "INQUIRY",
        SELLER_ASKED,
        "prepare",
        "PROOF_OF_FULFILLMENT",
        actions=("provide_evidence", "escalate"),
        mandate="cart",
        signer="passkey",
        shipped=True,
        buyer_message="Nothing has arrived.",
        must_mention=(TRACKING, "USPS", "mandate"),
    ),
    Case(
        "cart mandate, not received, not shipped: the mandate record is all there is",
        "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        "agent",
        "INQUIRY",
        SELLER_ASKED,
        "prepare",
        "OTHER",
        actions=("escalate",),
        mandate="cart",
        signer="demo_signer",
        buyer_message="Nothing has arrived.",
        must_mention=("mandate", "shipment"),
    ),
    Case(
        "a person's order, not received, shipped: tracking, written by Palisade",
        "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        "human",
        "INQUIRY",
        SELLER_ASKED,
        "prepare",
        "PROOF_OF_FULFILLMENT",
        actions=("provide_evidence",),
        shipped=True,
        buyer_message="Nothing has arrived.",
    ),
    Case(
        "a person's order, not received, not shipped: the merchant answers",
        "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        "human",
        "INQUIRY",
        SELLER_ASKED,
        "merchant",
        None,
        actions=("provide_evidence",),
        buyer_message="Nothing has arrived.",
    ),
    Case(
        "a person's order, not as described: the merchant answers",
        "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
        "human",
        "INQUIRY",
        SELLER_ASKED,
        "merchant",
        None,
        actions=("escalate",),
    ),
    Case(
        "a person's order, paid by other means: the merchant answers",
        "PAYMENT_BY_OTHER_MEANS",
        "human",
        "CHARGEBACK",
        SELLER_ASKED,
        "merchant",
        None,
        actions=("provide_evidence",),
        buyer_message="I paid for this by card already.",
    ),
    Case(
        "cart mandate by passkey, unauthorised, claim asks the seller",
        "UNAUTHORISED",
        "agent",
        "CHARGEBACK",
        SELLER_ASKED,
        "prepare",
        "OTHER",
        actions=("provide_evidence",),
        mandate="cart",
        signer="passkey",
        buyer_message="I did not make this purchase.",
        must_mention=("passkey", "signed", "mandate"),
    ),
    Case(
        "intent mandate by the demo signer, incorrect amount, claim asks the seller",
        "INCORRECT_AMOUNT",
        "agent",
        "CHARGEBACK",
        SELLER_ASKED,
        "prepare",
        "OTHER",
        actions=("provide_evidence",),
        mandate="intent",
        signer="demo_signer",
        buyer_message="I was charged more than I allowed.",
        must_mention=("cap", "120.00", "mandate"),
    ),
    Case(
        "cart mandate, duplicate transaction, inquiry under review: nothing asked",
        "DUPLICATE_TRANSACTION",
        "agent",
        "INQUIRY",
        "UNDER_REVIEW",
        "wait",
        None,
        mandate="cart",
        signer="passkey",
        buyer_message="I was charged twice.",
    ),
    Case(
        "cart mandate, not as described, inquiry open: nothing asked yet",
        "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
        "agent",
        "INQUIRY",
        "OPEN",
        "wait",
        None,
        mandate="cart",
        signer="passkey",
    ),
    Case(
        "cart mandate, resolved for the seller: done",
        "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
        "agent",
        "CHARGEBACK",
        "RESOLVED",
        "done",
        None,
        mandate="cart",
        signer="passkey",
        outcome="RESOLVED_SELLER_FAVOUR",
    ),
    Case(
        "cart mandate, pre-chargeback alert, automatic mode: the refund answers it",
        "UNAUTHORISED",
        "agent",
        "INQUIRY",
        SELLER_ASKED,
        "wait",
        None,
        actions=("provide_evidence",),
        mandate="cart",
        signer="passkey",
        mode="automatic",
        dispute_channel="ALERT",
        buyer_message="I do not recognise this charge.",
    ),
    Case(
        "cart mandate, pre-chargeback alert, review mode: the record is prepared",
        "UNAUTHORISED",
        "agent",
        "INQUIRY",
        SELLER_ASKED,
        "prepare",
        "OTHER",
        actions=("provide_evidence",),
        mandate="cart",
        signer="passkey",
        dispute_channel="ALERT",
        buyer_message="I do not recognise this charge.",
        must_mention=("passkey", "mandate"),
    ),
    Case(
        "intent mandate by passkey, not received, shipped: tracking in the inquiry",
        "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        "agent",
        "INQUIRY",
        SELLER_ASKED,
        "prepare",
        "PROOF_OF_FULFILLMENT",
        actions=("provide_evidence", "escalate"),
        mandate="intent",
        signer="passkey",
        shipped=True,
        buyer_message="Nothing has arrived.",
        must_mention=(TRACKING, "cap", "mandate"),
    ),
    Case(
        "cart mandate by the demo signer, credit not processed, inquiry asks the seller",
        "CREDIT_NOT_PROCESSED",
        "agent",
        "INQUIRY",
        SELLER_ASKED,
        "prepare",
        "OTHER",
        actions=("escalate",),
        mandate="cart",
        signer="demo_signer",
        buyer_message="I returned the shoes and got no refund.",
        must_mention=("demo signer", "mandate"),
    ),
    Case(
        "cart mandate by passkey, reason other, inquiry asks the seller",
        "OTHER",
        "agent",
        "INQUIRY",
        SELLER_ASKED,
        "prepare",
        "OTHER",
        actions=("escalate", "send_message"),
        mandate="cart",
        signer="passkey",
        buyer_message="Something else is wrong with this order.",
        must_mention=("passkey", "mandate"),
    ),
    Case(
        "evidence prepared, inquiry: the merchant reviews it",
        "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
        "agent",
        "INQUIRY",
        SELLER_ASKED,
        "review",
        None,
        actions=("escalate",),
        mandate="cart",
        signer="passkey",
        existing="prepared",
    ),
    Case(
        "evidence approved, inquiry offers escalation: escalate",
        "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
        "agent",
        "INQUIRY",
        SELLER_ASKED,
        "escalate",
        None,
        actions=("escalate",),
        mandate="cart",
        signer="passkey",
        existing="approved",
    ),
    Case(
        "evidence approved, escalated already: wait for the claim",
        "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
        "agent",
        "INQUIRY",
        SELLER_ASKED,
        "wait",
        None,
        actions=("escalate",),
        mandate="cart",
        signer="passkey",
        existing="approved",
        escalated=True,
    ),
    Case(
        "evidence approved, claim takes evidence: file",
        "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
        "agent",
        "CHARGEBACK",
        SELLER_ASKED,
        "file",
        None,
        actions=("provide_evidence",),
        mandate="cart",
        signer="passkey",
        existing="approved",
    ),
    Case(
        "evidence filed in the claim: done",
        "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
        "agent",
        "CHARGEBACK",
        "UNDER_REVIEW",
        "done",
        None,
        mandate="cart",
        signer="passkey",
        existing="filed",
    ),
)


def order_for(case: Case) -> OrderRecord:
    """The order as the response job loads it, built in memory from the case."""
    ids = case.ids()
    order = OrderRecord(
        id=uuid5(NAMESPACE, ids["order"]),
        number=ids["order"],
        channel=case.channel,
        agent_id=AGENT_ID if case.channel == "agent" else None,
        policy_hash=POLICY_HASH if case.channel == "agent" else None,
        status="captured",
        currency="USD",
        total_cents=TOTAL_CENTS,
        paypal_order_id=ids["paypal_order"],
        capture_id=ids["capture"],
        custom_id=f"{ids['mandate']}:{ids['token_hash']}"
        if case.mandate is not None
        else None,
    )
    order.created_at = PLACED
    if case.mandate is not None:
        order.mandate = MandateRecord(
            id=UUID(ids["mandate"]),
            kind=case.mandate,
            agent_id=AGENT_ID,
            cap_cents=TOTAL_CENTS if case.mandate == "cart" else 30_000,
            currency="USD",
            categories=["Road"] if case.mandate == "intent" else [],
            lines=[LINE.model_dump(mode="json")] if case.mandate == "cart" else [],
            policy_hash=POLICY_HASH,
            token="unused-in-the-eval",  # noqa: S106
            token_hash=ids["token_hash"],
            key_hash="unused-in-the-eval",
            status="signed",
            signed_by=case.signer,
            signed_at=PLACED - timedelta(minutes=1),
            expires_at=PLACED
            + (
                timedelta(minutes=14) if case.mandate == "cart" else timedelta(hours=23)
            ),
        )
    if case.shipped:
        order.shipment = ShipmentRecord(
            carrier="USPS",
            tracking_number=TRACKING,
            paypal_tracker_id=f"{ids['capture']}-{TRACKING}",
            shipped_at=PLACED + timedelta(days=1),
        )
    return order


def live_for(case: Case) -> PayPalDispute:
    """PayPal's dispute object for the case, in the sandbox's shape."""
    ids = case.ids()
    href = f"https://api-m.sandbox.paypal.com/v1/customer/disputes/{ids['dispute']}"
    return PayPalDispute.model_validate(
        {
            "dispute_id": ids["dispute"],
            "create_time": OPENED.isoformat(),
            "update_time": (OPENED + timedelta(minutes=3)).isoformat(),
            "disputed_transactions": [
                {
                    "seller_transaction_id": ids["capture"],
                    "custom": f"{ids['mandate']}:{ids['token_hash']}"
                    if case.mandate is not None
                    else None,
                }
            ],
            "reason": case.reason,
            "status": case.status,
            "dispute_amount": money(TOTAL_CENTS).model_dump(),
            "dispute_life_cycle_stage": case.stage,
            "dispute_channel": case.dispute_channel,
            "dispute_outcome": None
            if case.outcome is None
            else {"outcome_code": case.outcome},
            "messages": [
                {
                    "posted_by": "BUYER",
                    "time_posted": OPENED.isoformat(),
                    "content": case.buyer_message,
                }
            ],
            "links": [
                {"rel": "self", "href": href, "method": "GET"},
                *(
                    {"rel": rel, "href": f"{href}/{rel}", "method": "POST"}
                    for rel in case.actions
                ),
            ],
        }
    )


def loaded_for(case: Case) -> Loaded:
    """What the response job has in hand for the case, with any answer already on it."""
    ids = case.ids()
    order = order_for(case)
    record = DisputeRecord(id=ids["dispute"], order_id=order.id, status=case.status)
    rows: list[EvidenceRecord] = []
    if case.existing is not None:
        rows.append(
            EvidenceRecord(
                dispute_id=ids["dispute"],
                stage=case.stage,
                evidence_type="OTHER",
                notes="",
                document_name="palisade-record.pdf",
                document=b"",
                document_sha256="",
                status=case.existing,
            )
        )
    return Loaded(record, order, rows, case.mode, case.escalated)


def route(case: Case) -> tuple[FilingAction, EvidenceType | None]:
    """What the real routing code decides for the case: the console's action and the filing."""
    loaded = loaded_for(case)
    action, _ = decide(live_for(case), loaded)
    filing = plan(loaded.order, case.reason)
    evidence_type = filing.evidence_type if isinstance(filing, Filing) else None
    return action, evidence_type if action == "prepare" else None


def facts_for(case: Case) -> Facts:
    """The facts record for the case, as the response job would assemble it."""
    ids = case.ids()
    order = order_for(case)
    mandate = order.mandate
    return Facts(
        generated_at=OPENED + timedelta(minutes=4),
        merchant=MERCHANT_NAME,
        dispute=DisputeFacts(
            id=ids["dispute"],
            reason=case.reason,
            stage=case.stage,
            status=case.status,
            channel=case.dispute_channel,
            amount=money(TOTAL_CENTS),
            opened_at=OPENED,
            buyer_messages=[case.buyer_message],
        ),
        order=OrderFacts(
            number=order.number,
            placed_at=PLACED,
            channel=case.channel,
            status="captured",
            total=money(TOTAL_CENTS),
            lines=[
                OrderLine(
                    product_id=LINE.product_id,
                    size=LINE.size,
                    quantity=LINE.quantity,
                    brand="Nike",
                    name="Free RN Flyknit",
                    unit_price=LINE.unit_price,
                    line_total=money(TOTAL_CENTS),
                )
            ],
            paypal_order_id=order.paypal_order_id,
            capture_id=order.capture_id,
            custom_id=order.custom_id,
        ),
        agent=None
        if case.channel == "human"
        else AgentFacts(
            id=AGENT_ID,
            name=AGENT_NAME,
            public_key=AGENT_KEY,
            signature_scheme=SIGNATURE_SCHEME,
            verified_requests=5,
        ),
        mandate=None
        if mandate is None or case.signer is None
        else MandateFacts(
            id=mandate.id,
            kind=mandate.kind,
            token_format=MANDATE_FORMAT,
            signed_by=SIGNERS[case.signer],
            signed_at=mandate.signed_at,
            cap=money(mandate.cap_cents),
            categories=list(mandate.categories),
            lines=[LINE] if case.mandate == "cart" else [],
            token_hash=mandate.token_hash,
            policy_hash=POLICY_HASH,
            expires_at=mandate.expires_at,
            named_on_payment=True,
        ),
        policy=None
        if case.channel == "human"
        else PolicyFacts(
            hash=POLICY_HASH,
            allowed_agents=["palisade-shopper", "palisade-mcp"],
            per_order_cap=money(100_000_000),
            per_agent_daily_cap=money(100_000_000),
            blocked_categories=[],
        ),
        shipment=None
        if order.shipment is None
        else ShipmentFacts(
            carrier=order.shipment.carrier,
            tracking_number=order.shipment.tracking_number,
            paypal_tracker_id=order.shipment.paypal_tracker_id,
            shipped_at=order.shipment.shipped_at,
        ),
        paypal_calls=[
            CallFacts(
                action="create", at=PLACED, status_code=201, debug_id="eval-create"
            ),
            CallFacts(
                action="capture",
                at=PLACED + timedelta(seconds=2),
                status_code=201,
                debug_id="eval-capture",
            ),
        ],
    )
