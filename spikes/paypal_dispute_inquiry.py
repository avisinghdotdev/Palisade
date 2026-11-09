"""Spike: the inquiry-stage actions PayPal offers on the shop's disputes, and the send-message shape.

Run from the repo root as `uv run --frozen --env-file .env python spikes/paypal_dispute_inquiry.py STEP`:

    options                  seller app: every dispute PayPal lists for the shop since its first
        order, with stage, status, the links offered, and `allowed_response_options`
    messages DISPUTE_ID      seller app: the dispute's message thread
    message DISPUTE_ID TEXT  seller app: send-message, first as multipart with an `input` JSON
        part (the provide-evidence shape), then as a plain JSON body when PayPal refuses the
        first; prints which shape PayPal took. Proven 2026-11-08 on `PP-R-ZCC-10196688`: the
        multipart shape answered 200 and the message appeared in the thread as SELLER

Nothing here accepts a claim or makes an offer: both move sandbox money or pause the dispute.
"""

import argparse
import asyncio
import json
from typing import Any

from sandbox import PayPal, connect, http_client

# The shop's first order was placed on 2026-10-23; a dispute follows its payment.
FIRST_ORDER = "2026-10-23T00:00:00.000Z"


def rels(detail: dict[str, Any]) -> list[str]:
    return sorted(
        link["rel"] for link in detail.get("links", []) if link["rel"] != "self"
    )


async def options(paypal: PayPal) -> None:
    listed = await paypal.call(
        "GET",
        f"/v1/customer/disputes?start_time={FIRST_ORDER}&page_size=50",
        expect=200,
    )
    for item in listed.get("items", []):
        detail = await paypal.call(
            "GET", f"/v1/customer/disputes/{item['dispute_id']}", expect=200
        )
        print(
            f"  {detail['dispute_id']}  {detail.get('dispute_life_cycle_stage')}  "
            f"{detail['status']}  {detail['reason']}"
        )
        print(f"    links: {', '.join(rels(detail)) or 'none'}")
        print(
            f"    allowed_response_options: "
            f"{json.dumps(detail.get('allowed_response_options'))}"
        )
        print(f"    messages: {len(detail.get('messages', []))}")


async def messages(paypal: PayPal, dispute_id: str) -> None:
    detail = await paypal.call("GET", f"/v1/customer/disputes/{dispute_id}", expect=200)
    for message in detail.get("messages", []):
        print(
            f"  {message.get('time_posted')}  {message.get('posted_by')}: "
            f"{message.get('content')}"
        )


async def message(paypal: PayPal, dispute_id: str, text: str) -> None:
    path = f"/v1/customer/disputes/{dispute_id}/send-message"
    status, payload, debug_id = await paypal.probe(
        "POST",
        path,
        files={"input": (None, json.dumps({"message": text}), "application/json")},
    )
    print(f"  multipart with an `input` part -> {status}  debug id {debug_id}")
    if status != 200:
        print(json.dumps(payload, indent=2))
        status, payload, debug_id = await paypal.probe("POST", path, {"message": text})
        print(f"  plain JSON body -> {status}  debug id {debug_id}")
        if status != 200:
            print(json.dumps(payload, indent=2))
            return
    print(f"  answer: {json.dumps(payload)}")
    await messages(paypal, dispute_id)


async def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("options")
    commands.add_parser("messages").add_argument("dispute_id")
    sending = commands.add_parser("message")
    sending.add_argument("dispute_id")
    sending.add_argument("text")
    args = parser.parse_args()

    async with http_client() as http:
        paypal = await connect(http)
        match args.command:
            case "options":
                await options(paypal)
            case "messages":
                await messages(paypal, args.dispute_id)
            case "message":
                await message(paypal, args.dispute_id, args.text)


if __name__ == "__main__":
    asyncio.run(main())
