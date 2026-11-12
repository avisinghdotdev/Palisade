"""Palisade's answer to a dispute: prepared for review or filed on its own, as PayPal allows."""

from collections.abc import Iterator
from typing import final
from uuid import UUID

import httpx2
import psycopg
import pytest
from dispute_fixtures import (
    DISPUTES_PATH,
    TRACKING,
    ScriptedReader,
    new_dispute_id,
    paid_order,
    paypal_dispute,
)
from paypal_stub import PayPalStub, json_answer
from pydantic import BaseModel, JsonValue
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.activity.router import activities as activities_dependency
from palisade_api.activity.store import Activities
from palisade_api.disputes.facts import Facts
from palisade_api.disputes.grounding import Statement
from palisade_api.disputes.models import DisputeRecord, TriageRecord
from palisade_api.disputes.narrative import Narrative, prompt
from palisade_api.disputes.responses import (
    DisputeResponse,
    NotYetOffered,
    Responses,
)
from palisade_api.disputes.router import disputes as disputes_dependency
from palisade_api.disputes.router import responses as responses_dependency
from palisade_api.disputes.service import Disputes
from palisade_api.disputes.tasks import enqueue_response
from palisade_api.disputes.triage import Triage
from palisade_api.main import app
from palisade_api.orders.calls import PayPalCalls
from palisade_api.orders.router import orders as orders_dependency
from palisade_api.orders.store import Order, Orders
from palisade_api.paypal.auth import PayPalAuth
from palisade_api.paypal.client import PayPalClient
from palisade_api.policy.engine import Policies
from palisade_api.policy.router import policies as policies_dependency

pytestmark = pytest.mark.anyio

NOTES = "The buyer's agent placed the order under a mandate the buyer signed."


class EvidenceItem(BaseModel):
    evidence_type: str
    notes: str
    evidence_info: dict[str, JsonValue] | None = None


class EvidenceInput(BaseModel):
    """The JSON part of a provide-evidence request, as PayPal reads it."""

    evidences: list[EvidenceItem]


@final
class ScriptedNarrator:
    """Answers every record with the same notes and keeps the prompts it was given."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def write(self, facts: Facts) -> Narrative:
        self.prompts.append(prompt(facts))
        return Narrative(
            text=NOTES,
            statements=[
                Statement(
                    text=NOTES,
                    sources=["mandate.id", "mandate.signed_by"],
                    kept=True,
                    dropped_because=None,
                ),
                Statement(
                    text="The order shipped on 2026-10-30.",
                    sources=[],
                    kept=False,
                    dropped_because="2026-10-30 is not in the record",
                ),
            ],
        )


@pytest.fixture
def paypal() -> PayPalStub:
    return PayPalStub()


@pytest.fixture
def narrator() -> ScriptedNarrator:
    return ScriptedNarrator()


@pytest.fixture
def enqueued() -> list[str]:
    return []


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
def responses(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    disputes: Disputes,
    narrator: ScriptedNarrator,
    enqueued: list[str],
) -> Responses:
    async def enqueue(
        _connection: psycopg.AsyncConnection[object], dispute_id: str
    ) -> int:
        enqueued.append(dispute_id)
        return len(enqueued)

    http = paypal.client()
    calls = PayPalCalls(sessions, PayPalClient(http, PayPalAuth(http, "id", "secret")))
    service = Responses(
        sessions,
        calls,
        disputes,
        Policies(sessions),
        Activities(sessions),
        narrator,
        # Every inquiry here is fought; the triage's other answers have their own tests.
        Triage(sessions, calls, Activities(sessions), ScriptedReader()),
        enqueue,
    )
    disputes.on_change(service.schedule)
    return service


@pytest.fixture
def console(
    sessions: async_sessionmaker[AsyncSession],
    disputes: Disputes,
    responses: Responses,
) -> Iterator[None]:
    app.dependency_overrides[disputes_dependency] = lambda: disputes
    app.dependency_overrides[responses_dependency] = lambda: responses
    app.dependency_overrides[activities_dependency] = lambda: Activities(sessions)
    app.dependency_overrides[orders_dependency] = lambda: Orders(sessions)
    app.dependency_overrides[policies_dependency] = lambda: Policies(sessions)
    yield
    app.dependency_overrides.clear()


def answer_with(
    paypal: PayPalStub, dispute_id: str, body: dict[str, JsonValue]
) -> None:
    paypal.on("GET", f"{DISPUTES_PATH}/{dispute_id}", json_answer(200, body))


async def kinds(
    sessions: async_sessionmaker[AsyncSession], dispute_id: str
) -> list[str]:
    return [row.kind for row in await Activities(sessions).for_dispute(dispute_id)]


async def filing_mode(client: httpx2.AsyncClient, mode: str) -> None:
    response = await client.put("/api/policy/dispute-filing", json={"mode": mode})
    assert response.status_code == 200


def input_part(request: httpx2.Request) -> EvidenceInput:
    """The JSON part of a provide-evidence request, checked to sit beside the record PDF."""
    content = request.content
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert b'name="evidence-file"; filename="palisade-record.pdf"' in content
    assert b"%PDF-" in content
    start = content.index(b"\r\n\r\n", content.index(b'name="input"')) + 4
    end = content.index(b"\r\n--", start)
    return EvidenceInput.model_validate_json(content[start:end])


@pytest.mark.usefixtures("console")
async def test_an_agent_dispute_is_prepared_for_review_then_escalated_and_filed(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    disputes: Disputes,
    narrator: ScriptedNarrator,
    enqueued: list[str],
    client: httpx2.AsyncClient,
    wallet_id: UUID,
) -> None:
    # The database is shared, so it holds whatever filing mode the deployed shop is in.
    await filing_mode(client, "review")
    order = await paid_order(sessions, wallet_id, agent=True)
    dispute_id = new_dispute_id()
    detail = f"{DISPUTES_PATH}/{dispute_id}"
    inquiry = paypal_dispute(
        dispute_id,
        order.capture_id,
        status="WAITING_FOR_SELLER_RESPONSE",
        custom=order.custom_id,
        actions=("escalate", "send_message", "accept_claim"),
    )
    answer_with(paypal, dispute_id, inquiry)
    _ = await disputes.sync(dispute_id, "webhook")
    _ = await disputes.sync(dispute_id, "webhook")
    assert enqueued == [dispute_id]
    async with sessions.begin() as session:
        stored = await session.get_one(DisputeRecord, dispute_id)
        stored.sandbox_complaint = "I never ordered these."

    before = DisputeResponse.model_validate_json(
        (await client.get(f"/api/disputes/{dispute_id}/response")).content
    )
    assert (before.filing_mode, before.action, before.evidence) == (
        "review",
        "triage",
        [],
    )

    prepared = DisputeResponse.model_validate_json(
        (await client.post(f"/api/disputes/{dispute_id}/response")).content
    )
    # The picked fight needs no call to PayPal, so the notes and the record follow at once.
    [picked] = prepared.triage
    assert (picked.action, picked.status, picked.options) == (
        "fight",
        "done",
        ["fight", "message", "accept"],
    )
    [evidence] = prepared.evidence
    assert (prepared.action, evidence.status, evidence.evidence_type) == (
        "review",
        "prepared",
        "OTHER",
    )
    assert evidence.notes == NOTES
    assert evidence.prompt is not None
    assert f"\norder.custom_id: {order.custom_id}\n" in evidence.prompt
    # The signer in words a reviewer reads, never the stored code.
    assert (
        "\nmandate.signed_by: The demo signer, a simulated stand-in" in evidence.prompt
    )
    assert "demo_signer" not in evidence.prompt
    assert narrator.prompts == [evidence.prompt]
    # The simulator's complaint is the triage's to read. PayPal does not hold it, so the notes
    # filed with PayPal are written without it.
    assert "I never ordered these." not in evidence.prompt
    async with sessions() as session:
        asked = await session.scalar(
            select(TriageRecord.prompt).where(TriageRecord.dispute_id == dispute_id)
        )
    assert asked is not None
    assert "[Sandbox simulator] I never ordered these." in asked
    assert evidence.grounding is not None
    assert [(row.kept, row.sources) for row in evidence.grounding] == [
        (True, ["mandate.id", "mandate.signed_by"]),
        (False, []),
    ]
    assert evidence.grounding[1].dropped_because is not None
    document = await client.get(
        f"/api/disputes/{dispute_id}/evidence/{evidence.id}/document"
    )
    assert document.headers["content-type"] == "application/pdf"
    assert document.content.startswith(b"%PDF-")
    assert len(evidence.document_sha256) == 64
    # Prepared once: a second ask changes nothing.
    again = DisputeResponse.model_validate_json(
        (await client.post(f"/api/disputes/{dispute_id}/response")).content
    )
    assert [row.id for row in again.evidence] == [evidence.id]

    paypal.on("POST", f"{detail}/escalate", json_answer(200, {"links": []}))
    escalated = DisputeResponse.model_validate_json(
        (
            await client.post(f"/api/disputes/{dispute_id}/evidence/{evidence.id}/file")
        ).content
    )
    assert (escalated.action, escalated.evidence[0].status) == ("wait", "approved")
    [sent] = paypal.sent("POST", f"{detail}/escalate")
    assert "mandate" in sent.content.decode()
    assert paypal.sent("POST", f"{detail}/provide-evidence") == []

    answer_with(
        paypal,
        dispute_id,
        paypal_dispute(
            dispute_id,
            order.capture_id,
            updated="2026-11-01T08:07:35.000Z",
            stage="CHARGEBACK",
            status="WAITING_FOR_SELLER_RESPONSE",
            custom=order.custom_id,
            actions=("provide_evidence", "send_message"),
        ),
    )
    paypal.on("POST", f"{detail}/provide-evidence", json_answer(200, {"links": []}))
    filed = DisputeResponse.model_validate_json(
        (
            await client.post(f"/api/disputes/{dispute_id}/evidence/{evidence.id}/file")
        ).content
    )
    [row] = filed.evidence
    assert (filed.action, row.status, row.stage, row.status_code) == (
        "done",
        "filed",
        "CHARGEBACK",
        200,
    )
    assert row.filed_at is not None
    [sent] = paypal.sent("POST", f"{detail}/provide-evidence")
    assert input_part(sent) == EvidenceInput(
        evidences=[EvidenceItem(evidence_type="OTHER", notes=NOTES)]
    )
    assert sent.headers["PayPal-Request-Id"] == row.request_id
    assert await kinds(sessions, dispute_id) == [
        "dispute_opened",
        "triage_decided",
        "evidence_prepared",
        "dispute_escalated",
        "dispute_updated",
        "evidence_filed",
    ]
    [prepared_row] = [
        row
        for row in await Activities(sessions).for_dispute(dispute_id)
        if row.kind == "evidence_prepared"
    ]
    assert prepared_row.details["dropped"] == 1
    shown = Order.model_validate_json(
        (await client.get(f"/api/orders/{order.number}")).content
    )
    assert [(call.action, call.dispute_id) for call in shown.calls] == [
        ("escalate", dispute_id),
        ("provide_evidence", dispute_id),
    ]
    # Filed in this stage: the job has nothing more to do.
    assert (
        await client.post(f"/api/disputes/{dispute_id}/evidence/{evidence.id}/file")
    ).status_code == 409


@pytest.mark.usefixtures("console")
async def test_automatic_mode_answers_an_alert_with_the_refund_not_evidence(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    disputes: Disputes,
    responses: Responses,
    narrator: ScriptedNarrator,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
) -> None:
    await filing_mode(client, "automatic")
    order = await paid_order(sessions, wallet_id, agent=True)
    dispute_id = new_dispute_id()
    answer_with(
        paypal,
        dispute_id,
        paypal_dispute(
            dispute_id,
            order.capture_id,
            status="WAITING_FOR_SELLER_RESPONSE",
            custom=order.custom_id,
            actions=("provide_evidence",),
            channel="ALERT",
        ),
    )
    _ = await disputes.sync(dispute_id, "webhook")

    response = await responses.respond(dispute_id)

    assert (response.action, response.evidence) == ("wait", [])
    assert "20-hour window" in response.why
    assert narrator.prompts == []
    assert paypal.sent("POST", f"{DISPUTES_PATH}/{dispute_id}/provide-evidence") == []


@pytest.mark.usefixtures("console")
async def test_automatic_mode_files_the_tracking_on_a_shipped_inquiry_from_the_job(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    disputes: Disputes,
    responses: Responses,
    narrator: ScriptedNarrator,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
) -> None:
    await filing_mode(client, "automatic")
    order = await paid_order(sessions, wallet_id, shipped=True)
    dispute_id = new_dispute_id()
    detail = f"{DISPUTES_PATH}/{dispute_id}"
    answer_with(
        paypal,
        dispute_id,
        paypal_dispute(
            dispute_id,
            order.capture_id,
            reason="MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
            status="WAITING_FOR_SELLER_RESPONSE",
            actions=("provide_evidence", "escalate"),
        ),
    )
    paypal.on("POST", f"{detail}/provide-evidence", json_answer(200, {"links": []}))
    _ = await disputes.sync(dispute_id, "webhook")

    response = await responses.respond(dispute_id)

    [row] = response.evidence
    assert (response.action, row.status, row.evidence_type, row.prompt) == (
        "done",
        "filed",
        "PROOF_OF_FULFILLMENT",
        None,
    )
    assert row.grounding is None
    assert TRACKING in row.notes
    assert narrator.prompts == []
    [sent] = paypal.sent("POST", f"{detail}/provide-evidence")
    [item] = input_part(sent).evidences
    assert item.evidence_info == {
        "tracking_info": [{"carrier_name": "USPS", "tracking_number": TRACKING}]
    }
    assert paypal.sent("POST", f"{detail}/escalate") == []
    assert await kinds(sessions, dispute_id) == [
        "dispute_opened",
        "evidence_prepared",
        "evidence_filed",
    ]


@pytest.mark.usefixtures("console")
async def test_the_job_escalates_then_waits_for_the_claim(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    disputes: Disputes,
    responses: Responses,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
) -> None:
    await filing_mode(client, "automatic")
    order = await paid_order(sessions, wallet_id, agent=True)
    dispute_id = new_dispute_id()
    detail = f"{DISPUTES_PATH}/{dispute_id}"
    answer_with(
        paypal,
        dispute_id,
        paypal_dispute(
            dispute_id,
            order.capture_id,
            status="WAITING_FOR_SELLER_RESPONSE",
            custom=order.custom_id,
            actions=("escalate",),
        ),
    )
    paypal.on("POST", f"{detail}/escalate", json_answer(200, {"links": []}))
    _ = await disputes.sync(dispute_id, "webhook")

    with pytest.raises(NotYetOffered):
        _ = await responses.respond(dispute_id)

    assert len(paypal.sent("POST", f"{detail}/escalate")) == 1
    # PayPal's object lags the escalation by minutes and lists the note as the seller's OTHER
    # evidence in the inquiry: neither a second escalation nor a filing to mistake it for.
    answer_with(
        paypal,
        dispute_id,
        paypal_dispute(
            dispute_id,
            order.capture_id,
            updated="2026-11-01T08:05:06.000Z",
            status="WAITING_FOR_SELLER_RESPONSE",
            custom=order.custom_id,
            actions=("escalate",),
            escalated=True,
        ),
    )

    with pytest.raises(NotYetOffered):
        _ = await responses.respond(dispute_id)

    assert len(paypal.sent("POST", f"{detail}/escalate")) == 1
    # The claim is open and PayPal lists the record already: a worker that died between the
    # call and its record files nothing twice.
    answer_with(
        paypal,
        dispute_id,
        paypal_dispute(
            dispute_id,
            order.capture_id,
            updated="2026-11-01T08:07:35.000Z",
            stage="CHARGEBACK",
            status="UNDER_REVIEW",
            custom=order.custom_id,
            actions=("provide_supporting_info",),
            escalated=True,
            seller_filed="OTHER",
        ),
    )

    response = await responses.respond(dispute_id)

    [row] = response.evidence
    assert (response.action, row.status, row.stage) == ("done", "filed", "CHARGEBACK")
    assert paypal.sent("POST", f"{detail}/provide-evidence") == []
    assert len(paypal.sent("POST", f"{detail}/escalate")) == 1
    [filed] = [
        row
        for row in await Activities(sessions).for_dispute(dispute_id)
        if row.kind == "evidence_filed"
    ]
    assert filed.details["already_on_file"] is True


async def test_a_change_while_a_job_waits_is_stored_and_queued_on_the_real_queue(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    disputes: Disputes,
    narrator: ScriptedNarrator,
    wallet_id: UUID,
) -> None:
    """The queue's own insert, in the storing transaction, on the database the app runs on.

    A queueing lock once refused the second job with a unique violation that silently
    aborted the transaction around it, so PayPal's newer state was never stored.
    """
    http = paypal.client()
    calls = PayPalCalls(sessions, PayPalClient(http, PayPalAuth(http, "id", "secret")))
    disputes.on_change(
        Responses(
            sessions,
            calls,
            disputes,
            Policies(sessions),
            Activities(sessions),
            narrator,
            Triage(sessions, calls, Activities(sessions), ScriptedReader()),
            enqueue_response,
        ).schedule
    )
    order = await paid_order(sessions, wallet_id, agent=True)
    dispute_id = new_dispute_id()
    answer_with(paypal, dispute_id, paypal_dispute(dispute_id, order.capture_id))
    _ = await disputes.sync(dispute_id, "webhook")
    answer_with(
        paypal,
        dispute_id,
        paypal_dispute(
            dispute_id,
            order.capture_id,
            updated="2026-11-01T08:03:36.000Z",
            stage="CHARGEBACK",
            status="WAITING_FOR_SELLER_RESPONSE",
        ),
    )

    waiting = await disputes.sync(dispute_id, "webhook")

    assert (waiting.stage, waiting.status) == (
        "CHARGEBACK",
        "WAITING_FOR_SELLER_RESPONSE",
    )
    assert await kinds(sessions, dispute_id) == ["dispute_opened", "dispute_updated"]
    async with sessions() as session:
        queued = (
            await session.execute(
                text(
                    """
                    SELECT status, lock, queueing_lock FROM procrastinate_jobs
                    WHERE args->>'dispute_id' = :dispute_id ORDER BY id
                    """
                ),
                {"dispute_id": dispute_id},
            )
        ).all()
    assert [tuple(row) for row in queued] == [
        ("todo", f"dispute:{dispute_id}", None),
        ("todo", f"dispute:{dispute_id}", None),
    ]


@pytest.mark.usefixtures("console")
async def test_a_human_order_without_tracking_is_flagged_once(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    disputes: Disputes,
    responses: Responses,
    narrator: ScriptedNarrator,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
) -> None:
    order = await paid_order(sessions, wallet_id)
    dispute_id = new_dispute_id()
    answer_with(
        paypal,
        dispute_id,
        paypal_dispute(
            dispute_id,
            order.capture_id,
            reason="MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
            status="WAITING_FOR_SELLER_RESPONSE",
            actions=("provide_evidence",),
        ),
    )
    _ = await disputes.sync(dispute_id, "webhook")

    first = await responses.respond(dispute_id)
    second = DisputeResponse.model_validate_json(
        (await client.post(f"/api/disputes/{dispute_id}/response")).content
    )

    assert (first.action, second.action) == ("merchant", "merchant")
    assert "Mark the order shipped" in first.why
    assert first.evidence == []
    assert narrator.prompts == []
    assert await kinds(sessions, dispute_id) == ["dispute_opened", "dispute_flagged"]


@pytest.mark.usefixtures("console")
async def test_evidence_paypal_refuses_is_marked_refused(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalStub,
    disputes: Disputes,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
) -> None:
    order = await paid_order(sessions, wallet_id, agent=True)
    dispute_id = new_dispute_id()
    detail = f"{DISPUTES_PATH}/{dispute_id}"
    answer_with(
        paypal,
        dispute_id,
        paypal_dispute(
            dispute_id,
            order.capture_id,
            stage="CHARGEBACK",
            status="WAITING_FOR_SELLER_RESPONSE",
            custom=order.custom_id,
            actions=("provide_evidence",),
        ),
    )
    paypal.on(
        "POST",
        f"{detail}/provide-evidence",
        json_answer(
            422,
            {
                "name": "UNPROCESSABLE_ENTITY",
                "debug_id": "debug-refused",
                "details": [{"issue": "EVIDENCE_TYPE_IS_NOT_ALLOWED"}],
            },
        ),
    )
    _ = await disputes.sync(dispute_id, "webhook")
    prepared = DisputeResponse.model_validate_json(
        (await client.post(f"/api/disputes/{dispute_id}/response")).content
    )
    [evidence] = prepared.evidence

    refused = await client.post(
        f"/api/disputes/{dispute_id}/evidence/{evidence.id}/file"
    )

    assert refused.status_code == 422
    assert "EVIDENCE_TYPE_IS_NOT_ALLOWED" in refused.text
    after = DisputeResponse.model_validate_json(
        (await client.get(f"/api/disputes/{dispute_id}/response")).content
    )
    [row] = after.evidence
    # The debug id comes from PayPal's response header, as on every call.
    assert (after.action, row.status, row.status_code, row.debug_id) == (
        "prepare",
        "refused",
        422,
        "debug-422",
    )
    assert await kinds(sessions, dispute_id) == [
        "dispute_opened",
        "evidence_prepared",
        "evidence_refused",
    ]
