"""Palisade's answer to a dispute: the evidence it prepares, the merchant's say, and the filing.

The seller's evidence is the record of how the order was placed: the buyer's signed mandate,
the verified agent, the payment PayPal holds the mandate's hash on, and the shipment. Claude
writes the notes from that record, the record itself goes along as a PDF, and PayPal takes both
through provide-evidence once it asks the seller to respond.
"""

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, final
from uuid import UUID, uuid7

import psycopg
import structlog
from fastapi import status
from pydantic import BaseModel, JsonValue, TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload

from palisade_api.activity.models import ActivityRecord
from palisade_api.activity.store import Activities
from palisade_api.disputes.document import DOCUMENT_NAME, render
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
    with_complaint,
)
from palisade_api.disputes.grounding import Statement
from palisade_api.disputes.models import DisputeRecord, EvidenceRecord, TriageRecord
from palisade_api.disputes.narrative import Narrator, prompt
from palisade_api.disputes.service import (
    Change,
    Disputes,
    PayPalDispute,
    SyncSource,
    UnknownDispute,
)
from palisade_api.disputes.triage import (
    MAX_MESSAGES,
    Triage,
    TriageAction,
    TriageDecision,
    decision,
    due,
    messages_sent,
    options,
    rounds,
)
from palisade_api.jobs import driver_connection
from palisade_api.mandates.service import LINES
from palisade_api.orders.calls import PayPalCalls
from palisade_api.orders.models import OrderRecord, PayPalCallRecord
from palisade_api.orders.store import load_order, order_line
from palisade_api.paypal.client import Attachment, PayPalError
from palisade_api.policy.engine import FilingMode, Policies
from palisade_api.policy.models import PolicyRecord
from palisade_api.shop.catalog import money

EvidenceType = Literal["PROOF_OF_FULFILLMENT", "OTHER"]
# "prepared" awaits the merchant; "approved" files as soon as PayPal takes it.
EvidenceStatus = Literal["prepared", "approved", "filed", "refused"]
# What the console can do about the dispute now:
#   triage: an inquiry waits for the seller, and Claude has yet to pick the answer
#   confirm: the picked answer waits for the merchant, or for PayPal to take it
#   prepare: PayPal asks the seller and nothing is prepared yet
#   review: evidence is prepared and waits for the merchant to send it
#   escalate: approved evidence needs the claim stage, which PayPal offers to open
#   file: approved evidence that PayPal takes now
#   wait: approved evidence that PayPal takes nothing for yet, or nothing asked of the seller
#   merchant: nothing Palisade holds answers this dispute
#   done: filed in this stage, or decided
FilingAction = Literal[
    "triage",
    "confirm",
    "prepare",
    "review",
    "escalate",
    "file",
    "wait",
    "merchant",
    "done",
]
ESCALATE_NOTE = (
    "The order was placed by a shopping agent under a mandate the buyer signed; the record is"
    " attached once the claim takes evidence."
)
# Queues the response job for the dispute on the given connection, inside its transaction; the
# job id.
Enqueue = Callable[[psycopg.AsyncConnection[object], str], Awaitable[int]]
STATEMENTS = TypeAdapter(list[Statement])
GROUNDING = TypeAdapter(list[dict[str, JsonValue]])
# Why a picked answer waits for the merchant, and what follows once it went, by the answer.
PROPOSED: dict[str, str] = {
    "message": "Claude picked a message to the buyer. Sending it posts it in PayPal's thread"
    + " on this dispute.",
    "offer": "Claude picked a partial refund offer. An offer moves money once the buyer"
    + " accepts it, so it waits for the merchant.",
    "accept": "Claude picked a full refund. A refund moves money and cannot be undone, so it"
    + " waits for the merchant.",
}
SENT: dict[str, str] = {
    "message": f"Palisade wrote to the buyer (at most {MAX_MESSAGES} messages on one"
    + " dispute). When the buyer writes back, Claude reads the dispute again.",
    "offer": "Palisade offered the buyer a partial refund. PayPal refunds that amount and"
    + " closes the dispute if the buyer accepts.",
    "accept": "Palisade accepted the claim. PayPal refunds the buyer in full and closes the"
    + " dispute.",
}

logger = structlog.stdlib.get_logger(__name__)


class Evidence(BaseModel):
    """One answer Palisade prepared, as the console shows it; the PDF comes from its own route."""

    id: UUID
    stage: str
    evidence_type: EvidenceType
    notes: str
    prompt: str | None
    # Claude's notes sentence by sentence with the record lines each cites, and the sentences
    # the grounding check dropped; None when Palisade wrote the notes itself.
    grounding: list[Statement] | None
    document_name: str
    document_sha256: str
    status: EvidenceStatus
    request_id: str | None
    status_code: int | None
    debug_id: str | None
    prepared_at: datetime
    filed_at: datetime | None


class DisputeResponse(BaseModel):
    filing_mode: FilingMode
    action: FilingAction
    why: str
    # The inquiry's rounds of triage, oldest first.
    triage: list[TriageDecision]
    evidence: list[Evidence]


@dataclass(frozen=True)
class Document:
    name: str
    content: bytes


@dataclass(frozen=True)
class Filing:
    """What to file: the tracking in the inquiry, or the record once the dispute is a claim."""

    evidence_type: EvidenceType
    # OTHER is accepted only in the claim stage, so an inquiry is escalated first.
    needs_claim: bool


@dataclass(frozen=True)
class MerchantOnly:
    why: str


Route = Filing | MerchantOnly


@dataclass(frozen=True)
class Loaded:
    record: DisputeRecord
    order: OrderRecord
    evidence: list[EvidenceRecord]
    triage: list[TriageRecord]
    mode: FilingMode
    # Whether Palisade already escalated the inquiry: PayPal's object lags the call by minutes.
    escalated: bool


class UnknownEvidence(LookupError):
    def __init__(self, evidence_id: UUID) -> None:
        super().__init__(f"no evidence has the id {evidence_id}")


class NothingToFile(Exception):
    def __init__(self, dispute_id: str, action: FilingAction, why: str) -> None:
        super().__init__(f"nothing to file on {dispute_id} ({action}): {why}")
        self.action: FilingAction = action


class NotYetOffered(Exception):
    """PayPal has not taken the answer yet, so the job tries again later.

    A claim opens minutes after an escalation, and an answer PayPal did not acknowledge goes
    out again under the same request id.
    """

    def __init__(self, dispute_id: str, why: str) -> None:
        super().__init__(f"PayPal has not taken the answer on {dispute_id} yet: {why}")


def plan(order: OrderRecord, reason: str) -> Route:
    """What answers the dispute, from what the store holds on the order."""
    if reason == "MERCHANDISE_OR_SERVICE_NOT_RECEIVED" and order.shipment is not None:
        return Filing("PROOF_OF_FULFILLMENT", needs_claim=False)
    if order.mandate is not None:
        return Filing("OTHER", needs_claim=True)
    if reason == "MERCHANDISE_OR_SERVICE_NOT_RECEIVED":
        return MerchantOnly(
            "The buyer says nothing arrived, a person placed the order, and it is not marked"
            + " shipped, so Palisade has no tracking to file. Mark the order shipped, or"
            + " answer in PayPal's Resolution Center."
        )
    return MerchantOnly(
        "A person placed this order, so there is no signed mandate to file. The merchant"
        + " answers in PayPal's Resolution Center."
    )


def open_evidence(rows: list[EvidenceRecord]) -> EvidenceRecord | None:
    """The answer still to be filed, if any; at most one is open at a time."""
    return next(
        (row for row in reversed(rows) if row.status in ("prepared", "approved")), None
    )


def filed_in(rows: list[EvidenceRecord], stage: str) -> bool:
    return any(row.status == "filed" and row.stage == stage for row in rows)


def seller_filed(live: PayPalDispute, evidence_type: str) -> bool:
    """Whether PayPal's own list already shows this filing in this stage, record attached.

    The record is what tells it apart from the escalation note, which PayPal lists as the
    seller's OTHER evidence in the inquiry.
    """
    return any(
        item.source == "SUBMITTED_BY_SELLER"
        and item.evidence_type == evidence_type
        and item.dispute_life_cycle_stage == live.dispute_life_cycle_stage
        and any(document.name == DOCUMENT_NAME for document in item.documents)
        for item in live.evidences
    )


def seller_asked(live: PayPalDispute, filing: Filing) -> bool:
    """Whether PayPal wants the seller's answer now, or offers the step that leads to it."""
    return (
        live.status == "WAITING_FOR_SELLER_RESPONSE"
        or "provide_evidence" in live.actions
        or (filing.needs_claim and "escalate" in live.actions)
    )


def triage_options(live: PayPalDispute, loaded: Loaded) -> list[TriageAction]:
    return options(
        live,
        fightable=isinstance(plan(loaded.order, live.reason), Filing),
        messages_sent=messages_sent(loaded.triage),
    )


def triage_step(live: PayPalDispute, loaded: Loaded) -> tuple[FilingAction, str] | None:
    """What the inquiry's triage asks for now; None when the evidence path decides.

    An inquiry with evidence already prepared is being fought, whether Claude picked the fight
    or the answer was prepared before the triage existed.
    """
    if (
        live.dispute_life_cycle_stage != "INQUIRY"
        or live.dispute_channel == "ALERT"
        or loaded.evidence
    ):
        return None
    last = loaded.triage[-1] if loaded.triage else None
    if last is not None and last.status == "proposed":
        return "confirm", PROPOSED[last.action]
    if last is not None and last.status == "approved":
        return (
            "confirm",
            "Approved, and PayPal has not taken it yet. Sending it again repeats the same"
            + " request.",
        )
    if due(live, loaded.triage, triage_options(live, loaded)):
        return (
            "triage",
            "PayPal waits for the seller's answer. Claude reads the buyer's complaint and"
            + " the record and picks one: fight with the record, write to the buyer, offer"
            + " a partial refund, or refund in full.",
        )
    if last is None:
        # PayPal asks the seller minutes after an inquiry opens. Until it does, an order with
        # nothing to file is not yet the merchant's alone to answer: the triage comes first.
        if live.status != "WAITING_FOR_SELLER_RESPONSE" and isinstance(
            plan(loaded.order, live.reason), MerchantOnly
        ):
            return (
                "wait",
                "PayPal is not waiting for the seller on this inquiry now. When it is, Claude"
                + " reads the buyer's complaint and the record and picks the answer.",
            )
        return None
    if last.action == "fight":
        return None
    if last.status == "refused":
        return (
            "merchant",
            "PayPal refused this answer. The merchant answers in PayPal's Resolution"
            + " Center.",
        )
    return "wait", SENT[last.action]


def decide(live: PayPalDispute, loaded: Loaded) -> tuple[FilingAction, str]:
    """What the console can do now, and why, from PayPal's state and the stored answers."""
    route = plan(loaded.order, live.reason)
    rows, mode = loaded.evidence, loaded.mode
    if live.status == "RESOLVED":
        return "done", "PayPal has decided this dispute."
    if live.dispute_channel == "ALERT" and mode == "automatic":
        return (
            "wait",
            "A pre-chargeback alert: the buyer asked their card issuer for a refund. Palisade"
            + " refunds in full before PayPal's 20-hour window closes, which avoids the"
            + " chargeback, and files no evidence.",
        )
    triaged = triage_step(live, loaded)
    if triaged is not None:
        return triaged
    if isinstance(route, MerchantOnly):
        return "merchant", route.why
    stage = live.dispute_life_cycle_stage
    if filed_in(rows, stage):
        return (
            "done",
            "Palisade filed the evidence in this stage; PayPal is reviewing it.",
        )
    current = open_evidence(rows)
    inquiry_first = route.needs_claim and stage == "INQUIRY"
    if current is None:
        if seller_asked(live, route):
            return (
                "prepare",
                "PayPal asks the seller to respond. Palisade prepares the record and the notes"
                + (" and files them." if mode == "automatic" else " for review."),
            )
        return "wait", "PayPal asks nothing of the seller right now."
    if current.status == "prepared":
        return (
            "review",
            "The record and the notes are ready. Filing"
            + (
                " first escalates this inquiry to a claim, which takes the record a few"
                + " minutes later."
                if inquiry_first
                else " sends them to PayPal now."
            ),
        )
    if "provide_evidence" in live.actions and not inquiry_first:
        return "file", "PayPal takes the evidence now."
    if inquiry_first and "escalate" in live.actions and not loaded.escalated:
        return (
            "escalate",
            "The record is accepted only once the dispute is a claim; escalating opens one.",
        )
    return (
        "wait",
        "Approved. Palisade files it as soon as PayPal takes evidence; a claim opens a few"
        + " minutes after an escalation.",
    )


def evidence(row: EvidenceRecord) -> Evidence:
    call = row.call
    return Evidence.model_validate(
        {
            "id": row.id,
            "stage": row.stage,
            "evidence_type": row.evidence_type,
            "notes": row.notes,
            "prompt": row.prompt,
            "grounding": None
            if row.grounding is None
            else STATEMENTS.validate_python(row.grounding),
            "document_name": row.document_name,
            "document_sha256": row.document_sha256,
            "status": row.status,
            "request_id": None if call is None else call.request_id,
            "status_code": None if call is None else call.status_code,
            "debug_id": None if call is None else call.debug_id,
            "prepared_at": row.prepared_at,
            "filed_at": row.filed_at,
        }
    )


def shipment_notes(facts: Facts) -> str:
    """The notes on a shipped order the buyer placed in person, where there is no mandate to tell of."""
    shipment = facts.shipment
    if shipment is None:
        raise ValueError("shipment notes need a shipment")
    order = facts.order
    return (
        f"Order {order.number} was placed at {facts.merchant} on"
        f" {order.placed_at:%Y-%m-%d} and paid through PayPal (capture"
        f" {order.capture_id}). The merchant shipped it on {shipment.shipped_at:%Y-%m-%d}"
        f" with {shipment.carrier}, tracking number {shipment.tracking_number}, which PayPal"
        f" holds on the payment as tracker {shipment.paypal_tracker_id}. The attached record"
        " lists the order, the payment, and the shipment as the store recorded them."
    )


@final
class Responses:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        calls: PayPalCalls,
        disputes: Disputes,
        policies: Policies,
        activities: Activities,
        narrator: Narrator,
        triage: Triage,
        enqueue: Enqueue,
    ) -> None:
        self._sessions = sessions
        self._calls = calls
        self._disputes = disputes
        self._policies = policies
        self._activities = activities
        self._narrator = narrator
        self._triage = triage
        self._enqueue = enqueue

    async def schedule(self, session: AsyncSession, change: Change) -> None:
        """Queue the response job for a dispute whose state just changed, in the same transaction.

        Every change is a cue: the job reads PayPal's state and does what it offers, or nothing.
        """
        record = change.record
        if record.status == "RESOLVED":
            return
        job_id = await self._enqueue(await driver_connection(session), record.id)
        logger.info("dispute_response_queued", dispute_id=record.id, job_id=job_id)

    async def view(self, dispute_id: str) -> DisputeResponse:
        """The response as it stands, from the stored copy of the dispute; no call to PayPal.

        Raises:
            UnknownDispute: no stored dispute has the id.
        """
        loaded = await self._load(dispute_id)
        live = PayPalDispute.model_validate(loaded.record.detail)
        return self._response(loaded, live)

    async def document(self, dispute_id: str, evidence_id: UUID) -> Document:
        """The record PDF of one answer.

        Raises:
            UnknownEvidence: no answer on the dispute has the id.
        """
        async with self._sessions() as session:
            row = await session.scalar(
                select(EvidenceRecord).where(
                    EvidenceRecord.id == evidence_id,
                    EvidenceRecord.dispute_id == dispute_id,
                )
            )
        if row is None:
            raise UnknownEvidence(evidence_id)
        return Document(row.document_name, row.document)

    async def prepare(self, dispute_id: str) -> DisputeResponse:
        """Take the dispute one step on: triage an inquiry, then write the notes and the record.

        An inquiry that waits for the seller is triaged first. A picked message goes to PayPal
        at once in automatic mode; any other pick but the fight waits for the merchant. The
        notes and the record follow a picked fight, and every dispute past its inquiry.

        Nothing happens when an answer is already open or filed in this stage, when PayPal
        asks nothing of the seller, or when the merchant alone can answer, which is flagged
        once. In automatic mode the evidence is approved as it is prepared.

        Raises:
            UnknownDispute: no stored dispute has the id.
            TriageUnavailable: Claude did not deliver a pick the rules allow.
            NarrativeUnavailable, NarrativeTooLong: Claude did not deliver the notes.
            PayPalError: PayPal refused the picked answer or did not answer.
        """
        loaded = await self._load(dispute_id)
        live = PayPalDispute.model_validate(loaded.record.detail)
        action, why = decide(live, loaded)
        if action == "triage":
            await self._triage.decide(
                loaded.record,
                with_complaint(
                    await self._facts(loaded, live), loaded.record.sandbox_complaint
                ),
                triage_options(live, loaded),
                loaded.mode,
                len(loaded.triage),
            )
            loaded = await self._load(dispute_id)
            action, why = decide(live, loaded)
        if action == "confirm" and loaded.triage[-1].status == "approved":
            return await self._send(dispute_id)
        filing = plan(loaded.order, live.reason)
        if action == "merchant":
            await self._flag(loaded.record, live, why)
        if action != "prepare" or isinstance(filing, MerchantOnly):
            return self._response(loaded, live)
        facts = await self._facts(loaded, live)
        if loaded.order.mandate is None:
            asked, notes, grounding, dropped = None, shipment_notes(facts), None, None
        else:
            asked = prompt(facts)
            narrative = await self._narrator.write(facts)
            notes = narrative.text
            grounding = GROUNDING.validate_python(
                STATEMENTS.dump_python(narrative.statements, mode="json")
            )
            dropped = narrative.dropped
        document = render(facts)
        status: EvidenceStatus = (
            "approved" if loaded.mode == "automatic" else "prepared"
        )
        async with self._sessions.begin() as session:
            session.add(
                EvidenceRecord(
                    id=uuid7(),
                    dispute_id=dispute_id,
                    stage=live.dispute_life_cycle_stage,
                    evidence_type=filing.evidence_type,
                    notes=notes,
                    prompt=asked,
                    grounding=grounding,
                    document_name=DOCUMENT_NAME,
                    document=document,
                    document_sha256=hashlib.sha256(document).hexdigest(),
                    status=status,
                )
            )
            self._activities.add(
                session,
                "evidence_prepared",
                order_id=loaded.record.order_id,
                dispute_id=dispute_id,
                evidence_type=filing.evidence_type,
                status=status,
                characters=len(notes),
                dropped=dropped,
            )
        return await self.view(dispute_id)

    async def confirm(self, dispute_id: str, triage_id: UUID) -> DisputeResponse:
        """The merchant's go on the answer Claude picked: PayPal carries it out now.

        Raises:
            UnknownTriage: no answer on the dispute has the id.
            NothingToConfirm: the answer went to PayPal already, or PayPal refused it.
            PayPalError: PayPal refused the call or did not answer.
        """
        await self._triage.confirm(dispute_id, triage_id)
        return await self._send(dispute_id)

    async def file(self, dispute_id: str, evidence_id: UUID) -> DisputeResponse:
        """The merchant's go: approve the prepared answer and send what PayPal takes now.

        Raises:
            UnknownDispute, UnknownEvidence: nothing to file.
            NothingToFile: the answer was filed or refused already.
            PayPalError: PayPal refused a call or did not answer.
        """
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(EvidenceRecord)
                .where(
                    EvidenceRecord.id == evidence_id,
                    EvidenceRecord.dispute_id == dispute_id,
                )
                .with_for_update()
            )
            if row is None:
                raise UnknownEvidence(evidence_id)
            if row.status in ("filed", "refused"):
                raise NothingToFile(
                    dispute_id, "done", f"this evidence is already {row.status}"
                )
            row.status = "approved"
        return await self.submit(dispute_id)

    async def submit(self, dispute_id: str) -> DisputeResponse:
        """Send the approved answer as far as PayPal lets it go right now.

        Reads the dispute from PayPal first: evidence the seller already filed in this stage is
        recorded without a second call, an inquiry that must become a claim is escalated, and
        evidence PayPal takes is filed.

        Raises:
            UnknownDispute: no stored dispute has the id.
            PayPalError: PayPal refused a call or did not answer.
        """
        live = await self._refresh(dispute_id, "filing")
        loaded = await self._load(dispute_id)
        filing = plan(loaded.order, live.reason)
        current = open_evidence(loaded.evidence)
        if (
            current is not None
            and current.status == "approved"
            and isinstance(filing, Filing)
            and seller_filed(live, current.evidence_type)
        ):
            await self._mark_filed(
                current.id, live.dispute_life_cycle_stage, repeat=True
            )
            return await self.view(dispute_id)
        action, _ = decide(live, loaded)
        if action == "escalate":
            await self._escalate(loaded.record, live)
            live = await self._refresh(dispute_id, "filing")
        elif action == "file" and current is not None and isinstance(filing, Filing):
            await self._file(loaded, current, filing, live)
            live = await self._refresh(dispute_id, "filing")
        return self._response(await self._load(dispute_id), live)

    async def respond(self, dispute_id: str) -> DisputeResponse:
        """What the job does on every cue: triage, prepare, then file whatever is approved.

        PayPal's refusal of a picked answer is on the timeline already and ends the run.

        Raises:
            NotYetOffered: PayPal did not answer, or an approved answer waits for PayPal to
                take evidence; retried later.
            TriageUnavailable, NarrativeUnavailable: Claude did not deliver; retried later.
        """
        try:
            response = await self.prepare(dispute_id)
        except PayPalError as error:
            if error.outcome_unknown:
                raise NotYetOffered(dispute_id, str(error)) from error
            return await self.view(dispute_id)
        if open_evidence_status(response) == "approved":
            response = await self.submit(dispute_id)
        if response.action in ("wait", "escalate") and (
            open_evidence_status(response) == "approved"
        ):
            raise NotYetOffered(dispute_id, response.why)
        return response

    async def _load(self, dispute_id: str) -> Loaded:
        async with self._sessions() as session:
            record = await session.get(DisputeRecord, dispute_id)
            if record is None:
                raise UnknownDispute(dispute_id)
            order = await load_order(session, record.order_id)
            rows = (
                await session.scalars(
                    select(EvidenceRecord)
                    .options(joinedload(EvidenceRecord.call))
                    .where(EvidenceRecord.dispute_id == dispute_id)
                    .order_by(EvidenceRecord.id)
                )
            ).all()
            triage = await rounds(session, dispute_id)
            policy = await self._policies.current(session)
            escalated = await session.scalar(
                select(PayPalCallRecord.id).where(
                    PayPalCallRecord.dispute_id == dispute_id,
                    PayPalCallRecord.action == "escalate",
                    PayPalCallRecord.status_code == status.HTTP_200_OK,
                )
            )
        return Loaded(
            record,
            order,
            list(rows),
            triage,
            policy.dispute_filing,
            escalated is not None,
        )

    async def _refresh(self, dispute_id: str, source: SyncSource) -> PayPalDispute:
        """PayPal's dispute now, stored like any other read."""
        live, raw = await self._disputes.fetch(dispute_id)
        async with self._sessions.begin() as session:
            change = await self._disputes.upsert(session, live, raw)
            await self._disputes.record(session, change, source)
        return live

    async def _send(self, dispute_id: str) -> DisputeResponse:
        """Carry out the approved triage answer, then store the dispute as PayPal holds it after."""
        await self._triage.carry_out(dispute_id)
        live = await self._refresh(dispute_id, "triage")
        return self._response(await self._load(dispute_id), live)

    def _response(self, loaded: Loaded, live: PayPalDispute) -> DisputeResponse:
        action, why = decide(live, loaded)
        return DisputeResponse(
            filing_mode=loaded.mode,
            action=action,
            why=why,
            triage=[decision(row) for row in loaded.triage],
            evidence=[evidence(row) for row in loaded.evidence],
        )

    async def _flag(self, record: DisputeRecord, live: PayPalDispute, why: str) -> None:
        """Note once per stage that the merchant must answer this dispute themselves."""
        stage = live.dispute_life_cycle_stage
        async with self._sessions.begin() as session:
            flagged = await session.scalars(
                select(ActivityRecord.details).where(
                    ActivityRecord.dispute_id == record.id,
                    ActivityRecord.kind == "dispute_flagged",
                )
            )
            if all(details.get("stage") != stage for details in flagged):
                self._activities.add(
                    session,
                    "dispute_flagged",
                    order_id=record.order_id,
                    dispute_id=record.id,
                    stage=stage,
                    why=why,
                )

    async def _escalate(self, record: DisputeRecord, live: PayPalDispute) -> None:
        body: dict[str, JsonValue] = {"note": ESCALATE_NOTE}
        async with self._sessions.begin() as session:
            call = self._calls.add(
                session, record.order_id, "escalate", body, dispute_id=record.id
            )
            call_id, request_id = call.id, call.request_id
        _ = await self._calls.post(
            call_id, f"/v1/customer/disputes/{record.id}/escalate", body, request_id
        )
        await self._activities.record(
            "dispute_escalated",
            order_id=record.order_id,
            dispute_id=record.id,
            from_stage=live.dispute_life_cycle_stage,
        )

    async def _file(
        self,
        loaded: Loaded,
        current: EvidenceRecord,
        plan: Filing,
        live: PayPalDispute,
    ) -> None:
        item: dict[str, JsonValue] = {
            "evidence_type": plan.evidence_type,
            "notes": current.notes,
        }
        shipment = loaded.order.shipment
        if plan.evidence_type == "PROOF_OF_FULFILLMENT" and shipment is not None:
            item["evidence_info"] = {
                "tracking_info": [
                    {
                        "carrier_name": shipment.carrier,
                        "tracking_number": shipment.tracking_number,
                    }
                ]
            }
        body: dict[str, JsonValue] = {"evidences": [item]}
        record = loaded.record
        async with self._sessions.begin() as session:
            call = self._calls.add(
                session, record.order_id, "provide_evidence", body, dispute_id=record.id
            )
            row = await session.get_one(EvidenceRecord, current.id)
            row.call_id = call.id
            call_id, request_id = call.id, call.request_id
        try:
            _ = await self._calls.post(
                call_id,
                f"/v1/customer/disputes/{record.id}/provide-evidence",
                body,
                request_id,
                Attachment(current.document_name, current.document, "application/pdf"),
            )
        except PayPalError as error:
            async with self._sessions.begin() as session:
                row = await session.get_one(EvidenceRecord, current.id)
                row.status = "refused"
                self._activities.add(
                    session,
                    "evidence_refused",
                    order_id=record.order_id,
                    dispute_id=record.id,
                    evidence_type=plan.evidence_type,
                    issues=", ".join(error.issues),
                    debug_id=error.debug_id,
                )
            raise
        await self._mark_filed(current.id, live.dispute_life_cycle_stage, repeat=False)

    async def _mark_filed(self, evidence_id: UUID, stage: str, *, repeat: bool) -> None:
        async with self._sessions.begin() as session:
            row = await session.get_one(
                EvidenceRecord, evidence_id, options=[joinedload(EvidenceRecord.call)]
            )
            dispute = await session.get_one(DisputeRecord, row.dispute_id)
            row.status = "filed"
            row.stage = stage
            row.filed_at = datetime.now(UTC)
            self._activities.add(
                session,
                "evidence_filed",
                order_id=dispute.order_id,
                dispute_id=row.dispute_id,
                evidence_type=row.evidence_type,
                stage=stage,
                document_sha256=row.document_sha256,
                debug_id=None if row.call is None else row.call.debug_id,
                already_on_file=repeat,
            )

    async def _facts(self, loaded: Loaded, live: PayPalDispute) -> Facts:
        record, order = loaded.record, loaded.order
        mandate = order.mandate
        agent = order.agent
        policy = None
        if order.policy_hash is not None:
            async with self._sessions() as session:
                policy = await session.scalar(
                    select(PolicyRecord).where(PolicyRecord.hash == order.policy_hash)
                )
        verified = sum(
            1
            for row in await self._activities.for_order(order.number)
            if row.kind in ("signature_accepted", "checkout_accepted")
        )
        return Facts(
            generated_at=datetime.now(UTC),
            merchant=MERCHANT_NAME,
            dispute=DisputeFacts(
                id=record.id,
                reason=live.reason,
                stage=live.dispute_life_cycle_stage,
                status=live.status,
                channel=live.dispute_channel,
                amount=live.dispute_amount,
                opened_at=live.create_time,
                buyer_messages=[
                    message.content
                    for message in live.messages
                    if message.posted_by == "BUYER"
                ],
            ),
            order=OrderFacts(
                number=order.number,
                placed_at=order.created_at,
                channel=order.channel,
                status=order.status,
                total=money(order.total_cents),
                lines=[order_line(line) for line in order.lines],
                paypal_order_id=order.paypal_order_id,
                capture_id=order.capture_id,
                custom_id=order.custom_id,
            ),
            agent=None
            if agent is None
            else AgentFacts(
                id=agent.id,
                name=agent.name,
                public_key=agent.public_key,
                signature_scheme=SIGNATURE_SCHEME,
                verified_requests=verified,
            ),
            mandate=None
            if mandate is None
            else MandateFacts(
                id=mandate.id,
                kind=mandate.kind,
                token_format=MANDATE_FORMAT,
                signed_by=None
                if mandate.signed_by is None
                else SIGNERS[mandate.signed_by],
                signed_at=mandate.signed_at,
                cap=money(mandate.cap_cents),
                categories=list(mandate.categories),
                lines=LINES.validate_python(mandate.lines),
                token_hash=mandate.token_hash,
                policy_hash=mandate.policy_hash,
                expires_at=mandate.expires_at,
                named_on_payment=None
                if order.custom_id is None
                else record.custom == order.custom_id,
            ),
            policy=None
            if policy is None
            else PolicyFacts(
                hash=policy.hash,
                allowed_agents=list(policy.allowed_agents),
                per_order_cap=money(policy.per_order_cap_cents),
                per_agent_daily_cap=money(policy.per_agent_daily_cap_cents),
                blocked_categories=list(policy.blocked_categories),
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
                    action=call.action,
                    at=call.started_at,
                    status_code=call.status_code,
                    debug_id=call.debug_id,
                )
                for call in order.calls
                if call.dispute_id is None
            ],
        )


def open_evidence_status(response: DisputeResponse) -> EvidenceStatus | None:
    """The status of the answer still to be filed, if the response has one."""
    return next(
        (
            row.status
            for row in reversed(response.evidence)
            if row.status in ("prepared", "approved")
        ),
        None,
    )
