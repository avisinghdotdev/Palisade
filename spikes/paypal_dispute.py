"""Spike S4 and S5: a sandbox dispute created, answered with evidence, and adjudicated by API.

The sandbox files a dispute as the buyer, and only a PayPal account can be a buyer, so the demo
buyer is a sandbox business account with its own REST app (`PAYPAL_BUYER_*` in `.env`): that
app's token is the buyer. The buyer saves their wallet once (a browser step), later orders pass
the vault id, and the seller app handles the dispute.

Run from the repo root as `uv run --frozen --env-file .env python spikes/paypal_dispute.py STEP`,
one step at a time; each step waits for the sandbox, which takes minutes per state change and
hours before a claim can be adjudicated:

    vault-start                              seller app: prints the approval link for the buyer
    vault-finish SETUP_TOKEN_ID              seller app: saves the wallet, prints the vault id
    order VAULT_ID                           seller app: AUTHORIZE order from the wallet, capture
    --app buyer dispute BUYER_TRANSACTION_ID buyer app: files the dispute
    show DISPUTE_ID                          seller app: state, links, evidence
    evidence DISPUTE_ID                      seller app: notes, tracking and a PDF
    escalate DISPUTE_ID                      seller app: inquiry to claim, then evidence again
    adjudicate DISPUTE_ID                    seller app: sandbox rules for the seller
    get PATH                                 any GET, for exploring

The buyer transaction id is the one on the buyer's activity page; the seller's capture id is
refused. `dispute --buyer-email EMAIL` or `dispute --buyer-payer-id ID` with the seller app
reproduces the refused `PayPal-Auth-Assertion` call for the PayPal support ticket.
"""

import argparse
import asyncio
import hashlib
import json

from sandbox import PayPal, auth_assertion, check, connect, http_client

AMOUNT = {"currency_code": "USD", "value": "95.00"}
MANDATE_HASH = hashlib.sha256(
    b"intent: running shoes, size 10, up to 120 USD, free returns"
).hexdigest()


async def vault_start(paypal: PayPal) -> None:
    setup = await paypal.call(
        "POST",
        "/v3/vault/setup-tokens",
        {
            "payment_source": {
                "paypal": {
                    "usage_type": "MERCHANT",
                    "experience_context": {
                        "return_url": "https://example.com/palisade/return",
                        "cancel_url": "https://example.com/palisade/cancel",
                    },
                }
            }
        },
        expect=201,
    )
    approve = next(link["href"] for link in setup["links"] if link["rel"] == "approve")
    print(f"  setup token {setup['id']} status {setup['status']}")
    print(f"  approve as the sandbox buyer: {approve}")


async def vault_finish(paypal: PayPal, setup_token_id: str) -> None:
    token = await paypal.call(
        "POST",
        "/v3/vault/payment-tokens",
        {"payment_source": {"token": {"id": setup_token_id, "type": "SETUP_TOKEN"}}},
        expect=201,
    )
    wallet = token["payment_source"]["paypal"]
    print(f"  vault id {token['id']}")
    print(f"  buyer payer id {wallet['payer_id']} email {wallet['email_address']}")


async def order(paypal: PayPal, vault_id: str) -> None:
    created = await paypal.call(
        "POST",
        "/v2/checkout/orders",
        {
            "intent": "AUTHORIZE",
            "purchase_units": [{"custom_id": MANDATE_HASH, "amount": AMOUNT}],
            "payment_source": {"paypal": {"vault_id": vault_id}},
        },
        expect=201,
    )
    print(f"  order {created['id']} status {created['status']}")
    check(
        created["status"] == "COMPLETED", "vaulted wallet order completed with no click"
    )
    authorization = created["purchase_units"][0]["payments"]["authorizations"][0]
    capture = await paypal.call(
        "POST",
        f"/v2/payments/authorizations/{authorization['id']}/capture",
        {"final_capture": True},
        expect=201,
    )
    check(capture["status"] == "COMPLETED", "capture completed")
    check(capture.get("custom_id") == MANDATE_HASH, "custom_id on the capture")
    print(f"  capture (seller transaction id) {capture['id']}")
    print(f"  payer {json.dumps(created.get('payer'))}")


async def dispute(
    paypal: PayPal, transaction_id: str, subject: dict[str, str] | None
) -> None:
    """Files an item-not-received dispute; the body is JSON, not the documented multipart.

    `subject` names the buyer for a `PayPal-Auth-Assertion` sent with the seller app's token.
    """
    body = {
        "disputed_transactions": [{"buyer_transaction_id": transaction_id}],
        "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        "dispute_amount": AMOUNT,
        "extensions": {
            "merchant_contacted": True,
            "merchant_contacted_outcome": "NO_RESPONSE",
            "merchandize_dispute_properties": {"issue_type": "PRODUCT"},
        },
    }
    headers = {"PayPal-Auth-Assertion": auth_assertion(subject)} if subject else {}
    created = await paypal.call(
        "POST", "/v1/customer/disputes", body, headers=headers, expect=201
    )
    print(json.dumps(created, indent=2))


async def get(paypal: PayPal, path: str) -> None:
    print(json.dumps(await paypal.call("GET", path, expect=200), indent=2))


async def show(paypal: PayPal, dispute_id: str) -> None:
    await get(paypal, f"/v1/customer/disputes/{dispute_id}")


async def escalate(paypal: PayPal, dispute_id: str) -> None:
    await paypal.call(
        "POST",
        f"/v1/customer/disputes/{dispute_id}/escalate",
        {"note": "Evidence of the buyer's signed mandate is attached; please review."},
        expect=200,
    )
    await show(paypal, dispute_id)


def minimal_pdf(text: str) -> bytes:
    """A one-page PDF with `text`, built by hand so the spike needs no PDF library."""
    stream = f"BT /F1 14 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        (
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R"
            b"/Resources<</Font<</F1 5 0 R>>>>>>"
        ),
        b"<</Length "
        + str(len(stream)).encode()
        + b">>stream\n"
        + stream
        + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<</Size {len(objects) + 1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


async def evidence(paypal: PayPal, dispute_id: str) -> None:
    """Seller evidence as the documented multipart form: an `input` JSON part and a PDF part.

    An item-not-received dispute accepts PROOF_OF_FULFILLMENT with tracking and refuses OTHER;
    the mandate narrative travels in the notes and the attached PDF.
    """
    body = {
        "evidences": [
            {
                "evidence_type": "PROOF_OF_FULFILLMENT",
                "evidence_info": {
                    "tracking_info": [
                        {
                            "carrier_name": "USPS",
                            "tracking_number": "9400111899223197428490",
                        }
                    ]
                },
                "notes": (
                    "The buyer's agent placed this order under a mandate the buyer signed: "
                    f"running shoes, size 10, up to 120 USD. Mandate hash {MANDATE_HASH} "
                    "is recorded in the order's custom_id. The attached PDF is the signed "
                    "mandate record."
                ),
            }
        ]
    }
    await paypal.call(
        "POST",
        f"/v1/customer/disputes/{dispute_id}/provide-evidence",
        files={
            "input": (None, json.dumps(body), "application/json"),
            "evidence-file": (
                "mandate-record.pdf",
                minimal_pdf("Palisade signed mandate record"),
                "application/pdf",
            ),
        },
        expect=200,
    )
    await show(paypal, dispute_id)


async def adjudicate(paypal: PayPal, dispute_id: str) -> None:
    await paypal.call(
        "POST",
        f"/v1/customer/disputes/{dispute_id}/adjudicate",
        {"adjudication_outcome": "SELLER_FAVOR"},
        expect=200,
    )
    await show(paypal, dispute_id)


def buyer_subject(args: argparse.Namespace) -> dict[str, str] | None:
    if args.buyer_email:
        return {"email": args.buyer_email}
    if args.buyer_payer_id:
        return {"payer_id": args.buyer_payer_id}
    return None


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--app",
        choices=("seller", "buyer"),
        default="seller",
        help="whose REST app makes the calls",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("vault-start")
    commands.add_parser("vault-finish").add_argument("setup_token_id")
    commands.add_parser("order").add_argument("vault_id")
    dispute_parser = commands.add_parser("dispute")
    dispute_parser.add_argument("transaction_id")
    subject = dispute_parser.add_mutually_exclusive_group()
    subject.add_argument("--buyer-email")
    subject.add_argument("--buyer-payer-id")
    commands.add_parser("get").add_argument("path")
    for name in ("show", "escalate", "evidence", "adjudicate"):
        commands.add_parser(name).add_argument("dispute_id")
    args = parser.parse_args()

    async with http_client() as http:
        paypal = await connect(http, args.app)
        match args.command:
            case "vault-start":
                await vault_start(paypal)
            case "vault-finish":
                await vault_finish(paypal, args.setup_token_id)
            case "order":
                await order(paypal, args.vault_id)
            case "dispute":
                await dispute(paypal, args.transaction_id, buyer_subject(args))
            case "get":
                await get(paypal, args.path)
            case "show":
                await show(paypal, args.dispute_id)
            case "escalate":
                await escalate(paypal, args.dispute_id)
            case "evidence":
                await evidence(paypal, args.dispute_id)
            case "adjudicate":
                await adjudicate(paypal, args.dispute_id)


if __name__ == "__main__":
    asyncio.run(main())
