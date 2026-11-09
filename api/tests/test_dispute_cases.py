"""Every labelled dispute case routes as its label says, and its record renders.

The cases double as the eval runner's input; here they run with no model on every push.
"""

import pytest

from evals.dispute_cases import CASES, Case, facts_for, route
from palisade_api.disputes.document import render
from palisade_api.disputes.facts import leaves
from palisade_api.disputes.narrative import prompt


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_the_case_routes_as_labelled(case: Case) -> None:
    assert route(case) == (case.action, case.evidence_type)


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_the_case_record_renders_and_names_the_dispute(case: Case) -> None:
    facts = facts_for(case)
    ids = case.ids()
    assert render(facts).startswith(b"%PDF-")
    paths = [leaf.path for leaf in leaves(facts)]
    assert "dispute.id" in paths
    assert ("mandate.id" in paths) is (case.mandate is not None)
    assert ("shipment.tracking_number" in paths) is case.shipped
    assert f"dispute.id: {ids['dispute']}" in prompt(facts)


def test_the_narrated_cases_are_the_agent_orders_palisade_prepares() -> None:
    narrated = [case for case in CASES if case.narrated]
    assert len(narrated) >= 10
    assert all(
        case.channel == "agent" and case.action == "prepare" for case in narrated
    )
    assert all(case.must_mention for case in narrated)
