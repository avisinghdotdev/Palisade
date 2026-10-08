"""Spike S2 and S3: card-funded AUTHORIZE orders in the sandbox, one captured, one voided.

Run from the repo root: uv run --frozen --env-file .env python spikes/paypal_authorize_capture.py
"""

import asyncio
import hashlib

from sandbox import PayPal, check, connect, http_client

# Visa from https://developer.paypal.com/tools/sandbox/card-testing/; any future expiry and CVV work.
TEST_CARD = {
    "name": "Pat Buyer",
    "number": "4005519200000004",
    "expiry": "2030-12",
    "security_code": "123",
    "billing_address": {
        "address_line_1": "2211 North First Street",
        "admin_area_2": "San Jose",
        "admin_area_1": "CA",
        "postal_code": "95131",
        "country_code": "US",
    },
}
AMOUNT = {"currency_code": "USD", "value": "95.00"}
MANDATE_HASH = hashlib.sha256(
    b"intent: running shoes, size 10, up to 120 USD, free returns"
).hexdigest()


async def create_authorized_order(paypal: PayPal, label: str) -> tuple[str, str]:
    order = await paypal.call(
        "POST",
        "/v2/checkout/orders",
        {
            "intent": "AUTHORIZE",
            "purchase_units": [
                {
                    "reference_id": label,
                    "custom_id": MANDATE_HASH,
                    "amount": AMOUNT,
                }
            ],
            "payment_source": {"card": TEST_CARD},
        },
        expect=201,
    )
    authorization = order["purchase_units"][0]["payments"]["authorizations"][0]
    print(f"  order {order['id']} status {order['status']}")
    print(f"  authorization {authorization['id']} status {authorization['status']}")
    check(
        order["status"] == "COMPLETED",
        f"order {label} completed without a browser step",
    )
    check(authorization["status"] == "CREATED", f"authorization {label} created")
    return order["id"], authorization["id"]


async def main() -> None:
    async with http_client() as http:
        paypal = await connect(http)

        print("\n[1] order A: create with intent AUTHORIZE and a test card")
        order_a, auth_a = await create_authorized_order(paypal, "A")

        print("\n[2] order A: read it back")
        fetched = await paypal.call("GET", f"/v2/checkout/orders/{order_a}", expect=200)
        check(
            fetched["purchase_units"][0]["custom_id"] == MANDATE_HASH,
            "custom_id comes back on the order",
        )

        print("\n[3] order A: capture the authorization")
        capture = await paypal.call(
            "POST",
            f"/v2/payments/authorizations/{auth_a}/capture",
            {"final_capture": True},
            expect=201,
        )
        print(f"  capture {capture['id']} status {capture['status']}")
        check(capture["status"] == "COMPLETED", "capture completed")
        check(
            capture.get("custom_id") == MANDATE_HASH,
            "custom_id comes back on the capture",
        )

        print(
            "\n[4] order A: voiding a captured authorization must be refused with 422"
        )
        refusal = await paypal.call(
            "POST", f"/v2/payments/authorizations/{auth_a}/void", expect=422
        )
        print(f"  refusal: {refusal['name']}, {refusal['details'][0]['issue']}")

        print("\n[5] order B: create, then void the authorization")
        _order_b, auth_b = await create_authorized_order(paypal, "B")
        # With "Prefer: return=representation" the void answers 200 with the authorization, not 204.
        voided = await paypal.call(
            "POST", f"/v2/payments/authorizations/{auth_b}/void", expect=200
        )
        check(
            voided["status"] == "VOIDED", "void answered with the voided authorization"
        )
        fetched_b = await paypal.call(
            "GET", f"/v2/payments/authorizations/{auth_b}", expect=200
        )
        check(fetched_b["status"] == "VOIDED", "authorization B reads back as VOIDED")

        print("\nS2 and S3 pass.")
        print(f"  card-funded capture: {capture['id']}")
        print(f"  mandate hash in custom_id: {MANDATE_HASH}")


if __name__ == "__main__":
    asyncio.run(main())
