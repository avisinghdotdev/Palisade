"""Spike S7: can the seller app act for the personal buyer with `PayPal-Auth-Assertion`?

PayPal support asked for the buyer's payer id in both `payer_id` and `sub` instead of the email.
This spike finds the buyer's payer id in the seller's transaction history, then sends every
assertion shape to the buyer-side dispute endpoints and reports each answer with its debug id.

Run from the repo root as `uv run --frozen --env-file .env python spikes/paypal_buyer_assertion.py COMMAND`:

    transactions                                  seller app: the last 31 days of transactions with payer ids
    probe PAYER_ID TRANSACTION_ID [--email EMAIL] seller app: list and create disputes as that buyer, one assertion shape at a time
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sandbox import PayPal, auth_assertion, connect, http_client

DISPUTE_BODY = {
    "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
    "dispute_amount": {"currency_code": "USD", "value": "95.00"},
    "extensions": {
        "merchant_contacted": True,
        "merchant_contacted_outcome": "NO_RESPONSE",
        "merchandize_dispute_properties": {"issue_type": "PRODUCT"},
    },
}


async def transactions(paypal: PayPal) -> None:
    end = datetime.now(UTC)
    start = end - timedelta(days=31)
    stamp = "%Y-%m-%dT%H:%M:%S-0000"
    page = await paypal.call(
        "GET",
        "/v1/reporting/transactions"
        f"?start_date={start.strftime(stamp)}&end_date={end.strftime(stamp)}"
        "&fields=transaction_info,payer_info&page_size=500",
        expect=200,
    )
    print(f"  {page['total_items']} transactions")
    for detail in page["transaction_details"]:
        info = detail["transaction_info"]
        payer = detail.get("payer_info", {})
        amount = info.get("transaction_amount", {})
        print(
            f"  {info['transaction_initiation_date'][:19]}  {info['transaction_id']}  "
            f"{info['transaction_event_code']}  {info['transaction_status']}  "
            f"{amount.get('value', '')} {amount.get('currency_code', '')}  "
            f"payer {payer.get('account_id', '-')}  {payer.get('email_address', '-')}  "
            f"ref {info.get('paypal_reference_id', '-')} ({info.get('paypal_reference_id_type', '-')})"
        )


def shapes(payer_id: str, email: str | None) -> dict[str, dict[str, str]]:
    shapes = {
        "payer_id + sub (PayPal's suggestion)": {"payer_id": payer_id, "sub": payer_id},
        "payer_id only": {"payer_id": payer_id},
    }
    if email:
        shapes["email (the documented shape)"] = {"email": email}
    return shapes


def report(label: str, status: int, payload: Any, debug_id: str) -> None:
    print(f"    {label}: {status}  debug id {debug_id}")
    if payload is not None and status >= 400:
        print(f"      {json.dumps(payload)[:400]}")


async def probe(
    paypal: PayPal, payer_id: str, transaction_id: str, email: str | None
) -> None:
    for name, claims in shapes(payer_id, email).items():
        headers = {"PayPal-Auth-Assertion": auth_assertion(claims)}
        print(f"\n  assertion {name}: {json.dumps(claims)}")
        status, payload, debug_id = await paypal.probe(
            "GET", "/v1/customer/disputes?page_size=5", headers=headers
        )
        report("list disputes as the buyer", status, payload, debug_id)
        body = {
            **DISPUTE_BODY,
            "disputed_transactions": [{"buyer_transaction_id": transaction_id}],
        }
        status, payload, debug_id = await paypal.probe(
            "POST", "/v1/customer/disputes", body, headers=headers
        )
        report("create dispute as the buyer", status, payload, debug_id)
        if status == 201:
            print(f"      created: {json.dumps(payload)[:400]}")
            print("  stopping: one dispute is enough")
            return


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--app",
        choices=("seller", "buyer"),
        default="seller",
        help="whose REST app makes the calls; `buyer` lists the demo buyer's own transactions",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("transactions")
    probing = commands.add_parser("probe")
    probing.add_argument("payer_id")
    probing.add_argument("transaction_id")
    probing.add_argument("--email")
    args = parser.parse_args()

    async with http_client() as http:
        paypal = await connect(http, args.app)
        if args.command == "transactions":
            await transactions(paypal)
        else:
            await probe(paypal, args.payer_id, args.transaction_id, args.email)


if __name__ == "__main__":
    asyncio.run(main())
