"""The rules every agent purchase must obey, written as checks over any policy and any purchase.

Each check states one rule the merchant's policy engine must keep for every input Hypothesis
can draw. `test_policy_properties.py` runs them against the real engine; `test_policy_mutants.py`
breaks one rule in the engine at a time and checks that a case is found which these notice.
"""

from collections.abc import Callable

from hypothesis import strategies as st

from palisade_api.policy.engine import Policy, Purchase, Rules
from palisade_api.shop.catalog import CATEGORIES

Evaluate = Callable[[Policy, Purchase], list[str]]
Case = tuple[Policy, Purchase]
Check = Callable[[Evaluate, Policy, Purchase], None]

AGENTS = ("palisade-shopper", "palisade-mcp", "other-agent", "stranger")
KNOWN_REASONS = frozenset(
    {"AGENT_NOT_ALLOWED", "ORDER_CAP_EXCEEDED", "AGENT_DAILY_CAP_EXCEEDED"}
)
CENTS = st.integers(min_value=0, max_value=200_000)

rules = st.builds(
    Rules,
    allowed_agents=st.lists(st.sampled_from(AGENTS), unique=True),
    per_order_cap_cents=CENTS,
    per_agent_daily_cap_cents=CENTS,
    blocked_categories=st.lists(st.sampled_from(CATEGORIES), unique=True),
)
policies = st.builds(
    Policy,
    rules=rules,
    hash=st.just("policy-under-test"),
    dispute_filing=st.sampled_from(["review", "automatic"]),
)
purchases = st.builds(
    Purchase,
    agent_id=st.sampled_from(AGENTS),
    total_cents=st.integers(min_value=1, max_value=200_000),
    categories=st.sets(st.sampled_from(CATEGORIES)),
    spent_cents_today=CENTS,
)
cases = st.tuples(policies, purchases)


def within(policy: Policy, purchase: Purchase) -> Purchase:
    """The purchase bent to fit every rule of the policy, so the engine has nothing to refuse."""
    policy_rules = policy.rules
    agent = policy_rules.allowed_agents[0] if policy_rules.allowed_agents else "nobody"
    room = min(
        policy_rules.per_order_cap_cents,
        policy_rules.per_agent_daily_cap_cents - purchase.spent_cents_today,
    )
    return Purchase(
        agent_id=agent,
        total_cents=max(1, min(purchase.total_cents, room)),
        categories=purchase.categories - set(policy_rules.blocked_categories),
        spent_cents_today=purchase.spent_cents_today,
    )


def check_a_purchase_inside_every_rule_passes(
    evaluate: Evaluate, policy: Policy, purchase: Purchase
) -> None:
    fitted = within(policy, purchase)
    if not policy.rules.allowed_agents or fitted.total_cents > min(
        policy.rules.per_order_cap_cents,
        policy.rules.per_agent_daily_cap_cents - fitted.spent_cents_today,
    ):
        return
    assert evaluate(policy, fitted) == []


def check_the_order_cap_refuses_only_what_is_over_it(
    evaluate: Evaluate, policy: Policy, purchase: Purchase
) -> None:
    over = purchase.total_cents > policy.rules.per_order_cap_cents
    assert ("ORDER_CAP_EXCEEDED" in evaluate(policy, purchase)) is over


def check_the_daily_cap_counts_what_was_spent_today(
    evaluate: Evaluate, policy: Policy, purchase: Purchase
) -> None:
    over = (
        purchase.spent_cents_today + purchase.total_cents
        > policy.rules.per_agent_daily_cap_cents
    )
    assert ("AGENT_DAILY_CAP_EXCEEDED" in evaluate(policy, purchase)) is over


def check_only_a_listed_agent_may_buy(
    evaluate: Evaluate, policy: Policy, purchase: Purchase
) -> None:
    listed = purchase.agent_id in policy.rules.allowed_agents
    assert ("AGENT_NOT_ALLOWED" in evaluate(policy, purchase)) is not listed


def check_every_blocked_category_is_named_once(
    evaluate: Evaluate, policy: Policy, purchase: Purchase
) -> None:
    named = [
        reason
        for reason in evaluate(policy, purchase)
        if reason.startswith("CATEGORY_BLOCKED:")
    ]
    blocked = purchase.categories & set(policy.rules.blocked_categories)
    assert sorted(named) == sorted(f"CATEGORY_BLOCKED:{name}" for name in blocked)


def check_a_bigger_total_is_never_refused_less(
    evaluate: Evaluate, policy: Policy, purchase: Purchase
) -> None:
    bigger = purchase.model_copy(update={"total_cents": purchase.total_cents + 1})
    assert set(evaluate(policy, purchase)) <= set(evaluate(policy, bigger))


def check_nothing_outside_the_rules_is_said(
    evaluate: Evaluate, policy: Policy, purchase: Purchase
) -> None:
    for reason in evaluate(policy, purchase):
        assert reason in KNOWN_REASONS or reason.startswith("CATEGORY_BLOCKED:")


CHECKS: tuple[Check, ...] = (
    check_a_purchase_inside_every_rule_passes,
    check_the_order_cap_refuses_only_what_is_over_it,
    check_the_daily_cap_counts_what_was_spent_today,
    check_only_a_listed_agent_may_buy,
    check_every_blocked_category_is_named_once,
    check_a_bigger_total_is_never_refused_less,
    check_nothing_outside_the_rules_is_said,
)


def holds(evaluate: Evaluate, case: Case) -> bool:
    """Whether every check passes for the case under the given engine."""
    policy, purchase = case
    try:
        for check in CHECKS:
            check(evaluate, policy, purchase)
    except AssertionError:
        return False
    return True
