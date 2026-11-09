"""Mutation check on the policy engine: break one rule at a time and expect the properties to notice.

Each mutant is a one-line change to the source of `evaluate`, the kind of slip a refactor makes:
a comparison flipped, a term dropped, a rule removed. Hypothesis then searches for a purchase on
which the mutant and the properties in `policy_properties.py` disagree. A mutant nobody can
catch would mean a rule the tests do not really check.
"""

import inspect
import textwrap
from typing import cast

import pytest
from hypothesis import find, settings
from hypothesis.errors import NoSuchExample
from policy_properties import Case, Evaluate, cases, holds

from palisade_api.policy.engine import Policy, Purchase, evaluate

# How the engine is broken, as a replacement in its source: (what is changed, what it becomes).
MUTANTS = {
    "an unknown agent is let through": (
        "purchase.agent_id not in rules.allowed_agents",
        "purchase.agent_id in rules.allowed_agents",
    ),
    "the order cap refuses the cap itself": (
        "purchase.total_cents > rules.per_order_cap_cents",
        "purchase.total_cents >= rules.per_order_cap_cents",
    ),
    "the order cap is never applied": (
        "purchase.total_cents > rules.per_order_cap_cents",
        "False",
    ),
    "today's spend is forgotten": (
        "purchase.spent_cents_today + purchase.total_cents",
        "purchase.total_cents",
    ),
    "the daily cap refuses the cap itself": (
        "> rules.per_agent_daily_cap_cents",
        ">= rules.per_agent_daily_cap_cents",
    ),
    "the daily cap is never applied": (
        "> rules.per_agent_daily_cap_cents",
        "> rules.per_agent_daily_cap_cents + 10**9",
    ),
    "the allowed categories are reported as blocked": (
        "purchase.categories & set(rules.blocked_categories)",
        "purchase.categories - set(rules.blocked_categories)",
    ),
    "only the first blocked category is reported": (
        "for category in sorted(purchase.categories & set(rules.blocked_categories))",
        "for category in sorted(purchase.categories & set(rules.blocked_categories))[:1]",
    ),
}
SEARCH = settings(max_examples=1000, database=None)


def compiled(source: str) -> Evaluate:
    """An engine built from source text; exec gives back untyped objects, so the type is asserted."""
    namespace: dict[str, object] = {"Policy": Policy, "Purchase": Purchase}
    exec(source, namespace)  # noqa: S102
    engine = namespace["evaluate"]
    assert callable(engine)
    return cast("Evaluate", engine)


def mutant(old: str, new: str) -> Evaluate:
    """The engine with one line changed, compiled from its own source."""
    source = textwrap.dedent(inspect.getsource(evaluate))
    assert source.count(old) == 1, f"the mutation site {old!r} must appear exactly once"
    return compiled(source.replace(old, new))


def test_the_unchanged_engine_is_not_a_mutant() -> None:
    rebuilt = compiled(textwrap.dedent(inspect.getsource(evaluate)))

    def disagrees(case: Case) -> bool:
        return not holds(rebuilt, case)

    with pytest.raises(NoSuchExample):
        find(cases, disagrees, settings=SEARCH)


@pytest.mark.parametrize(("old", "new"), MUTANTS.values(), ids=MUTANTS.keys())
def test_every_mutant_is_caught(old: str, new: str) -> None:
    broken = mutant(old, new)

    def disagrees(case: Case) -> bool:
        return not holds(broken, case)

    policy, purchase = find(cases, disagrees, settings=SEARCH)
    assert evaluate(policy, purchase) != broken(policy, purchase)
