"""Properties of the gates money passes: the merchant's rules, the signed cart, the mandate token.

Hypothesis draws the cases. Each test is one rule that must hold whatever it draws.
"""

import base64
import secrets
from uuid import uuid7

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from policy_properties import CHECKS, Case, Check, cases
from pydantic import JsonValue

from palisade_api.mandates.service import MandateLine, same_lines
from palisade_api.mandates.tokens import MandateTokens
from palisade_api.policy.engine import evaluate
from palisade_api.shop.catalog import CATEGORIES, money
from palisade_api.shop.models import CartLineRecord

# The alphabet an SD-JWT is written in: base64url for each part, "." and "~" between them.
SDJWT_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
PRODUCTS = (
    "nike-free-rn-flyknit",
    "hoka-mach-x-2",
    "brooks-ghost-16",
    "asics-gel-kayano-31",
)
SIZES = ("8", "9", "9.5", "10", "11")

lines = st.lists(
    st.builds(
        MandateLine,
        product_id=st.sampled_from(PRODUCTS),
        size=st.sampled_from(SIZES),
        quantity=st.integers(min_value=1, max_value=5),
        name=st.just("A shoe"),
        unit_price=st.just(money(12_000)),
    ),
    min_size=1,
    max_size=4,
    unique_by=lambda line: (line.product_id, line.size),
)


def seed() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()


def cart_lines(signed: list[MandateLine]) -> list[CartLineRecord]:
    return [
        CartLineRecord(
            id=uuid7(),
            product_id=line.product_id,
            size=line.size,
            quantity=line.quantity,
            brand="Brand",
            name=line.name,
            unit_price_cents=12_000,
        )
        for line in signed
    ]


@pytest.mark.parametrize("check", CHECKS, ids=[check.__name__ for check in CHECKS])
@given(case=cases)
def test_the_policy_engine_keeps_every_rule(check: Check, case: Case) -> None:
    policy, purchase = case
    check(evaluate, policy, purchase)


@given(signed=lines, data=st.data())
def test_a_cart_matches_its_signed_lines_in_any_order(
    signed: list[MandateLine], data: st.DataObject
) -> None:
    shuffled = data.draw(st.permutations(signed))
    assert same_lines(signed, cart_lines(shuffled))


@given(signed=lines, data=st.data())
def test_any_change_to_a_signed_line_breaks_the_match(
    signed: list[MandateLine], data: st.DataObject
) -> None:
    index = data.draw(st.integers(min_value=0, max_value=len(signed) - 1))
    line = signed[index]

    def with_quantity(quantity: int) -> MandateLine:
        return line.model_copy(update={"quantity": quantity})

    def with_size(size: str) -> MandateLine:
        return line.model_copy(update={"size": size})

    def with_product(product: str) -> MandateLine:
        return line.model_copy(update={"product_id": product})

    changed = data.draw(
        st.one_of(
            st.builds(
                with_quantity,
                st.integers(min_value=1, max_value=5).filter(
                    lambda quantity: quantity != line.quantity
                ),
            ),
            st.builds(
                with_size, st.sampled_from(SIZES).filter(lambda size: size != line.size)
            ),
            st.builds(
                with_product,
                st.sampled_from(PRODUCTS).filter(
                    lambda product: product != line.product_id
                ),
            ),
        )
    )
    cart = [*signed[:index], changed, *signed[index + 1 :]]
    if {(item.product_id, item.size) for item in cart} == {
        (item.product_id, item.size) for item in signed
    } and sorted(item.quantity for item in cart) == sorted(
        item.quantity for item in signed
    ):
        # A swap that lands on another signed line's product and size; the sets are equal
        # only when the quantities agree too, which same_lines rightly accepts.
        return
    assert not same_lines(signed, cart_lines(cart))


@given(signed=lines)
def test_a_cart_with_a_line_more_or_less_does_not_match(
    signed: list[MandateLine],
) -> None:
    extra = MandateLine(
        product_id="new-balance-1080",
        size="12",
        quantity=1,
        name="A shoe",
        unit_price=money(12_000),
    )
    assert not same_lines(signed, cart_lines([*signed, extra]))
    assert not same_lines(signed, cart_lines(signed[1:]))


@settings(max_examples=150)
@given(
    cap=st.integers(min_value=100, max_value=1_000_000),
    categories=st.lists(st.sampled_from(CATEGORIES), unique=True),
    signed=lines,
    data=st.data(),
)
def test_no_change_to_a_token_yields_claims_that_were_not_issued(
    cap: int, categories: list[str], signed: list[MandateLine], data: st.DataObject
) -> None:
    """A tampered token is refused, or it still says exactly what Palisade signed.

    SD-JWT lets a holder leave a disclosable claim out, so a mutation that drops a disclosure
    may still verify; what it can never do is change a claim's value.
    """
    tokens = MandateTokens(seed())
    claims: dict[str, JsonValue] = {
        "jti": str(uuid7()),
        "iss": "http://palisade.test",
        "merchant": "ridgeline-running",
        "agent": "palisade-shopper",
        "cap": money(cap).model_dump(),
        "categories": list(categories),
        "lines": [line.model_dump(mode="json") for line in signed],
        "policy_hash": "a" * 64,
        "exp": 1_900_000_000,
    }
    token = tokens.issue("cart", claims)
    assert tokens.verify(token) == {"vct": "mandate.checkout.1", **claims}
    position = data.draw(st.integers(min_value=0, max_value=len(token) - 1))
    replacement = data.draw(
        st.sampled_from(SDJWT_ALPHABET).filter(lambda char: char != token[position])
    )
    tampered = token[:position] + replacement + token[position + 1 :]

    try:
        verified = tokens.verify(tampered)
    except Exception:  # noqa: BLE001
        # Refused, in whichever way the parser or the signature check noticed: the right answer.
        return
    for name, value in verified.items():
        assert value == {"vct": "mandate.checkout.1", **claims}[name]
