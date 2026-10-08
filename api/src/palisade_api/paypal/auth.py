"""Client-credentials access tokens for the PayPal sandbox."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, final

import httpx2
from pydantic import BaseModel

SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"
REFRESH_MARGIN = timedelta(minutes=5)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"]
    expires_in: int
    scope: str
    app_id: str


@dataclass(frozen=True)
class AccessToken:
    value: str
    app_id: str
    scopes: tuple[str, ...]
    expires_at: datetime


@final
class PayPalAuth:
    """Fetches an access token and reuses it until shortly before it expires."""

    def __init__(
        self, http: httpx2.AsyncClient, client_id: str, client_secret: str
    ) -> None:
        self._http = http
        self._credentials = httpx2.BasicAuth(client_id, client_secret)
        self._token: AccessToken | None = None
        self._lock = asyncio.Lock()

    async def token(self) -> AccessToken:
        async with self._lock:
            if (
                self._token is None
                or self._token.expires_at - REFRESH_MARGIN <= datetime.now(UTC)
            ):
                self._token = await self._fetch()
            return self._token

    async def _fetch(self) -> AccessToken:
        response = await self._http.post(
            "/v1/oauth2/token",
            auth=self._credentials,
            data={"grant_type": "client_credentials"},
        )
        response.raise_for_status()
        body = TokenResponse.model_validate_json(response.content)
        return AccessToken(
            value=body.access_token,
            app_id=body.app_id,
            scopes=tuple(body.scope.split()),
            expires_at=datetime.now(UTC) + timedelta(seconds=body.expires_in),
        )
