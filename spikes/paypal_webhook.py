"""Spike S6: the sandbox webhook that delivers to the public API.

Run from the repo root as `uv run --frozen --env-file .env python spikes/paypal_webhook.py COMMAND`,
always with the seller app:

    create URL          subscribe URL to the events Palisade handles; prints the webhook id
    update WEBHOOK_ID   replace a webhook's events with the events Palisade handles
    list                webhooks on the app
    delete WEBHOOK_ID   remove a webhook
    events              the last 20 notifications PayPal sent, newest first
"""

import argparse
import asyncio

from sandbox import PayPal, connect, http_client

# Every event a handler in the API reads (orders/webhooks.py, wallets/webhooks.py), plus the
# order approval and the dispute events the store keeps for the console and for M3.
EVENT_TYPES = [
    "CHECKOUT.ORDER.APPROVED",
    "PAYMENT.AUTHORIZATION.CREATED",
    "PAYMENT.AUTHORIZATION.VOIDED",
    "PAYMENT.CAPTURE.PENDING",
    "PAYMENT.CAPTURE.COMPLETED",
    "PAYMENT.CAPTURE.DECLINED",
    "PAYMENT.CAPTURE.REFUNDED",
    "PAYMENT.CAPTURE.REVERSED",
    "VAULT.PAYMENT-TOKEN.DELETED",
    "CUSTOMER.DISPUTE.CREATED",
    "CUSTOMER.DISPUTE.UPDATED",
    "CUSTOMER.DISPUTE.RESOLVED",
]


async def create(paypal: PayPal, url: str) -> None:
    webhook = await paypal.call(
        "POST",
        "/v1/notifications/webhooks",
        {"url": url, "event_types": [{"name": name} for name in EVENT_TYPES]},
        expect=201,
    )
    print(f"  webhook id {webhook['id']} for {webhook['url']}")


async def update(paypal: PayPal, webhook_id: str) -> None:
    webhook = await paypal.call(
        "PATCH",
        f"/v1/notifications/webhooks/{webhook_id}",
        [
            {
                "op": "replace",
                "path": "/event_types",
                "value": [{"name": name} for name in EVENT_TYPES],
            }
        ],
        expect=200,
    )
    names = ", ".join(event["name"] for event in webhook["event_types"])
    print(f"  webhook {webhook['id']} now listens to {names}")


async def list_webhooks(paypal: PayPal) -> None:
    page = await paypal.call("GET", "/v1/notifications/webhooks", expect=200)
    for webhook in page["webhooks"]:
        names = ", ".join(event["name"] for event in webhook["event_types"])
        print(f"  {webhook['id']} {webhook['url']}\n    {names}")


async def delete(paypal: PayPal, webhook_id: str) -> None:
    await paypal.call("DELETE", f"/v1/notifications/webhooks/{webhook_id}", expect=204)


async def events(paypal: PayPal) -> None:
    page = await paypal.call(
        "GET", "/v1/notifications/webhooks-events?page_size=20", expect=200
    )
    for event in page["events"]:
        print(
            f"  {event['create_time']} {event['event_type']} {event['id']} {event['summary']}"
        )


async def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create").add_argument("url")
    commands.add_parser("update").add_argument("webhook_id")
    commands.add_parser("list")
    commands.add_parser("delete").add_argument("webhook_id")
    commands.add_parser("events")
    args = parser.parse_args()

    async with http_client() as http:
        paypal = await connect(http)
        match args.command:
            case "create":
                await create(paypal, args.url)
            case "update":
                await update(paypal, args.webhook_id)
            case "list":
                await list_webhooks(paypal)
            case "delete":
                await delete(paypal, args.webhook_id)
            case "events":
                await events(paypal)


if __name__ == "__main__":
    asyncio.run(main())
