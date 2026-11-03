"""The seller's notes on a dispute, written by Claude from the facts record and nothing else."""

from typing import Protocol, final

from anthropic import APIConnectionError, APIStatusError, AsyncAnthropic
from anthropic.types import MessageParam

from palisade_api.disputes.facts import Facts, as_xml

MODEL = "claude-opus-5-5"
# PayPal takes at most this many characters of notes on a piece of evidence.
NOTES_LIMIT = 2000
# What the model is asked for, so an answer a little over still fits.
TARGET_LENGTH = 1800
# Room for the notes and for the thinking that counts toward it.
MAX_TOKENS = 8000

SYSTEM = f"""You write the seller's response to a PayPal dispute for Palisade, the trust layer an online store uses when an AI shopping agent buys with the buyer's signed permission.

Rules:
- Use only the facts in the record you are given. Where the record says "Not specified", say so or leave it out; never guess or invent.
- Write for PayPal's dispute reviewer: plain English, short sentences, third person, no markdown, no headings, no lists.
- Say what the buyer signed and how (the mandate, its cap or exact items, the signer), how the agent was identified and verified, what was paid and when, what PayPal holds on the payment (the mandate's id and hash as the custom id), and what was shipped if anything.
- Close by asking PayPal to decide in the seller's favour, in one sentence.
- Keep the whole response under {TARGET_LENGTH} characters.
- Return the response text only, with nothing before or after it."""


class Narrator(Protocol):
    async def write(self, prompt: str) -> str:
        """The notes for `prompt`, at most NOTES_LIMIT characters.

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


def prompt(facts: Facts) -> str:
    """The request to the model: the task, then the record in its own tags."""
    return (
        f"Write the seller's response to PayPal dispute {facts.dispute.id}, filed by the"
        f" buyer as {facts.dispute.reason}. The facts record follows.\n\n{as_xml(facts)}"
    )


@final
class ClaudeNarrator:
    """Claude Opus 5.5 through one plain Messages call, the length checked here, with one retry."""

    def __init__(self, client: AsyncAnthropic) -> None:
        self._client = client

    async def write(self, prompt: str) -> str:
        messages: list[MessageParam] = [{"role": "user", "content": prompt}]
        text = await self._ask(messages)
        if len(text) <= NOTES_LIMIT:
            return text
        messages.append({"role": "assistant", "content": text})
        messages.append(
            {
                "role": "user",
                "content": f"That is {len(text)} characters, and PayPal takes at most"
                + f" {NOTES_LIMIT}. Write it again in under {TARGET_LENGTH} characters,"
                + " keeping to the same facts.",
            }
        )
        shorter = await self._ask(messages)
        if len(shorter) <= NOTES_LIMIT:
            return shorter
        raise NarrativeTooLong(len(shorter))

    async def _ask(self, messages: list[MessageParam]) -> str:
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
        return "".join(
            block.text for block in message.content if block.type == "text"
        ).strip()
