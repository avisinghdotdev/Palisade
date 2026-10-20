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
    --app buyer dispute BUYER_TRANSACTION_ID [--reason REASON] [--accelerated]
        buyer app: files the dispute; --accelerated asks for Accelerated Response (a 10-day
        inquiry instead of 20; INR and SNAD only)
    show DISPUTE_ID                          seller app: state, links, evidence
    wait-for DISPUTE_ID REL [MINUTES]        seller app: poll until the HATEOAS link REL appears
    --app buyer change-reason DISPUTE_ID REASON   buyer app: new reason (SNAD, UNAUTHORISED, OTHER, ...)
    require-evidence DISPUTE_ID ACTION       seller app, sandbox only: SELLER_EVIDENCE or BUYER_EVIDENCE
    evidence DISPUTE_ID [--type TYPE]        seller app: PROOF_OF_FULFILLMENT (default) or OTHER, with a PDF
    escalate DISPUTE_ID                      seller app: inquiry to claim, then evidence again
    adjudicate DISPUTE_ID                    seller app: sandbox rules for the seller
    track ORDER_ID CAPTURE_ID                seller app: USPS tracker on the capture
    refund CAPTURE_ID [AMOUNT]               seller app: refund, full when no amount
    chargeback CAPTURE_ID MERCHANT_ID        seller app, sandbox only: card chargeback on a card capture
    get PATH                                 any GET, for exploring

The buyer transaction id is the one on the buyer's activity page; the seller's capture id is
refused. `dispute --buyer-email EMAIL` or `dispute --buyer-payer-id ID` with the seller app
reproduces the refused `PayPal-Auth-Assertion` call for the PayPal support ticket.
"""

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

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
    paypal: PayPal,
    transaction_id: str,
    subject: dict[str, str] | None,
    reason: str,
    accelerated: bool,
) -> None:
    """Files a dispute; the body is JSON, not the documented multipart.

    `subject` names the buyer for a `PayPal-Auth-Assertion` sent with the seller app's token.
    `accelerated` asks the sandbox for Accelerated Response (a 10-day inquiry instead of 20),
    which PayPal's test guide enables through a note on the create call.
    """
    body: dict[str, Any] = {
        "disputed_transactions": [{"buyer_transaction_id": transaction_id}],
        "reason": reason,
        "dispute_amount": AMOUNT,
        "extensions": {
            "merchant_contacted": True,
            "merchant_contacted_outcome": "NO_RESPONSE",
            "merchandize_dispute_properties": {"issue_type": "PRODUCT"},
        },
    }
    if accelerated:
        body["evidences"] = [{"notes": "ACCELERATED_RESPONSE_ENABLED"}]
    headers = {"PayPal-Auth-Assertion": auth_assertion(subject)} if subject else {}
    created = await paypal.call(
        "POST", "/v1/customer/disputes", body, headers=headers, expect=201
    )
    print(json.dumps(created, indent=2))


async def get(paypal: PayPal, path: str) -> None:
    print(json.dumps(await paypal.call("GET", path, expect=200), indent=2))


async def show(paypal: PayPal, dispute_id: str) -> None:
    await get(paypal, f"/v1/customer/disputes/{dispute_id}")


async def summary(paypal: PayPal, dispute_id: str) -> dict[str, Any]:
    """One line of state: status, stage, reason, and the actions PayPal currently offers."""
    detail = await paypal.call("GET", f"/v1/customer/disputes/{dispute_id}", expect=200)
    rels = sorted(
        link["rel"] for link in detail.get("links", []) if link["rel"] != "self"
    )
    print(
        f"  {detail['status']}  {detail['dispute_life_cycle_stage']}  {detail['reason']}  "
        f"links: {', '.join(rels) or 'none'}"
    )
    return detail


async def wait_for(paypal: PayPal, dispute_id: str, rel: str, minutes: int) -> None:
    """Polls once a minute until the link `rel` is offered; sandbox actions appear minutes late."""
    for attempt in range(minutes + 1):
        detail = await summary(paypal, dispute_id)
        if any(link["rel"] == rel for link in detail.get("links", [])):
            print(f"  {rel} available after {attempt} minute(s)")
            return
        await asyncio.sleep(60)
    sys.exit(f"FAILED: {rel} not offered within {minutes} minutes")


async def change_reason(paypal: PayPal, dispute_id: str, reason: str) -> None:
    """Buyer side: the demo buyer's own app changes the reason; the seller app is refused."""
    await paypal.call(
        "POST",
        f"/v1/customer/disputes/{dispute_id}/change-reason",
        {
            "reason": reason,
            "note": "The item arrived but does not match the listing; changing the reason.",
        },
        expect=200,
    )
    await summary(paypal, dispute_id)


async def require_evidence(paypal: PayPal, dispute_id: str, action: str) -> None:
    """Sandbox only: moves the dispute to the waiting-for-buyer or waiting-for-seller state."""
    await paypal.call(
        "POST",
        f"/v1/customer/disputes/{dispute_id}/require-evidence",
        {"action": action},
        expect=200,
    )
    await summary(paypal, dispute_id)


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


NOTES = (
    "The buyer's agent placed this order under a mandate the buyer signed: running shoes, "
    f"size 10, up to 120 USD. Mandate hash {MANDATE_HASH} is recorded in the order's "
    "custom_id. The attached PDF is the signed mandate record."
)


async def evidence(paypal: PayPal, dispute_id: str, evidence_type: str) -> None:
    """Seller evidence as the documented multipart form: an `input` JSON part and a PDF part.

    PROOF_OF_FULFILLMENT carries tracking and suits item-not-received; OTHER carries only notes
    and the attached documents, which is what the mandate record needs for every other reason.
    """
    item: dict[str, Any] = {"evidence_type": evidence_type, "notes": NOTES}
    if evidence_type == "PROOF_OF_FULFILLMENT":
        item["evidence_info"] = {
            "tracking_info": [
                {"carrier_name": "USPS", "tracking_number": "9400111899223197428490"}
            ]
        }
    body = {"evidences": [item]}
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


async def track(paypal: PayPal, order_id: str, capture_id: str) -> None:
    """Adds a USPS tracker to a capture through the order, so fulfillment evidence has it."""
    updated = await paypal.call(
        "POST",
        f"/v2/checkout/orders/{order_id}/track",
        {
            "capture_id": capture_id,
            "tracking_number": "9400111899223197428490",
            "carrier": "USPS",
            "notify_payer": False,
        },
        expect=201,
    )
    unit = updated["purchase_units"][0]
    trackers = unit.get("shipping", {}).get("trackers", [])
    print(f"  trackers on the order: {json.dumps(trackers)[:400]}")
    check(bool(trackers), "the order now carries a tracker")


async def refund(paypal: PayPal, capture_id: str, value: str | None) -> None:
    """Refunds a capture, in full when no amount is given."""
    # An empty JSON object refunds the full amount; no body at all is answered with 415.
    body: dict[str, Any] = (
        {"amount": {"currency_code": "USD", "value": value}} if value else {}
    )
    refunded = await paypal.call(
        "POST", f"/v2/payments/captures/{capture_id}/refund", body, expect=201
    )
    print(
        f"  refund {refunded['id']} status {refunded['status']} "
        f"{json.dumps(refunded.get('amount'))}"
    )


async def chargeback(
    paypal: PayPal, capture_id: str, merchant_id: str, reason_code: str
) -> None:
    """Sandbox only: a card-network chargeback against a card-funded capture.

    The Visa reason code decides the dispute reason PayPal shows: C2 gave not-as-described.
    """
    now = datetime.now(UTC)
    stamp = "%Y-%m-%dT%H:%M:%S.000Z"
    body = {
        "adjacency": "PAYPAL",
        "file_layout": "ATCK_CB",
        "merchant_id": merchant_id,
        "financial_institution": {"processor": "FDMS"},
        "transaction": {"id_enc": capture_id, "amount": AMOUNT},
        "instrument": {
            "instrument_type": "CARD",
            "card_brand": "VISA",
            "credit_card_transaction_id": "2347289342893472",
            "external": False,
        },
        "dispute": {
            "id": None,
            "stage": "CHARGEBACK",
            "response_date": (now + timedelta(days=20)).strftime(stamp),
            "status": "2",
            "amount": AMOUNT,
            "receive_date": now.strftime(stamp),
            "event_code": "CHARGEBACK_INITIATED",
            "money_movement_date": now.strftime(stamp),
            "event_type": "DEBIT",
            "chargeback_type": "REPORTED_TO_PROCESSOR",
            "processor_info": {
                "reference_number": "31639435342702423793399",
                "reason_code": reason_code,
                "notes": "TEST_MEMO",
            },
        },
    }
    created = await paypal.call(
        "POST", "/v2/customer-support/process-chargeback", body, expect=201
    )
    print(f"  chargeback dispute {json.dumps(created)}")
    dispute_id = created["dispute_id"]
    detail = await summary(paypal, dispute_id)
    print(
        f"  dispute_channel {detail.get('dispute_channel')}  "
        f"reason {detail.get('reason')}  outcome {json.dumps(detail.get('dispute_outcome'))}"
    )


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
    dispute_parser.add_argument(
        "--reason", default="MERCHANDISE_OR_SERVICE_NOT_RECEIVED"
    )
    dispute_parser.add_argument("--accelerated", action="store_true")
    subject = dispute_parser.add_mutually_exclusive_group()
    subject.add_argument("--buyer-email")
    subject.add_argument("--buyer-payer-id")
    commands.add_parser("get").add_argument("path")
    for name in ("show", "escalate", "adjudicate"):
        commands.add_parser(name).add_argument("dispute_id")
    waiting = commands.add_parser("wait-for")
    waiting.add_argument("dispute_id")
    waiting.add_argument("rel")
    waiting.add_argument("minutes", nargs="?", type=int, default=15)
    changing = commands.add_parser("change-reason")
    changing.add_argument("dispute_id")
    changing.add_argument("reason")
    requiring = commands.add_parser("require-evidence")
    requiring.add_argument("dispute_id")
    requiring.add_argument("action", choices=("SELLER_EVIDENCE", "BUYER_EVIDENCE"))
    evidencing = commands.add_parser("evidence")
    evidencing.add_argument("dispute_id")
    evidencing.add_argument("--type", default="PROOF_OF_FULFILLMENT")
    tracking = commands.add_parser("track")
    tracking.add_argument("order_id")
    tracking.add_argument("capture_id")
    refunding = commands.add_parser("refund")
    refunding.add_argument("capture_id")
    refunding.add_argument("amount", nargs="?")
    charging = commands.add_parser("chargeback")
    charging.add_argument("capture_id")
    charging.add_argument("merchant_id")
    charging.add_argument("--reason-code", default="C2")
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
                await dispute(
                    paypal,
                    args.transaction_id,
                    buyer_subject(args),
                    args.reason,
                    args.accelerated,
                )
            case "get":
                await get(paypal, args.path)
            case "show":
                await show(paypal, args.dispute_id)
            case "wait-for":
                await wait_for(paypal, args.dispute_id, args.rel, args.minutes)
            case "change-reason":
                await change_reason(paypal, args.dispute_id, args.reason)
            case "require-evidence":
                await require_evidence(paypal, args.dispute_id, args.action)
            case "escalate":
                await escalate(paypal, args.dispute_id)
            case "evidence":
                await evidence(paypal, args.dispute_id, args.type)
            case "track":
                await track(paypal, args.order_id, args.capture_id)
            case "refund":
                await refund(paypal, args.capture_id, args.amount)
            case "chargeback":
                await chargeback(
                    paypal, args.capture_id, args.merchant_id, args.reason_code
                )
            case "adjudicate":
                await adjudicate(paypal, args.dispute_id)


if __name__ == "__main__":
    asyncio.run(main())
