"""Every credential in `.env` answers: both PayPal apps, the webhook, and the Anthropic key.

Run from the repo root: uv run --frozen --env-file .env python spikes/connections.py
"""

import asyncio
import json
import os
import sys

import httpx2
from sandbox import check, connect, http_client

ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
MODEL = "claude-sonnet-5-5"


async def paypal_apps() -> None:
    webhook_id = os.environ["PAYPAL_WEBHOOK_ID"]
    async with http_client() as http:
        print("\n[1] seller app: token, then the webhook named by PAYPAL_WEBHOOK_ID")
        seller = await connect(http)
        page = await seller.call("GET", "/v1/notifications/webhooks", expect=200)
        webhooks = {webhook["id"]: webhook for webhook in page["webhooks"]}
        check(webhook_id in webhooks, f"webhook {webhook_id} exists on the seller app")
        webhook = webhooks[webhook_id]
        print(
            f"  delivers to {webhook['url']} for {len(webhook['event_types'])} event types"
        )

        print("\n[2] buyer app: token")
        buyer = await connect(http, app="buyer")
        page = await buyer.call("GET", "/v1/notifications/webhooks", expect=200)
        check(
            webhook_id not in {webhook["id"] for webhook in page["webhooks"]},
            "buyer app credentials differ from the seller app's",
        )


async def anthropic_call(
    http: httpx2.AsyncClient,
    method: str,
    path: str,
    body: dict[str, object] | None = None,
) -> dict[str, object]:
    response = await http.request(method, path, json=body)
    request_id = response.headers.get("request-id", "")
    print(f"{method} {path} -> {response.status_code}  request id {request_id}")
    payload = response.json()
    if response.status_code != 200:
        print(json.dumps(payload, indent=2))
        sys.exit("expected 200")
    return payload


async def anthropic() -> None:
    headers = {
        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
        "anthropic-version": ANTHROPIC_VERSION,
    }
    async with httpx2.AsyncClient(
        base_url=ANTHROPIC_BASE_URL, headers=headers, timeout=httpx2.Timeout(60.0)
    ) as http:
        print("\n[3] Anthropic: the key, the model, and one short message")
        models = await anthropic_call(http, "GET", "/v1/models?limit=1000")
        check(
            MODEL in {model["id"] for model in models["data"]},
            f"{MODEL} is available to this key",
        )
        reply = await anthropic_call(
            http,
            "POST",
            "/v1/messages",
            {
                "model": MODEL,
                "max_tokens": 8,
                "messages": [
                    {"role": "user", "content": "Reply with the single word OK."}
                ],
            },
        )
        text = reply["content"][0]["text"]
        usage = reply["usage"]
        print(
            f"  {reply['model']} said {text!r}; "
            f"{usage['input_tokens']} tokens in, {usage['output_tokens']} out"
        )
        check(reply["stop_reason"] == "end_turn", "the message round trip completed")


async def main() -> None:
    await paypal_apps()
    await anthropic()
    print("\nAll connections answer.")


if __name__ == "__main__":
    asyncio.run(main())
