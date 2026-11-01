"""Spike S7: can the demo buyer's app find its own transaction id for a seller capture?

A dispute filed by API needs the buyer's transaction id, which differs from the seller's capture
id. Transaction Search (`GET /v1/reporting/transactions`) lists an account's own transactions
with the `custom_field` the order carried, so the buyer app may be able to look its id up by the
order's `custom_id`. This spike measures whether the buyer app has the scope, what fields come
back, and how long a payment takes to appear.

Run from the repo root as `uv run --frozen --env-file .env python spikes/paypal_transaction_search.py STEP`:

    scopes [--app buyer|seller]          print the token's scopes; look for reporting/search/read
    list [--app buyer|seller] [--days N] the account's transactions of the last N days (default 2)
    find CAPTURE_ID                      the buyer-side transaction for a seller capture id,
        matched on custom field and amount; prints both sides
    by-id TRANSACTION_ID [--app buyer|seller]   the `transaction_id` filter on either side
"""

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from sandbox import PayPal, http_client

from palisade_api.paypal.auth import PayPalAuth

FIELDS = "transaction_info,payer_info,cart_info"


async def token(app: str) -> tuple[str, tuple[str, ...]]:
    prefix = "PAYPAL" if app == "seller" else "PAYPAL_BUYER"
    async with http_client() as http:
        auth = PayPalAuth(
            http,
            os.environ[f"{prefix}_CLIENT_ID"],
            os.environ[f"{prefix}_CLIENT_SECRET"],
        )
        access = await auth.token()
        return access.value, access.scopes


def window(days: int) -> dict[str, str]:
    now = datetime.now(UTC)
    stamp = "%Y-%m-%dT%H:%M:%S-0000"
    return {
        "start_date": (now - timedelta(days=days)).strftime(stamp),
        "end_date": now.strftime(stamp),
    }


def line(transaction: dict[str, Any]) -> str:
    info = transaction["transaction_info"]
    amount = info.get("transaction_amount", {})
    payer = transaction.get("payer_info", {}).get("email_address", "")
    return (
        f"  {info['transaction_initiation_date']}  {info['transaction_id']}  "
        f"{info.get('transaction_event_code')}  {info.get('transaction_status')}  "
        f"{amount.get('value')} {amount.get('currency_code')}  "
        f"ref {info.get('paypal_reference_id')} ({info.get('paypal_reference_id_type')})  "
        f"custom {info.get('custom_field')}  payer {payer}"
    )


async def search(paypal: PayPal, params: dict[str, str]) -> list[dict[str, Any]]:
    query = "&".join(f"{key}={value}" for key, value in params.items())
    page = await paypal.call("GET", f"/v1/reporting/transactions?{query}", expect=200)
    print(
        f"  page {page.get('page')} of {page.get('total_pages')}, "
        f"{page.get('total_items')} items, last refreshed {page.get('last_refreshed_datetime')}"
    )
    return page.get("transaction_details", [])


async def scopes(app: str) -> None:
    _, granted = await token(app)
    for scope in granted:
        print(f"  {scope}")
    print(
        "  reporting/search/read: "
        + ("yes" if any("reporting/search/read" in s for s in granted) else "NO")
    )


async def list_transactions(app: str, days: int) -> None:
    value, _ = await token(app)
    async with http_client() as http:
        paypal = PayPal(http, value)
        for transaction in await search(
            paypal, {**window(days), "fields": FIELDS, "page_size": "100"}
        ):
            print(line(transaction))


async def by_id(app: str, transaction_id: str) -> None:
    value, _ = await token(app)
    async with http_client() as http:
        paypal = PayPal(http, value)
        for transaction in await search(
            paypal,
            {**window(31), "transaction_id": transaction_id, "fields": FIELDS},
        ):
            print(line(transaction))
            print(json.dumps(transaction, indent=2)[:3000])


async def find(capture_id: str) -> None:
    """The seller's view of the capture, then the buyer's transactions that match it."""
    seller_token, _ = await token("seller")
    buyer_token, _ = await token("buyer")
    async with http_client() as http:
        seller = PayPal(http, seller_token)
        capture = await seller.call(
            "GET", f"/v2/payments/captures/{capture_id}", expect=200
        )
        custom = capture.get("custom_id")
        amount = capture["amount"]["value"]
        created = capture["create_time"]
        print(
            f"  seller capture {capture_id}: {amount} USD at {created}, custom_id {custom}"
        )
        buyer = PayPal(http, buyer_token)
        rows = await search(buyer, {**window(31), "fields": FIELDS, "page_size": "500"})
        matches = [
            row
            for row in rows
            if row["transaction_info"].get("custom_field") == custom
            and row["transaction_info"].get("transaction_amount", {}).get("value")
            in {amount, f"-{amount}"}
        ]
        print(
            f"  buyer-side rows with the same custom field and amount: {len(matches)}"
        )
        for row in matches:
            print(line(row))
        exact = [
            row
            for row in rows
            if row["transaction_info"].get("paypal_reference_id") == capture_id
            or row["transaction_info"]["transaction_id"] == capture_id
        ]
        print(f"  buyer-side rows naming the capture id itself: {len(exact)}")
        for row in exact:
            print(line(row))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", choices=("seller", "buyer"), default="buyer")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("scopes")
    commands.add_parser("list").add_argument("--days", type=int, default=2)
    commands.add_parser("find").add_argument("capture_id")
    commands.add_parser("by-id").add_argument("transaction_id")
    args = parser.parse_args()
    match args.command:
        case "scopes":
            await scopes(args.app)
        case "list":
            await list_transactions(args.app, args.days)
        case "find":
            await find(args.capture_id)
        case "by-id":
            await by_id(args.app, args.transaction_id)


if __name__ == "__main__":
    asyncio.run(main())
