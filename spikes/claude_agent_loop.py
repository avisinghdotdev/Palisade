"""Spike S9: Claude as the shop agent over stub tools, and the evidence narrative under 2,000 characters.

The agent gets five tools that mirror the planned MCP surface (search, product, cart, checkout,
order status). Checkout never captures: it hands back a consent link for the human to sign the
mandate. The narrative call turns a structured facts record into Resolution Center notes.

Run from the repo root: uv run --frozen --env-file .env --with anthropic python spikes/claude_agent_loop.py
"""

import json
import sys
import uuid
from typing import Any

import anthropic
from anthropic import beta_tool

# The plan's default model for Palisade (section 5).
MODEL = "claude-sonnet-5-5"

CATALOG = {
    "RR-TRAIL-10": {
        "name": "Ridgeline Trail Runner, size 10",
        "price": "98.00",
        "category": "running shoes",
    },
    "RR-ROAD-10": {
        "name": "Ridgeline Road Racer, size 10",
        "price": "139.00",
        "category": "running shoes",
    },
    "RR-SOCK-M": {
        "name": "Merino running socks, M",
        "price": "18.00",
        "category": "socks",
    },
}
carts: dict[str, dict[str, Any]] = {}
checkouts: dict[str, dict[str, Any]] = {}
calls: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        sys.exit(f"FAILED: {message}")
    print(f"  ok: {message}")


@beta_tool
def search_products(query: str) -> str:
    """Search the Ridgeline Running catalog.

    Args:
        query: Words from the shopper's request, for example "trail shoes size 10".
    """
    calls.append("search_products")
    words = query.lower().split()
    hits = [
        {"sku": sku, **item}
        for sku, item in CATALOG.items()
        if any(word in f"{item['name']} {item['category']}".lower() for word in words)
    ]
    return json.dumps(hits or [{"sku": sku, **item} for sku, item in CATALOG.items()])


@beta_tool
def get_product(sku: str) -> str:
    """Full details for one product.

    Args:
        sku: The product's SKU from search results.
    """
    calls.append("get_product")
    item = CATALOG.get(sku)
    return json.dumps({"sku": sku, **item} if item else {"error": "unknown sku"})


@beta_tool
def create_cart(sku: str, quantity: int) -> str:
    """Create a cart with one line.

    Args:
        sku: The product to buy.
        quantity: How many.
    """
    calls.append("create_cart")
    item = CATALOG[sku]
    total = f"{float(item['price']) * quantity:.2f}"
    cart_id = f"cart_{uuid.uuid4().hex[:8]}"
    carts[cart_id] = {"lines": [{"sku": sku, "quantity": quantity}], "total": total}
    return json.dumps(
        {"cart_id": cart_id, "total": {"currency_code": "USD", "value": total}}
    )


@beta_tool
def request_checkout(cart_id: str) -> str:
    """Ask the human to sign the mandate for this cart. Payment is never taken here.

    Args:
        cart_id: The cart to check out.
    """
    calls.append("request_checkout")
    mandate_id = f"01JAX{uuid.uuid4().hex[:21].upper()}"
    checkouts[cart_id] = {"mandate_id": mandate_id, "status": "awaiting_signature"}
    return json.dumps(
        {
            "status": "awaiting_signature",
            "mandate_id": mandate_id,
            "consent_url": f"https://palisade.avisingh.dev/shop/approve/{mandate_id}",
            "note": "The human signs with their passkey; the order is created after that.",
        }
    )


@beta_tool
def get_order_status(mandate_id: str) -> str:
    """Status of the order behind a mandate.

    Args:
        mandate_id: The mandate id returned by request_checkout.
    """
    calls.append("get_order_status")
    return json.dumps({"mandate_id": mandate_id, "status": "awaiting_signature"})


AGENT_SYSTEM = (
    "You are Palisade Shopper, a registered shopping agent at Ridgeline Running. Find what the "
    "human asked for inside their budget, create the cart, then call request_checkout and give "
    "the human the consent link. You never take payment yourself. Reply in three short sentences."
)

NARRATIVE_SYSTEM = (
    "You write the seller's evidence notes for a PayPal dispute. Use only the facts in the "
    "record you are given; invent nothing. Plain English, no headings, at most 1,800 characters."
)


def agent_run(client: anthropic.Anthropic) -> None:
    print("\n[1] agent loop: find trail shoes under 120 USD and request checkout")
    runner = client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=4096,
        system=AGENT_SYSTEM,
        tools=[
            search_products,
            get_product,
            create_cart,
            request_checkout,
            get_order_status,
        ],
        messages=[
            {
                "role": "user",
                "content": "Buy me trail running shoes, size 10, and keep it under 120 USD.",
            }
        ],
    )
    final = None
    for message in runner:
        final = message
        for block in message.content:
            if block.type == "tool_use":
                print(f"  tool call {block.name}({json.dumps(block.input)})")
    check(
        final is not None and final.stop_reason == "end_turn",
        "the loop ended on end_turn",
    )
    text = "".join(block.text for block in final.content if block.type == "text")
    print(f"  final reply: {text.strip()[:300]}")
    check("request_checkout" in calls, "the agent asked the human to sign")
    check(len(checkouts) == 1, "exactly one checkout requested")
    cart = next(iter(carts.values()))
    check(
        float(cart["total"]) <= 120, f"cart total {cart['total']} stays under the cap"
    )
    check(
        "consent_url" in text or "approve" in text,
        "the reply hands over the consent link",
    )
    print(f"  usage: {final.usage.input_tokens} in, {final.usage.output_tokens} out")


def narrative(client: anthropic.Anthropic) -> None:
    print("\n[2] evidence narrative from a facts record")
    facts = {
        "dispute": {
            "id": "PP-R-SAA-10193650",
            "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        },
        "order": {
            "id": "0G073714EJ2097419",
            "capture_id": "2ER22717L6947533R",
            "amount": "95.00 USD",
        },
        "mandate": {
            "id": "01JAX3K9V8Q2M7R4T6W8Y0Z1AB",
            "signed_at": "2026-10-09T00:07:40Z",
            "signer": "buyer passkey, verified",
            "cart": "Ridgeline Trail Runner, size 10, quantity 1",
            "amount_cap": "120.00 USD",
            "hash_in_custom_id": True,
        },
        "agent": {
            "name": "Palisade Shopper",
            "tier": "verified",
            "signature": "RFC 9421, valid",
        },
        "policy": {"spend_cap_checked": True, "challenge_required": False},
        "shipping": {
            "carrier": "USPS",
            "tracking_number": "9400111899223197428490",
            "status": "delivered 2026-10-14",
        },
    }
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=NARRATIVE_SYSTEM,
        messages=[{"role": "user", "content": json.dumps(facts, indent=2)}],
    )
    check(response.stop_reason == "end_turn", "the narrative ended on end_turn")
    text = "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()
    print(f"  {len(text)} characters:\n{text}")
    check(len(text) <= 2000, "narrative fits PayPal's 2,000-character note limit")
    check("9400111899223197428490" in text, "the tracking number made it in")
    check("01JAX3K9V8Q2M7R4T6W8Y0Z1AB" in text, "the mandate id made it in")
    print(
        f"  usage: {response.usage.input_tokens} in, {response.usage.output_tokens} out"
    )


if __name__ == "__main__":
    client = anthropic.Anthropic()
    agent_run(client)
    narrative(client)
    print("\nAgent loop and narrative both work.")
