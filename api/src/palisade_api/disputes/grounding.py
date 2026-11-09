"""The check between what Claude wrote and what the record holds, sentence by sentence.

Claude cites the record's lines as it writes, and the check adds the lines that hold each
hard value a sentence names: an id, an amount, a date, a hash. A sentence that names a value
the record does not hold is dropped before anything is filed, and kept with the reason, so
the console can show what was removed and why.
"""

import re
from dataclasses import dataclass

from pydantic import BaseModel

from palisade_api.disputes.facts import Leaf

# A word with a digit in it, a hyphenated code in capitals (an order number such as RR-CVQYZP),
# or a hex hash: the kinds of thing a narrative can only know from the record.
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9.,:/-]*")
CODE = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$")
HEX = re.compile(r"^[0-9a-f]{16,}$")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# How long a token must be before the check names the record lines that hold it.
DERIVED_TOKEN_LENGTH = 5


class Statement(BaseModel):
    """One sentence of the narrative and the record lines it rests on."""

    text: str
    # Paths of the record lines Claude cited for this sentence, such as `order.custom_id`.
    sources: list[str]
    kept: bool
    dropped_because: str | None


@dataclass(frozen=True)
class Span:
    """A piece of Claude's answer and the record lines cited for it."""

    text: str
    sources: list[str]


def hard_tokens(text: str) -> list[str]:
    """The words in `text` that must come from the record.

    A small whole number on its own ("two items" written as 2, "15 minutes") is left alone:
    it is prose, and the ids, amounts, dates, and hashes are what a reviewer checks.
    """
    tokens: list[str] = []
    for match in WORD.finditer(text):
        word = match.group().rstrip(".,:/")
        if word.isdigit() and len(word) <= 2:
            continue
        if any(char.isdigit() for char in word) or CODE.match(word) or HEX.match(word):
            tokens.append(word)
    return tokens


def in_record(token: str, record: list[Leaf]) -> bool:
    wanted = token.lower()
    return any(wanted in leaf.text.lower() for leaf in record)


def statements(spans: list[Span], record: list[Leaf]) -> list[Statement]:
    """Claude's answer as sentences, each judged against the record.

    A sentence's sources are the lines Claude cited for the spans it overlaps, plus the lines
    that hold each id, amount, date, or hash it names, found by the check itself. It is dropped
    when it names something the record does not hold anywhere.
    """
    text = "".join(span.text for span in spans)
    bounds: list[tuple[int, int, list[str]]] = []
    position = 0
    for span in spans:
        bounds.append((position, position + len(span.text), span.sources))
        position += len(span.text)
    result: list[Statement] = []
    start = 0
    for sentence in SENTENCE_END.split(text):
        if not sentence.strip():
            start += len(sentence)
            continue
        begin = text.index(sentence, start)
        end = begin + len(sentence)
        start = end
        sources: list[str] = []
        for span_start, span_end, span_sources in bounds:
            if span_start < end and span_end > begin:
                sources.extend(path for path in span_sources if path not in sources)
        result.append(_judge(sentence.strip(), sources, record))
    return result


def kept_text(result: list[Statement]) -> str:
    return " ".join(statement.text for statement in result if statement.kept)


def _judge(sentence: str, cited: list[str], record: list[Leaf]) -> Statement:
    tokens = hard_tokens(sentence)
    missing = [token for token in tokens if not in_record(token, record)]
    sources = list(cited)
    for token in tokens:
        # A short token ("2026", "10") sits in too many lines to name one; the long ones do.
        if len(token) < DERIVED_TOKEN_LENGTH:
            continue
        sources.extend(
            leaf.path
            for leaf in record
            if token.lower() in leaf.text.lower() and leaf.path not in sources
        )
    why = (
        f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not in the record"
        if missing
        else None
    )
    return Statement(
        text=sentence, sources=sources, kept=why is None, dropped_because=why
    )
