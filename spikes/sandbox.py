"""Shared helpers for the sandbox spikes: one token, one printing HTTP caller, one checker."""

import base64
import json
import os
import sys
import uuid
from typing import Any

import httpx2

from palisade_api.paypal.auth import SANDBOX_BASE_URL, PayPalAuth


def http_client() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(base_url=SANDBOX_BASE_URL, timeout=httpx2.Timeout(30.0))


class PayPal:
    def __init__(self, http: httpx2.AsyncClient, token: str) -> None:
        self._http = http
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Prefer": "return=representation",
        }

    async def call(
        self,
        method: str,
        path: str,
        # A JSON Patch (RFC 6902) body is a list of operations.
        body: dict[str, Any] | list[Any] | None = None,
        *,
        expect: int,
        files: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        response = await self._http.request(
            method,
            path,
            json=body,
            files=files,
            headers={
                **self._headers,
                **(headers or {}),
                "PayPal-Request-Id": str(uuid.uuid4()),
            },
        )
        payload = response.json() if response.content else None
        debug_id = response.headers.get("paypal-debug-id", "")
        print(f"{method} {path} -> {response.status_code}  debug id {debug_id}")
        if response.status_code != expect:
            print(json.dumps(payload, indent=2))
            sys.exit(f"expected {expect}")
        return payload

    async def probe(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
        files: dict[str, Any] | None = None,
    ) -> tuple[int, Any, str]:
        """Like `call`, but reports the answer instead of exiting: for calls whose failure is the finding."""
        response = await self._http.request(
            method,
            path,
            json=body,
            files=files,
            headers={
                **self._headers,
                **(headers or {}),
                "PayPal-Request-Id": str(uuid.uuid4()),
            },
        )
        payload = response.json() if response.content else None
        return (
            response.status_code,
            payload,
            response.headers.get("paypal-debug-id", ""),
        )


async def connect(http: httpx2.AsyncClient, app: str = "seller") -> PayPal:
    """Token for the seller app (`PAYPAL_*`) or the demo buyer's app (`PAYPAL_BUYER_*`)."""
    prefix = "PAYPAL" if app == "seller" else "PAYPAL_BUYER"
    auth = PayPalAuth(
        http, os.environ[f"{prefix}_CLIENT_ID"], os.environ[f"{prefix}_CLIENT_SECRET"]
    )
    return PayPal(http, (await auth.token()).value)


def check(condition: bool, message: str) -> None:
    if not condition:
        sys.exit(f"FAILED: {message}")
    print(f"  ok: {message}")


def auth_assertion(account: dict[str, str]) -> str:
    """Unsigned JWT that makes a call act for the PayPal account named by `account`.

    `account` is `{"email": ...}` or `{"payer_id": ...}`; the account must have granted the
    app consent first (Log in with PayPal).
    """

    def part(claims: dict[str, str]) -> str:
        raw = json.dumps(claims, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    header = part({"alg": "none", "typ": "JWT"})
    payload = part({"iss": os.environ["PAYPAL_CLIENT_ID"], **account})
    return f"{header}.{payload}."
