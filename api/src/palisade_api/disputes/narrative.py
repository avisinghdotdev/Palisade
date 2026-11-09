"""The seller's notes on a dispute, written by Claude from the facts record and nothing else.

The record goes to Claude as a document of one line per fact with citations on, so every
sentence comes back with the lines it rests on. The grounding check then drops any sentence
that names an id, an amount, a date, or a hash the record does not hold.
"""

from dataclasses import dataclass
from typing import Protocol, final

from anthropic import APIConnectionError, APIStatusError, AsyncAnthropic
from anthropic.types import (
    DocumentBlockParam,
    MessageParam,
    TextBlockParam,
)

from palisade_api.disputes.facts import Facts, Leaf, leaves, record_text
from palisade_api.disputes.grounding import Span, Statement, kept_text, statements

MODEL = "claude-opus-5-5"
# PayPal takes at most this many characters of notes on a piece of evidence.
NOTES_LIMIT = 2000
# What the model is asked for, so an answer a little over still fits.
TARGET_LENGTH = 1800
# Room for the notes and for the thinking that counts toward it.
MAX_TOKENS = 8000
DOCUMENT_TITLE = "Palisade facts record"

SYSTEM = f"""You write the seller's response to a PayPal dispute for Palisade, the trust layer an online store uses when an AI shopping agent buys with the buyer's signed permission.

Rules:
- Use only the facts in the record you are given, one fact per line as `path: value`. Where the record says "Not specified", say so or leave it out; never guess or invent.
- Cite the record lines you use. Every id, amount, date, or hash you write is checked against the record, and a sentence that names one the record does not hold is removed before filing.
- Write for PayPal's dispute reviewer: plain English, short sentences, third person, no markdown, no headings, no lists.
- Say what the buyer signed and how (the mandate, its cap or exact items, the signer), how the agent was identified and verified, what was paid and when, what PayPal holds on the payment (the mandate's id and hash as the custom id), and what was shipped if anything.
- Close by asking PayPal to decide in the seller's favour, in one sentence.
- Keep the whole response under {TARGET_LENGTH} characters.
- Return the response text only, with nothing before or after it."""


@dataclass(frozen=True)
class Narrative:
    """What Claude wrote, after the grounding check: the text to file and every sentence's fate."""

    text: str
    statements: list[Statement]

    @property
    def dropped(self) -> int:
        return sum(1 for statement in self.statements if not statement.kept)


class Narrator(Protocol):
    async def write(self, facts: Facts) -> Narrative:
        """The notes for the record, at most NOTES_LIMIT characters.

        Raises:
            NarrativeUnavailable: the model could not or would not answer.
            NarrativeTooLong: the model's answer did not fit after a retry.
        """
        ...


class NarrativeUnavailable(Exception):
    def __init__(self, why: str) -> None:
        super().__init__(f"the narrative could not be written: {why}")


class NarrativeTooLong(Exception):
    def __init__(self, length: int) -> None:
        super().__init__(
            f"the narrative ran to {length} characters twice; PayPal takes {NOTES_LIMIT}"
        )


def task(facts: Facts) -> str:
    return (
        f"Write the seller's response to PayPal dispute {facts.dispute.id}, filed by the"
        f" buyer as {facts.dispute.reason}. The facts record is attached."
    )


def prompt(facts: Facts) -> str:
    """What Claude is given, as one text, kept with the evidence for audit."""
    return f"{task(facts)}\n\n{record_text(facts)}"


def request(facts: Facts, record: list[Leaf]) -> MessageParam:
    """The user turn: the record as a citable document, one block per fact, then the task."""
    document: DocumentBlockParam = {
        "type": "document",
        "title": DOCUMENT_TITLE,
        "citations": {"enabled": True},
        "source": {
            "type": "content",
            "content": [{"type": "text", "text": leaf.line()} for leaf in record],
        },
    }
    ask: TextBlockParam = {"type": "text", "text": task(facts)}
    return {"role": "user", "content": [document, ask]}


@final
class ClaudeNarrator:
    """Claude Opus 5.5 through one plain Messages call with citations, the length checked here, with one retry."""

    def __init__(self, client: AsyncAnthropic) -> None:
        self._client = client

    async def write(self, facts: Facts) -> Narrative:
        record = leaves(facts)
        messages: list[MessageParam] = [request(facts, record)]
        narrative = await self._ask(messages, record)
        if len(narrative.text) <= NOTES_LIMIT:
            return narrative
        messages.append({"role": "assistant", "content": narrative.text})
        messages.append(
            {
                "role": "user",
                "content": f"That is {len(narrative.text)} characters, and PayPal takes at"
                + f" most {NOTES_LIMIT}. Write it again in under {TARGET_LENGTH}"
                + " characters, keeping to the same facts and citing the record as before.",
            }
        )
        shorter = await self._ask(messages, record)
        if len(shorter.text) <= NOTES_LIMIT:
            return shorter
        raise NarrativeTooLong(len(shorter.text))

    async def _ask(self, messages: list[MessageParam], record: list[Leaf]) -> Narrative:
        try:
            message = await self._client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM,
                messages=messages,
                output_config={"effort": "medium"},
            )
        except (APIStatusError, APIConnectionError) as error:
            raise NarrativeUnavailable(str(error)) from error
        if message.stop_reason != "end_turn":
            raise NarrativeUnavailable(f"the model stopped with {message.stop_reason}")
        spans: list[Span] = []
        for block in message.content:
            if block.type != "text":
                continue
            sources: list[str] = []
            for citation in block.citations or []:
                if citation.type == "content_block_location":
                    sources.extend(
                        record[index].path
                        for index in range(
                            citation.start_block_index, citation.end_block_index
                        )
                        if index < len(record) and record[index].path not in sources
                    )
            spans.append(Span(block.text, sources))
        checked = statements(spans, record)
        text = kept_text(checked)
        if not text:
            raise NarrativeUnavailable("every sentence failed the grounding check")
        return Narrative(text=text, statements=checked)
