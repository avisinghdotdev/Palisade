"""Claude's notes are kept only where they rest on the record: the grounding check and the call that cites it."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx2
import pytest
from anthropic import AsyncAnthropic
from pydantic import BaseModel, JsonValue

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
    leaves,
)
from palisade_api.disputes.grounding import (
    Span,
    hard_tokens,
    kept_text,
    statements,
)
from palisade_api.disputes.narrative import (
    DOCUMENT_TITLE,
    NOTES_LIMIT,
    ClaudeNarrator,
    NarrativeUnavailable,
)
from palisade_api.mandates.service import MandateLine
from palisade_api.orders.store import OrderLine
from palisade_api.shop.catalog import money

pytestmark = pytest.mark.anyio

MANDATE_ID = UUID("0192f3a0-7b1c-7a00-8000-000000000001")
HASH = "ab" * 32
CAPTURE = "CAP-7F3K2Q"
OPENED = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)


class Citation(BaseModel):
    type: str
    start_block_index: int
    end_block_index: int


class RequestDocument(BaseModel):
    type: str
    title: str | None = None
    citations: dict[str, bool] | None = None
    source: dict[str, JsonValue]


class RequestMessage(BaseModel):
    role: str
    content: str | list[dict[str, JsonValue]]


class ClaudeRequest(BaseModel):
    messages: list[RequestMessage]


def facts() -> Facts:
    line = MandateLine(
        product_id="nike-free-rn-flyknit",
        size="10",
        quantity=1,
        name="Nike Free RN Flyknit",
        unit_price=money(12_000),
    )
    return Facts(
        generated_at=OPENED + timedelta(minutes=5),
        merchant=MERCHANT_NAME,
        dispute=DisputeFacts(
            id="PP-R-TST-00000001",
            reason="MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
            stage="INQUIRY",
            status="WAITING_FOR_SELLER_RESPONSE",
            channel="INTERNAL",
            amount=money(12_000),
            opened_at=OPENED,
            buyer_messages=["The shoes are not the model I asked for."],
        ),
        order=OrderFacts(
            number="RR-CVQYZP",
            placed_at=OPENED - timedelta(days=2),
            channel="agent",
            status="captured",
            total=money(12_000),
            lines=[
                OrderLine(
                    product_id=line.product_id,
                    size=line.size,
                    quantity=1,
                    brand="Nike",
                    name="Free RN Flyknit",
                    unit_price=money(12_000),
                    line_total=money(12_000),
                )
            ],
            paypal_order_id="PP-ORDER-1",
            capture_id=CAPTURE,
            custom_id=f"{MANDATE_ID}:{HASH}",
        ),
        agent=AgentFacts(
            id="palisade-shopper",
            name="Ridgeline Assistant",
            public_key="PUBLIC-KEY",
            signature_scheme=SIGNATURE_SCHEME,
            verified_requests=4,
        ),
        mandate=MandateFacts(
            id=MANDATE_ID,
            kind="cart",
            token_format=MANDATE_FORMAT,
            signed_by=SIGNERS["demo_signer"],
            signed_at=OPENED - timedelta(days=2, minutes=1),
            cap=money(12_000),
            categories=[],
            lines=[line],
            token_hash=HASH,
            policy_hash="cd" * 32,
            expires_at=OPENED - timedelta(days=2) + timedelta(minutes=14),
            named_on_payment=True,
        ),
        policy=PolicyFacts(
            hash="cd" * 32,
            allowed_agents=["palisade-shopper", "palisade-mcp"],
            per_order_cap=money(100_000_000),
            per_agent_daily_cap=money(100_000_000),
            blocked_categories=[],
        ),
        shipment=None,
        paypal_calls=[
            CallFacts(
                action="create",
                at=OPENED - timedelta(days=2),
                status_code=201,
                debug_id="debug-create",
            )
        ],
    )


def test_hard_tokens_are_ids_amounts_dates_and_hashes() -> None:
    assert hard_tokens(
        f"Order RR-CVQYZP for 120.00 USD on 2026-11-01, capture {CAPTURE}, hash {HASH}; 2 pairs"
        + " in 15 minutes."
    ) == ["RR-CVQYZP", "120.00", "2026-11-01", CAPTURE, HASH]


def test_sentences_keep_their_sources_and_are_dropped_for_what_the_record_lacks() -> (
    None
):
    record = leaves(facts())
    spans = [
        Span("The buyer signed a cart mandate ", ["mandate.kind"]),
        Span(f"with the id {MANDATE_ID}. ", ["mandate.id"]),
        Span("The order total was 999.00 USD. ", []),
        Span(f"PayPal holds capture {CAPTURE}. ", []),
        Span("The seller asks PayPal to decide in its favour.", []),
    ]

    result = statements(spans, record)

    # The cited lines come first; the lines that hold the id follow, found by the check.
    assert [(row.kept, row.sources) for row in result] == [
        (True, ["mandate.kind", "mandate.id", "order.custom_id"]),
        (False, []),
        (True, ["order.capture_id"]),
        (True, []),
    ]
    assert result[1].dropped_because == "999.00 is not in the record"
    assert kept_text(result) == (
        f"The buyer signed a cart mandate with the id {MANDATE_ID}."
        + f" PayPal holds capture {CAPTURE}."
        + " The seller asks PayPal to decide in its favour."
    )


def cited(text: str, record_index: int | None) -> dict[str, JsonValue]:
    block: dict[str, JsonValue] = {"type": "text", "text": text}
    if record_index is not None:
        block["citations"] = [
            {
                "type": "content_block_location",
                "cited_text": "a record line",
                "document_index": 0,
                "document_title": DOCUMENT_TITLE,
                "start_block_index": record_index,
                "end_block_index": record_index + 1,
            }
        ]
    return block


def claude_answer(
    content: list[dict[str, JsonValue]], stop_reason: str = "end_turn"
) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 900, "output_tokens": 500},
        },
    )


def narrator(
    answers: list[httpx2.Response], requests: list[httpx2.Request]
) -> ClaudeNarrator:
    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return answers.pop(0)

    return ClaudeNarrator(
        AsyncAnthropic(
            api_key="test",
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
        )
    )


async def test_the_record_is_sent_as_a_citable_document_and_cited_sentences_are_kept() -> (
    None
):
    record = leaves(facts())
    paths = [leaf.path for leaf in record]
    requests: list[httpx2.Request] = []
    answers = [
        claude_answer(
            [
                cited("The buyer signed a cart mandate ", paths.index("mandate.kind")),
                cited(f"with the id {MANDATE_ID}. ", paths.index("mandate.id")),
                cited("The order total was 999.00 USD. ", None),
                cited("The seller asks PayPal to decide in its favour.", None),
            ]
        )
    ]

    narrative = await narrator(answers, requests).write(facts())

    assert narrative.text == (
        f"The buyer signed a cart mandate with the id {MANDATE_ID}."
        " The seller asks PayPal to decide in its favour."
    )
    assert narrative.dropped == 1
    assert [row.sources for row in narrative.statements] == [
        ["mandate.kind", "mandate.id", "order.custom_id"],
        [],
        [],
    ]
    [request] = requests
    [message] = ClaudeRequest.model_validate_json(request.content).messages
    assert isinstance(message.content, list)
    document = RequestDocument.model_validate(message.content[0])
    assert (document.type, document.title, document.citations) == (
        "document",
        DOCUMENT_TITLE,
        {"enabled": True},
    )
    content = document.source["content"]
    assert isinstance(content, list)
    assert len(content) == len(record)
    assert content[paths.index("order.custom_id")] == {
        "type": "text",
        "text": f"order.custom_id: {MANDATE_ID}:{HASH}",
    }
    assert message.content[1]["type"] == "text"


async def test_claude_is_asked_again_once_when_the_notes_run_long() -> None:
    requests: list[httpx2.Request] = []
    answers = [
        claude_answer([cited("Long. " * (NOTES_LIMIT // 5), None)]),
        claude_answer([cited("Short enough.", None)]),
    ]

    narrative = await narrator(answers, requests).write(facts())

    assert narrative.text == "Short enough."
    [first, second] = requests
    assert len(ClaudeRequest.model_validate_json(first.content).messages) == 1
    retry = ClaudeRequest.model_validate_json(second.content).messages
    assert [message.role for message in retry] == ["user", "assistant", "user"]
    assert isinstance(retry[2].content, str)
    assert str(NOTES_LIMIT) in retry[2].content


async def test_a_refusal_and_an_answer_with_nothing_grounded_leave_the_notes_unwritten() -> (
    None
):
    with pytest.raises(NarrativeUnavailable):
        _ = await narrator([claude_answer([], "refusal")], []).write(facts())
    with pytest.raises(NarrativeUnavailable, match="grounding"):
        _ = await narrator(
            [claude_answer([cited("The order came to 999.00 USD.", None)])], []
        ).write(facts())
