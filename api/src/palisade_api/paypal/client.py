"""Authenticated requests to the PayPal sandbox REST API."""

from typing import final

import httpx2

from palisade_api.paypal.auth import PayPalAuth


@final
class PayPalClient:
    def __init__(self, http: httpx2.AsyncClient, auth: PayPalAuth) -> None:
        self._http = http
        self._auth = auth

    async def post_json(self, path: str, body: bytes) -> httpx2.Response:
        """POST a JSON document given as bytes, so the caller controls its exact wire form."""
        token = await self._auth.token()
        return await self._http.post(
            path,
            content=body,
            headers={
                "Authorization": f"Bearer {token.value}",
                "Content-Type": "application/json",
            },
        )
