"""Score the dispute pipeline on the labelled cases and write the dated report.

Two parts. Routing runs the real `plan` and `decide` on every case with nothing faked but the
records, and must match every label. The narrative part has Claude write the notes for each
agent case through the production narrator, with citations and the grounding check, and
scores what came back: length, what it had to mention, the closing request, and how many
sentences the check dropped.

    uv run --frozen --env-file .env python -m evals.dispute_eval

The report lands in `docs/evals/`, named by the day, and is meant to be committed.
"""

import asyncio
import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from anthropic import AsyncAnthropic

from evals.dispute_cases import CASES, Case, facts_for, route
from palisade_api.disputes.narrative import (
    MODEL,
    NOTES_LIMIT,
    ClaudeNarrator,
    Narrative,
)

REPORTS = Path("docs/evals")
# How many narratives Claude writes at once.
PARALLEL = 4
ASKS_PAYPAL = re.compile(r"favou?r", re.IGNORECASE)


@dataclass(frozen=True)
class Scored:
    case: Case
    narrative: Narrative
    missing: list[str]
    asks: bool

    @property
    def passed(self) -> bool:
        return (
            len(self.narrative.text) <= NOTES_LIMIT and not self.missing and self.asks
        )


def cases_digest() -> str:
    return hashlib.sha256(Path("evals/dispute_cases.py").read_bytes()).hexdigest()[:16]


def commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError, FileNotFoundError:
        return "unknown"


async def narrate(
    case: Case, narrator: ClaudeNarrator, gate: asyncio.Semaphore
) -> Scored:
    async with gate:
        narrative = await narrator.write(facts_for(case))
    lower = narrative.text.lower()
    return Scored(
        case=case,
        narrative=narrative,
        missing=[want for want in case.must_mention if want.lower() not in lower],
        asks=ASKS_PAYPAL.search(narrative.text[-250:]) is not None,
    )


def routing_table() -> tuple[list[str], int]:
    lines = [
        "| Case | Reason | Order | Stage, status | Expected | Routed | Pass |",
        "|---|---|---|---|---|---|---|",
    ]
    failures = 0
    for case in CASES:
        action, evidence_type = route(case)
        expected = f"{case.action}" + (
            f", {case.evidence_type}" if case.evidence_type else ""
        )
        routed = f"{action}" + (f", {evidence_type}" if evidence_type else "")
        ok = (action, evidence_type) == (case.action, case.evidence_type)
        failures += 0 if ok else 1
        order = (
            case.channel
            if case.mandate is None
            else f"{case.channel}, {case.mandate} mandate"
        )
        lines.append(
            f"| {case.name} | {case.reason} | {order} | {case.stage}, {case.status} |"
            + f" {expected} | {routed} | {'yes' if ok else 'NO'} |"
        )
    return lines, failures


def narrative_table(scored: list[Scored]) -> list[str]:
    lines = [
        "| Case | Characters | Sentences kept / dropped | Must mention | Asks PayPal to decide | Pass |",
        "|---|---|---|---|---|---|",
    ]
    for item in scored:
        kept = sum(1 for statement in item.narrative.statements if statement.kept)
        mention = (
            "all"
            if not item.missing
            else f"missing {', '.join(repr(want) for want in item.missing)}"
        )
        lines.append(
            f"| {item.case.name} | {len(item.narrative.text)} | {kept} / {item.narrative.dropped} |"
            + f" {mention} | {'yes' if item.asks else 'no'} | {'yes' if item.passed else 'NO'} |"
        )
    return lines


def appendix(scored: list[Scored]) -> list[str]:
    lines: list[str] = []
    for item in scored:
        lines.extend(["", f"### {item.case.name}", "", item.narrative.text])
        dropped = [
            statement for statement in item.narrative.statements if not statement.kept
        ]
        if dropped:
            lines.extend(["", "Dropped before filing:", ""])
            lines.extend(
                f"- {statement.text} ({statement.dropped_because})"
                for statement in dropped
            )
        cited = sorted(
            {
                path
                for statement in item.narrative.statements
                for path in statement.sources
            }
        )
        lines.extend(
            ["", f"Record lines cited: {', '.join(cited) if cited else 'none'}"]
        )
    return lines


async def evaluate(today: str) -> tuple[list[str], bool, str]:
    """The report's lines, whether the gate was met, and the one-line summary."""
    routing, routing_failures = routing_table()
    narrated = [case for case in CASES if case.narrated]
    narrator = ClaudeNarrator(AsyncAnthropic())
    gate = asyncio.Semaphore(PARALLEL)
    scored = await asyncio.gather(*(narrate(case, narrator, gate) for case in narrated))
    passed = sum(1 for item in scored if item.passed)
    dropped = sum(item.narrative.dropped for item in scored)
    gate_met = routing_failures == 0 and passed == len(scored)
    report = [
        f"# Dispute pipeline eval, {today}",
        "",
        f"Model `{MODEL}` through `api/src/palisade_api/disputes/narrative.py`, cases from"
        + f" `evals/dispute_cases.py` (sha256 `{cases_digest()}`), run at commit `{commit()}`.",
        "",
        "Routing is the real `plan` and `decide` on each case's records: no model, no network."
        + " Narratives are Claude's notes through the production narrator, with the record sent"
        + " as a citable document and the grounding check applied; a dropped sentence never"
        + " reaches PayPal.",
        "",
        f"Pass gate: every case routed as labelled, and every narrative under {NOTES_LIMIT}"
        + " characters, mentioning what its case requires, and closing with the request to"
        + f" PayPal. **{'Met' if gate_met else 'Not met'}**: {len(CASES) - routing_failures} of"
        + f" {len(CASES)} cases routed as labelled; {passed} of {len(scored)} narratives passed;"
        + f" {dropped} sentence{'s' if dropped != 1 else ''} dropped by the grounding check.",
        "",
        "## Routing",
        "",
        *routing,
        "",
        "## Narratives",
        "",
        *narrative_table(scored),
        "",
        "## The narratives as written",
        *appendix(scored),
        "",
    ]
    summary = (
        f"routing {len(CASES) - routing_failures}/{len(CASES)}, narratives"
        + f" {passed}/{len(scored)}, dropped {dropped}"
    )
    return report, gate_met, summary


def main() -> int:
    today = datetime.now(UTC).date().isoformat()
    report, gate_met, summary = asyncio.run(evaluate(today))
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"dispute-pipeline-{today}.md"
    path.write_text("\n".join(report), encoding="utf-8", newline="\n")
    print(f"wrote {path}")
    print(summary)
    return 0 if gate_met else 1


if __name__ == "__main__":
    sys.exit(main())
