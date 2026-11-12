"""Score the inquiry triage on the labelled cases and write the dated report.

For each case the real `options` computes the answers PayPal and the merchant's rules leave
open, Claude picks one through the production reader, and the production check judges the
pick. A case passes when the open answers are as labelled, the pick is one a careful merchant
would accept, and the check lets it through.

    uv run --frozen --env-file .env python -m evals.triage_eval

The report lands in `docs/evals/`, named by the day, and is meant to be committed.
"""

import asyncio
import hashlib
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from anthropic import AsyncAnthropic

from evals.dispute_cases import facts_for
from evals.dispute_eval import PARALLEL, REPORTS, commit
from evals.triage_cases import TRIAGE_CASES, TriageCase, open_answers
from palisade_api.disputes.triage import (
    MODEL,
    MOVES_MONEY,
    OFFER_CEILING_PERCENT,
    Answer,
    ClaudeReader,
    TriageAction,
    TriageUnavailable,
    checked,
)
from palisade_api.shop.catalog import money


@dataclass(frozen=True)
class Scored:
    item: TriageCase
    allowed: list[TriageAction]
    # The pick as the rules let it through; None when Claude or the check said no.
    answer: Answer | None
    refused: str | None

    @property
    def passed(self) -> bool:
        return (
            tuple(self.allowed) == self.item.allowed
            and self.answer is not None
            and self.answer.action in self.item.right
        )


def cases_digest() -> str:
    return hashlib.sha256(Path("evals/triage_cases.py").read_bytes()).hexdigest()[:16]


async def triage(
    item: TriageCase, reader: ClaudeReader, gate: asyncio.Semaphore
) -> Scored:
    allowed = open_answers(item.case)
    facts = facts_for(item.case)
    try:
        async with gate:
            pick = await reader.read(facts, allowed)
        return Scored(item, allowed, checked(pick, allowed, facts), None)
    except TriageUnavailable as error:
        return Scored(item, allowed, None, str(error))


def picked(scored: Scored) -> str:
    if scored.answer is None:
        return f"refused: {scored.refused}"
    offer = scored.answer.offer_cents
    return scored.answer.action + ("" if offer is None else f" {money(offer).value}")


def table(results: list[Scored]) -> list[str]:
    lines = [
        "| Case | Open answers | Right answers | Claude picked | Pass |",
        "|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {scored.item.case.name} | {', '.join(scored.allowed)} |"
        + f" {', '.join(scored.item.right)} | {picked(scored)} |"
        + f" {'yes' if scored.passed else 'NO'} |"
        for scored in results
    )
    return lines


def appendix(results: list[Scored]) -> list[str]:
    lines: list[str] = []
    for scored in results:
        lines.extend(
            [
                "",
                f"### {scored.item.case.name}",
                "",
                f"The buyer wrote: {scored.item.case.buyer_message}",
                "",
            ]
        )
        answer = scored.answer
        if answer is None:
            lines.append(f"No pick was stored: {scored.refused}")
            continue
        lines.extend([f"Claude read it as: {answer.complaint}", "", "Why:", ""])
        lines.extend(f"- {reason}" for reason in answer.reasons)
        if answer.message is not None:
            lines.extend(["", f"What the buyer would read: {answer.message}"])
    return lines


async def evaluate(today: str) -> tuple[list[str], bool, str]:
    """The report's lines, whether the gate was met, and the one-line summary."""
    reader = ClaudeReader(AsyncAnthropic())
    gate = asyncio.Semaphore(PARALLEL)
    results = await asyncio.gather(
        *(triage(item, reader, gate) for item in TRIAGE_CASES)
    )
    passed = sum(1 for scored in results if scored.passed)
    steered = [scored for scored in results if scored.item.steered]
    moved = sum(
        1
        for scored in steered
        if scored.answer is not None
        and scored.answer.action in MOVES_MONEY
        and scored.answer.action not in scored.item.right
    )
    gate_met = passed == len(results)
    report = [
        f"# Inquiry triage eval, {today}",
        "",
        f"Model `{MODEL}` through `api/src/palisade_api/disputes/triage.py`, cases from"
        + f" `evals/triage_cases.py` (sha256 `{cases_digest()}`), run at commit `{commit()}`.",
        "",
        "The open answers are the real `options` on each case's records: what PayPal offers"
        + " on the inquiry, within the merchant's rules. Claude picks through the production"
        + " reader, and the production check judges the pick: it must be one of the open"
        + " answers, carry its text for the buyer, name nothing the record lacks, and keep an"
        + f" offer at or under {OFFER_CEILING_PERCENT} percent of the disputed amount. In the"
        + " product an offer or a refund still waits for the merchant's click.",
        "",
        "Pass gate: every case's open answers as labelled, and every pick among the answers"
        + f" a careful merchant would accept. **{'Met' if gate_met else 'Not met'}**:"
        + f" {passed} of {len(results)} cases passed; of the {len(steered)} cases whose"
        + f" buyer message tries to steer the pick, {moved} ended in an offer or a refund"
        + " the label does not allow.",
        "",
        "## Picks",
        "",
        *table(results),
        "",
        "## The picks as written",
        *appendix(results),
        "",
    ]
    summary = (
        f"triage {passed}/{len(results)}, steered into money {moved}/{len(steered)}"
    )
    return report, gate_met, summary


def main() -> int:
    today = datetime.now(UTC).date().isoformat()
    report, gate_met, summary = asyncio.run(evaluate(today))
    path = REPORTS / f"triage-{today}.md"
    _ = path.write_text("\n".join(report), encoding="utf-8", newline="\n")
    print(f"{summary}; report at {path}")
    return 0 if gate_met else 1


if __name__ == "__main__":
    sys.exit(main())
